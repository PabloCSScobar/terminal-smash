import math
import unittest
from unittest.mock import patch

from terminal_smash.capture import Style, parse_capture
from terminal_smash.tower import TowerWorld


def advance(world, seconds, fps=60):
    for _ in range(math.ceil(seconds * fps)):
        world.update(1 / fps)


def climb_route(world, fps=60):
    """Use the public controls to land on each route platform, bottom to top."""
    for target in reversed(world.platforms[:-1]):
        world.jump()
        for _ in range(fps * 3):
            distance = target.centre - world.player.x
            direction = 0 if abs(distance) < world.MOVE_SPEED / fps / 2 else (1 if distance > 0 else -1)
            world.move(direction)
            world.update(1 / fps)
            if world.finished or world.player.grounded:
                break
        if world.finished:
            return
        if abs(world.player.y - (target.row - 1)) > 0.01:
            raise AssertionError(f'Failed to land on {target}: {world.player}')


class TowerTests(unittest.TestCase):
    def test_routes_are_deterministic_bounded_and_reachable_for_varied_history(self):
        histories = (
            '',
            '\n' * 75,
            '\n'.join('x' * 120 for _ in range(70)),
            '\n'.join('short' if row % 11 == 0 else '' for row in range(70)),
            '\x1b[35m' + '\n'.join('界e\u0301\toutput' for _ in range(70)),
        )
        for width, viewport in ((8, 6), (44, 9), (100, 29)):
            for text in histories:
                with self.subTest(width=width, viewport=viewport, text=text[:20]):
                    world = TowerWorld(text, width, viewport)
                    platforms = list(world.platforms)
                    other = TowerWorld(text, width, viewport)
                    self.assertEqual(platforms, list(other.platforms))
                    for platform in platforms:
                        self.assertGreaterEqual(platform.left, 1)
                        self.assertLessEqual(platform.right, width - 1)
                        self.assertGreaterEqual(platform.right - platform.left, 4)
                    for upper, lower in zip(platforms, platforms[1:]):
                        self.assertTrue(1 <= lower.row - upper.row <= 4)
                        self.assertLessEqual(abs(lower.centre - upper.centre), 10)
                    climb_route(world)
                    self.assertEqual(world.finish_reason, 'summit')
                    self.assertEqual(world.progress, 1)

    def test_single_jump_route_works_at_different_frame_rates(self):
        for fps in (30, 90, 144):
            with self.subTest(fps=fps):
                world = TowerWorld(('a' * 80 + '\n') * 100, 80, 24)
                climb_route(world, fps)
                self.assertEqual(world.finish_reason, 'summit')
                self.assertEqual(world.jump_count, len(world.platforms) - 1)

    def test_dense_history_uses_text_and_empty_rows_use_bridges(self):
        dense = TowerWorld(('界' * 40 + '\n') * 70, 80, 24)
        self.assertTrue(any(not platform.synthetic for platform in dense.platforms))
        for platform in dense.platforms:
            if not platform.synthetic:
                cells = dense._history.cells(platform.row - dense.HISTORY_TOP)
                self.assertTrue(any(cell.x == platform.left for cell in cells))
                self.assertTrue(any(cell.x + cell.width == platform.right for cell in cells))
        empty = TowerWorld('\n' * 70, 80, 24)
        self.assertTrue(all(platform.synthetic for platform in empty.platforms))

    def test_background_keeps_unicode_ansi_state_and_discards_control_strings(self):
        text = '\x1b[31mred\n\x1b]0;hidden\nsecret\x07界e\u0301\tend\nlast'
        world = TowerWorld(text, 44, 14)
        cells = world.visible_cells()
        expected = parse_capture(text, 44, 14)
        self.assertEqual([(c.x, c.y - world.HISTORY_TOP, c.char, c.style, c.width) for c in cells],
                         [(c.x, c.y, c.char, c.style, c.width) for c in expected])
        self.assertEqual(len(world._history), 3)
        self.assertTrue(all(cell.style == Style(fg=1) for cell in cells))
        self.assertTrue(any(cell.char == '界' and cell.width == 2 for cell in cells))
        self.assertTrue(any(cell.char == 'e\u0301' for cell in cells))

    def test_large_history_indexes_once_and_only_parses_visible_rows(self):
        text = ('\x1b[32m' + 'x' * 80 + '\n') * 20000
        with patch('terminal_smash.tower.parse_capture', wraps=parse_capture) as parser:
            world = TowerWorld(text, 80, 24)
            self.assertEqual(parser.call_count, 0)
            self.assertGreater(len(world.platforms), 4000)
            world.visible_cells()
            initial = parser.call_count
            self.assertLessEqual(initial, world.viewport_height)
            for _ in range(30):
                world.update(1 / 60)
                world.visible_cells()
                world.visible_platforms()
            self.assertEqual(parser.call_count, initial)
            for camera in range(100, 450, 25):
                world.camera_y = camera
                cells = world.visible_cells()
                self.assertTrue(all(camera <= cell.y < camera + world.viewport_height for cell in cells))
                world.visible_platforms()
            self.assertLessEqual(len(world._history.cache), world._history.CACHE_ROWS)
            self.assertLessEqual(len(world.platforms.cache), world.platforms.CACHE_ROWS)

    def test_camera_and_progress_never_retreat_during_a_jump_and_fall(self):
        world = TowerWorld('\n' * 100, 44, 9)
        world.player.y = world.camera_y + 2
        world.player.grounded = False
        world.jump()
        previous_camera = world.camera_y
        previous_progress = world.progress
        world.player.x = 1
        for _ in range(240):
            world.update(1 / 60)
            self.assertLessEqual(world.camera_y, previous_camera)
            self.assertGreaterEqual(world.progress, previous_progress)
            previous_camera, previous_progress = world.camera_y, world.progress
            if world.finished:
                break
        self.assertEqual(world.finish_reason, 'fallen')

    def test_double_jump_is_limited_and_landing_restores_it(self):
        world = TowerWorld('\n' * 60, 44, 24)
        world.jump()
        world.update(0.1)
        world.jump()
        world.update(0.1)
        velocity = world.player.vy
        world.jump()
        self.assertEqual(world.player.vy, velocity)
        self.assertEqual(world.jump_count, 2)
        self.assertEqual(world.player.jumps, 2)
        advance(world, 2)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.jumps, 0)

    def test_jump_retains_momentum_between_terminal_key_repeats_and_can_reverse(self):
        world = TowerWorld('\n' * 60, 80, 24)
        world.move(1)
        world.update(0.03)
        world.jump()
        advance(world, 0.20)
        self.assertGreater(world.time, world.move_until)
        self.assertFalse(world.player.grounded)
        self.assertEqual(world.player.vx, world.MOVE_SPEED)
        before = world.player.x
        world.update(0.03)
        self.assertGreater(world.player.x, before)
        world.move(-1)
        world.update(0.01)
        self.assertEqual(world.player.vx, -world.MOVE_SPEED)

    def test_drop_ignores_platform_and_can_fall_below_start(self):
        world = TowerWorld('', 44, 14)
        world.drop()
        advance(world, 1)
        self.assertEqual(world.finish_reason, 'fallen')

    def test_only_route_platforms_support_player_and_upward_motion_passes_through(self):
        world = TowerWorld(('x' * 80 + '\n') * 30, 80, 24)
        target = world.platforms[-2]
        world.player.x = target.centre
        world.jump()
        advance(world, 0.4)
        self.assertLess(world.player.y, target.row - 1)
        self.assertFalse(world.player.grounded)
        advance(world, 0.4)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, target.row - 1)

    def test_start_and_wall_are_not_automatic_climbing_shortcuts(self):
        world = TowerWorld('\n' * 60, 44, 24)
        advance(world, 0.5)
        self.assertEqual(world.player.y, world.start_row - 1)
        world.player.x = 1
        world.player.y = world.camera_y + 5
        world.player.grounded = False
        before = world.player.y
        advance(world, 0.1)
        self.assertGreater(world.player.y, before)

    def test_real_elapsed_time_and_bounded_physics_on_a_stalled_frame(self):
        world = TowerWorld('\n' * 60, 44, 24)
        world.jump()
        start = world.player.y
        world.update(30)
        self.assertEqual(world.elapsed, 30)
        self.assertAlmostEqual(world.time, 0.12)
        self.assertLess(start - world.player.y, 3)
        for dt in (0, -1, float('inf'), float('nan')):
            world.update(dt)
        self.assertEqual(world.elapsed, 30)

    def test_resize_preserves_progress_and_keeps_player_and_support_in_view(self):
        world = TowerWorld(('x' * 120 + '\n') * 60, 120, 30)
        platform = world.platforms[-5]
        world.player.x = platform.centre
        world.player.y = platform.row - 1
        world.update(1 / 60)
        before = world.player.y, world.progress, world.elapsed
        for width, height in ((44, 9), (8, 6), (120, 50), (80, 20)):
            world.resize(width, height)
            self.assertEqual((world.player.y, world.progress, world.elapsed), before)
            self.assertGreaterEqual(world.player.y, world.camera_y)
            self.assertLess(world.player.y, world.camera_y + world.viewport_height)
            self.assertTrue(1 <= world.player.x <= width - 2)
            world.update(1 / 60)
            self.assertTrue(world.player.grounded)
            self.assertFalse(world.finished)
            before = world.player.y, world.progress, world.elapsed

    def test_finish_freezes_controls_and_resize_does_not_revive_reset_does(self):
        world = TowerWorld('first\nlast', 44, 14)
        climb_route(world)
        elapsed = world.elapsed
        position = world.player.x, world.player.y
        world.move(1)
        world.jump()
        world.drop()
        world.update(10)
        self.assertEqual((world.player.x, world.player.y), position)
        self.assertEqual(world.elapsed, elapsed)
        world.resize(80, 20)
        self.assertTrue(world.finished)
        self.assertEqual(world.finish_reason, 'summit')
        world.reset()
        self.assertFalse(world.finished)
        self.assertEqual(world.finish_reason, '')
        self.assertEqual(world.progress, 0)
        self.assertEqual(world.elapsed, 0)
        self.assertEqual(world.jump_count, 0)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, world.start_row - 1)


if __name__ == '__main__':
    unittest.main()
