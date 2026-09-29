#!/usr/bin/env python3
"""Measure active smash frames using real curses in a drained pseudo-terminal.

Example: python3 tools/benchmark.py --cols 300 --rows 100 --scene cascade --gravity on
Use --repo /path/to/old/checkout with the same script to compare old/new versions.
The scripted blast/dash/slam workload advances at 90 simulated FPS without sleeps.
Reported frame time includes input actions, physics, drawing and curses.doupdate;
PTY bytes quantify terminal traffic. It does not measure a terminal GUI's paint
cost, compositor, tmux forwarding, or input latency. The warmup frame is excluded.
"""
from __future__ import annotations

import argparse
import curses
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import statistics
import struct
import subprocess
import sys
import termios
import time


def child(args):
    sys.path.insert(0, str(args.repo))
    from terminal_smash.capture import Cell, Style
    from terminal_smash.model import World
    from terminal_smash.ui import Palette, TerrainLayer, _draw

    frame_pipe = int(os.environ['SMASH_FRAME_PIPE'])
    ack_pipe = int(os.environ['SMASH_ACK_PIPE'])
    output_elapsed = 0.0
    original_update = curses.doupdate

    def timed_update():
        nonlocal output_elapsed
        start = time.perf_counter()
        original_update()
        output_elapsed = (time.perf_counter() - start) * 1000

    def report(row):
        os.write(frame_pipe, json.dumps(row).encode() + b'\n')
        if not os.read(ack_pipe, 1):
            raise RuntimeError('benchmark reader closed')

    curses.doupdate = timed_update

    def main(win):
        curses.curs_set(0)
        win.nodelay(True)
        palette, terrain = Palette(), TerrainLayer()
        height = args.rows - 4
        cells = [Cell(x, y, chr(97 + (x + y) % 26),
                      Style(fg=(7, 51, 220, 82)[x // 14 % 4]))
                 for y in range(1, height - 1) if args.scene == 'dense' or y % 2 == 0
                 for x in range(args.cols)]
        world = World(cells, args.cols, height, seed=42, falling_enabled=args.gravity == 'on')
        _draw(win, world, palette, 'benchmark warmup', False, terrain)
        report({'init': True, 'cells': len(cells)})
        for frame in range(args.frames):
            before = time.perf_counter()
            if frame % 54 == 0:
                attack = frame // 54
                world.player.x = args.cols * (.2 + .15 * (attack % 5))
                world.player.y = height * (.25 + .13 * (attack % 4))
                world.player.grounded = False
                world.player.facing = 1 if attack % 2 == 0 else -1
                world.blast()
                world.dash()
            if frame % 54 == 20:
                world.jump()
                world.slam()
            action = time.perf_counter()
            world.update(1 / 90)
            stepped = time.perf_counter()
            _draw(win, world, palette, 'benchmark dense damage', False, terrain)
            drawn = time.perf_counter()
            report({'frame': frame, 'total': (drawn - before) * 1000,
                    'action': (action - before) * 1000, 'physics': (stepped - action) * 1000,
                    'draw': (drawn - stepped) * 1000, 'output_ms': output_elapsed,
                    'static': len(world.cells), 'falling': sum(len(c.cells) for c in world.falling),
                    'particles': len(world.particles), 'destroyed': world.destroyed})

    curses.wrapper(main)


def parent(args):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', args.rows, args.cols, 0, 0))
    read_frames, write_frames = os.pipe()
    read_ack, write_ack = os.pipe()
    env = os.environ.copy()
    env.update(TERM='xterm-256color', LC_ALL='C.UTF-8', SMASH_FRAME_PIPE=str(write_frames),
               SMASH_ACK_PIPE=str(read_ack))
    for key in ('COLUMNS', 'LINES', 'TMUX', 'TMUX_PANE'):
        env.pop(key, None)
    command = [sys.executable, str(Path(__file__).resolve()), '--child', '--repo', str(args.repo),
               '--rows', str(args.rows), '--cols', str(args.cols), '--scene', args.scene,
               '--frames', str(args.frames), '--gravity', args.gravity]
    proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env,
                            pass_fds=(write_frames, read_ack))
    for descriptor in (slave, write_frames, read_ack):
        os.close(descriptor)
    frames, init = [], {}
    total_bytes = prior_bytes = 0
    buffer = tail = b''

    def drain_terminal():
        nonlocal total_bytes, tail
        try:
            data = os.read(master, 65536)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            data = b''
        total_bytes += len(data)
        tail = (tail + data)[-2000:]
        return bool(data)

    try:
        while True:
            ready, _, _ = select.select([master, read_frames], [], [], 10)
            if not ready:
                raise RuntimeError('benchmark child stalled')
            if master in ready:
                drain_terminal()
            if read_frames not in ready:
                continue
            data = os.read(read_frames, 65536)
            if not data:
                break
            buffer += data
            while b'\n' in buffer:
                item, buffer = buffer.split(b'\n', 1)
                # The child waits for acknowledgement, so each byte belongs to
                # this completed frame rather than spilling into the next one.
                while select.select([master], [], [], 0)[0]:
                    if not drain_terminal():
                        break
                row = json.loads(item)
                row['bytes'] = total_bytes - prior_bytes
                prior_bytes = total_bytes
                if row.get('init'):
                    init = row
                else:
                    frames.append(row)
                os.write(write_ack, b'.')
        proc.wait(timeout=5)
        if proc.returncode:
            raise RuntimeError(tail.decode(errors='replace'))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for descriptor in (master, read_frames, write_ack):
            os.close(descriptor)

    def stats(key):
        values = sorted(frame[key] for frame in frames)
        return {'median': round(statistics.median(values), 3),
                'p95': round(values[int(.95 * (len(values) - 1))], 3),
                'max': round(max(values), 3)}

    if len(frames) != args.frames:
        raise RuntimeError(f'expected {args.frames} frames, received {len(frames)}')
    result = {'repo': str(args.repo), 'size': f'{args.cols}x{args.rows}', 'scene': args.scene,
              'gravity': args.gravity, 'frames': len(frames), 'initial_cells': init['cells'],
              **{key: stats(key) for key in ('total', 'action', 'physics', 'draw', 'output_ms', 'bytes')},
              'bytes_per_second_at90': round(sum(row['bytes'] for row in frames) / len(frames) * 90),
              'peak_falling': max(row['falling'] for row in frames),
              'peak_particles': max(row['particles'] for row in frames),
              'worst': sorted(frames, key=lambda row: row['total'], reverse=True)[:3]}
    print(json.dumps(result))
    if args.output:
        args.output.write_text(json.dumps({'result': result, 'frames': frames}, indent=2) + '\n')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--rows', type=int, default=100)
    parser.add_argument('--cols', type=int, default=300)
    parser.add_argument('--scene', choices=['dense', 'cascade'], default='dense')
    parser.add_argument('--gravity', choices=['on', 'off'], default='off')
    parser.add_argument('--frames', type=int, default=360)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rows < 14 or args.cols < 44 or args.rows > 500 or args.cols > 1000:
        parser.error('size must be 44..1000 columns and 14..500 rows')
    if not 1 <= args.frames <= 10000:
        parser.error('frames must be between 1 and 10000')
    args.repo = args.repo.resolve()
    return args


if __name__ == '__main__':
    options = arguments()
    child(options) if options.child else parent(options)
