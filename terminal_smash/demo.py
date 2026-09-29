"""Responsive, destructible demo scenery with two floor-to-roof jump routes."""

from __future__ import annotations


_LOGS = (
    "build::compile --all [OK]",
    "cache[128] -> render_frame()",
    "git push --force-smash",
    "for (char in screen) { BOOM; }",
    "queue[256]::worker::READY",
    "make clean && make BOOM",
    "physics.step(dt); score += 10;",
    "pipeline::ASCII::ARCADE::ONLINE",
)


def build_tower_demo(width: int) -> str:
    """A finite, colourful shell history for the scrollback climbing mode."""
    width = max(8, width)
    lines = ['\x1b[1;33m$ session started -- welcome to the summit\x1b[0m']
    for row in range(1, 160):
        if 48 <= row < 56 or row % 13 == 0:
            lines.append('')
            continue
        indent = 2 + (row // 6 * 5) % max(1, width - 25)
        fragment = _LOGS[row % len(_LOGS)]
        if row % 17 == 0:
            fragment = '$ make test && echo READY'
        elif row % 11 == 0:
            fragment = f'test_{row:03d} ... passed'
        colour = (32, 34, 35, 36)[row % 4]
        lines.append(' ' * indent + f'\x1b[{colour}m' + fragment[:width - indent] + '\x1b[0m')
    lines.append('\x1b[1;36m$ terminal-smash --tower\x1b[0m')
    return '\n'.join(lines)


def build_demo(width: int, height: int) -> str:
    """Build ANSI text for the visible terrain rectangle, without wrapping.

    ``height`` excludes the HUD. The last row is left clear above the floor;
    stairs extend to the penultimate row even on very tall terminals. Both
    sides have overlapping platforms separated by at most three rows, so a
    stationary single jump is enough to climb, without the return-to-top key.
    All scenery, including the stairs, is ordinary destructible text.
    """
    if width <= 0 or height <= 0:
        return ""
    canvas = [[(" ", "0") for _ in range(width)] for _ in range(height)]

    def put(x: int, y: int, text: str, colour: str) -> None:
        if 0 <= y < height:
            for column, character in enumerate(text, x):
                if 0 <= column < width:
                    canvas[y][column] = (character, colour)

    title = "TERMINAL SMASH // ASCII ARCADE"
    put(max(0, (width - len(title)) // 2), 0, title, "1;36")
    put(2, 1, "L DASH  X SLAM  J PUNCH  K BLAST", "33")

    side_width = max(1, min(14, width // 4 - 1))
    middle_start = side_width + 7
    middle_end = width - middle_start
    middle_width = max(0, middle_end - middle_start)
    shelf_rows = sorted(set(range(3, height - 1, 3)) | {height - 2})
    shelf_rows = [row for row in shelf_rows if row >= 3]

    # Dense central scenery leaves clear air around both climbing routes.
    for row in range(3, height - 1):
        fragment = _LOGS[(row - 3) % len(_LOGS)]
        tiled = (fragment + " | ") * (middle_width // (len(fragment) + 3) + 1)
        put(middle_start, row, tiled[:middle_width], ("32", "34", "35")[row % 3])

    for level, row in enumerate(shelf_rows):
        inset = 2 + (level % 2) * 2
        colour = ("36", "33", "32", "35")[level % 4]
        step = (f"[{level + 1:02d}]" + "=" * side_width)[:side_width]
        put(inset, row, step, colour)
        put(width - inset - side_width, row, step, colour)
        # Breaking these connected beams starts cascades through the code.
        put(middle_start, row, "+" + "=" * max(0, middle_width - 2) + "+"
            if middle_width >= 2 else "=" * middle_width, colour)

    # Keep the enemy count modest and their initial positions off the stairs.
    if middle_width >= 5:
        enemy_rows = [row for row in range(4, height - 2, 6) if row not in shelf_rows][:3]
        for row in enemy_rows:
            put(middle_start, row, " " * middle_width, "0")
            put(middle_start + (middle_width - 5) // 2, row, "ERROR", "1;31")

    result = []
    for row in canvas:
        # Group equal styles so demo captures stay small even on wide screens.
        last_colour = "0"
        line = []
        last_visible = max((i + 1 for i, (char, _) in enumerate(row) if char != " "), default=0)
        for character, colour in row[:last_visible]:
            if colour != last_colour:
                line.append(f"\x1b[{colour}m")
                last_colour = colour
            line.append(character)
        if last_colour != "0":
            line.append("\x1b[0m")
        result.append("".join(line))
    return "\n".join(result)
