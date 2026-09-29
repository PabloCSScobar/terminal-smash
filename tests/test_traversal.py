import math
import unittest

from terminal_smash.capture import Cell, parse_capture
from terminal_smash.model import World


def advance(world, seconds, fps=90, action=None):
    for _ in range(math.ceil(seconds * fps)):
        if action is not None:
            action()
        world.update(1 / fps)


class TraversalTests(unittest.TestCase):
    def attached(self, surface='left', *, width=80, height=35):
        world = World([], width, height, seed=1)
        world.player.x = 1 if surface == 'left' else width - 2
        world.player.y = height - 5
        if surface == 'ceiling':
            world.player.x, world.player.y = width / 2, 2.1
            world.player.vy = -21
        self.assertTrue(world.grip_enabled)
        world.update(1 / 90)
        self.assertEqual(world.grip_surface, surface)
        return world

    def test_new_game_and_interior_return_to_top_fall_without_ceiling_grab(self):
        for fps in (30, 90):
            world = World([], 80, 35)
            self.assertTrue(world.grip_enabled)
            self.assertEqual(world.grip_surface, '')
            advance(world, 0.3, fps)
            self.assertGreater(world.player.y, 2)
            self.assertEqual(world.grip_surface, '')
            world.return_to_top()
            advance(world, 0.3, fps)
            self.assertGreater(world.player.y, 2)
            self.assertEqual(world.grip_surface, '')
            world.reset()
            advance(world, 0.3, fps)
            self.assertGreater(world.player.y, 2)
            self.assertEqual(world.grip_surface, '')

    def test_upward_ceiling_contact_attaches_automatically_at_different_frame_rates(self):
        for fps in (30, 90):
            world = World([], 80, 35)
            world.player.y = 5
            world.jump()
            advance(world, 0.5, fps)
            self.assertEqual(world.grip_surface, 'ceiling')
            self.assertEqual(world.player.y, 2)
            advance(world, 0.5, fps)
            self.assertEqual(world.player.y, 2)
            world.jump()
            advance(world, 0.4, fps)
            self.assertEqual(world.grip_surface, '')
            self.assertGreater(world.player.y, 2)

    def test_ceiling_contact_and_release_survive_long_frames(self):
        for elapsed in (0.12, 0.5, 2.0):
            world = World([], 80, 35)
            world.player.y = 3.5
            world.jump()
            world.update(elapsed)
            self.assertEqual(world.grip_surface, 'ceiling')
            self.assertEqual(world.player.y, 2)
            world.jump()
            world.update(elapsed)
            self.assertEqual(world.grip_surface, '')
            self.assertGreater(world.player.y, 2)

    def test_reset_restores_automatic_wall_attachment_without_enabling_it(self):
        world = self.attached('left')
        world.reset()
        world.player.x, world.player.y = world.width - 2, 18
        world.update(1 / 90)
        self.assertEqual(world.grip_surface, 'right')

    def test_floor_wall_ceiling_route_reaches_isolated_upper_platform_without_top(self):
        for fps in (30, 90):
            with self.subTest(fps=fps):
                cells = [Cell(x, 7, '=') for x in range(34, 51)]
                world = World(cells, 80, 35, falling_enabled=False)
                advance(world, 1.5, fps)
                self.assertTrue(world.player.grounded)
                self.assertEqual(world.player.y, 33)
                self.assertTrue(world.grip_enabled)
                advance(world, 0.8, fps, lambda: world.move(-1))
                self.assertEqual(world.grip_surface, 'left')
                advance(world, 2.1, fps, world.climb_up)
                self.assertEqual(world.grip_surface, 'ceiling')
                self.assertEqual(world.player.y, 2)
                for _ in range(2 * fps):
                    if world.player.x >= 38:
                        break
                    world.move(1)
                    world.update(1 / fps)
                advance(world, 0.25, fps)
                self.assertTrue(34 <= world.player.x <= 50)
                world.jump()
                self.assertEqual(world.grip_surface, '')
                advance(world, 0.7, fps)
                self.assertTrue(world.player.grounded)
                self.assertEqual(world.player.y, 6)
                self.assertEqual(world.destroyed, 0)
                self.assertEqual(len(world.cells), len(cells))

    def test_both_walls_climb_and_transfer_to_ceiling_at_different_sizes_and_rates(self):
        for width, height in ((8, 6), (44, 10), (300, 100)):
            for fps in (30, 90):
                for surface, x in (('left', 1), ('right', width - 2)):
                    with self.subTest(size=(width, height), fps=fps, surface=surface):
                        world = World([], width, height)
                        world.player.x, world.player.y = x, height - 2
                        world.update(1 / fps)
                        self.assertEqual(world.grip_surface, surface)
                        for _ in range(math.ceil((height / 16 + 0.3) * fps)):
                            world.climb_up()
                            world.update(1 / fps)
                            self.assertTrue(1 <= world.player.x <= width - 2)
                            self.assertTrue(2 <= world.player.y <= height - 2)
                        self.assertEqual(world.grip_surface, 'ceiling')
                        self.assertEqual(world.player.y, 2)
                        self.assertEqual(world.player.x, x)

    def test_idle_wall_hangs_after_climb_input_expires_and_down_descends(self):
        for surface in ('left', 'right'):
            world = self.attached(surface)
            before = world.player.y
            world.climb_up()
            advance(world, 0.4)
            self.assertLess(world.player.y, before - 2.8)
            hanging = world.player.y
            advance(world, 1)
            self.assertEqual(world.player.y, hanging)
            self.assertEqual((world.player.vx, world.player.vy), (0, 0))
            world.climb_down()
            advance(world, 0.4)
            self.assertGreater(world.player.y, hanging + 2.8)
            self.assertEqual(world.grip_surface, surface)

    def test_ceiling_moves_in_both_directions_then_hangs_without_horizontal_drift(self):
        world = self.attached('ceiling')
        start = world.player.x
        advance(world, 0.3, action=lambda: world.move(1))
        advance(world, 0.25)
        self.assertGreater(world.player.x, start + 9)
        stop = world.player.x
        advance(world, 1)
        self.assertEqual(world.player.x, stop)
        advance(world, 0.3, action=lambda: world.move(-1))
        self.assertLess(world.player.x, stop - 9)
        self.assertEqual(world.player.y, 2)
        self.assertEqual(world.grip_surface, 'ceiling')

    def test_wall_jump_releases_inward_and_allows_a_second_jump(self):
        for surface, sign in (('left', 1), ('right', -1)):
            world = self.attached(surface)
            x = world.player.x
            world.jump()
            self.assertEqual(world.grip_surface, '')
            self.assertGreater(world.player.vx * sign, 0)
            self.assertLess(world.player.vy, 0)
            self.assertEqual(world.player.jumps, 1)
            advance(world, 0.3)
            self.assertGreater((world.player.x - x) * sign, 8)
            self.assertEqual(world.grip_surface, '')
            world.jump()
            self.assertEqual(world.player.jumps, 2)

    def test_ceiling_space_and_down_drop_instead_of_immediately_reattaching(self):
        for action in ('jump', 'climb_down'):
            world = self.attached('ceiling')
            getattr(world, action)()
            self.assertEqual(world.grip_surface, '')
            self.assertGreater(world.player.vy, 0)
            advance(world, 0.4)
            self.assertEqual(world.grip_surface, '')
            self.assertGreater(world.player.y, 4)
            self.assertTrue(world.grip_enabled)

    def test_moving_away_from_wall_releases_even_before_first_attachment(self):
        for attach_first in (False, True):
            for surface, sign in (('left', 1), ('right', -1)):
                world = World([], 80, 35)
                world.player.x = 1 if surface == 'left' else 78
                world.player.y = 18
                if attach_first:
                    world.update(1 / 90)
                x = world.player.x
                world.move(sign)
                advance(world, 0.3)
                self.assertEqual(world.grip_surface, '')
                self.assertGreater((world.player.x - x) * sign, 8)
                self.assertGreater(world.player.y, 18)

    def test_grip_off_and_interior_up_keep_regular_jump_controls(self):
        for enabled in (False, True):
            world = World([], 80, 35)
            world.player.y = 33
            world.player.grounded = True
            world.grip_enabled = enabled
            world.climb_up()
            self.assertEqual(world.grip_surface, '')
            self.assertLess(world.player.vy, 0)
            self.assertEqual(world.player.jumps, 1)
        world = self.attached()
        world.toggle_grip()
        self.assertEqual(world.grip_surface, '')
        self.assertFalse(world.grip_enabled)
        y = world.player.y
        advance(world, 0.4)
        self.assertGreater(world.player.y, y)

    def test_reenabling_grip_can_attach_at_boundary_on_the_next_step(self):
        world = self.attached()
        world.toggle_grip()
        world.toggle_grip()
        world.update(1 / 90)
        self.assertEqual(world.grip_surface, 'left')

    def test_finished_round_freezes_traversal_controls(self):
        world = self.attached()
        world._finish('dead')
        position = world.player.x, world.player.y
        for action in (world.toggle_grip, world.climb_up, world.climb_down, world.jump):
            action()
            self.assertTrue(world.grip_enabled)
            self.assertEqual(world.grip_surface, '')
        advance(world, 1)
        self.assertEqual((world.player.x, world.player.y), position)

    def test_accepted_attacks_release_grip(self):
        for action in ('punch', 'blast', 'dash', 'slam', 'drop'):
            with self.subTest(action=action):
                world = self.attached()
                getattr(world, action)()
                self.assertEqual(world.grip_surface, '')
                world.update(1 / 90)
                self.assertEqual(world.grip_surface, '')

    def test_reset_and_top_clear_attachment_and_reset_preserves_mode(self):
        world = self.attached()
        world.return_to_top()
        self.assertEqual(world.grip_surface, '')
        self.assertEqual(world.player.y, 2)
        self.assertTrue(world.grip_enabled)
        world.reset()
        self.assertTrue(world.grip_enabled)
        self.assertEqual(world.grip_surface, '')
        self.assertEqual(world._climb_until, 0)
        world.toggle_grip()
        world.reset()
        self.assertFalse(world.grip_enabled)

    def test_hanging_keeps_falling_chunks_particles_and_enemies_running(self):
        cells = [Cell(x, 5, '=') for x in range(20, 30)] + [Cell(25, 6, '|')]
        cells += [Cell(c.x + 45, c.y + 12, c.char) for c in parse_capture('ERROR', 80, 35)]
        world = World(cells, 80, 35, seed=3, falling_enabled=True)
        world.player.x, world.player.y = 1, 20
        world.update(1 / 90)
        world.destroy(25, 6, 0.4, 0.4)
        chunk = world.falling[0]
        particle = world.particles[0]
        enemy = world.enemies[0]
        x, life = enemy.x, particle.life
        advance(world, 0.12)
        self.assertGreater(chunk.offset_y, 0)
        self.assertLess(particle.life, life)
        self.assertLess(enemy.x, x)
        self.assertEqual(world.grip_surface, 'left')
        self.assertEqual(world.player.y, 20)

    def test_enemy_contact_can_damage_and_release_hanging_player(self):
        world = World(parse_capture('ERROR', 80, 35), 80, 35)
        world.time = 2
        world.player.x, world.player.y = 1, 20
        world.update(1 / 90)
        enemy = world.enemies[0]
        enemy.x, enemy.y = 2, 20
        hp = world.player.hp
        world.update(1 / 90)
        self.assertEqual(world.player.hp, hp - 1)
        self.assertGreater(world.hurt_until, world.time)
        self.assertEqual(world.grip_surface, '')
        self.assertLess(world.player.vy, 0)


if __name__ == '__main__':
    unittest.main()
