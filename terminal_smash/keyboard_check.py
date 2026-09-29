"""Interactive protocol check using the same decoder as the game."""
from __future__ import annotations

from collections import deque
import curses
import select
import sys
import time

from .keyboard import KeyboardReader
from .ui import _put, _run_curses


def _check(win):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    history = deque(maxlen=12)
    held = set()
    releases = 0
    started = time.monotonic()
    names = {curses.KEY_LEFT: 'LEFT', curses.KEY_RIGHT: 'RIGHT',
             curses.KEY_UP: 'UP', curses.KEY_DOWN: 'DOWN', ord(' '): 'SPACE',
             ord('a'): 'A', ord('d'): 'D', ord('w'): 'W', ord('s'): 'S'}
    with KeyboardReader(win) as keyboard:
        while True:
            for event in keyboard.poll():
                if event.kind == 'press' and event.key in (27, 3, ord('q'), ord('Q')):
                    return 0
                if event.kind in ('focus_out', 'resize'):
                    held.clear()
                if event.key not in names:
                    continue
                name = names[event.key]
                if event.enhanced and keyboard.enhanced:
                    if event.kind == 'press':
                        held.add(name)
                    elif event.kind == 'release':
                        held.discard(name)
                        releases += 1
                history.append(f'{time.monotonic() - started:6.2f}s  {name:5} {event.kind}')
            win.erase()
            _put(win, 0, 0, 'TERMINAL SMASH - KEYBOARD CHECK', curses.A_BOLD)
            if keyboard.enhanced:
                status = 'Press/release reporting enabled'
            elif keyboard.status == 'multiplexer':
                status = 'Run this check outside tmux/screen.'
            elif keyboard.status in ('querying', 'confirming'):
                status = 'Checking terminal support...'
            else:
                status = 'Legacy input: terminal did not confirm key-release support.'
            _put(win, 2, 0, status)
            _put(win, 4, 0, 'Hold an arrow for a second, release, then tap it.')
            _put(win, 5, 0, 'With precise input, DOWN lasts until the RELEASE event.')
            _put(win, 6, 0, 'DOWN: ' + (', '.join(sorted(held)) or 'none'))
            _put(win, 7, 0, f'Release events received: {releases}')
            for row, line in enumerate(history, 9):
                _put(win, row, 0, line)
            rows, _ = win.getmaxyx()
            _put(win, rows - 1, 0, 'Esc / Q exits. No key history is saved.')
            win.refresh()
            select.select([sys.stdin], [], [], 1 / 60)


def run() -> int:
    return _run_curses(_check) or 0
