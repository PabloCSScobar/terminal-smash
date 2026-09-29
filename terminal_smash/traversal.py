"""Optional wall climbing and ceiling hanging for the arcade player.

The mixin handles only player motion. World still advances falling terrain,
enemies, damage and visual effects on every physics step while the player hangs.
"""
from __future__ import annotations


class TraversalMixin:
    CLIMB_SPEED = 16.0
    CEILING_SPEED = 32.0
    GRIP_RELEASE_DELAY = 0.24

    def _reset_traversal(self) -> None:
        self.grip_enabled = getattr(self, 'grip_enabled', False)
        self.grip_surface = ''
        self._climb_direction = 0
        self._climb_until = 0.0
        self._grip_cooldown = 0.0

    def toggle_grip(self) -> bool:
        if not self.finished:
            self.grip_enabled = not self.grip_enabled
            if self.grip_enabled:
                self._grip_cooldown = self.time
            else:
                self._release_grip()
        return self.grip_enabled

    def _release_grip(self) -> None:
        """Detach without cancelling the action or knockback that caused it."""
        self.grip_surface = ''
        self._climb_direction = 0
        self._climb_until = 0.0
        self._grip_cooldown = self.time + self.GRIP_RELEASE_DELAY

    def _try_grip(self) -> bool:
        if (not self.grip_enabled or self.finished or self.slamming
                or self.time < self._grip_cooldown
                or self.time < self.dash_until or self.time < self.attack_until):
            return False
        if self.grip_surface:
            return True
        p = self.player
        if p.y <= 2.0 + 1e-7:
            self.grip_surface = 'ceiling'
            p.y = 2.0
        elif p.x <= 1.0 + 1e-7:
            self.grip_surface = 'left'
            p.x = 1.0
        elif p.x >= self.width - 2.0 - 1e-7:
            self.grip_surface = 'right'
            p.x = self.width - 2.0
        else:
            return False
        p.vx = p.vy = 0.0
        p.grounded = False
        p.jumps = 0
        return True

    def climb_up(self) -> None:
        if self.finished:
            return
        if self._try_grip():
            self._climb_direction = -1
            self._climb_until = self.time + 0.20
        else:
            self.jump()

    def climb_down(self) -> None:
        if self.finished:
            return
        if self._try_grip() and self.grip_surface != 'ceiling':
            self._climb_direction = 1
            self._climb_until = self.time + 0.20
        else:
            self._release_grip()
            self.drop()

    def _traversal_move(self, direction: int) -> None:
        # Moving away from a wall must work even if the movement key and the
        # first attachment would otherwise arrive in the same physics frame.
        if not self._try_grip():
            return
        inward = 1 if self.grip_surface == 'left' else -1
        if self.grip_surface in ('left', 'right') and direction * inward > 0:
            self._release_grip()
            self.player.vx = inward * 40.0
            self.player.vy = 0.0

    def _jump_from_grip(self) -> bool:
        if not self._try_grip():
            return False
        surface = self.grip_surface
        self._release_grip()
        p = self.player
        p.grounded = False
        self.move_until = 0.0
        if surface == 'ceiling':
            p.vx = 0.0
            p.vy = 5.0
        else:
            p.vx = 32.0 if surface == 'left' else -32.0
            p.vy = -21.0
            p.facing = 1 if p.vx > 0 else -1
            p.jumps = 1
            self.jump_started = self.time
        return True

    def _step_traversal(self, dt: float) -> bool:
        """Return whether attachment has handled this step's player movement."""
        if not self._try_grip():
            return False
        p = self.player
        p.grounded = False
        p.jumps = 0
        p.vx = p.vy = 0.0
        if self.grip_surface == 'ceiling':
            p.y = 2.0
            if self.time < self.move_until:
                p.vx = self.direction * self.CEILING_SPEED
                p.x = max(1.0, min(self.width - 2.0, p.x + p.vx * dt))
        else:
            p.x = 1.0 if self.grip_surface == 'left' else self.width - 2.0
            if self.time < self._climb_until:
                p.vy = self._climb_direction * self.CLIMB_SPEED
                p.y = max(2.0, min(self.height - 2.0, p.y + p.vy * dt))
            if p.y <= 2.0:
                self.grip_surface = 'ceiling'
                p.vy = 0.0
                self._climb_until = 0.0
        return True
