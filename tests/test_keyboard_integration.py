"""Exercise negotiated key press/release events through real curses and a PTY."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import termios
import textwrap
import time
import unittest

from tests.test_terminal_integration import Terminal


def key(code: int, event: int = 1, modifiers: int = 1) -> bytes:
    return f'\x1b[{code};{modifiers}:{event}u'.encode()


def arrow(direction: int, event: int = 1, modifiers: int = 1) -> bytes:
    return f'\x1b[1;{modifiers}:{event}{"C" if direction > 0 else "D"}'.encode()


OBSERVER = textwrap.dedent('''\
    import json, os
    from pathlib import Path
    from terminal_smash import cli, ui
    from terminal_smash.tower import TowerWorld

    os.environ.pop('STY', None)
    directory = Path(os.environ['XDG_STATE_HOME'])
    state = directory / 'keyboard-state.json'
    source = directory / 'keyboard-source.log'
    source.write_text((' ' * 100 + 'build worker completed request processing successfully\\n') * 80)
    draw = ui._draw_tower
    reset = TowerWorld.reset
    resets = 0
    frames = 0

    def count_reset(world):
        global resets
        resets += 1
        reset(world)

    def observe(win, world, palette, label, help_open, *args, **kwargs):
        global frames
        draw(win, world, palette, label, help_open, *args, **kwargs)
        frames += 1
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(
            frame=frames, x=world.player.x, y=world.player.y,
            vx=world.player.vx, grounded=world.player.grounded,
            elapsed=world.elapsed, jumps=world.jump_count,
            help=help_open, resets=resets, finished=world.finished)))
        temporary.replace(state)
        if (directory / 'raise-render-error').exists():
            raise RuntimeError('intentional keyboard cleanup fixture')

    TowerWorld.reset = count_reset
    ui._draw_tower = observe
    raise SystemExit(cli.main(['--file', str(source), '--tower']))
''')


@unittest.skipUnless(sys.platform.startswith('linux'), 'Requires Linux PTYs and termios')
class KeyboardIntegrationTests(unittest.TestCase):
    def open_game(self, *, observer=OBSERVER, rows=30, columns=240, title=b'SCROLLBACK TOWER'):
        terminal = Terminal([sys.executable, '-c', observer], rows=rows, columns=columns)
        self.addCleanup(terminal.close)
        terminal.until(b'\x1b[?u')
        offset = len(terminal.output)
        terminal.send(b'\x1b[?0u\x1b[?1;2c')
        terminal.until(b'\x1b[>11u', after=offset)
        terminal.until(b'\x1b[?u', after=offset)
        terminal.send(b'\x1b[?11u\x1b[?1;2c')
        terminal.until(title)
        self.snapshot(terminal, lambda state: state['elapsed'] > 0.04)
        return terminal

    def snapshot(self, terminal, check=lambda state: True, *, after=0, timeout=3):
        path = Path(terminal.state_directory.name) / 'keyboard-state.json'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            terminal.pump(0.003)
            if path.exists():
                state = json.loads(path.read_text())
                if state['frame'] > after and check(state):
                    return state
            if terminal.process.poll() is not None:
                self.fail(f'Keyboard fixture exited: {bytes(terminal.output[-1600:])!r}')
        self.fail(f'No matching keyboard state; last state: {state if path.exists() else None}')

    def assert_still(self, terminal, start, *, duration=0.12):
        frame = start['frame']
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            state = self.snapshot(terminal, after=frame)
            self.assertEqual(state['x'], start['x'])
            self.assertEqual(state['vx'], 0)
            frame = state['frame']
        return state

    def finish_game(self, terminal):
        terminal.send(key(27))
        self.assertEqual(terminal.finish(), 0)
        self.assertNotIn(b'Traceback', bytes(terminal.output))
        self.assert_restored(terminal)

    def assert_restored(self, terminal):
        output = bytes(terminal.output)
        self.assertIn(b'\x1b[>11u', output)
        self.assertIn(b'\x1b[<u', output)
        self.assertGreater(output.rfind(b'\x1b[<u'), output.find(b'\x1b[>11u'))
        self.assertIn(b'\x1b[?1004l', output)
        self.assertIn(b'\x1b[?2004l', output)
        restored = termios.tcgetattr(terminal.slave)
        self.assertEqual(restored[3] & (termios.ECHO | termios.ICANON),
                         terminal.original_mode[3] & (termios.ECHO | termios.ICANON))

    def test_a_held_arrow_moves_every_frame_without_repeats_and_release_stops(self):
        terminal = self.open_game()
        terminal.send(arrow(1))
        moving = self.snapshot(terminal, lambda state: state['vx'] > 0)
        first = moving
        samples = 0
        while moving['elapsed'] - first['elapsed'] < 0.7:
            state = self.snapshot(terminal, after=moving['frame'])
            self.assertGreater(state['x'], moving['x'], 'Holding must not wait for OS autorepeat')
            self.assertGreater(state['vx'], 0)
            self.assertTrue(state['grounded'])
            moving = state
            samples += 1
        self.assertGreater(samples, 20)
        self.assertGreater(moving['x'] - first['x'], 28)

        terminal.send(arrow(1, 3))
        stopped = self.snapshot(terminal, lambda state: state['vx'] == 0, after=moving['frame'])
        self.assertLess(stopped['elapsed'] - moving['elapsed'], 0.08)
        self.assert_still(terminal, stopped)

        # A fresh tap gets one small movement, with no guessed hold afterwards.
        terminal.send(arrow(-1))
        tapped = self.snapshot(terminal, lambda state: state['x'] < stopped['x'])
        terminal.send(arrow(-1, 3))
        released = self.snapshot(terminal, lambda state: state['vx'] == 0, after=tapped['frame'])
        self.assertLess(stopped['x'] - released['x'], 4)
        self.assert_still(terminal, released)
        self.finish_game(terminal)

    def test_overlapping_directions_restore_the_still_held_key(self):
        terminal = self.open_game()
        terminal.send(arrow(1))
        right = self.snapshot(terminal, lambda state: state['vx'] > 0)
        terminal.send(arrow(-1))
        left = self.snapshot(terminal, lambda state: state['vx'] < 0, after=right['frame'])
        terminal.send(arrow(-1, 3))
        right_again = self.snapshot(terminal, lambda state: state['vx'] > 0, after=left['frame'])
        terminal.send(arrow(1, 3))
        stopped = self.snapshot(terminal, lambda state: state['vx'] == 0,
                                after=right_again['frame'])
        self.assert_still(terminal, stopped)
        self.finish_game(terminal)

    def test_modifier_changes_do_not_hide_a_held_movement_keys_release(self):
        terminal = self.open_game()
        for press, release in ((arrow(1), arrow(1, 3, modifiers=3)),
                               (key(ord('d')), key(ord('d'), 3, modifiers=5))):
            terminal.send(press)
            moving = self.snapshot(terminal, lambda state: state['vx'] > 0)
            terminal.send(release)
            stopped = self.snapshot(terminal, lambda state: state['vx'] == 0,
                                    after=moving['frame'])
            self.assert_still(terminal, stopped)
        self.finish_game(terminal)

    def test_focus_loss_clears_held_keys_and_focus_return_keeps_the_game_paused(self):
        terminal = self.open_game()
        terminal.send(key(ord('d')))
        moving = self.snapshot(terminal, lambda state: state['vx'] > 0)
        terminal.send(b'\x1b[O')
        paused = self.snapshot(terminal, lambda state: state['help'], after=moving['frame'])
        held = self.assert_still(terminal, paused)
        self.assertEqual(held['elapsed'], paused['elapsed'])
        terminal.send(b'\x1b[I' + key(ord('d'), 2))
        focused = self.snapshot(terminal, after=held['frame'])
        self.assertTrue(focused['help'])
        self.assertEqual(focused['elapsed'], paused['elapsed'])
        terminal.send(key(ord('?')))
        resumed = self.snapshot(terminal, lambda state: not state['help'], after=focused['frame'])
        self.assert_still(terminal, resumed)
        self.finish_game(terminal)

    def test_repeats_do_not_repeat_jump_restart_or_help_actions(self):
        terminal = self.open_game()
        terminal.send(key(ord('w')))
        jumped = self.snapshot(terminal, lambda state: state['jumps'] == 1)
        terminal.send(key(ord('w'), 2) * 5)
        repeated = self.snapshot(terminal, lambda state: state['elapsed'] > jumped['elapsed'] + 0.06)
        self.assertEqual(repeated['jumps'], 1)
        terminal.send(key(ord('w'), 3) + key(ord('w')))
        self.snapshot(terminal, lambda state: state['jumps'] == 2)

        before = self.snapshot(terminal)
        terminal.send(key(ord('r')))
        reset = self.snapshot(terminal, lambda state: state['resets'] > before['resets'])
        terminal.send(key(ord('r'), 2) * 5)
        repeated = self.snapshot(terminal, lambda state: state['elapsed'] > reset['elapsed'] + 0.06)
        self.assertEqual(repeated['resets'], reset['resets'])
        self.assertEqual(repeated['jumps'], 0)

        terminal.send(key(ord('?')))
        opened = self.snapshot(terminal, lambda state: state['help'])
        terminal.send(key(ord('?'), 2) * 5)
        repeated = self.snapshot(terminal, after=opened['frame'] + 3)
        self.assertTrue(repeated['help'])
        terminal.send(key(ord('?'), 3) + key(ord('?')))
        self.snapshot(terminal, lambda state: not state['help'])
        self.finish_game(terminal)

    def test_free_play_wall_climb_continues_while_held_and_stops_on_release(self):
        # Keep actual free-play physics and controls; the renderer merely
        # records its state, as the Tower observer does in the other cases.
        observer = OBSERVER.replace('ui._draw_tower', 'ui._draw')
        observer = observer.replace("world.elapsed", "world.round_elapsed")
        observer = observer.replace("jumps=world.jump_count", "jumps=world.player.jumps, grip=world.grip_surface")
        observer = observer.replace("['--file', str(source), '--tower']", "['--file', str(source)]")
        terminal = self.open_game(observer=observer, rows=50, columns=80, title=b'TERMINAL SMASH')
        self.snapshot(terminal, lambda state: state['grounded'])
        terminal.send(arrow(-1))
        attached = self.snapshot(terminal, lambda state: state['grip'] == 'left')
        terminal.send(arrow(-1, 3) + key(ord('w')))
        climbing = self.snapshot(terminal, lambda state: state['y'] < attached['y'] - 0.1)
        initial = climbing
        while climbing['elapsed'] - initial['elapsed'] < 0.65:
            state = self.snapshot(terminal, after=climbing['frame'])
            self.assertEqual(state['grip'], 'left')
            self.assertLess(state['y'], climbing['y'])
            climbing = state
        self.assertGreater(initial['y'] - climbing['y'], 9)
        terminal.send(key(ord('w'), 3))
        stopped = self.snapshot(terminal, after=climbing['frame'] + 1)
        end = self.snapshot(terminal, lambda state: state['elapsed'] > stopped['elapsed'] + 0.12)
        self.assertEqual(end['y'], stopped['y'])
        self.assertEqual(end['grip'], 'left')
        self.finish_game(terminal)

    def test_render_exception_restores_keyboard_modes_and_terminal_settings(self):
        terminal = self.open_game()
        terminal.send(arrow(1))
        self.snapshot(terminal, lambda state: state['vx'] > 0)
        (Path(terminal.state_directory.name) / 'raise-render-error').touch()
        self.assertNotEqual(terminal.finish(), 0)
        self.assertIn(b'intentional keyboard cleanup fixture', bytes(terminal.output))
        self.assert_restored(terminal)
