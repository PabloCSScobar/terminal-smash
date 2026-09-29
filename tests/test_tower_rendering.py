"""Visible scrollback, camera clipping and Tower overlays on real-sized grids."""

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
        for offset in (120, 80, 10):
            world.camera_y = offset
            world.player.y = offset + 12
            self.draw(screen, world)
            self.assertEqual(''.join(screen.grid[2][:4]), f'{offset - world.HISTORY_TOP:04d}')
            self.draw(screen, world, help_open=True)
            self.assertIn('HELP', '\n'.join(''.join(row) for row in screen.grid))
            self.draw(screen, world)
            self.assertNotIn('HELP', '\n'.join(''.join(row) for row in screen.grid))

    def test_bridge_never_leaves_half_of_an_overlapping_wide_character(self):
        world = TowerWorld(('界' * 20 + '\n') * 50, 44, 9)
        world.camera_y = world.HISTORY_TOP
        bridge = Platform(world.HISTORY_TOP + 1, 3, 11, True)
        screen = Canvas(14, 44)
        with patch.object(world, 'visible_platforms', return_value=[bridge]):
            self.draw(screen, world)
        row = screen.grid[3]
        self.assertEqual(row[:3], ['界', '~', ' '])
        self.assertEqual(''.join(row[3:11]), '[======]')
        self.assertEqual(row[11:14], [' ', '界', '~'])

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
                world = TowerWorld(build_tower_demo(width), width, 19)
                self.assertGreater(world.total_climb, 100)
                self.assertEqual(world.score, 0)
                world.camera_y = 0
                screen = Canvas(24, width)
                self.draw(screen, world)
                self.assertIn('SUMMIT', '\n'.join(''.join(row) for row in screen.grid[2:-3]))


if __name__ == '__main__':
    unittest.main()
