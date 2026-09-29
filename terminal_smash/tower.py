"""A finite, scrolling platform route through an inert terminal snapshot."""
from __future__ import annotations

from array import array
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, replace
import math

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


class _Platforms(Sequence):
    """The route is deterministic and does not parse unseen history rows."""

    CACHE_ROWS = 192

    def __init__(self, world: TowerWorld):
        self.world = world
        self.count = math.ceil(world.total_climb / 4) + 1
        self.cache: OrderedDict[int, Platform] = OrderedDict()

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        platform = self._make(index)
        self.cache[index] = platform
        if len(self.cache) > self.CACHE_ROWS:
            self.cache.popitem(last=False)
        return platform

    def at_row(self, row: int) -> Platform | None:
        if row == self.world.summit_row:
            return self[0]
        distance = self.world.start_row - row
        if distance >= 0 and distance % 4 == 0:
            index = len(self) - 1 - distance // 4
            if 0 < index < len(self):
                return self[index]
        return None

    def _make(self, index: int) -> Platform:
        world = self.world
        row = max(world.summit_row, world.start_row - (len(self) - 1 - index) * 4)
        size = min(9, max(4, world.width // 7), world.width - 2)
        span = world.width - size - 2
        # A triangular path moves at most six columns per platform. Real text
        # may deviate by two columns, keeping every next centre within ten.
        phase = span // 2 + (len(self) - index - 1) * 6
        offset = abs((phase + span) % (2 * span) - span) if span else 0
        left = 1 + offset
        target = left + (size - 1) / 2
        if row == world.start_row:
            return Platform(row, left, left + size, True)
        if row == world.summit_row:
            size = min(world.width - 2, max(8, size))
            left = min(world.width - size - 1, max(1, round(target - (size - 1) / 2)))
            return Platform(row, left, left + size, True)
        cells = world._history.cells(row - world.HISTORY_TOP)
        best = None
        best_rank = None
        for start, cell in enumerate(cells):
            if cell.x < 1:
                continue
            right = cell.x
            for other_index in range(start, len(cells)):
                other = cells[other_index]
                if other.x != right or other.x + other.width - cell.x > size:
                    break
                right = other.x + other.width
                if right > world.width - 1:
                    break
                if right - cell.x < 4:
                    continue
                distance = abs((cell.x + right - 1) / 2 - target)
                rank = (distance, -(right - cell.x), cell.x)
                if distance <= 2 and (best_rank is None or rank < best_rank):
                    best = Platform(row, cell.x, right)
                    best_rank = rank
        return best or Platform(row, left, left + size, True)


class TowerWorld:
    """Coordinates are world cells; player.y is the player's feet row.

    The camera follows the best height and never retreats during play. Resizing
    may reframe the camera to keep the same player position visible.
    """

    HISTORY_TOP = 3
    GRAVITY = 54.0
    JUMP_SPEED = 22.0
    MOVE_SPEED = 30.0
    MAX_FALL_SPEED = 38.0

    def __init__(self, text: str, width: int, viewport_height: int):
        self.width = max(8, int(width))
        self.viewport_height = max(6, int(viewport_height))
        self._history = _History(text, self.width, self.HISTORY_TOP)
        self.height = max(10, self.viewport_height, len(self._history) + 6)
        self.summit_row = self.HISTORY_TOP
        self.start_row = self.height - 2
        self.total_climb = self.start_row - self.summit_row
        self.platforms = _Platforms(self)
        self.reset()

    @property
    def progress(self) -> float:
        return min(1.0, max(0.0, (self.start_row - 1 - self.best_y) / self.total_climb))

    @property
    def score(self) -> int:
        return round(self.progress * self.total_climb)

    def reset(self) -> None:
        base = self.platforms[-1]
        self.player = Player(base.centre, float(base.row - 1), grounded=True)
        self.time = self.elapsed = 0.0
        self.jump_count = 0
        self.finished = False
        self.finish_reason = ''
        self.best_y = self.player.y
        self.camera_y = max(0, self.height - self.viewport_height)
        self.direction = 0
        self.move_until = self.drop_until = 0.0

    def visible_cells(self) -> list[Cell]:
        first = max(0, self.camera_y - self.HISTORY_TOP)
        last = min(len(self._history), self.camera_y + self.viewport_height - self.HISTORY_TOP)
        return [cell for row in range(first, last) for cell in self._history.cells(row)]

    def visible_platforms(self) -> list[Platform]:
        return [platform for row in range(self.camera_y, self.camera_y + self.viewport_height)
                if (platform := self.platforms.at_row(row)) is not None]

    def move(self, direction: int) -> None:
        if self.finished:
            return
        self.direction = -1 if direction < 0 else 1 if direction > 0 else 0
        self.move_until = self.time + 0.14
        if self.direction:
            self.player.facing = self.direction

    def jump(self) -> None:
        if self.finished or self.player.jumps >= 2:
            return
        self.player.vy = -self.JUMP_SPEED
        self.player.grounded = False
        self.player.jumps += 1
        self.jump_count += 1
        self.drop_until = 0.0

    def drop(self) -> None:
        if self.finished:
            return
        self.drop_until = self.time + 0.20
        self.player.grounded = False
        self.player.vy = max(4.0, self.player.vy)

    def resize(self, width: int, viewport_height: int) -> None:
        width = max(8, int(width))
        viewport_height = max(6, int(viewport_height))
        if (width, viewport_height) == (self.width, self.viewport_height):
            return
        old_width = self.width
        self.width = width
        self.viewport_height = viewport_height
        self._history.resize(width)
        self.platforms = _Platforms(self)
        self.player.x = min(width - 2.0, max(1.0, self.player.x * width / old_width))
        if self.player.grounded:
            support = self.platforms.at_row(round(self.player.y + 1))
            if support is not None:
                self.player.x = min(support.right - 1.0, max(float(support.left), self.player.x))
        self.player.vx = 0.0
        self.move_until = self.time
        maximum = max(0, self.height - viewport_height)
        camera = min(self.camera_y, maximum)
        if not camera + 2 <= self.player.y <= camera + viewport_height - 2:
            camera = max(0, min(maximum, math.floor(self.player.y) - viewport_height // 2))
        self.camera_y = camera

    def update(self, dt: float) -> None:
        if self.finished or not math.isfinite(dt) or dt <= 0:
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

    def _step(self, dt: float) -> None:
        self.time += dt
        p = self.player
        if self.time < self.move_until:
            p.vx = self.direction * self.MOVE_SPEED
        elif p.grounded:
            p.vx *= math.exp(-24.0 * dt)
        p.x = min(self.width - 2.0, max(1.0, p.x + p.vx * dt))
        old_y = p.y
        p.vy = min(self.MAX_FALL_SPEED, p.vy + self.GRAVITY * dt)
        next_y = old_y + p.vy * dt
        p.grounded = False
        if p.vy >= 0 and self.time >= self.drop_until:
            for row in range(max(self.summit_row, math.ceil(old_y + 1 - 1e-7)),
                             min(self.start_row, math.floor(next_y + 1)) + 1):
                platform = self.platforms.at_row(row)
                if platform and p.x + 0.8 >= platform.left and p.x - 0.8 < platform.right:
                    next_y = float(row - 1)
                    p.vy = 0.0
                    p.grounded = True
                    p.jumps = 0
                    break
        if next_y < self.summit_row - 1:
            next_y = float(self.summit_row - 1)
            p.vy = max(0.0, p.vy)
        p.y = next_y
        self.best_y = min(self.best_y, p.y)
        follow_row = max(2, self.viewport_height // 3)
        self.camera_y = min(self.camera_y, max(0, math.floor(p.y) - follow_row))
        if p.grounded and p.y == self.summit_row - 1:
            self._finish('summit')
        elif p.y >= self.camera_y + self.viewport_height:
            self._finish('fallen')
