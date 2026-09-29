"""Bounded terminal input parsing and opt-in Kitty keyboard negotiation.

Protocol reference: https://sw.kovidgoyal.net/kitty/keyboard-protocol/
The reader owns its terminal modes only within its context manager.
"""
from __future__ import annotations

import curses
from dataclasses import dataclass
import os
import re
import sys
import time


@dataclass(frozen=True)
class KeyboardEvent:
    key: int
    kind: str = 'press'
    enhanced: bool = False


_LETTERS = dict(A=curses.KEY_UP, B=curses.KEY_DOWN, C=curses.KEY_RIGHT,
                D=curses.KEY_LEFT, H=curses.KEY_HOME, F=curses.KEY_END,
                P=curses.KEY_F1, Q=curses.KEY_F2, S=curses.KEY_F4)
_TILDES = {1: curses.KEY_HOME, 2: curses.KEY_IC, 3: curses.KEY_DC,
           4: curses.KEY_END, 5: curses.KEY_PPAGE, 6: curses.KEY_NPAGE,
           7: curses.KEY_HOME, 8: curses.KEY_END}
_TILDES.update(zip((11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 23, 24),
                  range(curses.KEY_F1, curses.KEY_F1 + 12)))
_KEYPAD = dict(zip(range(57417, 57427),
                   (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_UP, curses.KEY_DOWN,
                    curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END,
                    curses.KEY_IC, curses.KEY_DC)))
_KINDS = {1: 'press', 2: 'repeat', 3: 'release'}
_SHIFT = dict(zip('`1234567890-=[]\\;,./\'', '~!@#$%^&*()_+{}|:<>?"'))


class KeyboardParser:
    ESCAPE_DELAY = 0.025
    MAX_SEQUENCE = 128

    def __init__(self):
        self.enhanced = False
        self.state = 'normal'
        self.buffer = bytearray()
        self.escape_at = 0.0
        self._string_escape = False
        self._paste_tail = b''
        self._responses: list[int] = []
        self.device_attributes_count = 0

    def drain_responses(self) -> list[int]:
        result, self._responses = self._responses, []
        return result

    def expire(self, now: float) -> list[KeyboardEvent]:
        if self.state == 'escape' and now - self.escape_at >= self.ESCAPE_DELAY:
            self.state = 'normal'
            return [] if self.enhanced else [KeyboardEvent(27)]
        return []

    def feed(self, data: bytes, now: float) -> list[KeyboardEvent]:
        events = self.expire(now)
        for byte in data:
            if self.state == 'paste':
                self._paste_tail = (self._paste_tail + bytes((byte,)))[-6:]
                if self._paste_tail == b'\x1b[201~':
                    self.state = 'normal'
                    self._paste_tail = b''
                continue
            if self.state == 'string':
                if byte == 7 or (self._string_escape and byte == 92):
                    self.state = 'normal'
                self._string_escape = byte == 27
                continue
            if byte == 27:
                if self.state == 'escape' and not self.enhanced:
                    events.append(KeyboardEvent(27))
                self.state = 'escape'
                self.escape_at = now
                self.buffer.clear()
                continue
            if self.state == 'normal':
                # In report-all mode, raw text is paste/IME input, not a key.
                # UTF-8 continuation bytes can never contain ASCII controls.
                if not self.enhanced and byte < 128:
                    events.append(KeyboardEvent(byte))
            elif self.state == 'escape':
                self.state = {91: 'csi', 79: 'ss3', 93: 'string',
                              80: 'string', 94: 'string', 95: 'string'}.get(byte, 'normal')
                self._string_escape = False
                # Unknown ESC/Alt sequences are consumed, never replayed as keys.
            elif self.state in ('csi', 'ss3', 'discard'):
                if 0x40 <= byte <= 0x7e:
                    state, self.state = self.state, 'normal'
                    if state != 'discard':
                        event = self._sequence(bytes(self.buffer), chr(byte), state)
                        if event is not None:
                            events.append(event)
                    self.buffer.clear()
                elif 0x20 <= byte <= 0x3f and len(self.buffer) < self.MAX_SEQUENCE:
                    if self.state != 'discard':
                        self.buffer.append(byte)
                else:
                    self.state = 'discard'
                    self.buffer.clear()
        return events

    def _sequence(self, raw: bytes, final: str, state: str) -> KeyboardEvent | None:
        body = raw.decode('ascii')
        if state == 'ss3':
            key = {**_LETTERS, 'R': curses.KEY_F3}.get(final)
            return KeyboardEvent(key) if not body and key is not None else None
        if final == 'u' and re.fullmatch(r'\?\d{1,9}', body):
            if len(self._responses) < 16:
                self._responses.append(int(body[1:]))
            return None
        if final == 'c' and re.fullmatch(r'\?[\d;]*', body):
            self.device_attributes_count += 1
            return None
        if not body and final in ('I', 'O'):
            return KeyboardEvent(0, 'focus_in' if final == 'I' else 'focus_out')
        if final == '~' and body in ('200', '201'):
            if body == '200':
                self.state = 'paste'
                self._paste_tail = b''
            return None
        if not re.fullmatch(r'[\d:;]*', body):
            return None
        fields = body.split(';')
        if len(fields) > 3:
            return None
        try:
            codes = fields[0].split(':')
            number = int(codes[0] or '1')
            modifiers = fields[1].split(':') if len(fields) >= 2 else ['1']
            if len(modifiers) > 2:
                return None
            mods = int(modifiers[0] or '1') - 1
            kind = _KINDS[int(modifiers[1])] if len(modifiers) == 2 else 'press'
            if mods < 0 or mods > 255:
                return None
            enhanced = self.enhanced or final == 'u' or len(modifiers) == 2
            if final == 'u':
                key = _KEYPAD.get(number, number)
                if number == 0 or (number >= 128 and number not in _KEYPAD):
                    return None
                # Release identifies the physical base key even if modifiers
                # changed since its press (for example D, then Ctrl, then D-up).
                if kind != 'release' and mods & 4 and 97 <= key <= 122:
                    key -= 96
                elif kind != 'release' and mods & 1 and key < 128:
                    key = ord(_SHIFT.get(chr(key), chr(key)))
            elif final == '~':
                key = _TILDES.get(number)
            elif final in _LETTERS and number == 1:
                key = _LETTERS[final]
            else:
                return None
            # Alt/Super combinations belong to terminal/desktop shortcuts.
            if key is None or (kind != 'release' and mods & (2 | 8 | 16 | 32)):
                return None
            return KeyboardEvent(key, kind, enhanced)
        except (ValueError, KeyError):
            return None


def _write_control(sequence: str) -> None:
    sys.stdout.write(sequence)
    sys.stdout.flush()


class KeyboardReader:
    REQUIRED_FLAGS = 1 | 2 | 8
    NEGOTIATION_TIMEOUT = 0.5

    def __init__(self, win, *, clock=time.monotonic, write=None, environ=None):
        self.win = win
        self.clock = clock
        self.write = write or _write_control
        self.environ = os.environ if environ is None else environ
        self.parser = KeyboardParser()
        self.enhanced = False
        self.status = 'legacy'
        self._pushed = False
        self._open = False
        self._deadline = 0.0
        self._ignore_da = 0
        self._da_count = 0
        self._keypad_before = True

    def __enter__(self):
        self._keypad_before = self.win.is_keypad() if hasattr(self.win, 'is_keypad') else True
        self.win.keypad(False)
        self.win.nodelay(True)
        self._open = True
        try:
            self.write('\x1b[?1004h\x1b[?2004h')
            if self.environ.get('TMUX') or self.environ.get('STY'):
                self.status = 'multiplexer'
            else:
                self.status = 'querying'
                self._deadline = self.clock() + self.NEGOTIATION_TIMEOUT
                self.write('\x1b[?u\x1b[c')
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def _fallback(self):
        self.enhanced = self.parser.enhanced = False
        self.status = 'legacy'
        if self._pushed:
            self._pushed = False
            self.write('\x1b[<u')

    def _responses(self, now):
        for flags in self.parser.drain_responses():
            if self.status == 'querying':
                self.status = 'confirming'
                self._ignore_da = 1
                self._pushed = True
                self._deadline = now + self.NEGOTIATION_TIMEOUT
                self.write('\x1b[>11u\x1b[?u\x1b[c')
            elif self.status == 'confirming':
                if flags & self.REQUIRED_FLAGS == self.REQUIRED_FLAGS:
                    self.enhanced = self.parser.enhanced = True
                    self.status = 'enhanced'
                else:
                    self._fallback()
        while self._da_count < self.parser.device_attributes_count:
            self._da_count += 1
            if self.status == 'confirming' and self._ignore_da:
                self._ignore_da -= 1
            elif self.status in ('querying', 'confirming'):
                self._fallback()

    def poll(self) -> list[KeyboardEvent]:
        events = []
        now = self.clock()
        if self.status in ('querying', 'confirming') and now >= self._deadline:
            self._fallback()
        for _ in range(4096):
            key = self.win.getch()
            if key == -1:
                break
            if key == curses.KEY_RESIZE:
                events.append(KeyboardEvent(key, 'resize'))
            elif 0 <= key <= 255:
                parsed = self.parser.feed(bytes((key,)), now)
                self._responses(now)
                events.extend(event for event in parsed if not event.enhanced or self.enhanced)
        events.extend(self.parser.expire(now))
        return events

    def close(self):
        if not self._open:
            return
        self._open = False
        sequence = ('\x1b[<u' if self._pushed else '') + '\x1b[?1004l\x1b[?2004l'
        self._pushed = False
        self.enhanced = self.parser.enhanced = False
        try:
            self.write(sequence)
        finally:
            self.win.keypad(self._keypad_before)
