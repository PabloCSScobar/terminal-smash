"""Infer a bounded horizontal hold from ordinary terminal key presses.

Classic curses has no key-release events. A first press is a short step; only
observed rapid repeats extend it into a run. The initial system repeat delay
cannot be filled in without making a single tap keep moving after release.
"""
from __future__ import annotations


class HorizontalInput:
    TAP_DURATION = 0.06
    REPEAT_WINDOW = 0.15
    MIN_REPEAT_GRACE = 0.06
    MAX_REPEAT_GRACE = 0.12

    def __init__(self) -> None:
        self.direction = 0
        self.expires_at = 0.0
        self.last_press_at: float | None = None
        self.last_direction = 0

    def press(self, direction: int, now: float) -> None:
        direction = -1 if direction < 0 else 1
        interval = now - self.last_press_at if self.last_press_at is not None else float('inf')
        repeating = direction == self.last_direction and 0 <= interval <= self.REPEAT_WINDOW
        grace = (min(self.MAX_REPEAT_GRACE, max(self.MIN_REPEAT_GRACE, interval * 1.5 + 0.01))
                 if repeating else self.TAP_DURATION)
        self.direction = direction
        self.last_direction = direction
        self.last_press_at = now
        self.expires_at = now + grace

    def apply(self, world, now: float) -> None:
        """Feed the existing physics without adding its own extra release tail."""
        remaining = self.expires_at - now
        if self.direction and remaining > 0:
            world.move(self.direction)
            world.move_until = min(world.move_until, world.time + remaining)
        elif self.direction:
            world.move_until = world.time
            if (world.player.grounded and world.time >= getattr(world, 'dash_until', 0.0)
                    and not getattr(world, 'slamming', False) and not getattr(world, 'grip_surface', '')):
                world.player.vx = 0.0
            self.direction = 0

    def clear(self, world=None) -> None:
        self.direction = 0
        self.expires_at = 0.0
        self.last_press_at = None
        self.last_direction = 0
        if world is not None:
            world.move_until = world.time
            world.player.vx = 0.0
