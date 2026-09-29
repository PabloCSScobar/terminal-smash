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

    def test_single_tap_moves_two_to_three_columns_and_stops_before_tenth_second(self):
        start_x = self.world.player.x
        self.input.press(-1, self.now)
        self.advance(11)
        self.assertLess(self.now, 0.1)
        self.assertEqual(self.world.player.vx, 0)
        distance = start_x - self.world.player.x
        self.assertGreater(distance, 2.0)
        self.assertLess(distance, 3.1)
        stopped_x = self.world.player.x
        self.advance(60)
        self.assertEqual(self.world.player.x, stopped_x)

    def test_first_repeat_after_half_second_cannot_extend_the_original_tap(self):
        start_x = self.world.player.x
        self.input.press(1, self.now)
        self.advance(60)
        self.assertLess(self.world.player.x - start_x, 3.1)
        self.assertEqual(self.world.player.vx, 0)
        repeated_x = self.world.player.x
        self.input.press(1, self.now)
        self.advance(11)
        self.assertLess(self.world.player.x - repeated_x, 3.1)
        self.assertEqual(self.world.player.vx, 0)

    def test_confirmed_repeats_move_smoothly_at_thirty_and_ten_hertz(self):
        for frames_between_presses in (4, 12):
            with self.subTest(frames_between_presses=frames_between_presses):
                self.setUp()
                self.input.press(1, self.now)
                self.advance(frames_between_presses)
                velocities = []
                for _ in range(6):
                    self.input.press(1, self.now)
                    velocities.extend(self.advance(frames_between_presses))
                self.assertTrue(all(speed == self.world.MOVE_SPEED for speed in velocities))

    def test_release_after_confirmed_repeats_stops_within_bounded_window(self):
        for frames_between_presses in (4, 12):
            with self.subTest(frames_between_presses=frames_between_presses):
                self.setUp()
                for _ in range(3):
                    self.input.press(1, self.now)
                    self.advance(frames_between_presses)
                self.input.press(1, self.now)
                released_x = self.world.player.x
                self.advance(18)
                self.assertEqual(self.world.player.vx, 0)
                self.assertLess(self.world.player.x - released_x, 5.6)
                stopped_x = self.world.player.x
                self.advance(24)
                self.assertEqual(self.world.player.x, stopped_x)

    def test_direction_change_is_immediate_and_starts_a_short_tap(self):
        self.input.press(1, self.now)
        self.advance(12)
        self.input.press(1, self.now)
        self.advance(4)
        self.assertEqual(self.world.player.vx, self.world.MOVE_SPEED)
        self.input.press(-1, self.now)
        self.assertEqual(self.advance(1), [-self.world.MOVE_SPEED])
        self.assertEqual(self.world.player.facing, -1)
        self.advance(10)
        self.assertEqual(self.world.player.vx, 0)

    def test_separate_taps_two_tenths_apart_each_remain_short(self):
        for _ in range(4):
            start_x = self.world.player.x
            self.input.press(1, self.now)
            self.advance(24)
            self.assertGreater(self.world.player.x - start_x, 2.0)
            self.assertLess(self.world.player.x - start_x, 3.1)
            self.assertEqual(self.world.player.vx, 0)

    def test_jump_keeps_horizontal_motion(self):
        self.input.press(1, self.now)
        self.advance(3)
        self.world.jump()
        velocities = self.advance(36)
        self.assertTrue(all(speed == self.world.MOVE_SPEED for speed in velocities))
        self.assertGreater(self.world.jump_count, 0)

    def test_clear_cancels_active_hold_and_airborne_momentum(self):
        self.input.press(1, self.now)
        self.advance(3)
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

    def test_expiring_tap_preserves_active_grounded_dash(self):
        world = World([], 120, 30)
        world.player.x = 60
        world.player.y = 28
        world.player.grounded = True
        self.input.press(1, 0.0)
        self.input.apply(world, 0.0)
        self.assertTrue(world.dash())
        world.update(0.08)
        self.assertTrue(world.player.grounded)
        self.assertLess(world.time, world.dash_until)
        self.input.apply(world, 0.08)
        self.assertEqual(world.player.vx, 110)
        previous_x = world.player.x
        world.update(0.05)
        self.assertAlmostEqual(world.player.x - previous_x, 5.5)
        self.assertEqual(world.player.vx, 110)


if __name__ == '__main__':
    unittest.main()
