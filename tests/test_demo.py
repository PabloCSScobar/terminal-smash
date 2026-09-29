import unittest

from terminal_smash.capture import parse_capture
from terminal_smash.demo import build_demo
from terminal_smash.model import World


class DemoTests(unittest.TestCase):
    SIZES = ((44, 9), (80, 19), (100, 25), (160, 45))

    def test_demo_uses_full_height_with_more_text_on_larger_screens(self):
        totals = []
        for width, height in self.SIZES:
            with self.subTest(width=width, height=height):
                text = build_demo(width, height)
                cells = parse_capture(text, width + 10, height + 10)
                self.assertEqual(len(text.split('\n')), height)
                self.assertTrue(all(0 <= c.x < width and 0 <= c.y < height - 1 for c in cells))
                self.assertEqual(max(c.y for c in cells), height - 2)
                self.assertGreater(len(cells), width * height * 0.35)
                self.assertGreaterEqual(len({c.style.fg for c in cells}), 4)
                totals.append(len(cells))
        self.assertGreater(totals[1], 523)  # Previous fixed 80x19 demo.
        self.assertEqual(totals, sorted(set(totals)))
        self.assertGreater(totals[-1], totals[1] * 3)

    def test_both_side_routes_can_be_climbed_from_floor_with_single_jumps(self):
        for width, height in self.SIZES:
            for side in ('left', 'right'):
                with self.subTest(width=width, height=height, side=side):
                    world = World(parse_capture(build_demo(width, height), width, height),
                                  width, height + 1, seed=1)
                    # Isolate terrain reachability from enemy contact/knockback.
                    world.enemies.clear()
                    world.player.x = 7 if side == 'left' else width - 8
                    world.player.y = height - 1
                    world.player.grounded = True
                    landings = []
                    for _ in range(height):
                        before = world.player.y
                        world.jump()
                        for _ in range(90):
                            world.update(1 / 90)
                            if world.grip_surface == 'ceiling':
                                # The last jump now catches the ceiling. Space
                                # releases onto the real row-3 stair below it.
                                self.assertEqual(world.player.y, 2)
                                self.assertIn((round(world.player.x), 3), world.occupied)
                                world.jump()
                            if world.player.grounded:
                                break
                        self.assertTrue(world.player.grounded)
                        self.assertLess(world.player.y, before)
                        self.assertEqual(world.player.jumps, 0)
                        landings.append(world.player.y)
                        if world.player.y <= 2:
                            break
                    self.assertEqual(world.player.y, 2)
                    self.assertGreaterEqual(len(landings), 2)

    def test_enemies_stay_bounded_as_demo_grows(self):
        for width, height in self.SIZES:
            with self.subTest(width=width, height=height):
                world = World(parse_capture(build_demo(width, height), width, height),
                              width, height + 1)
                self.assertGreaterEqual(len(world.enemies), 1)
                self.assertLessEqual(len(world.enemies), 3)

    def test_empty_and_tiny_rectangles_remain_clipped(self):
        for width, height in ((0, 10), (10, 0), (-1, 3), (3, -1)):
            self.assertEqual(build_demo(width, height), '')
        for width, height in ((1, 1), (3, 2), (8, 6)):
            cells = parse_capture(build_demo(width, height), width + 20, height + 20)
            self.assertTrue(all(c.x < width and c.y < height for c in cells))


if __name__ == '__main__':
    unittest.main()
