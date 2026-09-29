"""Check work performed under load, without machine-dependent time limits."""
import unittest

from terminal_smash.capture import Cell
from terminal_smash.model import World


class ObservedCells(list):
    def __init__(self, cells):
        super().__init__(cells)
        self.traversals = 0

    def __iter__(self):
        self.traversals += 1
        return super().__iter__()


class FallingWorkTests(unittest.TestCase):
    def detached_bridge(self):
        world = World([Cell(x, 5, '=') for x in range(300)] + [Cell(150, 6, '|')],
                      300, 100)
        world.destroy(150, 6, 0.4, 0.4)
        self.assertEqual(len(world.falling), 1)
        chunk = world.falling[0]
        chunk.cells = ObservedCells(chunk.cells)
        return world, chunk

    def test_subcell_fall_does_not_scan_entire_bridge_for_collisions(self):
        world, chunk = self.detached_bridge()
        for _ in range(5):
            world.update(1 / 120)
        self.assertGreater(chunk.offset_y, 0)
        self.assertLess(chunk.offset_y, 1)
        self.assertEqual(chunk.cells.traversals, 0)
        self.assertEqual(world.destroyed, 1)

    def test_attacks_outside_chunk_bounds_do_not_scan_flying_letters(self):
        world, chunk = self.detached_bridge()
        for x in range(0, 300, 10):
            self.assertEqual(world.destroy(x, 50, 4, 2), 0)
        self.assertEqual(chunk.cells.traversals, 0)
        self.assertEqual(len(chunk.cells), 300)
        self.assertEqual(world.destroyed, 1)

    def test_wide_chunk_bounds_refresh_after_partial_destruction(self):
        cells = [Cell(x, 5, '界', width=2) for x in (10, 12, 14)]
        world = World(cells + [Cell(12, 6, '|')], 50, 25)
        world.destroy(12, 6, 0.4, 0.4)
        chunk = world.falling[0]
        self.assertEqual(world.destroy(10.5, 5, 0.4, 0.4), 1)
        self.assertEqual((chunk.left, chunk.right), (12, 16))
        self.assertEqual(chunk.centre_x, 13.5)
        self.assertEqual(world.destroy(14.5, 5, 0.4, 0.4), 1)
        self.assertEqual((chunk.left, chunk.right), (12, 14))
        self.assertEqual(world.destroy(12.5, 5, 0.4, 0.4), 1)
        self.assertFalse(world.falling)
        self.assertEqual(world.destroyed, world.total)

    def test_fast_fall_sweeps_intervening_rows_and_wide_glyph_columns(self):
        cells = [Cell(10, 5, '界', width=2), Cell(10, 6, '|'), Cell(11, 8, '#')]
        world = World(cells, 50, 25)
        world.destroy(10, 6, 0.4, 0.4)
        chunk = world.falling[0]
        chunk.vy = 42
        world._step_falling(0.12)
        self.assertFalse(world.falling)
        self.assertFalse(world.cells)
        self.assertEqual(world.destroyed, world.total)


if __name__ == '__main__':
    unittest.main()
