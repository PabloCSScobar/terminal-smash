"""Regression tests for restoring text beneath moving sprites and overlays."""

import unittest
import unicodedata
from unittest.mock import patch

from terminal_smash.capture import Cell
from terminal_smash.model import World
from terminal_smash.records import arena_key
from terminal_smash.ui import RoundRecord, TerrainLayer, _actor_pose, _draw, _text_runs


class Canvas:
    """Small terminal grid to check the visible result of a background copy."""

    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.writes = []
        self.erase()

    def getmaxyx(self):
        return self.rows, self.cols

    def erase(self):
        self.grid = [[' ' for _ in range(self.cols)] for _ in range(self.rows)]

    def addstr(self, y, x, text, attr=0):
        self.writes.append((y, x, text))
        for character in text:
            self.grid[y][x] = character
            width = 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
            if width == 2:
                self.grid[y][x + 1] = '~'  # The occupied trailing column.
            x += width

    def noutrefresh(self):
        pass

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

    def test_damage_repaints_only_affected_rows_on_a_large_scene(self):
        self.screen = Canvas(60, 200)
        self.world = World([Cell(x, y, '#') for y in range(54) for x in range(200)], 200, 56, falling_enabled=False)
        self.draw()
        self.assertLessEqual(len(self.layer.window.writes), 54)
        self.layer.window.writes.clear()
        self.world.destroy(100, 25, 0.4, 0.4)
        self.draw()
        self.assertEqual(self.screen.grid[27][100], ' ')
        self.assertEqual(self.screen.grid[26][100], '#')
        self.assertEqual(self.screen.grid[28][100], '#')
        self.assertEqual({y for y, _, _ in self.layer.window.writes}, {27})
        self.assertLessEqual(len(self.layer.window.writes), 3)

    def test_falling_run_cache_refreshes_when_letters_are_hit(self):
        self.world = World([Cell(5, 5, 'A'), Cell(6, 5, 'B'), Cell(5, 6, '|')], 80, 20)
        self.world.destroy(5, 6, 0.4, 0.4)
        chunk = self.world.falling[0]
        self.assertEqual(self.layer.falling_runs(chunk)[0][2], 'AB')
        self.world.destroy(5, 5, 0.4, 0.4)
        self.assertEqual(self.layer.falling_runs(chunk)[0][2], 'B')

    def test_grouped_runs_keep_wide_and_combining_characters_atomic(self):
        cells = [Cell(0, 0, '界', width=2), Cell(2, 0, 'e\u0301'), Cell(4, 0, 'X')]
        runs = _text_runs(cells)
        self.assertEqual([(row, x, text, width) for row, x, text, width, _ in runs],
                         [(0, 0, '界e\u0301', 3), (0, 4, 'X', 1)])

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

    def test_health_bar_follows_actor_and_hud_survives_ceiling_and_small_screen(self):
        for rows, cols in ((14, 44), (24, 80)):
            with self.subTest(rows=rows, cols=cols):
                self.screen = Canvas(rows, cols)
                self.world = World([], cols, rows - 4)
                self.world.player.x, self.world.player.y = 12, 7
                self.world.player.hp = 3
                with patch('terminal_smash.ui.curses.doupdate'):
                    _draw(self.screen, self.world, self.palette, '', False, self.layer)
                    self.assertIn('HP 3/5', ''.join(self.screen.grid[0]))
                    self.assertIn('[###--]', ''.join(self.screen.grid[6]))
                    self.world.player.x, self.world.player.y = cols - 2, 2
                    self.world.grip_enabled = True
                    self.world.grip_surface = 'ceiling'
                    self.world.player.hp = 1
                    _draw(self.screen, self.world, self.palette, '', False, self.layer)
                    self.assertIn('HP 1/5 [#----]', ''.join(self.screen.grid[0]))
                    self.assertNotIn('[###--]', ''.join(self.screen.grid[6]))
                    self.assertIn('E grip ON', ''.join(self.screen.grid[-1]))

    def test_death_overlay_has_restart_and_correct_mode_switch(self):
        for duration, switch in ((None, 'challenge'), (30, 'free play')):
            self.world = World([], 80, 20, duration=duration)
            self.world.player.hp = 0
            self.world._finish('dead')
            with patch('terminal_smash.ui.curses.doupdate'):
                _draw(self.screen, self.world, self.palette, '', False, self.layer)
            visible = '\n'.join(''.join(row) for row in self.screen.grid)
            self.assertIn('GAME OVER - NO HEALTH!', visible)
            self.assertIn('[R] retry  [C] ' + switch, visible)


class RoundRecordTests(unittest.TestCase):
    def world(self, duration=30):
        return World([Cell(3, 3, 'X')], 80, 20, duration=duration)

    def test_incomplete_round_never_writes_and_completed_round_saves_once(self):
        world = self.world()
        with patch('terminal_smash.ui.load_best', return_value=10), patch('terminal_smash.ui.save_best', return_value=120) as save:
            record = RoundRecord(world)
            record.finish(world)
            save.assert_not_called()
            world.score = 120
            world.update(30)
            record.finish(world)
            record.finish(world)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(record.best, 120)
            self.assertTrue(record.new_record)

    def test_respawns_and_reset_keep_the_same_record_key(self):
        world = self.world()
        with patch('terminal_smash.ui.load_best', return_value=0):
            key = RoundRecord(world).key
            self.assertEqual(key, arena_key(list(world.original), world.width, world.height))
            world.destroy(world.width / 2, world.height / 2, world.width, world.height,
                          enemy_damage=2)
            self.assertFalse(world.finished)
            self.assertFalse(world.enemies)
            before = world.generated_enemies
            world.update(2.1)
            self.assertGreater(world.generated_enemies, before)
            self.assertEqual(RoundRecord(world).key, key)
            world.reset()
            self.assertEqual(RoundRecord(world).key, key)

    def test_free_play_never_reads_or_saves_records(self):
        with patch('terminal_smash.ui.load_best') as load, patch('terminal_smash.ui.save_best') as save:
            world = self.world(duration=None)
            record = RoundRecord(world)
            record.finish(world)
            load.assert_not_called()
            save.assert_not_called()

    def test_storage_failure_preserves_finished_game_and_is_not_retried_each_frame(self):
        world = self.world(duration=0.01)
        with patch('terminal_smash.ui.load_best', return_value=10), patch('terminal_smash.ui.save_best', side_effect=OSError('read only')) as save:
            record = RoundRecord(world)
            world.update(0.02)
            record.finish(world)
            record.finish(world)
            self.assertTrue(record.error)
            self.assertTrue(world.finished)
            self.assertEqual(save.call_count, 1)


class ActorPoseTests(unittest.TestCase):
    def test_mobility_attacks_and_landing_are_visually_distinct(self):
        world = World([], 80, 30)
        world.player.y = 28
        world.player.grounded = True
        idle = _actor_pose(world)
        world.dash()
        self.assertNotEqual(_actor_pose(world), idle)
        world.return_to_top()
        world.player.y = 15
        self.assertTrue(world.slam())
        slam = _actor_pose(world)
        self.assertNotEqual(slam, idle)
        for _ in range(30):
            world.update(1 / 90)
            if world.player.grounded:
                break
        self.assertTrue(world.player.grounded)
        self.assertGreater(world.landing_until, world.time)
        self.assertNotEqual(_actor_pose(world), slam)


if __name__ == '__main__':
    unittest.main()
