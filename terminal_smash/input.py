"""Infer a bounded horizontal hold from ordinary terminal key presses.

Classic curses has no key-release events. A first press therefore needs enough
grace for the keyboard's initial repeat delay; once repeats arrive, a shorter
lease gives prompt release. A tap necessarily shares that initial grace.
"""
from __future__ import annotations


class HorizontalInput:
    FIRST_REPEAT_GRACE = 0.65
    REPEAT_RELEASE_GRACE = 0.12

    def __init__(self) -> None:
        self.direction = 0
        self.expires_at = 0.0

    def press(self, direction: int, now: float) -> None:
        direction = -1 if direction < 0 else 1
        repeating = direction == self.direction and now < self.expires_at
        grace = self.REPEAT_RELEASE_GRACE if repeating else self.FIRST_REPEAT_GRACE
        self.direction = direction
        self.expires_at = now + grace

    def apply(self, world, now: float) -> None:
        """Feed the existing physics without adding its own extra release tail."""
        remaining = self.expires_at - now
        if self.direction and remaining > 0:
            world.move(self.direction)
            world.move_until = min(world.move_until, world.time + remaining)
        elif self.direction:
            world.move_until = world.time
            self.direction = 0

    def clear(self, world=None) -> None:
        self.direction = 0
        self.expires_at = 0.0
        if world is not None:
            world.move_until = world.time
            world.player.vx = 0.0
