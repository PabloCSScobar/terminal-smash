"""Check work performed under load, without machine-dependent time limits."""
import unittest
from unittest.mock import patch

from terminal_smash.capture import Cell
from terminal_smash.model import Particle, World


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
                      300, 100, falling_enabled=True)
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
        world = World(cells + [Cell(12, 6, '|')], 50, 25, falling_enabled=True)
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
        world = World(cells, 50, 25, falling_enabled=True)
        world.destroy(10, 6, 0.4, 0.4)
        chunk = world.falling[0]
        chunk.vy = 42
        world._step_falling(0.12)
        self.assertFalse(world.falling)
        self.assertEqual(world.cells[(11, 8)].char, '#')
        self.assertEqual(world.cells[(10, 7)].char, '界')
        self.assertEqual(world.cells[(10, 7)].width, 2)
        self.assertEqual(world.destroyed, 1)
        self.assertEqual(world.score, 10)
        self.assertEqual(world.occupied[(11, 7)], (10, 7))
        self.assertFalse(world.waves)


    def test_stacked_fragments_cannot_pass_through_or_destroy_each_other(self):
        cells = [Cell(x, 3, 'A') for x in range(5, 10)]
        cells += [Cell(x, 4, 'B') for x in range(5, 10)]
        cells += [Cell(7, 5, '|')]
        world = World(cells, 30, 14, falling_enabled=True)
        world.destroy(7, 5, 0.4, 0.4)
        self.assertEqual(len(world.falling), 2)
        for chunk in world.falling:
            chunk.vy = 42 if chunk.row == 3 else 1
        world.particles.clear()
        score = world.score
        with patch.object(world, '_debris') as debris, \
                patch.object(world, '_sparks') as sparks, \
                patch.object(world, '_wave') as wave, \
                patch.object(world, '_destroy_static') as damage:
            for _ in range(180):
                world._step_falling(1 / 90)
                remaining = len(world.cells) + sum(len(c.cells) for c in world.falling)
                self.assertEqual(world.destroyed + remaining, world.total)
            debris.assert_not_called()
            sparks.assert_not_called()
            wave.assert_not_called()
            damage.assert_not_called()
        self.assertFalse(world.falling)
        self.assertEqual(world.destroyed, 1)
        self.assertEqual(world.score, score)
        self.assertEqual({(x, 11): 'A' for x in range(5, 10)} |
                         {(x, 12): 'B' for x in range(5, 10)},
                         {key: cell.char for key, cell in world.cells.items()})
        self.assertEqual({(x, row): cell for row, cells in enumerate(world.cells_by_row)
                          for x, cell in cells.items()}, world.cells)
        self.assertEqual(world.occupied, {key: key for key in world.cells})
        self.assertGreater(world.terrain_row_revisions[11], 0)
        self.assertGreater(world.terrain_row_revisions[12], 0)

    def test_landed_text_is_solid_and_can_be_attacked_again(self):
        world = World([Cell(10, 4, '界', width=2), Cell(10, 5, '|')],
                      30, 14, falling_enabled=True)
        world.destroy(10, 5, 0.4, 0.4)
        for _ in range(120):
            world.update(1 / 90)
        self.assertFalse(world.falling)
        self.assertEqual(world.destroyed, 1)
        self.assertEqual(world.cells[(10, 12)].char, '界')
        world.player.x, world.player.y = 11, 5
        world.player.vx = world.player.vy = 0
        for _ in range(120):
            world.update(1 / 90)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, 11)
        self.assertEqual(world.destroy(10.5, 12, 0.4, 0.4), 1)
        self.assertTrue(world.cleared)
        self.assertEqual(world.destroyed, world.total)

    def test_large_direct_hit_bounds_particle_allocations_before_truncation(self):
        cells = [Cell(x, y, '#') for x in range(100) for y in range(50)]
        world = World(cells, 100, 55)
        with patch('terminal_smash.model.Particle', wraps=Particle) as particle:
            world.destroy(50, 25, 100, 55)
        self.assertEqual(world.destroyed, 5000)
        self.assertTrue(world.cleared)
        self.assertLessEqual(particle.call_count, 828)
        self.assertLessEqual(len(world.particles), 800)


if __name__ == '__main__':
    unittest.main()
