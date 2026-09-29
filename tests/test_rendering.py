"""Regression tests for restoring text beneath moving sprites and overlays."""

import unittest
import unicodedata
from unittest.mock import patch

from terminal_smash.capture import Cell
from terminal_smash.model import World
from terminal_smash.ui import TerrainLayer


class Canvas:
    """Small terminal grid to check the visible result of a background copy."""

    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.erase()

    def getmaxyx(self):
        return self.rows, self.cols

    def erase(self):
        self.grid = [[' ' for _ in range(self.cols)] for _ in range(self.rows)]

    def addstr(self, y, x, text, attr=0):
        for character in text:
            self.grid[y][x] = character
            width = 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
            if width == 2:
                self.grid[y][x + 1] = '~'  # The occupied trailing column.
            x += width

    def overwrite(self, destination):
        destination.grid = [row[:] for row in self.grid]


class PlainPalette:
    def attr(self, style):
        return 0


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch('terminal_smash.ui.curses.newwin', side_effect=Canvas)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.palette = PlainPalette()
        self.layer = TerrainLayer()
        self.screen = Canvas(24, 80)
        self.world = World([Cell(5, 3, 'X'), Cell(10, 5, '界', width=2)], 80, 20)

    def draw(self):
        self.layer.blit(self.screen, self.world, self.palette)

    def test_moving_actor_and_help_leave_no_trails(self):
        self.draw()
        self.screen.addstr(5, 5, 'O')
        self.screen.addstr(10, 8, 'HELP WINDOW AND FLYING DEBRIS')
        self.draw()
        self.assertEqual(self.screen.grid[5][5], 'X')
        self.assertEqual(''.join(self.screen.grid[10]).strip(), '')
        self.assertEqual(self.screen.grid[7][10:12], ['界', '~'])

    def test_destroy_then_reset_restores_the_entire_wide_character(self):
        self.draw()
        self.world.destroy(10, 5, 2, 2)
        self.draw()
        self.assertEqual(self.screen.grid[7][10:12], [' ', ' '])
        self.world.reset()
        self.draw()
        self.assertEqual(self.screen.grid[7][10:12], ['界', '~'])

    def test_new_scene_and_window_dimensions_replace_old_text(self):
        self.draw()
        self.world = World([Cell(20, 4, 'N')], 80, 20)
        self.draw()
        self.assertEqual(self.screen.grid[5][5], ' ')
        self.assertEqual(self.screen.grid[6][20], 'N')
        self.screen = Canvas(16, 45)
        self.draw()
        self.assertEqual(len(self.screen.grid), 16)
        self.assertEqual(len(self.screen.grid[0]), 45)
        self.assertEqual(self.screen.grid[6][20], 'N')


if __name__ == '__main__':
    unittest.main()
