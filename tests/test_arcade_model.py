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
        indexed = {(x, row): cell for row, cells in enumerate(world.cells_by_row)
                   for x, cell in cells.items()}
        self.assertEqual(indexed, world.cells)

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
        self.assertLessEqual(world.destroyed, 17)
        self.assertIn((10, 15), world.cells)
        self.assertIn((30, 15), world.cells)
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

    def test_gravity_off_keeps_unsupported_letters_solid_and_animated_debris(self):
        bridge = [Cell(x, 8, '=') for x in range(6, 16)]
        world = World(bridge + [Cell(10, 9, '|')], 50, 25, falling_enabled=False)
        self.assertEqual(world.destroy(10, 9, 0.4, 0.4), 1)
        self.assertTrue(world.particles)
        self.assertFalse(world.falling)
        self.assertEqual(set(world.cells), {(cell.x, cell.y) for cell in bridge})
        world.player.x, world.player.y = 10, 2
        advance(world, 1)
        self.assertTrue(world.player.grounded)
        self.assertEqual(world.player.y, 7)
        self.assertEqual(world.destroyed, 1)
        self.assert_conserved(world)

    def test_gravity_choice_survives_reset_and_defaults_to_off(self):
        cells = [Cell(10, 5, '='), Cell(10, 6, '|')]
        self.assertFalse(World(cells, 50, 25).falling_enabled)
        for enabled in (False, True):
            world = World(cells, 50, 25, falling_enabled=enabled, duration=30)
            world.destroy(10, 6, 0.4, 0.4)
            self.assertEqual(bool(world.falling), enabled)
            world.reset()
            self.assertEqual(world.falling_enabled, enabled)
            self.assertEqual(world.duration, 30)
            self.assertEqual(world.destroyed, 0)
            self.assertFalse(world.falling)
            world.destroy(10, 6, 0.4, 0.4)
            self.assertEqual(bool(world.falling), enabled)
            self.assert_conserved(world)

    def test_row_index_revisions_track_wide_cells_enemies_and_detachment(self):
        cells = [Cell(10, 5, '界', width=2), Cell(10, 6, '|'), Cell(20, 9, '#')]
        cells += [Cell(i, 2, char) for i, char in enumerate('ERROR')]
        world = World(cells, 50, 25, falling_enabled=True)
        self.assertFalse(world.cells_by_row[2])
        before = world.terrain_row_revisions[:]
        world.destroy(10, 6, 0.4, 0.4)
        self.assertGreater(world.terrain_row_revisions[5], before[5])
        self.assertGreater(world.terrain_row_revisions[6], before[6])
        self.assertEqual(world.terrain_row_revisions[9], before[9])
        self.assertFalse(world.cells_by_row[5])
        self.assertFalse(world.cells_by_row[6])
        self.assert_conserved(world)
        world.reset()
        self.assert_conserved(world)

    def test_undamaged_floating_text_remains_static(self):
        world = World([Cell(x, 5, '#') for x in range(10, 30)], 60, 30)
        before = dict(world.cells)
        advance(world, 3)
        self.assertEqual(world.cells, before)
        self.assertFalse(world.falling)
        self.assertEqual(world.destroyed, 0)

    def test_falling_bridge_settles_quietly_without_damaging_lower_text(self):
        bridge = [Cell(x, 5, '=') for x in range(6, 16)]
        lower = [Cell(x, 12, '#') for x in range(5, 19)]
        world = World(bridge + lower + [Cell(10, 6, '|'), Cell(40, 4, '!')], 60, 25, seed=3, falling_enabled=True)
        revision = world.terrain_revision
        self.assertEqual(world.destroy(10, 6, 0.4, 0.4), 1)
        self.assertTrue(world.falling)
        self.assertEqual(sum(len(c.cells) for c in world.falling), 10)
        self.assertGreater(world.terrain_revision, revision)
        self.assertIn((40, 4), world.cells)
        for _ in range(180):
            world.update(1 / 90)
            self.assert_conserved(world)
        self.assertEqual(world.destroyed, 1)
        self.assertEqual(world.score, 10)
        self.assertTrue(all(world.cells[(c.x, c.y)] == c for c in lower))
        self.assertTrue(all(world.cells[(c.x, 11)].char == c.char for c in bridge))
        self.assertEqual(world.cells[(40, 4)].char, '!')
        self.assertFalse(world.falling)
        self.assertFalse(world.waves)
        self.assertFalse(world.particles)

    def test_bridge_waits_until_last_support_is_destroyed(self):
        cells = [Cell(x, 5, '=') for x in range(5, 16)] + [Cell(5, 6, '|'), Cell(15, 6, '|')]
        world = World(cells, 50, 25, falling_enabled=True)
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
        world = World([Cell(10, 5, '界', style, 2), Cell(10, 6, '|')], 50, 25, falling_enabled=True)
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

    def test_combo_improves_points_without_expanding_attack_reach(self):
        for attack in ('punch', 'blast'):
            outcomes = []
            for multiplier in (1, 5):
                cells = [Cell(x, y, '#') for y in range(30) for x in range(80)]
                world = World(cells, 80, 35)
                world.player.x, world.player.y = 40, 15
                world.multiplier = multiplier
                count = getattr(world, attack)()
                self.assertGreater(count, 0)
                self.assertLess(count, 80)
                self.assertIn((48, 14), world.cells)
                self.assertIn((40, 10), world.cells)
                outcomes.append(set(world.cells))
                self.assert_conserved(world)
            self.assertEqual(outcomes[0], outcomes[1])

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

    def test_challenge_adds_targets_without_changing_captured_terrain(self):
        cells = parse_capture('shell prompt\n$ echo ready\nready', 80, 25)
        for falling_enabled in (False, True):
            world = World(cells, 80, 25, duration=30, falling_enabled=falling_enabled)
            self.assertEqual(world.generated_enemies, 3)
            self.assertEqual(len(world.enemies), 3)
            self.assertEqual(world.total, len(cells) + 15)
            self.assertEqual(world.destroyed, 0)
            self.assertEqual(world.original, tuple(cells))
            self.assertEqual(world.cells, {(cell.x, cell.y): cell for cell in cells})
            for enemy in world.enemies:
                self.assertEqual(''.join(cell.char for cell in enemy.cells), 'ERROR')
                self.assertEqual(enemy.hp, 2)
                self.assertTrue(all(cell.style == Style(fg=196, bold=True)
                                    for cell in enemy.cells))
            self.assert_conserved(world)

    def test_challenge_preserves_natural_error_count_and_free_play(self):
        for duration in (None, 30):
            cells = parse_capture('one ERROR here', 80, 25)
            world = World(cells, 80, 25, duration=duration)
            self.assertEqual(world.generated_enemies, 0)
            self.assertEqual(len(world.enemies), 1)
            self.assertEqual(world.total, len(cells))
            self.assert_conserved(world)
        for cells in ([], parse_capture('no failures', 80, 25)):
            world = World(cells, 80, 25)
            self.assertEqual(world.generated_enemies, 0)
            self.assertFalse(world.enemies)
            self.assertEqual(world.total, len(cells))
            self.assert_conserved(world)

    def test_generated_errors_are_in_bounds_and_clear_of_starting_player(self):
        for width, height in ((1, 1), (8, 6), (9, 7), (44, 9), (80, 25), (300, 100)):
            with self.subTest(width=width, height=height):
                world = World([], width, height, duration=30)
                self.assertGreater(len(world.enemies), 0)
                self.assertLessEqual(len(world.enemies), 3)
                self.assertEqual(len({(e.x, e.y) for e in world.enemies}), len(world.enemies))
                for enemy in world.enemies:
                    self.assertGreaterEqual(enemy.x - 2, 0)
                    self.assertLessEqual(enemy.x + 2, world.width - 1)
                    self.assertGreaterEqual(enemy.y - 1, 0)
                    self.assertLessEqual(enemy.y, world.height - 2)
                    self.assertFalse(abs(enemy.x - world.player.x) < 3.2
                                     and abs(enemy.y - world.player.y) < 1.7)
                world.update(1 / 90)
                self.assertEqual(world.hurt_until, 0)
                self.assert_conserved(world)

    def test_generated_errors_can_be_killed_and_reset_reproduces_them(self):
        world = World([], 80, 25, seed=1, duration=30)
        before = [(e.x, e.y, e.hp, list(e.cells)) for e in world.enemies]
        enemy = world.enemies[0]
        self.assertEqual(world.destroy(enemy.x, enemy.y - 1, 2.1, 0.5), 0)
        self.assertEqual(enemy.hp, 1)
        advance(world, 0.12)
        self.assertEqual(world.destroy(enemy.x, enemy.y - 1, 2.1, 0.5), 5)
        self.assertEqual(len(world.enemies), 2)
        self.assertEqual(world.destroyed, 5)
        self.assertFalse(world.cleared)
        self.assertGreater(world.score, 50)
        self.assertTrue(any(p.char == 'E' and p.style.fg == 196 for p in world.particles))
        self.assert_conserved(world)
        world.destroy(40, 12, 80, 25, enemy_damage=2)
        self.assertEqual(world.destroyed, 15)
        self.assertEqual(world.generated_enemies, 3)
        self.assertTrue(world.cleared)
        self.assertFalse(world.finished)
        world.update(1)
        self.assertFalse(world.enemies)
        self.assert_conserved(world)
        world.reset()
        self.assertEqual([(e.x, e.y, e.hp, list(e.cells)) for e in world.enemies], before)
        self.assertEqual(world.generated_enemies, 3)
        self.assertEqual(world.total, 15)
        self.assertEqual(world.destroyed, 0)
        self.assertEqual(world.score, 0)
        self.assertEqual(world.original, ())
        self.assertFalse(world.finished)
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

    def test_clear_waits_for_falling_and_enemies_without_ending_survival(self):
        for duration in (None, 30):
            world = World([Cell(10, 5, '='), Cell(10, 6, '|')], 50, 25, duration=duration, falling_enabled=True)
            world.destroy(10, 6, 0.4, 0.4)
            self.assertFalse(world.cleared)
            self.assertFalse(world.finished)
            advance(world, 2)
            self.assertEqual(world.destroyed, 1)
            self.assertFalse(world.cleared)
            world.destroy(10, world.height - 2, 0.4, 0.4)
            self.assertEqual(world.destroyed, 2)
            if duration is not None:
                self.assertFalse(world.cleared)
                self.assertFalse(world.finished)
                world.destroy(world.width / 2, world.height / 2,
                              world.width, world.height, enemy_damage=2)
            self.assertTrue(world.cleared)
            self.assertFalse(world.finished)
            self.assertEqual(world.destroyed, world.total)
            self.assert_conserved(world)
        world = World(parse_capture('ERROR', 50, 25), 50, 25, duration=30, falling_enabled=True)
        self.assertFalse(world.cleared)
        self.assertFalse(world.finished)
        enemy = world.enemies[0]
        world.destroy(enemy.x, enemy.y - 1, 3, 2, enemy_damage=2)
        self.assertFalse(world.finished)
        self.assertTrue(world.cleared)

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

    def test_dense_falling_text_bounds_effects_and_conserves_every_character(self):
        cells = [Cell(x, y, '#') for y in range(1, 48) for x in range(160)]
        world = World(cells, 160, 52, seed=13, falling_enabled=True)
        world.destroy(80, 45, 55, 5)
        for frame in range(120):
            if frame % 9 == 0:
                world.destroy((frame * 13) % 160, (frame * 7) % 47, 9, 4)
            world.update(1 / 60)
            self.assert_conserved(world)
        self.assertGreater(world.destroyed, 0)
        self.assertLessEqual(len(world.waves), 24)

    def test_many_falling_chunks_settle_without_secondary_destruction(self):
        # Many detached fragments land on lower text without damaging it.
        positions = range(2, 320, 4)
        cells = [Cell(x, 4, '=') for x in positions]
        cells += [Cell(x, 5, '|') for x in positions]
        cells += [Cell(x + dx, 15, '*') for x in positions for dx in (-1, 0, 1)]
        world = World(cells, 320, 30, falling_enabled=True)
        world.destroy(160, 5, 200, 0.4)
        self.assertEqual(len(world.falling), 80)
        for chunk in world.falling:
            chunk.vy = 42
        for _ in range(90):
            world.update(1 / 90)
            self.assert_conserved(world)
        self.assertEqual(world.destroyed, 80)
        self.assertFalse(world.falling)
        self.assertTrue(all((x + dx, 15) in world.cells for x in positions for dx in (-1, 0, 1)))


if __name__ == '__main__':
    unittest.main()
