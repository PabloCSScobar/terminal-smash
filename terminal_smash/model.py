"""Frame-rate independent game physics; no terminal or subprocess side effects."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random

from .capture import Cell, Style


@dataclass
class Player:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    facing: int = 1
    grounded: bool = False
    jumps: int = 0


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


class World:
    """Coordinates are character cells; player.y is the row occupied by feet."""

    def __init__(self, cells: list[Cell], width: int, height: int, seed: int | None = None):
        self.width = max(8, width)
        self.height = max(6, height)
        self.original = tuple(cells)
        self.rng = random.Random(seed)
        self.time = 0.0
        self.reset()

    def reset(self) -> None:
        self.cells = {(c.x, c.y): c for c in self.original
                      if 0 <= c.x < self.width and 0 <= c.y < self.height - 1}
        self.occupied = {(c.x + dx, c.y): (c.x, c.y)
                         for c in self.cells.values() for dx in range(c.width)}
        self.total = len(self.cells)
        self.destroyed = 0
        self.player = Player(float(max(2, self.width // 3)), 2.0)
        self.particles: list[Particle] = []
        self.waves: list[Wave] = []
        self.direction = 0
        self.move_until = 0.0
        self.drop_until = 0.0
        self.attack_until = 0.0
        self.next_punch = 0.0
        self.next_blast = 0.0
        self.shake_until = 0.0

    def move(self, direction: int) -> None:
        self.direction = 1 if direction > 0 else -1
        self.player.facing = self.direction
        self.move_until = self.time + 0.18

    def jump(self) -> None:
        if self.player.grounded or self.player.jumps < 2:
            self.player.vy = -16.0
            self.player.grounded = False
            self.player.jumps += 1

    def drop(self) -> None:
        self.drop_until = self.time + 0.22
        self.player.grounded = False
        self.player.vy = max(5.0, self.player.vy)

    def return_to_top(self) -> None:
        """Reach isolated text again after falling below it, keeping destruction."""
        self.player.y = 2.0
        self.player.vx = self.player.vy = 0.0
        self.player.grounded = False
        self.player.jumps = 0
        self.move_until = self.drop_until = 0.0

    def punch(self) -> int:
        if self.time < self.next_punch:
            return 0
        self.next_punch = self.time + 0.14
        self.attack_until = self.time + 0.16
        p = self.player
        return self.destroy(p.x + p.facing * 3.0, p.y - 0.8, 4.5, 2.2)

    def blast(self) -> int:
        if self.time < self.next_blast:
            return 0
        self.next_blast = self.time + 0.8
        p = self.player
        self.waves.append(Wave(p.x, p.y - 1.0))
        self.shake_until = self.time + 0.15
        return self.destroy(p.x, p.y - 1.0, 12.0, 5.5)

    def destroy(self, x: float, y: float, rx: float, ry: float) -> int:
        hit = [key for key, c in self.cells.items()
               if ((c.x + (c.width - 1) / 2 - x) / rx) ** 2 + ((c.y - y) / ry) ** 2 <= 1]
        for key in hit:
            c = self.cells.pop(key)
            for dx in range(c.width):
                self.occupied.pop((c.x + dx, c.y), None)
            vx = (c.x - x) * 2 + self.rng.uniform(-9, 9)
            vy = -self.rng.uniform(3, 12) + (c.y - y) * 0.7
            self.particles.append(Particle(c.char, c.style, c.x, c.y, vx, vy,
                                           self.rng.uniform(0.6, 1.8), c.width))
        self.destroyed += len(hit)
        if hit:
            for _ in range(min(18, len(hit) * 2)):
                self.particles.append(Particle(self.rng.choice(".*+"), Style(fg=220, bold=True),
                                               x, y, self.rng.uniform(-22, 22),
                                               self.rng.uniform(-12, 4), self.rng.uniform(0.15, 0.45)))
        # Bound rendering and memory even for very large terminal windows.
        self.particles = self.particles[-800:]
        return len(hit)

    def update(self, dt: float) -> None:
        # Substeps avoid falling through a one-row platform on slow frames.
        remaining = max(0.0, min(dt, 0.12))
        while remaining > 1e-9:
            step = min(remaining, 1 / 120)
            self._step(step)
            remaining -= step

    def _step(self, dt: float) -> None:
        self.time += dt
        p = self.player
        if self.time < self.move_until:
            p.vx = self.direction * 25.0
        else:
            p.vx *= math.exp(-16.0 * dt)
        p.x = max(1.0, min(self.width - 2.0, p.x + p.vx * dt))
        old_y = p.y
        p.vy = min(30.0, p.vy + 32.0 * dt)
        next_y = old_y + p.vy * dt
        p.grounded = False
        if p.vy >= 0:
            floor = self.height - 2.0
            landing = floor if next_y >= floor else None
            if self.time >= self.drop_until:
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
        if next_y < 2.0:
            next_y = 2.0
            p.vy = max(0.0, p.vy)
        p.y = next_y
        alive = []
        for particle in self.particles:
            particle.life -= dt
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vy += 22.0 * dt
            if particle.life > 0 and -4 < particle.y < self.height + 3:
                alive.append(particle)
        self.particles = alive
        for wave in self.waves:
            wave.age += dt
        self.waves = [wave for wave in self.waves if wave.age < 0.38]
