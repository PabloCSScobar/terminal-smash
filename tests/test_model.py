import unittest

from terminal_smash.capture import Cell, Style
from terminal_smash.model import World


class PhysicsTests(unittest.TestCase):
    def test_player_lands_on_a_line_then_falls_when_it_is_destroyed(self):
        cells = [Cell(x, 10, '=') for x in range(40)]
        world = World(cells, 40, 22, seed=5)
        for _ in range(120):
            world.update(1 / 60)
        self.assertAlmostEqual(world.player.y, 9)
        self.assertTrue(world.player.grounded)
        world.destroy(world.player.x, 10, 5, 2)
        for _ in range(120):
            world.update(1 / 60)
        self.assertAlmostEqual(world.player.y, 20)
        self.assertEqual(len(cells), 40)  # The source snapshot is immutable.

    def test_large_frames_do_not_tunnel_through_platforms(self):
        world = World([Cell(x, 8, '-') for x in range(30)], 30, 25)
        world.player.vy = 30
        for _ in range(5):
            world.update(0.1)
        self.assertAlmostEqual(world.player.y, 7)

    def test_double_jump_is_limited_until_landing(self):
        world = World([], 40, 25)
        world.player.y = 15
        world.jump()
        world.update(0.1)
        world.jump()
        self.assertEqual(world.player.jumps, 2)
        world.update(0.1)
        before = world.player.vy
        world.jump()
        self.assertEqual(world.player.vy, before)
        for _ in range(200):
            world.update(1 / 60)
        self.assertEqual(world.player.jumps, 0)
        self.assertTrue(world.player.grounded)

    def test_drop_passes_through_the_platform(self):
        world = World([Cell(x, 8, '=') for x in range(40)], 40, 25)
        for _ in range(100):
            world.update(1 / 60)
        self.assertAlmostEqual(world.player.y, 7)
        world.drop()
        for _ in range(40):
            world.update(1 / 60)
        self.assertGreater(world.player.y, 8)

    def test_wide_character_platform_and_debris_keep_width_and_style(self):
        style = Style(fg=201, bg=17, bold=True)
        world = World([Cell(8, 8, '界', style, 2)], 30, 20, seed=1)
        world.player.x = 10
        for _ in range(90):
            world.update(1 / 60)
        self.assertAlmostEqual(world.player.y, 7)
        self.assertEqual(world.destroy(9, 8, 2, 2), 1)
        self.assertFalse(world.occupied)
        self.assertEqual(world.particles[0].width, 2)
        self.assertEqual(world.particles[0].style, style)

    def test_cooldown_and_reset(self):
        world = World([Cell(x, y, '#') for x in range(40) for y in range(20)], 40, 25, seed=7)
        self.assertGreater(world.blast(), 0)
        self.assertEqual(world.blast(), 0)
        self.assertLessEqual(len(world.particles), 800)
        self.assertGreater(world.destroyed, 0)
        world.reset()
        self.assertEqual(len(world.cells), 800)
        self.assertEqual(world.destroyed, 0)
        self.assertFalse(world.particles)
        self.assertFalse(world.waves)

    def test_return_to_top_reaches_isolated_text_without_resetting_damage(self):
        world = World([Cell(x, 0, '=') for x in range(80)], 80, 30)
        world.destroy(0, 0, 3, 2)
        removed = world.destroyed
        for _ in range(200):
            world.update(1 / 60)
        self.assertAlmostEqual(world.player.y, 28)
        world.return_to_top()
        self.assertEqual(world.destroyed, removed)
        self.assertEqual(world.player.y, 2)
        self.assertGreater(world.blast(), 0)

    def test_fast_movement_is_consistent_across_frame_rates_and_reverses(self):
        distances = []
        for fps in (30, 60, 90, 144):
            world = World([], 200, 25)
            start = world.player.x
            for _ in range(fps // 2):
                world.move(1)
                world.update(1 / fps)
            distances.append(world.player.x - start)
            before_turn = world.player.x
            world.move(-1)
            world.update(1 / fps)
            self.assertLess(world.player.x, before_turn)
        self.assertTrue(all(19.5 <= distance <= 20.5 for distance in distances))
        self.assertLess(max(distances) - min(distances), 0.2)

    def test_fast_jump_keeps_platform_reach_and_lands_without_floating(self):
        world = World([], 80, 30)
        world.player.y = 28
        world.player.grounded = True
        world.jump()
        heights = []
        for _ in range(80):
            world.update(1 / 90)
            heights.append(world.player.y)
        self.assertLess(min(heights), 24.2)
        self.assertGreater(min(heights), 23.5)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, 28)

    def test_running_jump_keeps_horizontal_momentum_without_more_move_keys(self):
        for fps in (30, 60, 90, 144):
            for direction in (-1, 1):
                with self.subTest(fps=fps, direction=direction):
                    world = World([], 200, 30)
                    world.player.y = 28
                    world.player.grounded = True
                    world.move(direction)
                    world.update(0.1)
                    world.jump()
                    takeoff_x = world.player.x
                    # The jump key replaces movement autorepeat in a terminal.
                    for _ in range(fps // 2):
                        world.update(1 / fps)
                    self.assertFalse(world.player.grounded)
                    self.assertAlmostEqual((world.player.x - takeoff_x) * direction, 20, delta=0.2)

    def test_stationary_jump_stays_vertical(self):
        world = World([], 200, 30)
        world.player.y = 28
        world.player.grounded = True
        takeoff_x = world.player.x
        world.jump()
        for _ in range(80):
            world.update(1 / 90)
            self.assertEqual(world.player.x, takeoff_x)
        self.assertTrue(world.player.grounded)

    def test_double_jump_keeps_momentum_and_airborne_direction_can_change(self):
        world = World([], 200, 30)
        world.player.y = 28
        world.player.grounded = True
        world.move(1)
        world.jump()  # Both key events may be read in the same frame.
        for _ in range(27):
            world.update(1 / 90)
        takeoff_x = world.player.x
        world.jump()
        for _ in range(18):
            world.update(1 / 90)
        self.assertGreater(world.player.x - takeoff_x, 7)
        turn_x = world.player.x
        world.move(-1)
        for _ in range(27):
            world.update(1 / 90)
        self.assertLess(world.player.x - turn_x, -11)
        self.assertFalse(world.player.grounded)

    def test_landing_brakes_after_a_running_jump(self):
        world = World([], 200, 30)
        world.player.y = 28
        world.player.grounded = True
        world.move(1)
        world.jump()
        for _ in range(90):
            world.update(1 / 90)
            if world.player.grounded:
                break
        self.assertTrue(world.player.grounded)
        landing_x = world.player.x
        for _ in range(45):
            world.update(1 / 90)
        self.assertLess(world.player.x - landing_x, 2)
        self.assertLess(abs(world.player.vx), 0.01)

    def test_particles_expire_and_motion_stays_in_bounds(self):
        world = World([Cell(10, 5, 'X')], 30, 20, seed=2)
        world.destroy(10, 5, 2, 2)
        for _ in range(250):
            world.move(-1)
            world.update(1 / 60)
        self.assertEqual(world.player.x, 1)
        self.assertFalse(world.particles)
        self.assertAlmostEqual(world.player.y, 18)


if __name__ == '__main__':
    unittest.main()
