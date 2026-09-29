"""Physics and source-preserving routes for the scrollback tower."""

import math
import unittest
from unittest.mock import patch

from terminal_smash.capture import Style, parse_capture
from terminal_smash.tower import TowerWorld


def advance(world, seconds, fps=60):
    for _ in range(math.ceil(seconds * fps)):
        world.update(1 / fps)


def steer(world, target, fps):
    distance = target - world.player.x
    world.move(0 if abs(distance) <= world.MOVE_SPEED / fps / 2 else (1 if distance > 0 else -1))


def climb_route(world, fps=60):
    """Walk to a real takeoff point and climb using only public controls."""
    for target in reversed(world.platforms[:-1]):
        if world.finished:
            break
        row = round(world.player.y + 1)
        if target.row >= row:
            # A short jump can land on a higher real step than the intended one.
            # Same-row connectors are handled as walkable takeoff supports.
            continue
        supports = world.platforms.by_row[row]
        source = max(supports, key=lambda platform: platform.right - platform.left)
        takeoff = world.landing_x(source, target.centre)
        for _ in range(fps * 5):
            if abs(takeoff - world.player.x) <= world.MOVE_SPEED / fps / 2:
                break
            steer(world, takeoff, fps)
            world.update(1 / fps)
            if not world.player.grounded or world.finished:
                raise AssertionError(f'Lost support walking from {source}: {world.player}')
        else:
            raise AssertionError(f'Could not reach takeoff on {source}')
        world.move(0)
        world.update(1 / fps)
        landing = world.landing_x(target, takeoff)
        delay = world.jump_delay(source, target)
        started = world.time
        world.jump()
        for _ in range(fps * 3):
            if delay is not None and world.time - started >= delay:
                world.jump()
                delay = None
            steer(world, landing, fps)
            world.update(1 / fps)
            if world.finished or world.player.grounded:
                break
        if world.finished:
            break
        if not world.player.grounded or world.player.y > target.row - 1:
            raise AssertionError(f'Failed to reach {target} from {source}: {world.player}')
    if world.finish_reason != 'summit':
        raise AssertionError(f'Route did not finish at summit: {world.finish_reason}, {world.player}')


class TowerTests(unittest.TestCase):
    def assert_original_footprints(self, world):
        for platform in world.platforms:
            if platform.synthetic:
                continue
            cells = world._history.cells(platform.row - world.HISTORY_TOP)
            selected = [cell for cell in cells if platform.left <= cell.x
                        and cell.x + cell.width <= platform.right]
            columns = {column for cell in selected for column in range(cell.x, cell.x + cell.width)}
            self.assertEqual(columns, set(range(platform.left, platform.right)))
            self.assertGreaterEqual(platform.left, 0)
            self.assertLessEqual(platform.right, world.width)

    def test_dense_routes_use_only_original_glyphs_including_edges_and_unicode(self):
        for line, width in (("output", 80), ("x", 44), (" " * 43 + "x", 44),
                            ("界e\u0301", 44), (" " * 42 + "界", 44), ("x" * 80, 80)):
            with self.subTest(line=line, width=width):
                world = TowerWorld((line + '\n') * 100, width, 24, seed=31)
                self.assertFalse(world.empty)
                self.assertTrue(world.platforms)
                self.assertFalse(any(platform.synthetic for platform in world.platforms))
                self.assertEqual(world.summit_row, world.HISTORY_TOP)
                self.assertEqual(world.start_row, world.HISTORY_TOP + 99)
                self.assert_original_footprints(world)
                climb_route(world)
                self.assertEqual(world.progress, 1)

    def test_seeded_routes_vary_spacing_and_repeat_after_reset_and_resize(self):
        text = ('output with more text\n') * 120
        world = TowerWorld(text, 80, 24, seed=37)
        route = list(world.platforms)
        self.assertEqual(route, list(TowerWorld(text, 80, 24, seed=37).platforms))
        self.assertNotEqual(route, list(TowerWorld(text, 80, 24, seed=2).platforms))
        gaps = [lower.row - upper.row for upper, lower in zip(route, route[1:])]
        self.assertTrue(all(6 <= gap <= 9 for gap in gaps))
        self.assertGreaterEqual(len(set(gaps)), 3)
        world.jump()
        world.update(0.1)
        world.reset()
        self.assertEqual(list(world.platforms), route)
        world.resize(100, 24)
        world.resize(80, 24)
        self.assertEqual(list(world.platforms), route)
        self.assertEqual(world.seed, 37)

    def test_actual_route_controls_reach_summit_at_varied_frame_rates_and_sizes(self):
        histories = [('word\n') * 65, ('long_original_output' * 4 + '\n') * 65,
                     'summit' + '\n' * 65 + 'start', 'top\n' + ' ' * 95 + 'bottom']
        for fps, width, viewport in ((30, 120, 24), (90, 44, 14), (144, 8, 6)):
            for text in histories:
                with self.subTest(fps=fps, width=width, viewport=viewport, text=text[:20]):
                    world = TowerWorld(text, width, viewport, seed=4)
                    climb_route(world, fps)
                    self.assertEqual(world.finish_reason, 'summit')

    def test_reachable_double_jump_gap_needs_no_artificial_bridge(self):
        world = TowerWorld('summit' + '\n' * 12 + 'start', 80, 24, seed=9)
        self.assertEqual(len(world.platforms), 2)
        self.assertFalse(any(platform.synthetic for platform in world.platforms))
        self.assertIsNotNone(world.jump_delay(world.platforms[-1], world.platforms[0]))
        climb_route(world)
        self.assertEqual(world.jump_count, 2)

    def test_only_disconnected_output_gets_bridges_and_keeps_original_endpoints(self):
        for text in ('top' + '\n' * 65 + 'bottom', 'top\n' + ' ' * 100 + 'bottom'):
            with self.subTest(text=text[:20]):
                world = TowerWorld(text, 120, 24, seed=1)
                artificial = [platform for platform in world.platforms if platform.synthetic]
                self.assertTrue(artificial)
                self.assertLessEqual(len(artificial), 8)
                self.assertFalse(world.platforms[0].synthetic)
                self.assertFalse(world.platforms[-1].synthetic)
                self.assert_original_footprints(world)
                climb_route(world)

    def test_empty_and_control_only_output_stay_empty_and_pause(self):
        for text in ('', '\n' * 75, ' \t\n', '\x1b]0;hidden\x07\x1b[31m'):
            with self.subTest(text=text):
                world = TowerWorld(text, 44, 14, seed=1)
                self.assertTrue(world.empty)
                self.assertEqual(list(world.platforms), [])
                before = world.player.x, world.player.y
                world.move(1)
                world.jump()
                world.drop()
                world.update(10)
                self.assertEqual((world.player.x, world.player.y), before)
                self.assertEqual(world.elapsed, 0)
                self.assertFalse(world.finished)

    def test_background_preserves_ansi_unicode_and_discards_control_strings(self):
        text = '\x1b[31mred\n\x1b]0;hidden\nsecret\x07界e\u0301\tend\nlast'
        world = TowerWorld(text, 44, 30, seed=3)
        cells = world.visible_cells()
        expected = parse_capture(text, 44, 30)
        self.assertEqual([(c.x, c.y - world.HISTORY_TOP, c.char, c.style, c.width) for c in cells],
                         [(c.x, c.y, c.char, c.style, c.width) for c in expected])
        self.assertEqual(len(world._history), 3)
        self.assertTrue(all(cell.style == Style(fg=1) for cell in cells))
        self.assertTrue(any(cell.char == '界' and cell.width == 2 for cell in cells))
        self.assertTrue(any(cell.char == 'e\u0301' for cell in cells))

    def test_history_scan_and_repeated_rendering_keep_bounded_row_cache(self):
        text = ('\x1b[32m' + 'output' * 12 + '\n') * 1200
        with patch('terminal_smash.tower.parse_capture', wraps=parse_capture) as parser:
            world = TowerWorld(text, 80, 24, seed=5)
            self.assertGreaterEqual(parser.call_count, 1200)
            self.assertLessEqual(len(world._history.cache), world._history.CACHE_ROWS)
            world.visible_cells()
            initial = parser.call_count
            for _ in range(20):
                world.update(1 / 60)
                world.visible_cells()
                world.visible_platforms()
            self.assertEqual(parser.call_count, initial)
            for camera in range(100, 450, 25):
                world.camera_y = camera
                cells = world.visible_cells()
                self.assertTrue(all(camera <= cell.y < camera + world.viewport_height for cell in cells))
            self.assertLessEqual(len(world._history.cache), world._history.CACHE_ROWS)

    def test_camera_and_progress_never_retreat_when_player_misses_the_route(self):
        world = TowerWorld('top' + '\n' * 100 + 'start', 80, 24, seed=3)
        world.player.y = world.camera_y + 2
        world.player.x = 78
        world.player.grounded = False
        world.jump()
        camera, progress = world.camera_y, world.progress
        for _ in range(240):
            world.update(1 / 60)
            self.assertLessEqual(world.camera_y, camera)
            self.assertGreaterEqual(world.progress, progress)
            camera, progress = world.camera_y, world.progress
            if world.finished:
                break
        self.assertEqual(world.finish_reason, 'fallen')

    def test_falling_is_snappy_and_double_jump_is_limited(self):
        world = TowerWorld('top' + '\n' * 80 + 'start', 80, 24, seed=2)
        world.player.x = 78
        world.player.y = world.camera_y + 7
        world.player.grounded = False
        before = world.player.y
        advance(world, 0.25, 120)
        self.assertGreater(world.player.y - before, 5)
        self.assertGreater(world.player.vy, 40)
        self.assertFalse(world.finished)
        world.reset()
        world.jump()
        world.update(0.1)
        world.jump()
        world.update(0.1)
        velocity = world.player.vy
        world.jump()
        self.assertEqual(world.player.vy, velocity)
        self.assertEqual(world.jump_count, 2)
        self.assertEqual(world.player.jumps, 2)
        advance(world, 1)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.jumps, 0)

    def test_jump_momentum_survives_key_repeat_gaps_and_can_reverse(self):
        world = TowerWorld(('x' * 80 + '\n') * 60, 80, 24, seed=11)
        world.move(1)
        world.update(0.03)
        world.jump()
        advance(world, 0.20)
        self.assertGreater(world.time, world.move_until)
        self.assertFalse(world.player.grounded)
        self.assertEqual(world.player.vx, world.MOVE_SPEED)
        world.move(-1)
        world.update(0.01)
        self.assertEqual(world.player.vx, -world.MOVE_SPEED)

    def test_drop_falls_below_start_and_walls_never_grip(self):
        world = TowerWorld(('text\n') * 60, 44, 24, seed=7)
        advance(world, 0.5)
        self.assertEqual(world.player.y, world.start_row - 1)
        world.drop()
        advance(world, 1)
        self.assertEqual(world.finish_reason, 'fallen')
        world.reset()
        world.player.x = 42
        world.player.y = world.camera_y + 5
        world.player.grounded = False
        before = world.player.y
        advance(world, 0.1)
        self.assertGreater(world.player.y, before)

    def test_upward_motion_passes_through_real_text_and_descending_lands_on_it(self):
        world = TowerWorld('platform' + '\n' * 8 + 'platform', 80, 24, seed=1)
        target = world.platforms[0]
        world.jump()
        advance(world, 0.3)
        self.assertLess(world.player.y, target.row - 1)
        self.assertFalse(world.player.grounded)
        advance(world, 0.4)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, target.row - 1)
        self.assertEqual(world.finish_reason, 'summit')

    def test_elapsed_clock_is_real_while_stalled_physics_and_invalid_dt_are_bounded(self):
        world = TowerWorld(('output\n') * 60, 44, 24, seed=1)
        world.jump()
        start = world.player.y
        world.update(30)
        self.assertEqual(world.elapsed, 30)
        self.assertAlmostEqual(world.time, 0.12)
        self.assertLess(start - world.player.y, 6)
        for dt in (0, -1, float('inf'), float('nan')):
            world.update(dt)
        self.assertEqual(world.elapsed, 30)

    def test_resize_preserves_attempt_and_pauses_if_occupied_text_is_clipped(self):
        text = 'top\n' + '\n' * 25 + ' ' * 70 + 'start'
        world = TowerWorld(text, 100, 24, seed=3)
        world.update(0.1)
        before = world.player.x, world.player.y, world.progress, world.elapsed
        world.resize(44, 24)
        self.assertTrue(world.resize_blocked)
        world.move(-1)
        world.jump()
        world.drop()
        world.update(10)
        self.assertEqual((world.player.x, world.player.y, world.progress, world.elapsed), before)
        world.resize(100, 24)
        self.assertFalse(world.resize_blocked)
        self.assertEqual((world.player.y, world.progress, world.elapsed), before[1:])
        world.update(0.1)
        self.assertTrue(world.player.grounded)
        self.assertGreater(world.elapsed, before[-1])

    def test_initially_clipped_output_starts_on_newest_real_text_after_widening(self):
        world = TowerWorld((' ' * 70 + 'output\n') * 30, 44, 24, seed=2)
        self.assertTrue(world.empty)
        world.update(4)
        world.resize(100, 24)
        self.assertFalse(world.empty)
        self.assertFalse(world.resize_blocked)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, world.start_row - 1)
        self.assertEqual(world.progress, 0)
        self.assertEqual(world.elapsed, 0)

    def test_finish_freezes_controls_and_resize_preserves_result_while_reset_restarts(self):
        world = TowerWorld('first' + '\n' * 8 + 'last', 44, 24, seed=1)
        climb_route(world)
        elapsed = world.elapsed
        position = world.player.x, world.player.y
        world.move(1)
        world.jump()
        world.drop()
        world.update(10)
        self.assertEqual((world.player.x, world.player.y), position)
        self.assertEqual(world.elapsed, elapsed)
        world.resize(80, 30)
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
