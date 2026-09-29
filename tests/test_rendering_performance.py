"""Rendering work regressions without machine-dependent timing thresholds."""
import curses
import unittest
from unittest.mock import patch

from terminal_smash.capture import Style
from terminal_smash.model import Particle, World
from terminal_smash.ui import Palette, TerrainLayer, _draw
from tests.test_rendering import Canvas


class RenderingWorkTests(unittest.TestCase):
    def test_equal_styles_reuse_attributes_without_dataclass_hashing(self):
        with patch('terminal_smash.ui.curses.has_colors', return_value=False):
            palette = Palette()
        with patch.object(palette, '_attribute', return_value=0) as resolve:
            with patch.object(Style, '__hash__', side_effect=AssertionError('per-letter hash')):
                for _ in range(800):
                    self.assertEqual(palette.attr(Style(fg=82)), 0)
            resolve.assert_called_once()
        self.assertEqual(palette.attr(Style(fg=82, bold=True)), curses.A_BOLD)

    def test_invisible_particles_do_not_resolve_colors_or_write_clipped_wide_glyphs(self):
        class RecordingPalette:
            def __init__(self):
                self.styles = []

            def attr(self, style):
                self.styles.append(style)
                return 0

        screen = Canvas(24, 80)
        world = World([], 80, 20)
        world.particles = [
            Particle('A', Style(fg=137), -1, 10, 0, 0, 1),
            Particle('B', Style(fg=138), 2, -1, 0, 0, 1),
            Particle('C', Style(fg=139), 2, 20, 0, 0, 1),
            Particle('界', Style(fg=140), 79, 10, 0, 0, 1, width=2),
            Particle('界', Style(fg=141), 77, 10, 0, 0, 1, width=2),
        ]
        palette = RecordingPalette()
        with patch('terminal_smash.ui.curses.newwin', side_effect=Canvas):
            with patch('terminal_smash.ui.curses.doupdate'):
                _draw(screen, world, palette, '', False, TerrainLayer())
        colors = [style.fg for style in palette.styles]
        self.assertTrue(all(color not in colors for color in (137, 138, 139, 140)))
        self.assertEqual(colors.count(141), 1)
        self.assertEqual(screen.grid[12][77:79], ['界', '~'])
        self.assertEqual(len(world.particles), 5)


if __name__ == '__main__':
    unittest.main()
