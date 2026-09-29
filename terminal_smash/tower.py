"""A finite, scrolling platform route through an inert terminal snapshot."""
from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, replace
import math
import random

from .capture import Cell, Style, _escape, parse_capture
from .model import Player


@dataclass(frozen=True)
class Platform:
    row: int
    left: int
    right: int  # Exclusive, in terminal columns.
    synthetic: bool = False

    @property
    def centre(self) -> float:
        return (self.left + self.right - 1) / 2


class _History:
    """Index ANSI state once; materialize only a bounded number of text rows."""

    CACHE_ROWS = 192

    def __init__(self, text: str, width: int, top: int):
        self.text = text
        self.width = width
        self.top = top
        self.starts = array('Q', [0])
        self.styles = [Style()]
        self.cache: OrderedDict[int, tuple[Cell, ...]] = OrderedDict()
        index = 0
        style = Style()
        while index < len(text):
            char = text[index]
            if char == '\x1b' or '\x80' <= char <= '\x9f':
                index, style = _escape(text, index, style)
                continue
            index += 1
            if char == '\n' and index < len(text):
                self.starts.append(index)
                self.styles.append(style)

    def __len__(self) -> int:
        return len(self.starts)

    def resize(self, width: int) -> None:
        if width != self.width:
            self.width = width
            self.cache.clear()

    def cells(self, row: int) -> tuple[Cell, ...]:
        if not 0 <= row < len(self):
            return ()
        if row in self.cache:
            self.cache.move_to_end(row)
            return self.cache[row]
        start = self.starts[row]
        end = self.starts[row + 1] if row + 1 < len(self) else len(self.text)
        cells = tuple(replace(cell, y=row + self.top) for cell in parse_capture(
            self.text[start:end], self.width, 1, initial_style=self.styles[row]))
        self.cache[row] = cells
        if len(self.cache) > self.CACHE_ROWS:
            self.cache.popitem(last=False)
        return cells


@dataclass
class _RouteNode:
    platform: Platform
    parent: _RouteNode | None = None
    connectors: tuple[Platform, ...] = ()
    cost: int = 0


class _FrontierRow:
    """Find a reachable predecessor without comparing every pair of words."""

    def __init__(self, world: TowerWorld, nodes: list[_RouteNode]):
        self.row = nodes[0].platform.row
        groups: dict[tuple[int, int], list[_RouteNode]] = {}
        for node in nodes:
            preference = abs(node.platform.right - node.platform.left
                             - world.platform_width(node.platform.row))
            groups.setdefault((node.cost, preference), []).append(node)
        self.groups = []
        for cost, items in sorted(groups.items()):
            items.sort(key=lambda node: world.landing_x(node.platform, -math.inf))
            lefts = [world.landing_x(node.platform, -math.inf) for node in items]
            rights = [world.landing_x(node.platform, math.inf) for node in items]
            maximum = []
            best = -math.inf
            for right in rights:
                best = max(best, right)
                maximum.append(best)
            self.groups.append((items, lefts, rights, maximum))

    def parent(self, left: float, right: float, rng: random.Random) -> _RouteNode | None:
        for items, lefts, rights, maximum in self.groups:
            first = bisect_left(maximum, left)
            last = bisect_right(lefts, right)
            if first < last:
                selected = rng.randrange(first, last)
                if rights[selected] < left:
                    selected = first
                return items[selected]
        return None


class _Platforms(list):
    """Selected route, ordered from its actual oldest text to its newest text."""

    def __init__(self, platforms=()):
        super().__init__(platforms)
        self.by_row: dict[int, list[Platform]] = {}
        for platform in self:
            self.by_row.setdefault(platform.row, []).append(platform)

    def at_row(self, row: int) -> Platform | None:
        platforms = self.by_row.get(row, ())
        return platforms[0] if platforms else None


class TowerWorld:
    """Climb selected original glyphs; bridge only disconnected pieces of output.

    Rows remain in snapshot coordinates. A route may include a wide emergency
    bridge: walk along it to choose a takeoff point before the next jump.
    """

    HISTORY_TOP = 14
    ASCENT_GRAVITY = 110.0
    FALL_GRAVITY = 180.0
    GRAVITY = ASCENT_GRAVITY
    JUMP_SPEED = 47.0
    MOVE_SPEED = 45.0
    MAX_FALL_SPEED = 75.0
    SCROLL_START_SPEED = 1.0
    SCROLL_ACCELERATION = 0.025
    SCROLL_MAX_SPEED = 4.0

    def __init__(self, text: str, width: int, viewport_height: int, *, seed: int | None = None):
        self.width = max(8, int(width))
        self.viewport_height = max(6, int(viewport_height))
        self.seed = random.SystemRandom().getrandbits(64) if seed is None else seed
        self._attempt_started = False
        self._position_width = self.width
        self.resize_blocked = False
        self._history = _History(text, self.width, self.HISTORY_TOP)
        self.height = max(self.viewport_height, len(self._history) + self.HISTORY_TOP + 2)
        self.summit_row = self.start_row = self.HISTORY_TOP
        self.floor_row = self.start_row + 3
        self._text_bounds = (self.HISTORY_TOP, self.HISTORY_TOP)
        self.total_climb = 0
        self._configure_jump()
        self._build_route()
        self.reset()

    def _configure_jump(self) -> None:
        # Short viewports need lower jumps and room below the player for the
        # camera chase. Even adjacent original rows must remain landable.
        maximum_rise = min(self.JUMP_SPEED ** 2 / (2 * self.ASCENT_GRAVITY),
                           max(3.0, self.viewport_height - 5.0))
        self.jump_speed = math.sqrt(2 * self.ASCENT_GRAVITY * maximum_rise)
        self.jump_height = maximum_rise
        self.max_step = max(1, math.floor(maximum_rise - 0.65))
        self.max_natural_step = max(1, math.floor(2 * maximum_rise - 0.70))
        self.follow_row = max(1, min(self.viewport_height // 3,
                                    self.viewport_height - math.ceil(maximum_rise) - 4))
        self._jump_options: dict[int, list[tuple[float | None, float]]] = {}
        ascent_time = self.jump_speed / self.ASCENT_GRAVITY
        available = self.viewport_height - self.follow_row - 1
        reserve = min(3.0, available - (maximum_rise - 1))
        landing_room = available - reserve
        for rise in range(self.max_natural_step + 1):
            options = []
            # Include the longest useful second-jump delay that still leaves
            # the destination in view, with a buffer for camera drift during
            # landing and the walk to the next takeoff point.
            extra_height = min(maximum_rise, max(0.0,
                rise + landing_room - maximum_rise))
            latest = (self.jump_speed - math.sqrt(max(0.0,
                self.jump_speed ** 2 - 2 * self.ASCENT_GRAVITY * extra_height))) / self.ASCENT_GRAVITY
            delays = sorted({ascent_time * fraction for fraction in (0.25, 0.5, 0.75, 1.0)} | {latest})
            for delay in [None, *delays]:
                height = maximum_rise if delay is None else maximum_rise + (
                    self.jump_speed * delay - 0.5 * self.ASCENT_GRAVITY * delay ** 2)
                if (height - rise < 0.65
                        or height - rise > landing_room):
                    continue
                flight = (delay or 0) + ascent_time + math.sqrt(2 * (height - rise) / self.FALL_GRAVITY)
                options.append((delay, self.MOVE_SPEED * flight - 3.0))
            self._jump_options[rise] = options

    def landing_x(self, platform: Platform, toward: float | None = None) -> float:
        """A supported player centre, including glyphs at the terminal edges."""
        left = min(self.width - 2.0, max(1.0, float(platform.left)))
        right = min(self.width - 2.0, max(left, float(platform.right - 1)))
        return min(right, max(left, platform.centre if toward is None else toward))

    def _can_jump(self, source: Platform, target: Platform) -> bool:
        rise = source.row - target.row
        if not 0 <= rise <= self.max_natural_step:
            return False
        takeoff = self.landing_x(source, target.centre)
        landing = self.landing_x(target, takeoff)
        return any(abs(landing - takeoff) <= reach for _, reach in self._jump_options[rise])

    def jump_delay(self, source: Platform, target: Platform) -> float | None:
        """A possible second-jump delay, or None when a single jump suffices."""
        takeoff = self.landing_x(source, target.centre)
        distance = abs(self.landing_x(target, takeoff) - takeoff)
        for delay, reach in self._jump_options.get(source.row - target.row, ()):
            if distance <= reach:
                return delay
        raise ValueError('No reachable jump between these platforms')

    def platform_width(self, row: int) -> int:
        """Prefer forgiving phrases at the base and precise footholds aloft."""
        top, bottom = self._text_bounds
        climbed = max(0.0, min(1.0, (bottom - row) / max(1, bottom - top)))
        broad = min(self.width, 22)
        narrow = min(broad, 6)
        return round(broad + (narrow - broad) * climbed)

    def platform_level(self, platform: Platform) -> int:
        """Count foothold heights from the base; shared rows share a colour."""
        return max(0, len(self._platform_rows) - bisect_left(self._platform_rows, platform.row) - 1)

    @property
    def floor_visible(self) -> bool:
        return self.camera_y <= self.floor_row < self.camera_y + self.viewport_height

    @property
    def scroll_speed(self) -> float:
        if not self.scroll_active:
            return 0.0
        limit = min(self.SCROLL_MAX_SPEED, self.viewport_height / 6.0)
        if self.player.grounded:
            supports = self.platforms.by_row.get(round(self.player.y + 1), ())
            span = max((platform.right - platform.left for platform in supports
                        if self.player.x + 1 >= platform.left and self.player.x - 1 < platform.right),
                       default=0)
            if span > 24:
                # A distant piece of real output can require walking across a
                # wide bridge. Leave time to cross it without stopping the chase.
                limit = min(limit, 1.5 / (span / self.MOVE_SPEED + 0.6))
        return min(limit, self.SCROLL_START_SPEED + self.SCROLL_ACCELERATION * self.scroll_elapsed)

    def _candidates(self, cells: tuple[Cell, ...], rng: random.Random) -> list[Platform]:
        groups: list[list[Cell]] = []
        for cell in cells:
            # A short existing space can join words into a single phrase.
            # Wide blank gaps remain separate; never manufacture source text.
            if groups and cell.x - (groups[-1][-1].x + groups[-1][-1].width) <= 2:
                groups[-1].append(cell)
            else:
                groups.append([cell])
        candidates = []
        for group in groups:
            first, last = group[0], group[-1]
            full = Platform(first.y, first.x, last.x + last.width)
            candidates.append(full)
            desired = self.platform_width(first.y)
            if full.right - full.left <= desired:
                continue
            # Retain the full phrase as a reachability fallback. Prefer smaller
            # windows at the current difficulty before considering that fallback.
            last_start = len(group) - 1
            while last_start > 0 and full.right - group[last_start].x < desired:
                last_start -= 1
            starts = {0, last_start, rng.randrange(last_start + 1), rng.randrange(last_start + 1)}
            for start in sorted(starts):
                width = desired
                end = start
                while end + 1 < len(group) and group[end].x + group[end].width - group[start].x < width:
                    end += 1
                fragment = Platform(first.y, group[start].x, group[end].x + group[end].width)
                if fragment not in candidates:
                    candidates.append(fragment)
        return candidates

    def _connect(self, source: Platform, target: Platform, rng: random.Random) -> tuple[Platform, ...]:
        """Bridge an unreachable edge, preserving both original endpoints."""
        rise = source.row - target.row
        count = max(1, math.ceil(rise / self.max_step))
        rows = [source.row - round(rise * step / count) for step in range(1, count)]
        source_x = self.landing_x(source, target.centre)
        target_x = self.landing_x(target, source_x)
        if not rows:
            # Real text can move from one edge to the other in adjacent rows.
            # A horizontal bridge is necessary here; its overlap with the source
            # lets the player walk to the other end before jumping.
            return (Platform(source.row, max(0, math.floor(min(source_x, target_x)) - 2),
                             min(self.width, math.ceil(max(source_x, target_x)) + 3), True),)
        bridges = []
        for index, row in enumerate(rows):
            width = max(1, min(self.width, self.platform_width(row) + rng.randint(-1, 1)))
            centre = source_x + (target_x - source_x) * (index + 1) / count
            left = min(self.width - width, max(0, round(centre - (width - 1) / 2)))
            bridges.append(Platform(row, left, left + width, True))
        path = [source, *bridges, target]
        if any(not self._can_jump(a, b) for a, b in zip(path, path[1:])):
            # One wide bridge can cover a genuinely unreachable horizontal gap
            # while retaining the minimum required number of vertical bridges.
            left = max(0, math.floor(min(source_x, target_x)) - 2)
            right = min(self.width, math.ceil(max(source_x, target_x)) + 3)
            bridges = [Platform(row, left, right, True) for row in rows]
        return tuple(bridges)

    def _build_route(self) -> None:
        self._platform_rows = []
        rng = random.Random(self.seed)
        recent: list[_FrontierRow] = []
        previous: list[_RouteNode] = []
        oldest = None
        first = next((row for row in range(len(self._history)) if self._history.cells(row)), None)
        if first is None:
            self.empty = True
            self.platforms = _Platforms()
            return
        last = next(row for row in range(len(self._history) - 1, first - 1, -1)
                    if self._history.cells(row))
        self._text_bounds = (first + self.HISTORY_TOP, last + self.HISTORY_TOP)
        for source_row in range(first, last + 1):
            candidates = self._candidates(self._history.cells(source_row), rng)
            if not candidates:
                continue
            row = candidates[0].row
            if oldest is None:
                oldest = row
                previous = [_RouteNode(platform) for platform in candidates]
                recent = [_FrontierRow(self, previous)]
                continue
            recent = [frontier for frontier in recent if row - frontier.row <= self.max_natural_step]
            nodes = []
            for platform in candidates:
                preferred_gap = rng.randint(max(1, self.max_step - 3), self.max_step)
                choices = []
                left = self.landing_x(platform, -math.inf)
                right = self.landing_x(platform, math.inf)
                for frontier in recent:
                    gap = row - frontier.row
                    options = self._jump_options.get(gap, ())
                    if not options:
                        continue
                    reach = max(limit for _, limit in options)
                    parent = frontier.parent(left - reach, right + reach, rng)
                    if parent is not None:
                        rank = (parent.cost,
                                abs(gap - preferred_gap) + rng.random() * 1.8
                                + (4 if gap > self.max_step else 0)
                                + abs(parent.platform.right - parent.platform.left
                                      - self.platform_width(parent.platform.row)) * 0.2)
                        choices.append((rank, parent))
                if choices:
                    _, parent = min(choices, key=lambda choice: choice[0])
                    nodes.append(_RouteNode(platform, parent, (), parent.cost))
                else:
                    # There is no natural continuation from this candidate.
                    # Compare necessary bridges to the preceding actual row.
                    connectors = []
                    for parent in previous:
                        bridge = self._connect(platform, parent.platform, rng)
                        rank = (parent.cost + len(bridge),
                                sum(p.right - p.left for p in bridge), rng.random())
                        connectors.append((rank, parent, bridge))
                    _, parent, bridge = min(connectors, key=lambda choice: choice[0])
                    nodes.append(_RouteNode(platform, parent, bridge, parent.cost + len(bridge)))
            previous = nodes
            recent.append(_FrontierRow(self, nodes))
        self.empty = not previous
        if self.empty:
            self.platforms = _Platforms()
            return
        node = min(previous, key=lambda item: (item.cost,
                   abs(item.platform.right - item.platform.left - self.platform_width(item.platform.row)),
                   rng.random()))
        route = [node.platform]
        while node.parent is not None:
            route.extend(node.connectors)
            node = node.parent
            route.append(node.platform)
        self.platforms = _Platforms(reversed(route))
        self._platform_rows = sorted(self.platforms.by_row)
        self.summit_row = self.platforms[0].row
        self.start_row = self.platforms[-1].row
        self.total_climb = self.start_row - self.summit_row
        self.floor_row = self.start_row + 3
        self.height = max(self.viewport_height, self.floor_row + 1)

    @property
    def progress(self) -> float:
        if self.total_climb == 0:
            return float(self.finished and self.finish_reason == 'summit')
        return min(1.0, max(0.0, (self.start_row - 1 - self.best_y) / self.total_climb))

    @property
    def score(self) -> int:
        return round(self.progress * self.total_climb)

    def reset(self) -> None:
        if self.resize_blocked:
            return
        base = self.platforms[-1] if self.platforms else None
        self._attempt_started = base is not None
        self._position_width = self.width
        self.player = Player(self.landing_x(base) if base else self.width / 2,
                             float(self.floor_row - 1),
                             grounded=base is not None)
        self.time = self.elapsed = 0.0
        self.jump_count = 0
        self.finished = False
        self.finish_reason = ''
        self.best_y = self.player.y
        self.camera_y = max(0, self.floor_row - self.viewport_height + 1)
        self.scroll_active = False
        self.scroll_elapsed = 0.0
        self._scroll_fraction = 0.0
        self.direction = 0
        self.move_until = self.drop_until = 0.0

    def visible_cells(self) -> list[Cell]:
        first = max(0, self.camera_y - self.HISTORY_TOP)
        last = min(len(self._history), self.camera_y + self.viewport_height - self.HISTORY_TOP)
        return [cell for row in range(first, last) for cell in self._history.cells(row)]

    def visible_platforms(self) -> list[Platform]:
        return [platform for row in range(self.camera_y, self.camera_y + self.viewport_height)
                for platform in self.platforms.by_row.get(row, ())]

    def move(self, direction: int) -> None:
        if self.finished or self.empty or self.resize_blocked:
            return
        self.direction = -1 if direction < 0 else 1 if direction > 0 else 0
        self.move_until = self.time + 0.14
        if self.direction:
            self.player.facing = self.direction

    def jump(self) -> None:
        if self.finished or self.empty or self.resize_blocked or self.player.jumps >= 2:
            return
        self.player.vy = -self.jump_speed
        self.player.grounded = False
        self.player.jumps += 1
        self.jump_count += 1
        self.drop_until = 0.0

    def drop(self) -> None:
        if self.finished or self.empty or self.resize_blocked:
            return
        self.drop_until = self.time + 0.20
        self.player.grounded = False
        self.player.vy = max(8.0, self.player.vy)

    def resize(self, width: int, viewport_height: int) -> None:
        width = max(8, int(width))
        viewport_height = max(6, int(viewport_height))
        if (width, viewport_height) == (self.width, self.viewport_height):
            return
        old_width = self._position_width
        grounded = self.player.grounded
        old_support = self.platforms.by_row.get(round(self.player.y + 1), ())
        self.width = width
        self.viewport_height = viewport_height
        self._history.resize(width)
        self._configure_jump()
        if self._attempt_started:
            required_rows = {self.summit_row, self.start_row}
            if grounded and any(not platform.synthetic for platform in old_support):
                required_rows.add(round(self.player.y + 1))
            if any(not self._history.cells(row - self.HISTORY_TOP) for row in required_rows):
                self.resize_blocked = True
                return
        self.resize_blocked = False
        self._build_route()
        if self.empty:
            # Preserve the attempt when narrowing clips every real glyph.
            return
        if not self._attempt_started:
            self.reset()
            return
        self._position_width = width
        self.player.x = min(width - 2.0, max(1.0, self.player.x * width / old_width))
        if grounded:
            row = round(self.player.y + 1)
            supports = self.platforms.by_row.get(row, ())
            if not supports:
                # Keep the occupied original step during this attempt even if
                # the regenerated random route selected a different row.
                natural = self._candidates(self._history.cells(row - self.HISTORY_TOP), random.Random(self.seed))
                if natural:
                    retained = min(natural, key=lambda p: abs(p.centre - self.player.x))
                    self.platforms.by_row.setdefault(row, []).append(retained)
                    supports = [retained]
                elif old_support:
                    old_bridge = next((p for p in old_support if p.synthetic), None)
                    if old_bridge is not None:
                        left = min(width - 1, max(0, round(old_bridge.left * width / old_width)))
                        right = min(width, max(left + 1, round(old_bridge.right * width / old_width)))
                        retained = Platform(row, left, right, True)
                        self.platforms.by_row.setdefault(row, []).append(retained)
                        supports = [retained]
            if supports:
                support = min(supports, key=lambda p: abs(p.centre - self.player.x))
                self.player.x = self.landing_x(support, self.player.x)
        self.player.vx = 0.0
        self.move_until = self.time
        self._platform_rows = sorted(self.platforms.by_row)
        maximum = max(0, self.height - viewport_height)
        camera = min(self.camera_y, maximum)
        if not camera + 2 <= self.player.y <= camera + viewport_height - 2:
            camera = min(maximum, math.floor(self.player.y) - viewport_height // 2)
        self.camera_y = camera

    def update(self, dt: float) -> None:
        if self.finished or self.empty or self.resize_blocked or not math.isfinite(dt) or dt <= 0:
            return
        self.elapsed += dt
        remaining = min(dt, 0.12)
        while remaining > 1e-9 and not self.finished:
            step = min(remaining, 1 / 120)
            self._step(step)
            remaining -= step

    def _finish(self, reason: str) -> None:
        self.finished = True
        self.finish_reason = reason
        self.player.vx = self.player.vy = 0.0

    def _advance_camera(self, dt: float) -> None:
        follow = math.floor(self.player.y) - self.follow_row
        if follow < self.camera_y:
            self.camera_y = follow
            self._scroll_fraction = 0.0
        if not self.scroll_active:
            if self.floor_visible:
                return
            self.scroll_active = True
        # Integrate fractional rows independently of the render frame rate.
        # Following a jump can move faster, but waiting never stops the chase.
        before = self.scroll_speed
        self.scroll_elapsed += dt
        self._scroll_fraction += (before + self.scroll_speed) * 0.5 * dt
        rows = math.floor(self._scroll_fraction)
        self.camera_y -= rows
        self._scroll_fraction -= rows

    def _step(self, dt: float) -> None:
        self.time += dt
        p = self.player
        if self.time < self.move_until:
            p.vx = self.direction * self.MOVE_SPEED
        elif p.grounded:
            p.vx *= math.exp(-24.0 * dt)
        p.x = min(self.width - 2.0, max(1.0, p.x + p.vx * dt))
        old_y = p.y
        gravity = self.ASCENT_GRAVITY if p.vy < 0 else self.FALL_GRAVITY
        p.vy = min(self.MAX_FALL_SPEED, p.vy + gravity * dt)
        next_y = old_y + p.vy * dt
        p.grounded = False
        landed = None
        if p.vy >= 0 and self.time >= self.drop_until:
            for row in range(max(self.summit_row, math.ceil(old_y + 1 - 1e-7)),
                             min(self.start_row, math.floor(next_y + 1)) + 1):
                supports = self.platforms.by_row.get(row, ())
                landed = next((platform for platform in supports
                               if p.x + 1.0 >= platform.left and p.x - 1.0 < platform.right), None)
                if landed is not None:
                    next_y = float(row - 1)
                    p.vy = 0.0
                    p.grounded = True
                    p.jumps = 0
                    break
        # The fixed base catches early mistakes, including a deliberate drop.
        # Once it has scrolled away, the visible lower boundary ends the run.
        # It is not a route platform and can never count as reaching the summit.
        if self.floor_visible and next_y >= self.floor_row - 1:
            next_y = float(self.floor_row - 1)
            p.vy = 0.0
            p.grounded = True
            p.jumps = 0
            landed = None
        p.y = next_y
        self.best_y = min(self.best_y, p.y)
        self._advance_camera(dt)
        summit = self.platforms[0]
        if landed == summit:
            self._finish('summit')
        elif p.y >= self.camera_y + self.viewport_height:
            self._finish('fallen')
