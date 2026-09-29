import itertools
import unittest

from terminal_smash.capture import Cell, parse_capture
from terminal_smash.model import World


class EnemySurvivalTests(unittest.TestCase):
    def assert_conserved(self, world):
        remaining = (len(world.cells) + sum(len(c.cells) for c in world.falling)
                     + sum(len(e.cells) for e in world.enemies))
        self.assertEqual(world.total, world.destroyed + remaining)
        self.assertLessEqual(len(world.enemies), 8)
        self.assertEqual({(x, row): cell for row, cells in enumerate(world.cells_by_row)
                          for x, cell in cells.items()}, world.cells)

    def assert_separate(self, world):
        for enemy in world.enemies:
            self.assertGreaterEqual(round(enemy.x) - 3, 0)
            self.assertLess(round(enemy.x) + 3, world.width)
            self.assertGreaterEqual(round(enemy.y) - 1, 0)
            self.assertLessEqual(round(enemy.y), world.height - 2)
        for first, second in itertools.combinations(world.enemies, 2):
            # This deliberately checks the displayed footprints independently
            # of the model collision helper, including integer rounding.
            a = {(x, y) for x in range(round(first.x) - 3, round(first.x) + 4)
                 for y in (round(first.y) - 1, round(first.y))}
            b = {(x, y) for x in range(round(second.x) - 3, round(second.x) + 4)
                 for y in (round(second.y) - 1, round(second.y))}
            self.assertFalse(a & b)

    def test_contact_costs_one_hp_and_invulnerability_blocks_repeated_damage(self):
        world = World(parse_capture('ERROR', 80, 25), 80, 25)
        enemy = world.enemies[0]
        self.assertEqual((world.player.hp, world.player.max_hp), (5, 5))
        world.player.x = enemy.x = 30
        world.player.y = enemy.y = 15
        world.time = 2
        world.combo, world.multiplier = 5, 2
        world._step_enemies(0)
        self.assertEqual(world.player.hp, 4)
        self.assertEqual((world.combo, world.multiplier), (0, 1))
        self.assertLess(world.player.vy, 0)
        world.time += 0.84
        world._step_enemies(0)
        self.assertEqual(world.player.hp, 4)
        world.time += 0.02
        world._step_enemies(0)
        self.assertEqual(world.player.hp, 3)
        self.assertFalse(world.finished)

    def test_fifth_hit_ends_round_and_freezes_actions_until_reset(self):
        for duration in (None, 30):
            with self.subTest(duration=duration):
                world = World(parse_capture('ERROR', 80, 25), 80, 25, duration=duration)
                enemy = world.enemies[0]
                world.player.x = enemy.x = 30
                world.player.y = enemy.y = 15
                for hit in range(5):
                    world.time = 2 + hit
                    world._step_enemies(0)
                    self.assertEqual(world.player.hp, 4 - hit)
                self.assertTrue(world.finished)
                self.assertEqual(world.finish_reason, 'dead')
                self.assertEqual((world.player.vx, world.player.vy), (0, 0))
                before = (world.time, world.round_elapsed, world.player.x,
                          world.player.y, world.total, world.score, len(world.enemies))
                world.update(40)
                world.move(1)
                world.jump()
                world.drop()
                world.return_to_top()
                self.assertFalse(world.dash())
                self.assertFalse(world.slam())
                self.assertEqual(world.punch(), 0)
                self.assertEqual(world.blast(), 0)
                self.assertEqual(before, (world.time, world.round_elapsed, world.player.x,
                                         world.player.y, world.total, world.score, len(world.enemies)))
                world.reset()
                self.assertEqual(world.player.hp, 5)
                self.assertFalse(world.finished)
                self.assertEqual(world.next_enemy_spawn, 2)
                self.assertEqual(world.generated_enemies, 0)
                self.assert_conserved(world)

    def test_clearing_challenge_spawns_again_without_ending_or_bursting_after_stall(self):
        world = World([], 100, 35, duration=30)
        self.assertEqual(world.destroy(50, 17, 100, 35, enemy_damage=2), 15)
        self.assertTrue(world.cleared)
        self.assertFalse(world.finished)
        world.update(1.9)
        self.assertFalse(world.enemies)
        world.update(0.11)
        self.assertEqual(len(world.enemies), 1)
        self.assertEqual(world.generated_enemies, 4)
        self.assertEqual((world.total, world.destroyed), (20, 15))
        world.update(10)
        self.assertEqual(len(world.enemies), 2)
        world.update(0.01)
        self.assertEqual(len(world.enemies), 2)
        world.update(2)
        self.assertEqual(len(world.enemies), 3)
        self.assertEqual(world.enemy_target, 3)
        self.assert_conserved(world)
        world.update(100)
        self.assertTrue(world.finished)
        self.assertEqual(world.finish_reason, 'time')
        before = (world.total, len(world.enemies), world.generated_enemies)
        world.update(10)
        self.assertEqual((world.total, len(world.enemies), world.generated_enemies), before)

    def test_natural_error_challenge_is_gradually_topped_up_and_free_play_is_not(self):
        cells = parse_capture('one ERROR\nready', 100, 35)
        for duration, expected in ((None, 1), (30, 3)):
            world = World(cells, 100, 35, duration=duration)
            self.assertEqual(len(world.enemies), 1)
            world.update(2)
            self.assertEqual(len(world.enemies), 1 if duration is None else 2)
            world.update(2)
            self.assertEqual(len(world.enemies), expected)
            self.assertEqual(world.original, tuple(cells))
            world.destroy(50, 17, 100, 35, enemy_damage=2)
            self.assertTrue(world.cleared)
            self.assertFalse(world.finished)
            world.update(2)
            self.assertEqual(len(world.enemies), 0 if duration is None else 1)
            self.assert_conserved(world)

    def test_respawn_is_safe_bounded_and_reset_restores_snapshot_accounting(self):
        cells = [Cell(10, 5, '#')]
        world = World(cells, 80, 25, duration=30)
        original_positions = [(e.x, e.y) for e in world.enemies]
        for frame in range(12):
            world.destroy(40, 12, 80, 25, enemy_damage=2)
            world.player.x = (1, 40, 78)[frame % 3]
            world.player.y = (2, 12, 23)[frame % 3]
            world.update(2.01)
            self.assertFalse(world.finished)
            self.assertEqual(len(world.enemies), 1)
            enemy = world.enemies[0]
            self.assertFalse(abs(enemy.x - world.player.x) < 6
                             and abs(enemy.y - world.player.y) < 2)
            self.assert_separate(world)
            self.assert_conserved(world)
        self.assertGreater(world.generated_enemies, 3)
        world.player.hp = 1
        world.reset()
        self.assertEqual(world.generated_enemies, 3)
        self.assertEqual(world.player.hp, 5)
        self.assertEqual(world.total, len(cells) + 15)
        self.assertEqual(world.destroyed, 0)
        self.assertEqual(world.next_enemy_spawn, 2)
        self.assertEqual([(e.x, e.y) for e in world.enemies], original_positions)
        self.assertEqual(world.original, tuple(cells))
        self.assert_conserved(world)

    def test_eight_pursuers_keep_full_sprite_spacing_at_all_corners(self):
        world = World(parse_capture(' '.join(['ERROR'] * 8), 100, 35), 100, 35, duration=30)
        self.assertEqual(len(world.enemies), 8)
        self.assertEqual(world.enemy_target, 8)
        world.hurt_until = 1000
        self.assert_separate(world)
        for target in ((1, 2), (98, 2), (98, 33), (1, 33), (50, 15)):
            world.player.x, world.player.y = target
            for _ in range(400):
                world.time += 1 / 60
                world._step_enemies(1 / 60)
                self.assert_separate(world)
        self.assert_conserved(world)

    def test_small_arenas_cap_actors_to_available_space_without_losing_letters(self):
        for width, height in ((8, 6), (14, 10), (21, 8), (44, 9)):
            with self.subTest(width=width, height=height):
                cells = parse_capture((' '.join(['ERROR'] * 8) + '\n') * 8, width, height)
                world = World(cells, width, height, duration=30)
                self.assertLessEqual(len(world.enemies), min(8, (width // 7) * ((height - 1) // 2)))
                self.assert_separate(world)
                self.assert_conserved(world)
                world.hurt_until = 1000
                for _ in range(120):
                    world.time += 1 / 60
                    world._step_enemies(1 / 60)
                    self.assert_separate(world)


if __name__ == '__main__':
    unittest.main()
