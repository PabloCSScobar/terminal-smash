"""Visible scrollback, camera clipping and Tower overlays on real-sized grids."""

import curses
import unittest
from unittest.mock import patch

from terminal_smash.demo import build_tower_demo
from terminal_smash.tower import Platform, TowerWorld
from terminal_smash.ui import _draw, _new_world
from tests.test_rendering import Canvas, PlainPalette


class TowerRenderingTests(unittest.TestCase):
    def draw(self, screen, world, *, help_open=False):
        with patch('terminal_smash.ui.curses.doupdate'):
            _draw(screen, world, PlainPalette(), 'history', help_open)

    def test_camera_offsets_history_and_erases_old_sprites_and_help(self):
        world = TowerWorld('\n'.join(f'{row:04d} session output' for row in range(220)), 80, 19)
        screen = Canvas(24, 80)
        for offset in (120, 80, world.HISTORY_TOP):
            world.camera_y = offset
            world.player.y = offset + 12
            self.draw(screen, world)
            self.assertEqual(''.join(screen.grid[2][:4]), f'{offset - world.HISTORY_TOP:04d}')
            self.draw(screen, world, help_open=True)
            self.assertIn('HELP', '\n'.join(''.join(row) for row in screen.grid))
            self.draw(screen, world)
            self.assertNotIn('HELP', '\n'.join(''.join(row) for row in screen.grid))

    def test_emergency_bridge_preserves_every_overlapping_wide_character(self):
        world = TowerWorld(('界' * 20 + '\n') * 50, 44, 9)
        world.camera_y = world.HISTORY_TOP
        bridge = Platform(world.HISTORY_TOP + 1, 3, 11, True)
        screen = Canvas(14, 44)
        with patch.object(world, 'visible_platforms', return_value=[bridge]):
            self.draw(screen, world)
        row = screen.grid[3]
        self.assertEqual(row[:40], ['界', '~'] * 20)
        self.assertNotIn('[', row)

    def test_hud_uses_climbed_rows_and_results_fit_small_and_large_terminals(self):
        for rows, cols in ((14, 44), (24, 80), (60, 200)):
            with self.subTest(rows=rows, cols=cols):
                world = TowerWorld('output\n' * 100, cols, rows - 5)
                world.best_y = world.start_row - 1 - 32
                world.elapsed = 75.2
                screen = Canvas(rows, cols)
                self.draw(screen, world)
                self.assertIn('CLIMBED 32/', ''.join(screen.grid[1]))
                self.assertIn('TIME 01:15.2', ''.join(screen.grid[0]))
                self.assertNotIn('HP', ''.join(screen.grid[0]))
                for reason, title in (('summit', 'SUMMIT REACHED!'),
                                      ('fallen', 'YOU FELL BELOW THE SCREEN!')):
                    world.finished, world.finish_reason = True, reason
                    self.draw(screen, world)
                    text = '\n'.join(''.join(row) for row in screen.grid)
                    self.assertIn(title, text)
                    self.assertIn('[R] retry', text)

    def test_safe_floor_stays_at_tower_base_and_scrolls_out_of_view(self):
        for rows, cols in ((14, 44), (24, 80), (60, 200)):
            with self.subTest(rows=rows, cols=cols):
                world = TowerWorld('output\n' * 140, cols, rows - 5, seed=3)
                screen = Canvas(rows, cols)
                floor_row = world.floor_row
                initial_camera = world.camera_y
                for camera in (initial_camera, initial_camera - 4, 0):
                    world.camera_y = camera
                    world.player.x = cols // 2
                    world.player.y = floor_row - 1
                    world.player.grounded = True
                    self.draw(screen, world)
                    self.assertEqual(world.floor_row, floor_row)
                    floor_screen_row = floor_row - camera + 2
                    scene = '\n'.join(''.join(row) for row in screen.grid[2:-3])
                    if 2 <= floor_screen_row < rows - 3:
                        self.assertEqual(''.join(screen.grid[floor_screen_row]),
                                         ' SAFE FLOOR '.center(cols, '='))
                        self.assertEqual(screen.grid[floor_screen_row - 1][cols // 2], '|')
                    else:
                        self.assertNotIn('SAFE FLOOR', scene)
                    self.assertEqual(''.join(screen.grid[rows - 3]), '-' * cols)
                    self.assertNotIn('YOU FELL', scene)
                    self.assertNotIn('SUMMIT REACHED', scene)

    def test_world_factory_keeps_history_for_tower_and_crops_free_play(self):
        text = 'oldest\n' + 'session\n' * 200 + 'newest'
        tower = _new_world(text, 24, 80, False, tower=True)
        free = _new_world(text, 24, 80, False)
        self.assertGreater(tower.height, 200)
        self.assertEqual(free.height, 20)
        tower.camera_y = 0
        self.assertIn('oldest', ''.join(cell.char for cell in tower.visible_cells()))
        self.assertNotIn('oldest', ''.join(cell.char for cell in free.original))

    def test_demo_is_a_finite_history_with_a_visible_summit(self):
        for width in (44, 80, 160):
            with self.subTest(width=width):
                world = TowerWorld(build_tower_demo(width), width, 19, seed=7)
                self.assertGreater(world.total_climb, 100)
                self.assertEqual(world.score, 0)
                world.camera_y = 0
                screen = Canvas(24, width)
                self.draw(screen, world)
                self.assertIn('SUMMIT', ''.join(screen.grid[1]))
                self.assertFalse(world.platforms[0].synthetic)
                self.assertFalse(world.platforms[-1].synthetic)
                scene = '\n'.join(''.join(row) for row in screen.grid[2:-3])
                self.assertIn('session started', scene)

    def test_dense_output_glyphs_are_unchanged_by_highlighting_including_summit(self):
        text = ('result: 界界 build completed successfully\n') * 70
        world = TowerWorld(text, 80, 19, seed=17)
        self.assertTrue(all(not platform.synthetic for platform in world.platforms))
        screen = Canvas(24, 80)
        # Hide the actor so the entire scene can be compared with the source.
        world.player.y = -100
        for camera in (0, 25, 50):
            world.camera_y = camera
            expected = Canvas(24, 80)
            for cell in world.visible_cells():
                expected.addstr(cell.y - camera + 2, cell.x, cell.char)
            self.draw(screen, world)
            self.assertEqual(screen.grid[2:-3], expected.grid[2:-3])

    def test_natural_phrase_platform_underlines_spaces_without_changing_text(self):
        world = TowerWorld(('first second third\n') * 70, 80, 19, seed=3)
        platform = world.platforms[-1]
        world.camera_y = world.start_row - 4
        world.player.y = -100
        screen = Canvas(24, 80)
        row = platform.row - world.camera_y + 2
        with patch.object(screen, 'addstr', wraps=screen.addstr) as writes:
            self.draw(screen, world)
        self.assertEqual(''.join(screen.grid[row][:18]), 'first second third')
        for column in (5, 12):
            self.assertTrue(any(call.args[:3] == (row, column, ' ')
                                and call.args[3] & curses.A_UNDERLINE
                                for call in writes.call_args_list))

    def test_empty_history_shows_message_without_fabricated_platforms(self):
        for text in ('', '\n' * 100, '   \t\n' * 30):
            world = TowerWorld(text, 80, 19)
            screen = Canvas(24, 80)
            self.draw(screen, world)
            rendered = '\n'.join(''.join(row) for row in screen.grid)
            self.assertIn('No visible output to climb.', rendered)
            self.assertNotIn('[===', rendered)
            self.assertFalse(world.platforms)

    def test_horizontal_gap_connector_and_real_text_on_same_row_both_render(self):
        world = TowerWorld(' ' * 70 + 'TOP\nBOTTOM', 80, 19, seed=5)
        world.camera_y = 0
        world.player.y = -100
        self.assertTrue(any(platform.synthetic for platform in world.platforms))
        screen = Canvas(24, 80)
        self.draw(screen, world)
        row = ''.join(screen.grid[world.start_row + 2])
        self.assertTrue(row.startswith('BOTTOM'))
        self.assertIn('=', row[6:70])
        self.assertEqual(''.join(screen.grid[world.summit_row + 2][70:73]), 'TOP')

    def test_clipped_route_explains_that_the_attempt_is_paused(self):
        world = TowerWorld('old output\n' + '\n' * 15 + ' ' * 60 + 'new output',
                           80, 19, seed=5)
        world.resize(44, 19)
        self.assertTrue(world.resize_blocked)
        screen = Canvas(24, 44)
        self.draw(screen, world)
        rendered = '\n'.join(''.join(row) for row in screen.grid)
        self.assertIn('Route clipped. Widen the terminal.', rendered)
        self.assertIn('Your climb and timer are paused.', rendered)


if __name__ == '__main__':
    unittest.main()
