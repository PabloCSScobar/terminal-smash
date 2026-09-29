"""Protocol bytes, fragmented terminal input, negotiation and mode cleanup."""
from collections import deque
import curses
import unittest

from terminal_smash.keyboard import KeyboardEvent, KeyboardParser, KeyboardReader


class KeyboardParserTests(unittest.TestCase):
    def test_legacy_ascii_arrows_ss3_and_page_keys(self):
        parser = KeyboardParser()
        events = parser.feed(b'a\x1b[A\x1bOD\x1b[6~\x1bOH', 0)
        self.assertEqual([e.key for e in events],
                         [ord('a'), curses.KEY_UP, curses.KEY_LEFT, curses.KEY_NPAGE, curses.KEY_HOME])
        self.assertTrue(all(e.kind == 'press' and not e.enhanced for e in events))

    def test_kitty_press_repeat_release_arrows_and_function_keys(self):
        parser = KeyboardParser()
        events = parser.feed(b'\x1b[97;1:1u\x1b[97;1:2u\x1b[97;1:3u'
                             b'\x1b[1;1:3D\x1b[5;1:2~', 0)
        self.assertEqual(events, [KeyboardEvent(97, 'press', True),
                                  KeyboardEvent(97, 'repeat', True),
                                  KeyboardEvent(97, 'release', True),
                                  KeyboardEvent(curses.KEY_LEFT, 'release', True),
                                  KeyboardEvent(curses.KEY_PPAGE, 'repeat', True)])

    def test_protocol_supports_split_at_every_byte_boundary(self):
        sequence = b'\x1b[1;1:3C\x1b[97;1:1u'
        expected = KeyboardParser().feed(sequence, 0)
        for split in range(len(sequence) + 1):
            with self.subTest(split=split):
                parser = KeyboardParser()
                self.assertEqual(parser.feed(sequence[:split], 0) + parser.feed(sequence[split:], 0.01),
                                 expected)

    def test_standalone_escape_waits_but_partial_csi_does_not_become_escape(self):
        parser = KeyboardParser()
        self.assertEqual(parser.feed(b'\x1b', 0), [])
        self.assertEqual(parser.expire(0.024), [])
        self.assertEqual(parser.expire(0.026), [KeyboardEvent(27)])
        self.assertEqual(parser.expire(1), [])
        self.assertEqual(parser.feed(b'\x1b[1;', 1), [])
        self.assertEqual(parser.expire(1.3), [])
        self.assertEqual(parser.feed(b'1:3D', 1.31), [KeyboardEvent(curses.KEY_LEFT, 'release', True)])

    def test_focus_query_replies_unknown_sequences_are_not_game_keys(self):
        parser = KeyboardParser()
        events = parser.feed(b'\x1b[?0u\x1b[?1;2c\x1b[?2027h\x1b[42;9R'
                             b'\x1b]0;aqRdw\x07\x1bPaqRw\x1b\\\x1b[I\x1b[O', 0)
        self.assertEqual(events, [KeyboardEvent(0, 'focus_in'), KeyboardEvent(0, 'focus_out')])
        self.assertEqual(parser.drain_responses(), [0])
        self.assertEqual(parser.device_attributes_count, 1)

    def test_bounded_buffer_and_fragmented_bracketed_paste(self):
        parser = KeyboardParser()
        self.assertEqual(parser.feed(b'\x1b[' + b'9' * 10000, 0), [])
        self.assertLessEqual(len(parser.buffer), parser.MAX_SEQUENCE)
        self.assertEqual(parser.feed(b'u\x1b[200~aqRdw\x1b[1;1:1D' + b'x' * 20000, 0.1), [])
        self.assertLessEqual(len(parser.buffer), parser.MAX_SEQUENCE)
        self.assertEqual(parser.feed(b'\x1b[20', 0.2), [])
        self.assertEqual(parser.feed(b'1~d', 0.3), [KeyboardEvent(ord('d'))])

    def test_modifiers_help_control_c_and_alternate_key_fields(self):
        parser = KeyboardParser()
        events = parser.feed(b'\x1b[47;2u\x1b[99;5u\x1b[97:65;2:1u'
                             b'\x1b[97;3u\x1b[0;;97:100u', 0)
        self.assertEqual([e.key for e in events], [ord('?'), 3, ord('a')])
        self.assertTrue(all(e.enhanced for e in events))

    def test_report_all_ignores_unframed_text_and_marks_plain_csi_press(self):
        parser = KeyboardParser()
        parser.enhanced = True
        self.assertEqual(parser.feed(b'aqRdw\x1b[C', 0), [KeyboardEvent(curses.KEY_RIGHT, 'press', True)])

    def test_release_keeps_base_key_when_modifiers_changed_since_press(self):
        from terminal_smash.input import HeldHorizontalInput

        parser = KeyboardParser()
        held = HeldHorizontalInput()
        for modifiers in (2, 3, 5, 9, 17, 33):
            with self.subTest(modifiers=modifiers):
                press = parser.feed(b'\x1b[100;1:1u', 0)[0]
                held.press(press.key, 1)
                release = parser.feed(f'\x1b[100;{modifiers}:3u'.encode(), 0.1)[0]
                self.assertEqual(release.key, press.key)
                held.release(release.key)
                self.assertEqual(held.direction, 0)
                arrow = parser.feed(f'\x1b[1;{modifiers}:3C'.encode(), 0.1)[0]
                self.assertEqual(arrow.key, curses.KEY_RIGHT)
        self.assertEqual(parser.feed(b'\x1b[100;5:1u', 0), [KeyboardEvent(4, 'press', True)])


class FakeWindow:
    def __init__(self):
        self.keys = deque()
        self.keypad_enabled = True

    def send(self, value):
        self.keys.extend(value)

    def getch(self):
        return self.keys.popleft() if self.keys else -1

    def is_keypad(self):
        return self.keypad_enabled

    def keypad(self, enabled):
        self.keypad_enabled = enabled

    def nodelay(self, enabled):
        self.nonblocking = enabled


class KeyboardReaderTests(unittest.TestCase):
    def setUp(self):
        self.win = FakeWindow()
        self.output = []
        self.now = 0.0
        self.reader = KeyboardReader(self.win, clock=lambda: self.now,
                                     write=self.output.append, environ={})

    def confirm(self):
        self.win.send(b'\x1b[?0u\x1b[?1;2c')
        self.assertEqual(self.reader.poll(), [])
        self.assertEqual(self.reader.status, 'confirming')
        self.assertFalse(self.reader.enhanced)
        self.assertIn('\x1b[>11u', ''.join(self.output))
        self.win.send(b'\x1b[?11u\x1b[?1;2c')
        self.assertEqual(self.reader.poll(), [])
        self.assertTrue(self.reader.enhanced)

    def test_two_stage_confirm_and_cleanup_restores_modes_after_error(self):
        with self.assertRaisesRegex(RuntimeError, 'game failed'):
            with self.reader:
                self.assertFalse(self.win.keypad_enabled)
                self.confirm()
                self.win.send(b'\x1b[1;1:1D\x1b[1;1:3D')
                self.assertEqual([e.kind for e in self.reader.poll()], ['press', 'release'])
                raise RuntimeError('game failed')
        self.assertTrue(self.win.keypad_enabled)
        output = ''.join(self.output)
        self.assertEqual(output.count('\x1b[>11u'), 1)
        self.assertEqual(output.count('\x1b[<u'), 1)
        self.assertTrue(output.endswith('\x1b[?1004l\x1b[?2004l'))

    def test_query_timeout_and_late_reply_never_enable_hold(self):
        with self.reader:
            self.now = 0.51
            self.assertEqual(self.reader.poll(), [])
            self.assertEqual(self.reader.status, 'legacy')
            self.win.send(b'\x1b[?11u\x1b[97;1:1ud')
            self.assertEqual(self.reader.poll(), [KeyboardEvent(ord('d'))])
            self.assertFalse(self.reader.enhanced)
        self.assertNotIn('\x1b[<u', ''.join(self.output))

    def test_missing_flags_and_confirmation_timeout_pop_only_our_push(self):
        for flags in (1, 3, 9, None):
            with self.subTest(flags=flags):
                self.setUp()
                with self.reader:
                    self.win.send(b'\x1b[?0u\x1b[?1;2c')
                    self.reader.poll()
                    if flags is None:
                        self.now = 0.51
                    else:
                        self.win.send(f'\x1b[?{flags}u'.encode())
                    self.reader.poll()
                    self.assertFalse(self.reader.enhanced)
                    self.assertEqual(self.reader.status, 'legacy')
                self.assertEqual(''.join(self.output).count('\x1b[<u'), 1)

    def test_device_attributes_without_support_short_circuits_negotiation(self):
        with self.reader:
            self.win.send(b'\x1b[?1;2c')
            self.reader.poll()
            self.assertEqual(self.reader.status, 'legacy')
        self.assertNotIn('\x1b[>11u', ''.join(self.output))

    def test_mux_skips_keyboard_negotiation_but_keeps_legacy_and_focus(self):
        for variable in ('TMUX', 'STY'):
            with self.subTest(variable=variable):
                output = []
                reader = KeyboardReader(self.win, write=output.append, environ={variable: 'session'})
                with reader:
                    self.assertEqual(reader.status, 'multiplexer')
                    self.win.send(b'd\x1b[D\x1b[O')
                    self.assertEqual([e.kind for e in reader.poll()], ['press', 'press', 'focus_out'])
                self.assertNotIn('\x1b[?u', ''.join(output))
                self.assertNotIn('\x1b[<u', ''.join(output))

    def test_resize_and_fragmented_escape_events_survive_polling(self):
        with self.reader:
            self.win.send([curses.KEY_RESIZE, 27])
            self.assertEqual(self.reader.poll(), [KeyboardEvent(curses.KEY_RESIZE, 'resize')])
            self.now = 0.03
            self.assertEqual(self.reader.poll(), [KeyboardEvent(27)])


if __name__ == '__main__':
    unittest.main()
