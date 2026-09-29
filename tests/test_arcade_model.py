import unittest

from terminal_smash.capture import Cell, Style, parse_capture
from terminal_smash.model import World


def advance(world, seconds, fps=90):
    for _ in range(round(seconds * fps)):
        world.update(1 / fps)


class ArcadePhysicsTests(unittest.TestCase):
    def assert_conserved(self, world):
        remaining = (len(world.cells) + sum(len(c.cells) for c in world.falling)
                     + sum(len(e.cells) for e in world.enemies))
        self.assertEqual(world.destroyed + remaining, world.total)
        self.assertLessEqual(len(world.falling), 80)
        self.assertLessEqual(len(world.particles), 800)
        self.assertLessEqual(len(world.trails), 32)
        self.assertLessEqual(len(world.enemies), 8)
        for cell in world.cells.values():
            for dx in range(cell.width):
                self.assertEqual(world.occupied[(cell.x + dx, cell.y)], (cell.x, cell.y))
        self.assertEqual(len(world.occupied), sum(c.width for c in world.cells.values()))

    def test_dash_sweeps_path_without_skipping_letters_and_keeps_momentum(self):
        cells = [Cell(x, 9, '#') for x in (8, 12, 16, 20, 24, 50)]
        world = World(cells, 100, 30, seed=1)
        world.player.x, world.player.y = 5, 10
        self.assertTrue(world.dash())
        self.assertFalse(world.dash())
        world.update(0.12)
        world.update(0.08)
        self.assertGreater(world.player.x, 25)
        self.assertEqual(world.destroyed, 5)
        self.assertIn((50, 9), world.cells)
        self.assertTrue(world.trails)
        self.assertLessEqual(abs(world.player.vx), 40)
        self.assert_conserved(world)
        advance(world, 0.6)
        self.assertTrue(world.dash())

    def test_dash_left_and_return_to_top_cancel_mobility(self):
        world = World([], 80, 30)
        world.player.x, world.player.y = 40, 15
        world.move(-1)
        world.dash()
        world.update(0.1)
        self.assertLess(world.player.x, 30)
        world.return_to_top()
        self.assertEqual(world.dash_until, 0)
        self.assertFalse(world.slamming)
        self.assertEqual((world.player.vx, world.player.vy), (0, 0))
        self.assertEqual(world.player.y, 2)

    def test_slam_hits_first_platform_even_with_drop_active(self):
        world = World([Cell(x, 15, '=') for x in range(5, 40)], 60, 30, seed=2)
        world.player.x, world.player.y = 20, 5
        world.drop()
        self.assertTrue(world.slam())
        self.assertFalse(world.slam())
        world.update(0.12)
        world.update(0.12)
        self.assertFalse(world.slamming)
        self.assertGreater(world.destroyed, 0)
        self.assertTrue(world.waves)
        self.assertGreater(world.landing_until, world.time)
        self.assertLess(world.player.y, 20)
        self.assert_conserved(world)

    def test_slam_requires_air_and_can_land_on_empty_floor(self):
        world = World([], 50, 24)
        world.player.y = 22
        world.player.grounded = True
        self.assertFalse(world.slam())
        world.jump()
        advance(world, 0.15)
        self.assertTrue(world.slam())
        advance(world, 0.2)
        self.assertFalse(world.slamming)
        self.assertEqual(world.player.y, 22)
        self.assertTrue(world.player.grounded)

    def test_undamaged_floating_text_remains_static(self):
        world = World([Cell(x, 5, '#') for x in range(10, 30)], 60, 30)
        before = dict(world.cells)
        advance(world, 3)
        self.assertEqual(world.cells, before)
        self.assertFalse(world.falling)
        self.assertEqual(world.destroyed, 0)

    def test_lost_support_collapses_bridge_and_cascades_into_lower_text(self):
        bridge = [Cell(x, 5, '=') for x in range(6, 16)]
        lower = [Cell(x, 12, '#') for x in range(5, 19)]
        world = World(bridge + lower + [Cell(10, 6, '|'), Cell(40, 4, '!')], 60, 25, seed=3)
        revision = world.terrain_revision
        self.assertEqual(world.destroy(10, 6, 0.4, 0.4), 1)
        self.assertTrue(world.falling)
        self.assertEqual(sum(len(c.cells) for c in world.falling), 10)
        self.assertGreater(world.terrain_revision, revision)
        self.assertIn((40, 4), world.cells)
        for _ in range(180):
            world.update(1 / 90)
            self.assert_conserved(world)
        self.assertEqual(world.destroyed, 25)
        self.assertEqual(set(world.cells), {(40, 4)})
        self.assertFalse(world.falling)
        self.assertGreater(world.combo, 1)

    def test_bridge_waits_until_last_support_is_destroyed(self):
        cells = [Cell(x, 5, '=') for x in range(5, 16)] + [Cell(5, 6, '|'), Cell(15, 6, '|')]
        world = World(cells, 50, 25)
        world.destroy(5, 6, 0.4, 0.4)
        self.assertFalse(world.falling)
        self.assertEqual(len(world.cells), 12)
        world.destroy(15, 6, 0.4, 0.4)
        self.assertEqual(sum(len(c.cells) for c in world.falling), 11)
        self.assertFalse(world.cells)
        self.assertFalse(world.cleared)
        self.assert_conserved(world)

    def test_flying_wide_glyphs_remain_atomic_and_keep_style(self):
        style = Style(fg=201, bold=True)
        world = World([Cell(10, 5, '界', style, 2), Cell(10, 6, '|')], 50, 25)
        world.destroy(10, 6, 0.4, 0.4)
        self.assertEqual(world.destroyed, 1)
        world.update(0.1)
        chunk = world.falling[0]
        self.assertEqual(world.destroy(10.5, 5 + chunk.offset_y, 1, 1), 1)
        debris = next(p for p in world.particles if p.char == '界')
        self.assertEqual(debris.width, 2)
        self.assertEqual(debris.style, style)
        self.assertFalse(world.falling)
        self.assertTrue(world.cleared)
        self.assert_conserved(world)

    def test_combo_rewards_successive_hits_and_expires(self):
        world = World([Cell(x, 0, '#') for x in (10, 30, 50, 70)], 100, 25)
        for x in (10, 30, 50):
            self.assertEqual(world.destroy(x, 0, 1, 1), 1)
        self.assertEqual((world.combo, world.multiplier, world.score), (3, 2, 40))
        score = world.score
        world.destroy(10, 0, 1, 1)
        self.assertEqual(world.score, score)
        self.assertEqual(world.combo, 3)
        advance(world, 1.8)
        self.assertEqual((world.combo, world.multiplier), (0, 1))
        world.destroy(70, 0, 1, 1)
        self.assertEqual(world.score, 50)
        self.assert_conserved(world)

    def test_combo_strength_extends_punch_reach(self):
        # A target just outside ordinary reach is caught by a high-combo punch.
        for multiplier, expected in ((1, 0), (5, 1)):
            world = World([Cell(18, 9, '#')], 50, 25)
            world.player.x, world.player.y = 10, 10
            world.multiplier = multiplier
            self.assertEqual(world.punch(), expected)

    def test_whole_error_words_awaken_and_account_for_original_letters(self):
        text = 'ERROR error XERROR ERRORS _ERROR ERROR_ [ERROR]'
        cells = parse_capture(text, 100, 20)
        world = World(cells, 100, 20)
        self.assertEqual(len(world.enemies), 3)
        self.assertEqual(world.destroyed, 0)
        self.assertEqual(len(world.cells), len(cells) - 15)
        self.assert_conserved(world)
        enemy = world.enemies[0]
        self.assertEqual(world.destroy(enemy.x, enemy.y - 1, 2.1, 1), 0)
        self.assertEqual(enemy.hp, 1)
        advance(world, 0.12)
        self.assertEqual(world.destroy(enemy.x, enemy.y - 1, 2.1, 1), 5)
        self.assertEqual(len(world.enemies), 2)
        self.assertGreater(world.score, 50)
        self.assert_conserved(world)

    def test_error_chases_and_contact_breaks_combo_once_during_invulnerability(self):
        world = World(parse_capture('ERROR', 60, 25), 60, 25)
        enemy = world.enemies[0]
        enemy.x, enemy.y = 45, 22
        world.player.x, world.player.y = 20, 22
        world.player.grounded = True
        advance(world, 1.2)
        self.assertLess(enemy.x, 45)
        enemy.x, enemy.y = world.player.x, world.player.y
        world.combo, world.multiplier, world.combo_until = 5, 2, world.time + 1
        world.update(1 / 90)
        self.assertEqual((world.combo, world.multiplier), (0, 1))
        self.assertGreater(world.hurt_until, world.time)
        self.assertLess(world.player.vy, 0)
        invulnerable_until = world.hurt_until
        enemy.x, enemy.y = world.player.x, world.player.y
        world.update(1 / 90)
        self.assertEqual(world.hurt_until, invulnerable_until)

    def test_error_count_is_bounded_and_extra_words_remain_terrain(self):
        cells = parse_capture(' '.join(['ERROR'] * 12), 100, 25)
        world = World(cells, 100, 25)
        self.assertEqual(len(world.enemies), 8)
        self.assertEqual(len(world.cells), 20)
        self.assert_conserved(world)

    def test_challenge_counts_full_stall_time_and_freezes_actions_at_expiry(self):
        world = World([Cell(30, 4, '#')], 100, 30, duration=30)
        world.update(29.5)
        self.assertAlmostEqual(world.time_left, 0.5)
        self.assertAlmostEqual(world.time, 0.12)
        self.assertFalse(world.finished)
        world.update(10)
        self.assertTrue(world.finished)
        self.assertEqual(world.finish_reason, 'time')
        self.assertEqual(world.time_left, 0)
        self.assertEqual(world.round_elapsed, 30)
        position = (world.player.x, world.player.y)
        terrain = dict(world.cells)
        world.move(1)
        world.jump()
        world.drop()
        world.return_to_top()
        self.assertFalse(world.dash())
        self.assertFalse(world.slam())
        self.assertEqual(world.punch(), 0)
        self.assertEqual(world.blast(), 0)
        self.assertEqual(world.destroy(30, 4, 10, 10), 0)
        world.update(10)
        self.assertEqual((world.player.x, world.player.y), position)
        self.assertEqual(world.cells, terrain)
        self.assertEqual(world.score, 0)

    def test_clear_waits_for_falling_and_enemies_then_freezes_only_challenge(self):
        for duration in (None, 30):
            world = World([Cell(10, 5, '='), Cell(10, 6, '|')], 50, 25, duration=duration)
            world.destroy(10, 6, 0.4, 0.4)
            self.assertFalse(world.cleared)
            self.assertFalse(world.finished)
            advance(world, 2)
            self.assertTrue(world.cleared)
            self.assertEqual(world.finished, duration is not None)
            self.assertEqual(world.destroyed, 2)
            if duration is not None:
                self.assertEqual(world.finish_reason, 'cleared')
        world = World(parse_capture('ERROR', 50, 25), 50, 25, duration=30)
        self.assertFalse(world.cleared)
        self.assertFalse(world.finished)
        enemy = world.enemies[0]
        world.destroy(enemy.x, enemy.y - 1, 3, 2, enemy_damage=2)
        self.assertTrue(world.finished)
        self.assertEqual(world.finish_reason, 'cleared')

    def test_reset_clears_arcade_state_and_preserves_challenge_and_snapshot(self):
        cells = parse_capture('ERROR\n==========\n   |', 60, 25)
        world = World(cells, 60, 25, duration=30)
        world.player.y = 12
        world.dash()
        world.update(0.1)
        world.slam()
        world.blast()
        world.update(40)
        revision = world.terrain_revision
        world.reset()
        self.assertEqual(world.duration, 30)
        self.assertEqual(world.time_left, 30)
        self.assertEqual(world.round_elapsed, 0)
        self.assertEqual(world.score, 0)
        self.assertEqual(world.combo, 0)
        self.assertEqual(world.multiplier, 1)
        self.assertFalse(world.finished)
        self.assertFalse(world.slamming)
        self.assertEqual(world.dash_until, 0)
        self.assertFalse(world.trails)
        self.assertFalse(world.falling)
        self.assertFalse(world.particles)
        self.assertEqual(len(world.enemies), 1)
        self.assertGreater(world.terrain_revision, revision)
        self.assertEqual(world.original, tuple(cells))
        self.assert_conserved(world)

    def test_zero_length_round_and_empty_snapshot_are_well_defined(self):
        immediate = World([], 50, 25, duration=0)
        self.assertTrue(immediate.finished)
        self.assertEqual(immediate.finish_reason, 'time')
        world = World([], 50, 25, duration=30)
        world.update(5)
        self.assertFalse(world.cleared)
        self.assertFalse(world.finished)
        self.assertEqual(world.time_left, 25)

    def test_dense_cascades_bound_effects_and_conserve_every_character(self):
        cells = [Cell(x, y, '#') for y in range(1, 48) for x in range(160)]
        world = World(cells, 160, 52, seed=13)
        world.destroy(80, 45, 55, 5)
        for frame in range(120):
            if frame % 9 == 0:
                world.destroy((frame * 13) % 160, (frame * 7) % 47, 9, 4)
            world.update(1 / 60)
            self.assert_conserved(world)
        self.assertGreater(world.destroyed, 0)
        self.assertLessEqual(len(world.waves), 24)

    def test_pending_falling_chunks_are_included_in_capacity(self):
        # Many existing fragments fall while impact disconnects more platforms.
        positions = range(2, 320, 4)
        cells = [Cell(x, 4, '=') for x in positions]
        cells += [Cell(x, 5, '|') for x in positions]
        cells += [Cell(x + dx, 15, '*') for x in positions for dx in (-1, 0, 1)]
        world = World(cells, 320, 30)
        world.destroy(160, 5, 200, 0.4)
        self.assertEqual(len(world.falling), 80)
        for chunk in world.falling:
            chunk.vy = 42
        for _ in range(90):
            world.update(1 / 90)
            self.assert_conserved(world)


if __name__ == '__main__':
    unittest.main()
