"""Frame-rate independent arcade physics, with no terminal or filesystem effects."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random

from .capture import Cell, Style
from .traversal import TraversalMixin


@dataclass
class Player:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    facing: int = 1
    grounded: bool = False
    jumps: int = 0
    hp: int = 5
    max_hp: int = 5


@dataclass
class Particle:
    char: str
    style: Style
    x: float
    y: float
    vx: float
    vy: float
    life: float
    width: int = 1


@dataclass
class Wave:
    x: float
    y: float
    age: float = 0.0


@dataclass
class FallingChunk:
    cells: list[Cell]
    offset_y: float = 0.0
    vy: float = 1.0
    row: int = field(init=False)
    left: int = field(init=False)
    right: int = field(init=False)
    centre_x: float = field(init=False)

    def __post_init__(self) -> None:
        # Chunks are horizontal runs. Cache their bounds once, rather than
        # revisiting every glyph during every physics substep and attack.
        self.refresh_bounds()

    def refresh_bounds(self) -> None:
        if self.cells:
            self.row = self.cells[0].y
            self.left = min(cell.x for cell in self.cells)
            self.right = max(cell.x + cell.width for cell in self.cells)
            self.centre_x = sum(cell.x + (cell.width - 1) / 2
                                for cell in self.cells) / len(self.cells)


@dataclass
class Trail:
    x: float
    y: float
    life: float = 0.18


@dataclass
class Enemy:
    # x is the centre of the five-column label, y the feet row.
    x: float
    y: float
    hp: int = 2
    label: str = "ERROR"
    facing: int = 1
    hurt_until: float = 0.0
    cells: list[Cell] = field(default_factory=list, repr=False)


class World(TraversalMixin):
    """Coordinates are character cells; player.y is the row occupied by feet."""

    def __init__(self, cells: list[Cell], width: int, height: int,
                 seed: int | None = None, *, duration: float | None = None,
                 falling_enabled: bool = True):
        self.width = max(8, width)
        self.height = max(6, height)
        self.original = tuple(cells)
        self.rng = random.Random(seed)
        self.duration = max(0.0, duration) if duration is not None else None
        self.terrain_revision = 0
        self.falling_enabled = falling_enabled
        self.reset()

    @property
    def time_left(self) -> float | None:
        return None if self.duration is None else max(0.0, self.duration - self.round_elapsed)

    @property
    def cleared(self) -> bool:
        return self.total > 0 and not self.cells and not self.falling and not self.enemies

    def reset(self) -> None:
        self.time = self.round_elapsed = 0.0
        self.cells = {(c.x, c.y): c for c in self.original
                      if 0 <= c.x and c.x + c.width <= self.width
                      and 0 <= c.y < self.height - 1}
        self.cells_by_row: list[dict[int, Cell]] = [{} for _ in range(self.height)]
        self.terrain_row_revisions = [0] * self.height
        for cell in self.cells.values():
            self.cells_by_row[cell.y][cell.x] = cell
        self.occupied = {(c.x + dx, c.y): (c.x, c.y)
                         for c in self.cells.values() for dx in range(c.width)}
        self.total = len(self.cells)
        self.destroyed = self.score = self.combo = 0
        self.multiplier = 1
        self.combo_until = 0.0
        self.finished = False
        self.finish_reason = ""
        self.player = Player(float(max(2, self.width // 3)), 2.0)
        self.particles: list[Particle] = []
        self.waves: list[Wave] = []
        self.falling: list[FallingChunk] = []
        self.trails: list[Trail] = []
        self.enemies: list[Enemy] = []
        self.generated_enemies = 0
        self.direction = 0
        self.move_until = self.drop_until = self.attack_until = 0.0
        self.next_punch = self.next_blast = self.next_dash = self.next_slam = 0.0
        self.dash_until = self.landing_until = self.hurt_until = 0.0
        self.jump_started = self.attack_started = -1.0
        self.slamming = False
        self._dash_direction = 1
        self._next_trail = 0.0
        self._pending_falling = 0
        self._reset_traversal()
        self.enemy_target = min(8, (self.width // 7) * ((self.height - 1) // 2))
        self.next_enemy_spawn = 2.0
        self._spawn_enemies()
        self._separate_enemies()
        if self.duration is not None and not self.enemies:
            self._spawn_challenge_enemies()
        self.enemy_target = min(self.enemy_target, max(3, len(self.enemies)))
        self.terrain_revision += 1
        if self.duration == 0:
            self._finish("time")

    def _spawn_enemies(self) -> None:
        """Animate whole ERROR words, retaining their letters for accounting."""
        def word_character(cell: Cell | None) -> bool:
            return bool(cell and (cell.char.isalnum() or cell.char == "_"))

        for (x, y), cell in sorted(list(self.cells.items()), key=lambda item: (item[0][1], item[0][0])):
            if len(self.enemies) >= self.enemy_target:
                break
            if cell.char.upper() != "E":
                continue
            letters = [self.cells.get((x + n, y)) for n in range(5)]
            if any(c is None or c.width != 1 for c in letters):
                continue
            if "".join(c.char for c in letters).upper() != "ERROR":
                continue
            before = self.cells.get(self.occupied.get((x - 1, y)))
            after = self.cells.get(self.occupied.get((x + 5, y)))
            if word_character(before) or word_character(after):
                continue
            source = [self._remove((c.x, c.y)) for c in letters]
            self.enemies.append(Enemy(float(x + 2), float(y + 1), cells=source))

    @staticmethod
    def _enemy_overlap(x: float, y: float, other: Enemy) -> bool:
        # Check the rendered seven-column label and two-row body, so rounding
        # fractional physics coordinates cannot merge two displayed actors.
        return abs(round(x) - round(other.x)) < 7 and abs(round(y) - round(other.y)) < 2

    def _enemy_position(self, preferred: tuple[float, float], *,
                        avoid_player: bool, others: list[Enemy]) -> tuple[float, float] | None:
        """Search a bounded, deterministic grid for a free full sprite footprint."""
        columns = min(9, self.width // 7)
        rows = min(9, (self.height - 1) // 2)
        xs = {3 + round((self.width - 7) * index / max(1, columns - 1))
              for index in range(columns)}
        ys = {1 + round((self.height - 3) * index / max(1, rows - 1))
              for index in range(rows)}
        px = max(3, min(self.width - 4, round(preferred[0])))
        py = max(1, min(self.height - 2, round(preferred[1])))
        candidates = [(px, py)] + [(x, y) for y in sorted(ys) for x in sorted(xs)]
        candidates.sort(key=lambda pos: ((pos[0] - px) ** 2 + 4 * (pos[1] - py) ** 2,
                                          pos[1], pos[0]))
        for x, y in candidates:
            if avoid_player and (abs(x - self.player.x) < 6
                                 and abs(y - self.player.y) < 2):
                continue
            if not any(self._enemy_overlap(x, y, other) for other in others):
                return float(x), float(y)
        return None

    def _separate_enemies(self) -> None:
        # Natural words can be adjacent. Place them once before movement starts;
        # later movement checks footprints instead of repeatedly pushing crowds.
        placed: list[Enemy] = []
        for enemy in self.enemies:
            position = self._enemy_position((enemy.x, enemy.y), avoid_player=False,
                                            others=placed)
            if position is None:
                # A centred natural word can consume two grid slots. Repack
                # once if that prevents a full, physically feasible placement.
                columns = self.width // 7
                for index, actor in enumerate(self.enemies):
                    actor.x = float(3 + (index % columns) * 7)
                    actor.y = float(1 + (index // columns) * 2)
                return
            enemy.x, enemy.y = position
            placed.append(enemy)

    def _spawn_challenge_enemy(self, index: int) -> bool:
        preferred = (3 + (self.width - 7) * (index % 3) / 2,
                     max(4, (self.height - 2) * (index % 3 + 1) / 4))
        position = self._enemy_position(preferred, avoid_player=True, others=self.enemies)
        if position is None:
            return False
        x, y = position
        letters = [Cell(int(x) - 2 + offset, int(y) - 1, char, Style(fg=196, bold=True))
                   for offset, char in enumerate("ERROR")]
        self.enemies.append(Enemy(x, y, cells=letters))
        self.total += len(letters)
        self.generated_enemies += 1
        return True

    def _spawn_challenge_enemies(self) -> None:
        """Supplement an ERROR-free snapshot without removing any terrain."""
        for index in range(min(3, self.enemy_target)):
            if not self._spawn_challenge_enemy(index):
                break

    def _refill_enemies(self) -> None:
        if (self.duration is None or self.finished
                or self.round_elapsed < self.next_enemy_spawn):
            return
        # Use the real round clock, but never catch up missed waves after a stall.
        self.next_enemy_spawn = self.round_elapsed + 2.0
        if len(self.enemies) < self.enemy_target:
            self._spawn_challenge_enemy(self.generated_enemies)

    def _remove(self, key: tuple[int, int]) -> Cell:
        cell = self.cells.pop(key)
        del self.cells_by_row[cell.y][cell.x]
        self.terrain_row_revisions[cell.y] += 1
        for dx in range(cell.width):
            self.occupied.pop((cell.x + dx, cell.y), None)
        return cell

    def move(self, direction: int) -> None:
        if self.finished:
            return
        self.direction = 1 if direction > 0 else -1
        self.player.facing = self.direction
        self._traversal_move(self.direction)
        self.move_until = self.time + 0.16

    def jump(self) -> None:
        if self.finished or self.slamming:
            return
        if self._jump_from_grip():
            return
        if self.player.grounded or self.player.jumps < 2:
            self.player.vy = -21.0
            self.player.grounded = False
            self.player.jumps += 1
            self.jump_started = self.time

    def drop(self) -> None:
        if self.finished:
            return
        self._release_grip()
        self.drop_until = self.time + 0.22
        self.player.grounded = False
        self.player.vy = max(5.0, self.player.vy)

    def return_to_top(self) -> None:
        """Reach isolated text again, retaining destruction and the round clock."""
        if self.finished:
            return
        self._release_grip()
        self.player.y = 2.0
        self.player.vx = self.player.vy = 0.0
        self.player.grounded = False
        self.player.jumps = 0
        self.move_until = self.drop_until = self.dash_until = 0.0
        self.slamming = False

    def punch(self) -> int:
        if self.finished or self.time < self.next_punch:
            return 0
        self._release_grip()
        self.next_punch = self.time + 0.09
        self.attack_started = self.time
        self.attack_until = self.time + 0.11
        p = self.player
        strength = 1 + (self.multiplier - 1) * 0.07
        return self.destroy(p.x + p.facing * 3.0, p.y - 0.8,
                            4.5 * strength, 2.2 * strength)

    def blast(self) -> int:
        if self.finished or self.time < self.next_blast:
            return 0
        self._release_grip()
        self.next_blast = self.time + 0.45
        self.attack_started = self.time
        self.attack_until = self.time + 0.16
        p = self.player
        self._wave(p.x, p.y - 1.0)
        strength = 1 + (self.multiplier - 1) * 0.07
        return self.destroy(p.x, p.y - 1.0, 12.0 * strength, 5.5 * strength,
                            enemy_damage=2)

    def dash(self) -> bool:
        if self.finished or self.slamming or self.time < self.next_dash:
            return False
        self._release_grip()
        self.next_dash = self.time + 0.55
        self.dash_until = self.time + 0.19
        self._dash_direction = self.player.facing
        self.player.vx = self._dash_direction * 110.0
        self._next_trail = self.time
        self.attack_started = self.time
        self.attack_until = self.dash_until
        return True

    def slam(self) -> bool:
        if (self.finished or self.slamming or self.player.grounded
                or self.time < self.next_slam):
            return False
        self._release_grip()
        self.next_slam = self.time + 0.5
        self.dash_until = self.move_until = 0.0
        self.slamming = True
        self.player.vy = 68.0
        self.player.vx *= 0.3
        self.attack_started = self.time
        return True

    def _award(self, count: int, *, bonus: int = 0) -> None:
        if not count and not bonus:
            return
        if self.time >= self.combo_until:
            self.combo = 0
        self.combo += 1
        self.multiplier = min(5, 1 + self.combo // 3)
        self.combo_until = self.time + 1.7
        self.score += (count * 10 + bonus) * self.multiplier

    def _wave(self, x: float, y: float) -> None:
        self.waves.append(Wave(x, y))
        self.waves = self.waves[-24:]

    def _debris(self, cell: Cell, x: float, y: float, *, offset_y: float = 0.0,
                position_x: float | None = None, position_y: float | None = None) -> None:
        px = cell.x if position_x is None else position_x
        py = cell.y + offset_y if position_y is None else position_y
        self.particles.append(Particle(cell.char, cell.style, px, py,
                                       (px - x) * 2.6 + self.rng.uniform(-12, 12),
                                       -self.rng.uniform(4, 15) + (py - y) * 0.9,
                                       self.rng.uniform(0.5, 1.4), cell.width))

    def _sparks(self, x: float, y: float, count: int) -> None:
        colour = (220, 214, 208, 201, 196)[self.multiplier - 1]
        for _ in range(min(28, count * 2 + self.multiplier - 1)):
            self.particles.append(Particle(self.rng.choice(".*+"), Style(fg=colour, bold=True),
                                           x, y, self.rng.uniform(-22, 22),
                                           self.rng.uniform(-12, 4), self.rng.uniform(0.15, 0.45)))
        self.particles = self.particles[-800:]

    def _ellipse_keys(self, x: float, y: float, rx: float, ry: float) -> set[tuple[int, int]]:
        """Use the occupancy index, so a dash does not scan the whole screen."""
        keys = set()
        for row in range(max(0, math.floor(y - ry)), min(self.height - 2, math.ceil(y + ry)) + 1):
            for col in range(max(0, math.floor(x - rx - 1)), min(self.width - 1, math.ceil(x + rx + 1)) + 1):
                key = self.occupied.get((col, row))
                if key is not None:
                    cell = self.cells[key]
                    if ((cell.x + (cell.width - 1) / 2 - x) / rx) ** 2 + ((cell.y - y) / ry) ** 2 <= 1:
                        keys.add(key)
        return keys

    def _destroy_static(self, keys: set[tuple[int, int]], x: float, y: float) -> int:
        removed = [self._remove(key) for key in sorted(keys) if key in self.cells]
        for cell in removed:
            self._debris(cell, x, y)
        if removed:
            self.destroyed += len(removed)
            self.terrain_revision += 1
            self._collapse(removed)
        return len(removed)

    def _collapse(self, removed: list[Cell]) -> None:
        """Only damaged neighbours lose their anchors; untouched text stays put.

        Horizontal text runs act as platforms. A severed fragment or a run above
        a removed support falls once no letters directly underneath support it.
        Cascades are driven by local occupancy lookups, with bounded live chunks.
        """
        if not self.falling_enabled:
            return
        pending: deque[tuple[int, int]] = deque()

        def neighbours(cell: Cell) -> None:
            for col, row in [(cell.x - 1, cell.y), (cell.x + cell.width, cell.y)]:
                key = self.occupied.get((col, row))
                if key is not None:
                    pending.append(key)
            for dx in range(cell.width):
                key = self.occupied.get((cell.x + dx, cell.y - 1))
                if key is not None:
                    pending.append(key)

        for cell in removed:
            neighbours(cell)
        seen: dict[tuple[int, int], tuple[int, int]] = {}
        examined = 0
        while pending and len(self.falling) + self._pending_falling < 80 and examined < 8192:
            key = pending.popleft()
            if key not in self.cells or key[1] == 0:
                continue
            row = key[1]
            revision = (self.terrain_row_revisions[row], self.terrain_row_revisions[row + 1])
            if seen.get(key) == revision:
                continue
            run: dict[tuple[int, int], Cell] = {}
            stack = [key]
            while stack:
                current = stack.pop()
                if current in run:
                    continue
                cell = self.cells[current]
                run[current] = cell
                for col in (cell.x - 1, cell.x + cell.width):
                    neighbour = self.occupied.get((col, cell.y))
                    if neighbour is not None and neighbour not in run:
                        stack.append(neighbour)
            seen.update((key, revision) for key in run)
            examined += len(run)
            supported = any((c.x + dx, c.y + 1) in self.occupied
                            for c in run.values() for dx in range(c.width))
            if supported:
                continue
            detached = [self._remove(key) for key in sorted(run)]
            self.falling.append(FallingChunk(detached))
            self.terrain_revision += 1
            # Only this row and the row above need reconsideration. Revision
            # checks leave all other examined runs cached through the cascade.
            for cell in detached:
                neighbours(cell)

    def destroy(self, x: float, y: float, rx: float, ry: float, *, enemy_damage: int = 1) -> int:
        if self.finished or rx <= 0 or ry <= 0:
            return 0
        count = self._destroy_static(self._ellipse_keys(x, y, rx, ry), x, y)
        # Detached letters remain destructible in flight, including wide glyphs.
        for chunk in self.falling:
            cy = chunk.row + chunk.offset_y
            if abs(cy - y) > ry or chunk.right - 1 < x - rx or chunk.left > x + rx:
                continue
            survivors = []
            for cell in chunk.cells:
                if (((cell.x + (cell.width - 1) / 2 - x) / rx) ** 2
                        + ((cell.y + chunk.offset_y - y) / ry) ** 2 <= 1):
                    self._debris(cell, x, y, offset_y=chunk.offset_y)
                    count += 1
                    self.destroyed += 1
                else:
                    survivors.append(cell)
            if len(survivors) != len(chunk.cells):
                chunk.cells = survivors
                chunk.refresh_bounds()
        self.falling = [chunk for chunk in self.falling if chunk.cells]
        bonus = 0
        for enemy in self.enemies:
            nearest_x = min(max(x, enemy.x - 2), enemy.x + 2)
            if (((nearest_x - x) / rx) ** 2 + ((enemy.y - 1 - y) / ry) ** 2 <= 1
                    and self.time >= enemy.hurt_until):
                enemy.hp -= enemy_damage + (self.multiplier - 1) // 3
                enemy.hurt_until = self.time + 0.10
                bonus += 10
                if enemy.hp <= 0:
                    for index, cell in enumerate(enemy.cells):
                        self._debris(cell, x, y, position_x=enemy.x - 2 + index,
                                     position_y=enemy.y - 1)
                    count += len(enemy.cells)
                    self.destroyed += len(enemy.cells)
                    bonus += 50
        self.enemies = [enemy for enemy in self.enemies if enemy.hp > 0]
        self._award(count, bonus=bonus)
        if count or bonus:
            self._sparks(x, y, count or 1)
        return count

    def _finish(self, reason: str) -> None:
        self._release_grip()
        self.finished = True
        self.finish_reason = reason
        self.slamming = False
        self.move_until = self.dash_until = 0.0
        self.player.vx = self.player.vy = 0.0

    def update(self, dt: float) -> None:
        if self.finished or not math.isfinite(dt) or dt <= 0:
            return
        elapsed = dt if self.duration is None else min(dt, self.time_left)
        self.round_elapsed += elapsed
        # The round clock uses real elapsed time; physics remains bounded after a stall.
        remaining = min(elapsed, 0.12)
        while remaining > 1e-9 and not self.finished:
            step = min(remaining, 1 / 120)
            self._step(step)
            remaining -= step
        if self.duration is not None and self.time_left <= 1e-9 and not self.finished:
            self._finish("time")
        self._refill_enemies()

    def _step(self, dt: float) -> None:
        self.time += dt
        if self.combo and self.time >= self.combo_until:
            self.combo = 0
            self.multiplier = 1
        p = self.player
        if not self._step_traversal(dt):
            was_grounded = p.grounded
            dashing = self.time < self.dash_until
            if dashing:
                p.vx = self._dash_direction * 110.0
                p.vy = 0.0
            elif self.time < self.move_until and not self.slamming:
                p.vx = self.direction * 40.0
            elif p.grounded:
                # Terminal jump input replaces autorepeat: retain airborne momentum.
                p.vx *= math.exp(-24.0 * dt)
            elif not self.slamming and abs(p.vx) > 40:
                p.vx = math.copysign(40.0, p.vx)
            old_x = p.x
            p.x = max(1.0, min(self.width - 2.0, p.x + p.vx * dt))
            if dashing:
                # A swept ellipse covers both endpoints, even on a slow frame.
                self.destroy((old_x + p.x) / 2, p.y - 0.7,
                             2.8 + abs(p.x - old_x) / 2, 1.7, enemy_damage=2)
                if self.finished:
                    return
                if self.time >= self._next_trail:
                    self.trails.append(Trail(p.x, p.y))
                    self.trails = self.trails[-32:]
                    self._next_trail = self.time + 0.018
            old_y = p.y
            p.vy = 0.0 if dashing else (68.0 if self.slamming else min(40.0, p.vy + 54.0 * dt))
            next_y = old_y + p.vy * dt
            p.grounded = False
            impact_y = None
            if p.vy >= 0:
                floor = self.height - 2.0
                landing = floor if next_y >= floor else None
                if self.slamming or self.time >= self.drop_until:
                    x = int(round(p.x))
                    for row in range(max(0, math.ceil(old_y + 1 - 1e-7)),
                                     min(self.height - 1, math.floor(next_y + 1)) + 1):
                        if any((col, row) in self.occupied for col in (x - 1, x, x + 1)):
                            candidate = float(row - 1)
                            if candidate >= old_y - 1e-7:
                                landing = candidate if landing is None else min(landing, candidate)
                                break
                if landing is not None:
                    next_y = landing
                    p.vy = 0.0
                    p.grounded = True
                    p.jumps = 0
                    if not was_grounded:
                        self.landing_until = self.time + (0.20 if self.slamming else 0.11)
                    if self.slamming:
                        impact_y = next_y + 1
            if next_y < 2.0:
                next_y = 2.0
                p.vy = max(0.0, p.vy)
            p.y = next_y
            if impact_y is not None:
                self.slamming = False
                self._wave(p.x, impact_y)
                self.attack_until = self.time + 0.2
                strength = 1 + (self.multiplier - 1) * 0.07
                self.destroy(p.x, impact_y, 15.0 * strength, 4.0 * strength, enemy_damage=3)
                if self.finished:
                    return
        self._step_falling(dt)
        if self.finished:
            return
        self._step_enemies(dt)
        if self.finished:
            return
        alive = []
        for particle in self.particles:
            particle.life -= dt
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vy += 34.0 * dt
            if particle.life > 0 and -4 < particle.y < self.height + 3:
                alive.append(particle)
        self.particles = alive
        for wave in self.waves:
            wave.age += dt
        self.waves = [wave for wave in self.waves if wave.age < 0.30]
        for trail in self.trails:
            trail.life -= dt
        self.trails = [trail for trail in self.trails if trail.life > 0]

    def _step_falling(self, dt: float) -> None:
        current, self.falling = self.falling, []
        self._pending_falling = len(current)
        for chunk in current:
            self._pending_falling -= 1
            old_offset = chunk.offset_y
            chunk.vy = min(42.0, chunk.vy + 38.0 * dt)
            chunk.offset_y += chunk.vy * dt
            old_row = math.floor(chunk.row + old_offset)
            new_row = math.floor(chunk.row + chunk.offset_y)
            floor_hit = new_row >= self.height - 2
            if old_row == new_row and not floor_hit:
                self.falling.append(chunk)
                continue
            hit: set[tuple[int, int]] = set()
            for row in range(max(0, old_row + 1), min(self.height - 2, new_row) + 1):
                if not self.cells_by_row[row]:
                    continue
                for cell in chunk.cells:
                    for dx in range(cell.width):
                        key = self.occupied.get((cell.x + dx, row))
                        if key is not None:
                            hit.add(key)
            if not hit and not floor_hit:
                self.falling.append(chunk)
                continue
            x = chunk.centre_x
            y = min(self.height - 2, chunk.cells[0].y + chunk.offset_y)
            count = self._destroy_static(hit, x, y)
            for cell in chunk.cells:
                self._debris(cell, x, y, offset_y=chunk.offset_y)
            self.destroyed += len(chunk.cells)
            self._award(count + len(chunk.cells))
            self._sparks(x, y, count + len(chunk.cells))
            self._wave(x, y)

    def _step_enemies(self, dt: float) -> None:
        p = self.player
        for enemy in self.enemies:
            dx, dy = p.x - enemy.x, p.y - enemy.y
            if abs(dx) > 0.15:
                enemy.facing = 1 if dx > 0 else -1
            next_x = enemy.x + (math.copysign(min(abs(dx), 7.5 * dt), dx) if dx else 0)
            next_y = enemy.y + (math.copysign(min(abs(dy), 4.0 * dt), dy) if dy else 0)
            next_x = max(3.0, min(self.width - 4.0, next_x))
            next_y = max(1.0, min(self.height - 2.0, next_y))
            others = [other for other in self.enemies if other is not enemy]
            # Slide along the unblocked axis, otherwise hold position. A blocked
            # pursuer cannot squeeze into the label/body of the enemy ahead.
            for x, y in ((next_x, next_y), (next_x, enemy.y), (enemy.x, next_y)):
                if not any(self._enemy_overlap(x, y, other) for other in others):
                    enemy.x, enemy.y = x, y
                    break
            if (self.time > 1.0 and self.time >= self.hurt_until
                    and self.time >= enemy.hurt_until and self.time >= self.dash_until
                    and not self.slamming and abs(p.x - enemy.x) < 3.2
                    and abs(p.y - enemy.y) < 1.7):
                self.hurt_until = self.time + 0.85
                self.combo = 0
                self.multiplier = 1
                self.combo_until = 0.0
                self._release_grip()
                p.hp = max(0, p.hp - 1)
                if p.hp == 0:
                    self._finish("dead")
                    return
                p.vx = -26.0 if enemy.x >= p.x else 26.0
                p.vy = -10.0
                p.grounded = False
                self.move_until = 0.0
