"""Curses renderer. All captured bytes are parsed as data, never replayed."""
from __future__ import annotations

import curses
import locale
import math
import select
import sys
import time

from .capture import Style, parse_capture
from .model import World


FRAME_INTERVAL = 1 / 90


class Palette:
    def __init__(self):
        self.pairs: dict[tuple[int, int], int] = {}
        self.attributes: dict[Style, int] = {}
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
        if style in self.attributes:
            return self.attributes[style]
        result = self._attribute(style)
        if len(self.attributes) < 4096:
            self.attributes[style] = result
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


def _help(win, palette: Palette) -> None:
    rows, cols = win.getmaxyx()
    lines = [
        "TERMINAL SMASH",
        "",
        "A / D or arrows   move",
        "W / up / space    jump (twice!)",
        "J                 punch text",
        "K                 blast",
        "S / down          drop through a line",
        "R                 rebuild the snapshot",
        "T / Home          return to the top",
        "?                 close this help",
        "Esc / Q           return to terminal",
        "",
        "Only a copy of the screen is destroyed.",
        "The underlying session keeps running.",
    ]
    width = min(48, cols - 2)
    height = min(len(lines) + 2, rows)
    left, top = (cols - width) // 2, (rows - height) // 2
    attr = palette.attr(Style(fg=15, bg=17))
    for row in range(height):
        _put(win, top + row, left, " " * width, attr)
    for i, line in enumerate(lines[:height - 2]):
        _put(win, top + 1 + i, left + 2, line[:width - 4], attr | (curses.A_BOLD if i == 0 else 0))


class TerrainLayer:
    """Keep the stationary text in a curses window; copy it in native code."""

    def __init__(self):
        self.window = None
        self.dimensions = None
        self.world = None
        self.cells = None
        self.destroyed = -1

    def blit(self, win, world: World, palette: Palette) -> None:
        dimensions = win.getmaxyx()
        if self.window is None or self.dimensions != dimensions:
            self.window = curses.newwin(*dimensions)
            self.dimensions = dimensions
            self.world = None
        if self.world is not world or self.cells is not world.cells or self.destroyed != world.destroyed:
            self.window.erase()
            rows, cols = dimensions
            for cell in world.cells.values():
                y = cell.y + 2
                if 0 <= cell.x and cell.x + cell.width <= cols and 2 <= y < rows - 2:
                    _put(self.window, y, cell.x, cell.char, palette.attr(cell.style))
            self.world = world
            self.cells = world.cells
            self.destroyed = world.destroyed
        # overwrite copies spaces as well, erasing the previous frame's effects.
        self.window.overwrite(win)


def _draw(win, world: World, palette: Palette, label: str, help_open: bool,
          terrain: TerrainLayer | None = None) -> None:
    if terrain is None:
        terrain = TerrainLayer()
    terrain.blit(win, world, palette)
    rows, cols = win.getmaxyx()
    cyan = palette.attr(Style(fg=51, bold=True))
    dim = palette.attr(Style(fg=244))
    yellow = palette.attr(Style(fg=220, bold=True))
    _put(win, 0, 0, " TERMINAL SMASH ", palette.attr(Style(fg=16, bg=51, bold=True)))
    _put(win, 0, 17, label, dim)
    ratio = world.destroyed * 100 // world.total if world.total else 0
    score = f" {world.destroyed}/{world.total} smashed  {ratio}% "
    if cols >= len(score) + 30:
        _put(win, 0, cols - len(score), score, yellow)
    _put(win, 1, 0, "-" * cols, dim)
    for particle in world.particles:
        x, y = round(particle.x), round(particle.y) + 2
        if 0 <= x and x + particle.width <= cols and 2 <= y < rows - 2:
            _put(win, y, x, particle.char, palette.attr(particle.style))
    for wave in world.waves:
        radius = wave.age * 64
        for i in range(32):
            angle = i * math.tau / 32
            x = round(wave.x + math.cos(angle) * radius)
            y = round(wave.y + math.sin(angle) * radius * 0.45) + 2
            if 0 <= x < cols and 2 <= y < rows - 2:
                _put(win, y, x, "." if wave.age > 0.14 else "*", yellow)
    p = world.player
    x, y = round(p.x), round(p.y) + 2
    punching = world.time < world.attack_until
    attr = yellow if punching else cyan
    _put(win, y - 2, x, "O", attr)
    torso = "-|=>" if p.facing > 0 else "<=|-"
    if not punching:
        torso = "/|\\"
    _put(win, y - 1, max(0, x - (2 if punching and p.facing < 0 else 1)), torso, attr)
    legs = "/ \\" if p.grounded else "< >"
    if p.grounded and abs(p.vx) > 2 and int(world.time * 12) % 2:
        legs = " | "
    _put(win, y, x - 1, legs, attr)
    _put(win, rows - 3, 0, "_" * cols, dim)
    if world.total and not world.cells:
        _put(win, max(3, rows // 2), max(0, (cols - 38) // 2), "ALL SMASHED!  R rebuild / Esc return", yellow)
    elif not world.total:
        _put(win, max(3, rows // 2), max(0, (cols - 40) // 2), "Empty snapshot. Esc, run a command, retry.", dim)
    hint = " A/D move  W/SPACE jump  J hit  K blast  S drop  R reset  ? help  ESC return "
    if cols < len(hint):
        hint = " A/D move  SPACE jump  J/K smash  ? help  ESC "
    _put(win, rows - 2, 0, hint, cyan)
    note = " COPY OF SCREEN | T/Home return to top | "
    if world.time < world.next_blast:
        note += "blast recharging"
    else:
        note += "blast ready [K]"
    _put(win, rows - 1, 0, note, dim)
    if help_open:
        _help(win, palette)
    win.noutrefresh()
    curses.doupdate()


def _main(win, text: str, label: str) -> None:
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
    dimensions = None
    help_open = False
    last = time.monotonic()
    # Labels from filenames are untrusted too; omit terminal control characters.
    label = "".join(c for c in label if c.isprintable())
    while True:
        start = time.monotonic()
        rows, cols = win.getmaxyx()
        playable = rows >= 14 and cols >= 44
        if playable and dimensions != (rows, cols):
            dimensions = (rows, cols)
            # Keep recent output when the HUD leaves less room than the source pane.
            content_rows = text.rstrip("\r\n").count("\n") + 1
            start_row = max(0, content_rows - (rows - 5))
            world = World(parse_capture(text, cols, rows - 5, start_row=start_row), cols, rows - 4)
        for _ in range(256):
            key = win.getch()
            if key == -1:
                break
            if key in (27, ord("q"), ord("Q"), 3):
                return
            if key == ord("?"):
                help_open = not help_open
            if not playable or help_open or world is None:
                continue
            if key in (ord("a"), ord("A"), curses.KEY_LEFT):
                world.move(-1)
            elif key in (ord("d"), ord("D"), curses.KEY_RIGHT):
                world.move(1)
            elif key in (ord("w"), ord("W"), ord(" "), curses.KEY_UP):
                world.jump()
            elif key in (ord("s"), ord("S"), curses.KEY_DOWN):
                world.drop()
            elif key in (ord("j"), ord("J")):
                world.punch()
            elif key in (ord("k"), ord("K")):
                world.blast()
            elif key in (ord("t"), ord("T"), curses.KEY_HOME):
                world.return_to_top()
            elif key in (ord("r"), ord("R")):
                world.reset()
        dt = start - last
        last = start
        if not playable:
            win.erase()
            _put(win, 0, 0, "Resize to at least 44 x 14. Esc exits.")
            win.refresh()
        elif world is not None:
            if not help_open:
                world.update(dt)
            _draw(win, world, palette, label, help_open, terrain)
        # Wait for the next frame, but wake immediately when a key arrives.
        # Terminal input has no release events; the model uses short movement pulses.
        wait = max(0, FRAME_INTERVAL - (time.monotonic() - start))
        select.select([sys.stdin], [], [], wait)


def run(text: str, label: str = "terminal") -> None:
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(_main, text, label)
    except KeyboardInterrupt:
        pass
