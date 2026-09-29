"""Key-repeat timing against real physics, without a terminal key-up protocol."""
import unittest

from terminal_smash.input import HorizontalInput
from terminal_smash.model import World
from terminal_smash.tower import TowerWorld


class HorizontalInputTests(unittest.TestCase):
    def setUp(self):
        self.world = TowerWorld('application output\n' * 40, 240, 25, seed=1)
        self.world.player.x = 120.0
        self.input = HorizontalInput()
        self.now = 0.0

    def advance(self, frames):
        velocities = []
        for _ in range(frames):
            self.input.apply(self.world, self.now)
            self.world.update(1 / 120)
            self.now += 1 / 120
            velocities.append(self.world.player.vx)
        return velocities

    def test_first_press_bridges_half_second_repeat_delay_without_slowing(self):
        self.input.press(1, self.now)
        velocities = self.advance(60)
        for _ in range(5):
            self.input.press(1, self.now)
            velocities.extend(self.advance(4))
        self.assertTrue(all(speed == self.world.MOVE_SPEED for speed in velocities))

    def test_releasing_after_repeats_uses_short_tail_not_first_press_grace(self):
        self.input.press(1, self.now)
        self.advance(60)
        self.input.press(1, self.now)
        self.advance(4)
        self.input.press(1, self.now)
        released_at = self.now
        released_x = self.world.player.x
        self.advance(40)
        self.assertLess(self.world.player.vx, 0.3)
        self.assertLess(self.world.player.x - released_x, 8.0)
        self.assertLess(self.now - released_at, self.input.FIRST_REPEAT_GRACE)
        self.assertEqual(self.input.direction, 0)

    def test_single_tap_has_bounded_first_repeat_grace(self):
        self.input.press(-1, self.now)
        self.advance(120)
        self.assertLess(abs(self.world.player.vx), 0.03)
        self.assertEqual(self.input.direction, 0)
        self.assertGreater(self.world.player.x, 85)

    def test_direction_change_is_immediate_and_has_its_own_initial_grace(self):
        self.input.press(1, self.now)
        self.advance(30)
        self.input.press(-1, self.now)
        velocities = self.advance(60)
        self.assertTrue(all(speed == -self.world.MOVE_SPEED for speed in velocities))
        self.assertEqual(self.world.player.facing, -1)

    def test_later_press_after_release_is_a_new_hold(self):
        self.input.press(1, self.now)
        self.advance(100)
        self.input.press(1, self.now)
        velocities = self.advance(60)
        self.assertTrue(all(speed == self.world.MOVE_SPEED for speed in velocities))

    def test_jump_keeps_horizontal_motion(self):
        self.input.press(1, self.now)
        self.advance(12)
        self.world.jump()
        velocities = self.advance(36)
        self.assertTrue(all(speed == self.world.MOVE_SPEED for speed in velocities))
        self.assertGreater(self.world.jump_count, 0)

    def test_clear_cancels_active_hold_and_airborne_momentum(self):
        self.input.press(1, self.now)
        self.advance(12)
        self.world.jump()
        self.advance(4)
        stopped_x = self.world.player.x
        self.input.clear(self.world)
        self.advance(12)
        self.assertEqual(self.world.player.x, stopped_x)
        self.assertEqual(self.world.player.vx, 0)
        self.assertEqual(self.input.direction, 0)

    def test_expired_hold_does_not_override_existing_airborne_momentum(self):
        self.input.press(1, self.now)
        self.advance(60)
        self.input.press(1, self.now)
        self.world.jump()
        self.advance(25)
        self.assertEqual(self.input.direction, 0)
        self.assertFalse(self.world.player.grounded)
        self.assertEqual(self.world.player.vx, self.world.MOVE_SPEED)

    def test_free_play_clear_never_calls_move_zero_which_means_left(self):
        world = World([], 120, 30)
        world.player.x = 60
        world.player.y = 28
        world.player.grounded = True
        self.input.press(1, 0.0)
        self.input.apply(world, 0.0)
        world.update(0.05)
        stopped_x = world.player.x
        self.input.clear(world)
        self.input.apply(world, 0.06)
        world.update(0.05)
        self.assertEqual(world.player.x, stopped_x)
        self.assertEqual(world.player.facing, 1)


if __name__ == '__main__':
    unittest.main()
