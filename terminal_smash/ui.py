"""Curses renderer. All captured bytes are parsed as data, never replayed."""
from __future__ import annotations

import curses
import locale
import math
import select
import sys
import time

from .capture import Style, parse_capture
from .demo import build_demo
from .model import World
from .records import arena_key, load_best, save_best


FRAME_INTERVAL = 1 / 90
ROUND_DURATION = 30.0


class Palette:
    def __init__(self):
        self.pairs: dict[tuple[int, int], int] = {}
        self.attributes: dict[tuple[int | None, int | None, bool], int] = {}
        self.enabled = curses.has_colors()
        self.default = False
        if self.enabled:
            curses.start_color()
            try:
                curses.use_default_colors()
                self.default = True
            except curses.error:
                pass

    def attr(self, style: Style) -> int:
        # Primitive tuple keys avoid Python dataclass hashing/equality for
        # every flying letter, and a single lookup also caches attribute zero.
        key = (style.fg, style.bg, style.bold)
        result = self.attributes.get(key)
        if result is not None:
            return result
        result = self._attribute(style)
        if len(self.attributes) < 4096:
            self.attributes[key] = result
        return result

    def _attribute(self, style: Style) -> int:
        attr = curses.A_BOLD if style.bold else 0
        if not self.enabled:
            return attr
        def color(value: int | None, background: bool = False) -> int:
            if value is None:
                return -1 if self.default else (0 if background else 7)
            if value < curses.COLORS:
                return value
            if curses.COLORS >= 8:
                # A small, readable fallback for terminals with only 8/16 colors.
                if value >= 232:
                    return 7 if value >= 244 else 0
                if value >= 16:
                    n = value - 16
                    r, g, b = n // 36, n // 6 % 6, n % 6
                    return (1 if r >= 3 else 0) + (2 if g >= 3 else 0) + (4 if b >= 3 else 0)
                return value % 8
            return 0
        pair = (color(style.fg), color(style.bg, True))
        if pair == (-1, -1):
            return attr
        if pair not in self.pairs:
            index = len(self.pairs) + 1
            if index >= min(curses.COLOR_PAIRS, 32767):
                return attr
            try:
                curses.init_pair(index, *pair)
                self.pairs[pair] = index
            except curses.error:
                return attr
        return attr | curses.color_pair(self.pairs[pair])


def _put(win, y: int, x: int, text: str, attr: int = 0) -> None:
    rows, cols = win.getmaxyx()
    if not (0 <= y < rows and 0 <= x < cols) or not text:
        return
    try:
        win.addstr(y, x, text[:cols - x], attr)
    except curses.error:
        # Writing the bottom-right cell reports ERR after drawing on ncurses.
        pass


HELP_LINES = [
    'A / D or arrows   run / steer in the air',
    'W / up            jump / climb up a wall',
    'Space             jump / release wall or ceiling',
    'Walls and ceiling grip automatically at the edge.',
    'J                 punch',
    'K                 blast',
    'L                 dash through text',
    'X                 aerial ground slam',
    'S / down          descend wall / drop / release',
    'T / Home          return to the top',
    'C                 switch FREE / 30s CHALLENGE',
    'G                 falling ON/OFF (restarts)',
    'R                 restart the current mode',
    '?                 close help (game is paused)',
    'Esc / Q           return to terminal',
    '',
    'Chain hits for x2..x5 score.',
    'Gravity ON: loose text falls without explosions.',
    'ERROR bugs chase you. Hit them before they hit you!',
    'CHALLENGE keeps spawning ERRORs until time runs out.',
    'Five health points. No health = game over.',
    '',
    'Only a copy of the screen is destroyed.',
    'The underlying session keeps running.',
]


def _help(win, palette: Palette, page: int = 0) -> None:
    rows, cols = win.getmaxyx()
    capacity = max(1, rows - 6)
    pages = max(1, math.ceil(len(HELP_LINES) / capacity))
    page = page % pages
    lines = HELP_LINES[page * capacity:(page + 1) * capacity]
    width = min(56, cols - 2)
    height = len(lines) + 4
    left, top = (cols - width) // 2, (rows - height) // 2
    attr = palette.attr(Style(fg=15, bg=17))
    for row in range(height):
        _put(win, top + row, left, ' ' * width, attr)
    _put(win, top, left + 2, f'HELP {page + 1}/{pages}  [ ? close ]', attr | curses.A_BOLD)
    for i, line in enumerate(lines):
        _put(win, top + 1 + i, left + 2, line[:width - 4], attr)
    footer = 'PgUp/PgDn or arrows: pages' if pages > 1 else 'Game and timer paused'
    _put(win, top + height - 1, left + 2, footer[:width - 4], attr)


def _text_runs(cells):
    """Batch contiguous cells with the same style into one terminal write."""
    runs = []
    text = []
    row = x = end = 0
    style = None
    for cell in sorted(cells, key=lambda c: (c.y, c.x)):
        if text and (cell.y != row or cell.x != end or cell.style != style):
            runs.append((row, x, ''.join(text), end - x, style))
            text = []
        if not text:
            row, x, end, style = cell.y, cell.x, cell.x, cell.style
        text.append(cell.char)
        end += cell.width
    if text:
        runs.append((row, x, ''.join(text), end - x, style))
    return runs


def _write_run(win, y, x, text, attr):
    # Callers have checked display width; preserve combining marks at the edge.
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


class TerrainLayer:
    """Redraw only damaged rows, batching text instead of writing glyph by glyph."""

    def __init__(self):
        self.window = None
        self.dimensions = None
        self.world = None
        self.cells = None
        self.row_revisions = []
        self.flying = {}

    def blit(self, win, world: World, palette: Palette) -> None:
        dimensions = win.getmaxyx()
        if self.window is None or self.dimensions != dimensions:
            self.window = curses.newwin(*dimensions)
            self.dimensions = dimensions
            self.world = None
        fresh = self.world is not world or self.cells is not world.cells
        if fresh:
            self.window.erase()
            self.row_revisions = [-1] * world.height
            self.flying.clear()
        rows, cols = dimensions
        blank = ' ' * cols
        for row, revision in enumerate(world.terrain_row_revisions):
            if self.row_revisions[row] == revision:
                continue
            y = row + 2
            if 2 <= y < rows - 3:
                if not fresh:
                    _write_run(self.window, y, 0, blank, 0)
                for _, x, text, width, style in _text_runs(world.cells_by_row[row].values()):
                    if 0 <= x and x + width <= cols:
                        _write_run(self.window, y, x, text, palette.attr(style))
            self.row_revisions[row] = revision
        self.world = world
        self.cells = world.cells
        self.window.overwrite(win)
        active = {id(chunk) for chunk in world.falling}
        if len(self.flying) > len(active):
            self.flying = {key: value for key, value in self.flying.items() if key in active}

    def falling_runs(self, chunk):
        key = id(chunk)
        entry = self.flying.get(key)
        if entry is None or entry[0] is not chunk.cells:
            entry = (chunk.cells, _text_runs(chunk.cells))
            self.flying[key] = entry
        return entry[1]


class RoundRecord:
    """Save only completed challenges, once; storage failure must not end play."""

    def __init__(self, world: World):
        self.key = arena_key(list(world.original), world.width, world.height,
                             falling_enabled=world.falling_enabled)
        self.best = 0
        self.saved = False
        self.new_record = False
        self.error = False
        if world.duration is not None:
            try:
                self.best = load_best(self.key)
            except OSError:
                self.error = True

    def finish(self, world: World) -> None:
        if not world.finished or world.duration is None or self.saved:
            return
        self.saved = True
        try:
            previous = self.best
            self.best = save_best(self.key, world.score)
            self.new_record = world.score > previous and world.score == self.best
        except OSError:
            self.error = True


def _actor_pose(world: World) -> list[tuple[int, int, str]]:
    """Small ASCII poses relative to the actor's feet, kept independent of curses."""
    p = world.player
    if world.slamming:
        return [(-1, -2, '\\O/'), (0, -1, '|'), (-1, 0, 'v v')]
    if world.time < world.dash_until:
        return [(-1, -1, '-O=>' if p.facing > 0 else '<=O-'), (-1, 0, '/ >' if p.facing > 0 else '< \\')]
    if world.time < world.hurt_until:
        return [(0, -2, 'o'), (-1, -1, '\\|/'), (-1, 0, '< >')]
    if world.time < world.attack_until:
        if world.time - world.attack_started < 0.035:
            return [(0, -2, 'O'), (-1, -1, '\\| ' if p.facing > 0 else ' |/'), (-1, 0, '/ \\')]
        return [(0, -2, 'O'), (-1 if p.facing > 0 else -3, -1, '-|==>' if p.facing > 0 else '<==|-'), (-1, 0, '/ \\')]
    if world.time < world.landing_until:
        return [(0, -1, 'O'), (-1, 0, '/v\\')]
    if world.grip_surface == 'ceiling':
        return [(-1, -2, '| |'), (0, -1, 'O'), (-1, 0, '/|\\')]
    if world.grip_surface in ('left', 'right'):
        left = world.grip_surface == 'left'
        return [(0, -2, 'O'), (-1, -1, '=|' if left else '|='),
                (-1, 0, '/|' if left else '|\\')]
    if not p.grounded:
        phase = int(max(0, world.time - world.jump_started) * 12) % 4
        if p.jumps >= 2:
            return [
                [(0, -2, 'O'), (-1, -1, '/|\\'), (-1, 0, '< >')],
                [(-1, -1, '<-O'), (0, 0, '/')],
                [(-1, -2, '\\ /'), (0, -1, '|'), (0, 0, 'O')],
                [(-1, -1, 'O->'), (0, 0, '\\')],
            ][phase]
        return [(0, -2, 'O'), (-1, -1, '\\|/' if p.vy < 0 else '/|\\'), (-1, 0, '< >')]
    if abs(p.vx) > 2:
        phase = int(world.time * 18) % 4
        arms = ('/|\\', '\\| ', '/|\\', ' |/')
        legs = ('/ \\', ' | ', '\\ /', ' | ')
        return [(0, -2, 'O'), (-1, -1, arms[phase]), (-1, 0, legs[phase])]
    return [(0, -2, 'o' if int(world.time * 2) % 12 == 0 else 'O'), (-1, -1, '/|\\'), (-1, 0, '/ \\')]


def _combo_color(world: World) -> int:
    return (51, 82, 220, 208, 201)[max(0, min(4, world.multiplier - 1))]


def _round_over(win, world: World, palette: Palette, record: RoundRecord | None) -> None:
    rows, cols = win.getmaxyx()
    width = min(52, cols - 2)
    left, top = (cols - width) // 2, max(2, (rows - 8) // 2)
    attr = palette.attr(Style(fg=15, bg=17, bold=True))
    for row in range(7):
        _put(win, top + row, left, ' ' * width, attr)
    dead = world.finish_reason == 'dead'
    title = 'GAME OVER - NO HEALTH!' if dead else 'ROUND OVER - TIME UP!'
    best = f'Best: {record.best if record else 0}' + ('  NEW RECORD!' if record and record.new_record else '')
    switch = 'free play' if world.duration is not None else 'challenge'
    lines = [title,
             f'Score: {world.score}    Smashed: {world.destroyed}/{world.total}',
             best if world.duration is not None else f'Health: {world.player.hp}/{world.player.max_hp}',
             'Local record unavailable' if record and record.error else 'Your terminal session is still running.',
             f'[R] retry  [C] {switch}  [Esc] return']
    for i, line in enumerate(lines):
        _put(win, top + 1 + i, left + 2, line[:width - 4], attr)


def _draw(win, world: World, palette: Palette, label: str, help_open: bool,
          terrain: TerrainLayer | None = None, record: RoundRecord | None = None,
          help_page: int = 0) -> None:
    if terrain is None:
        terrain = TerrainLayer()
    terrain.blit(win, world, palette)
    rows, cols = win.getmaxyx()
    cyan = palette.attr(Style(fg=51, bold=True))
    dim = palette.attr(Style(fg=244))
    yellow = palette.attr(Style(fg=220, bold=True))
    powered = palette.attr(Style(fg=_combo_color(world), bold=True))

    def scene(y: int, x: int, glyph: str, attr: int, width: int = 1) -> None:
        if 0 <= x and x + width <= cols and 2 <= y < rows - 3:
            _write_run(win, y, x, glyph, attr)

    for trail in world.trails:
        scene(round(trail.y) + 1, round(trail.x), '~', palette.attr(Style(fg=31 if trail.life < 0.1 else 45)))
    for chunk in world.falling:
        for row, x, text, width, style in terrain.falling_runs(chunk):
            scene(round(row + chunk.offset_y) + 2, x, text, palette.attr(style), width)
    for particle in world.particles:
        y, x = round(particle.y) + 2, round(particle.x)
        if 0 <= x and x + particle.width <= cols and 2 <= y < rows - 3:
            _write_run(win, y, x, particle.char, palette.attr(particle.style))
    for wave in world.waves:
        radius = wave.age * 64
        for i in range(32):
            angle = i * math.tau / 32
            x = round(wave.x + math.cos(angle) * radius)
            y = round(wave.y + math.sin(angle) * radius * 0.45) + 2
            scene(y, x, '.' if wave.age > 0.14 else '*', powered)
    for enemy in world.enemies:
        if enemy.hp <= 0:
            continue
        x, y = round(enemy.x), round(enemy.y) + 2
        attr = palette.attr(Style(fg=15 if world.time < enemy.hurt_until else 196, bold=True))
        body = '[' + enemy.label + ']'
        scene(y - 1, max(0, min(cols - len(body), x - len(body) // 2)), body, attr, len(body))
        scene(y, max(0, x - 1), '/ \\' if int(world.time * 10) % 2 else 'v v', attr, 3)
    p = world.player
    actor_attr = palette.attr(Style(fg=196, bold=True)) if world.time < world.hurt_until else powered
    for dx, dy, glyph in _actor_pose(world):
        scene(round(p.y) + dy + 2, max(0, min(cols - len(glyph), round(p.x) + dx)),
              glyph, actor_attr, len(glyph))
    if world.multiplier >= 3:
        for dx in (-3, 3):
            scene(round(p.y) + 1, round(p.x) + dx, '+' if int(world.time * 8) % 2 else '.', powered)

    health_color = 82 if p.hp > 2 else (220 if p.hp > 1 else 196)
    health_attr = palette.attr(Style(fg=health_color, bold=True))

    # Keep health next to the score, leaving captured text clear around the actor.
    score = f'SCORE {world.score}'
    if record and world.duration is not None and cols >= 62:
        with_best = score + f'  BEST {record.best}'
        if len(with_best) + 26 <= cols:
            score = with_best
    score_x = max(0, cols - len(score) - 1)
    health_prefix = f'HP {p.hp}/{p.max_hp} '
    bar_width = max(10, min(20, score_x - len(health_prefix) - 12))
    filled = round(bar_width * p.hp / max(1, p.max_hp))
    health = health_prefix + '[' + '#' * filled + '-' * (bar_width - filled) + ']'
    health_x = max(0, score_x - len(health) - 2)
    title = ' TERMINAL SMASH ' if health_x >= 17 else ' SMASH '
    if len(title) < health_x:
        _put(win, 0, 0, title, palette.attr(Style(fg=16, bg=51, bold=True)))
        label_width = health_x - len(title) - 2
        if cols >= 100 and label_width > 0:
            _put(win, 0, len(title) + 1, label[:label_width], dim)
    _put(win, 0, health_x, health, health_attr)
    _put(win, 0, score_x, score, yellow)
    mode = 'FREE PLAY' if world.duration is None else f'CHALLENGE {world.time_left:04.1f}s'
    _put(win, 1, 0, mode, yellow if world.time_left is not None and world.time_left < 10 else cyan)
    ratio = world.destroyed * 100 // world.total if world.total else 0
    stats = f'x{world.multiplier} COMBO {world.combo}  {ratio}% smashed'
    _put(win, 1, len(mode) + 2, stats, powered)
    if cols >= len(mode) + len(stats) + 16:
        _put(win, 1, cols - 11, f'ERROR {len(world.enemies)}', palette.attr(Style(fg=196)))
    _put(win, rows - 3, 0, '_' * cols, dim)
    if world.finished:
        _round_over(win, world, palette, record)
    elif world.duration is None and world.total and world.cleared:
        _put(win, max(3, rows // 2), max(0, (cols - 37) // 2), 'ALL SMASHED! R rebuild / C challenge', yellow)
    elif not world.total:
        _put(win, max(3, rows // 2), 1, 'Empty snapshot. Esc, run a command, retry.', dim)
    hint = ' A/D run  W climb  SPACE jump  J hit K blast  L dash X slam C 30s ? ESC '
    if cols < len(hint):
        hint = ' A/D W climb SPACE J/K L X C ? ESC '
    _put(win, rows - 2, 0, hint, cyan)
    blast = 'recharging' if world.time < world.next_blast else 'ready'
    dash = 'recharging' if world.time < world.next_dash else 'ready'
    gravity = 'ON' if world.falling_enabled else 'OFF'
    note = f' G falling {gravity} | Grip AUTO | K blast {blast} | T top R reset'
    if cols >= len(note) + len(dash) + 10:
        note += f' | L {dash}'
    if record and record.error:
        note = ' Local record unavailable | gameplay still works | R retry'
    _put(win, rows - 1, 0, note, dim)
    if help_open:
        _help(win, palette, help_page)
    win.noutrefresh()
    curses.doupdate()


def _new_world(text: str, rows: int, cols: int, challenge: bool,
               falling_enabled: bool = False, *, demo: bool = False,
               grip_enabled: bool = True) -> World:
    if demo:
        text = build_demo(cols, rows - 5)
    content_rows = text.rstrip('\r\n').count('\n') + 1
    start_row = max(0, content_rows - (rows - 5))
    cells = parse_capture(text, cols, rows - 5, start_row=start_row)
    world = World(cells, cols, rows - 4, duration=ROUND_DURATION if challenge else None,
                  falling_enabled=falling_enabled)
    world.grip_enabled = grip_enabled
    return world


def _main(win, text: str, label: str, challenge: bool = False,
          falling_enabled: bool = False, demo: bool = False) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.set_escdelay(25)
    win.keypad(True)
    win.nodelay(True)
    palette = Palette()
    terrain = TerrainLayer()
    world = None
    record = None
    dimensions = None
    help_open = False
    help_page = 0
    last = time.monotonic()
    label = ''.join(c for c in label if c.isprintable())
    while True:
        start = time.monotonic()
        rows, cols = win.getmaxyx()
        playable = rows >= 14 and cols >= 44
        rebuilt = False
        if playable and dimensions != (rows, cols):
            dimensions = (rows, cols)
            world = _new_world(text, rows, cols, challenge, falling_enabled, demo=demo)
            record = RoundRecord(world)
            rebuilt = True
        # Advance the old state before accepting new actions, so a key arriving
        # after the deadline cannot score after a slow/stalled terminal frame.
        now = time.monotonic()
        dt = 0.0 if rebuilt else now - last
        last = now
        if playable and world is not None and not help_open:
            world.update(dt)
            record.finish(world)
        for _ in range(256):
            key = win.getch()
            if key == -1:
                break
            if key in (27, ord('q'), ord('Q'), 3):
                return
            if key == ord('?'):
                help_open = not help_open
                help_page = 0
                continue
            if not playable or world is None:
                continue
            if help_open:
                if key in (curses.KEY_NPAGE, curses.KEY_DOWN, curses.KEY_RIGHT, ord(' ')):
                    help_page += 1
                elif key in (curses.KEY_PPAGE, curses.KEY_UP, curses.KEY_LEFT):
                    help_page -= 1
                continue
            if key in (ord('c'), ord('C')):
                challenge = not challenge
                world = _new_world(text, rows, cols, challenge, falling_enabled, demo=demo)
                record = RoundRecord(world)
                rebuilt = True
            elif key in (ord('g'), ord('G')):
                falling_enabled = not falling_enabled
                world = _new_world(text, rows, cols, challenge, falling_enabled, demo=demo)
                record = RoundRecord(world)
                rebuilt = True
            elif key in (ord('r'), ord('R')):
                world.reset()
                record = RoundRecord(world)
                rebuilt = True
            elif key in (ord('a'), ord('A'), curses.KEY_LEFT):
                world.move(-1)
            elif key in (ord('d'), ord('D'), curses.KEY_RIGHT):
                world.move(1)
            elif key in (ord('w'), ord('W'), curses.KEY_UP):
                world.climb_up()
            elif key == ord(' '):
                world.jump()
            elif key in (ord('s'), ord('S'), curses.KEY_DOWN):
                world.climb_down()
            elif key in (ord('j'), ord('J')):
                world.punch()
            elif key in (ord('k'), ord('K')):
                world.blast()
            elif key in (ord('l'), ord('L')):
                world.dash()
            elif key in (ord('x'), ord('X')):
                world.slam()
            elif key in (ord('t'), ord('T'), curses.KEY_HOME):
                world.return_to_top()
            # Save a finished round before another queued key can restart
            # the scene, switch modes or exit in this same input batch.
            record.finish(world)
        if rebuilt:
            last = time.monotonic()
        if not playable:
            win.erase()
            _put(win, 0, 0, 'Resize to at least 44 x 14. Esc exits.')
            win.refresh()
        elif world is not None:
            record.finish(world)
            _draw(win, world, palette, label, help_open, terrain, record, help_page)
        wait = max(0, FRAME_INTERVAL - (time.monotonic() - start))
        select.select([sys.stdin], [], [], wait)


def run(text: str, label: str = 'terminal', *, challenge: bool = False,
        falling_enabled: bool = False, demo: bool = False) -> None:

    locale.setlocale(locale.LC_ALL, '')
    try:
        curses.wrapper(_main, text, label, challenge, falling_enabled, demo)
    except KeyboardInterrupt:
        pass
