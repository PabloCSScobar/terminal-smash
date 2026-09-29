"""Turn a tmux ANSI capture into inert, positioned text cells.

Only SGR colour/bold attributes are interpreted. Other escapes are discarded,
including whole OSC/DCS strings, so captured terminal controls are never replayed.
The parser does not wrap lines: tmux has already laid out the captured screen.
"""

from dataclasses import dataclass, replace
from functools import lru_cache
import unicodedata


@dataclass(frozen=True)
class Style:
    fg: int | None = None
    bg: int | None = None
    bold: bool = False


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    char: str
    style: Style = Style()
    width: int = 1


# The first sixteen colours are terminal-configurable. Quantize true colour to
# the fixed 6x6x6 cube and greyscale ramp, whose RGB values have stable meanings.
_LEVELS = (0, 95, 135, 175, 215, 255)
_PALETTE = tuple(
    (16 + r * 36 + g * 6 + b, red, green, blue)
    for r, red in enumerate(_LEVELS)
    for g, green in enumerate(_LEVELS)
    for b, blue in enumerate(_LEVELS)
) + tuple((232 + n, 8 + n * 10, 8 + n * 10, 8 + n * 10) for n in range(24))


@lru_cache(maxsize=1024)
def _rgb_to_index(red: int, green: int, blue: int) -> int:
    return min(
        _PALETTE,
        key=lambda colour: (
            (red - colour[1]) ** 2
            + (green - colour[2]) ** 2
            + (blue - colour[3]) ** 2
        ),
    )[0]


def _number(value: str) -> int | None:
    # Refuse arbitrarily large numeric parameters without int() raising on them.
    if not value or len(value) > 4 or not value.isascii() or not value.isdigit():
        return None
    return int(value)


def _colour(mode: int | None, components: list[str]) -> int | None:
    if mode == 5 and len(components) == 1:
        value = _number(components[0])
        return value if value is not None and 0 <= value <= 255 else None
    if mode == 2 and len(components) == 3:
        values = [_number(component) for component in components]
        if all(value is not None and 0 <= value <= 255 for value in values):
            return _rgb_to_index(*values)
    return None


def _sgr(style: Style, parameters: str) -> Style:
    parts = parameters.split(";")
    index = 0
    while index < len(parts):
        part = parts[index]
        index += 1
        if ":" in part:
            fields = part.split(":")
            code = _number(fields[0])
            if code not in (38, 48) or len(fields) < 3:
                continue
            mode = _number(fields[1])
            components = fields[2:]
            if mode == 2 and len(components) == 4:
                # Colon syntax optionally includes an empty/default colourspace.
                if components[0] not in ("", "0"):
                    continue
                components = components[1:]
            colour = _colour(mode, components)
            if colour is not None:
                style = replace(style, **{"fg" if code == 38 else "bg": colour})
            continue
        code = 0 if part == "" else _number(part)
        if code == 0:
            style = Style()
        elif code == 1:
            style = replace(style, bold=True)
        elif code == 22:
            style = replace(style, bold=False)
        elif code == 39:
            style = replace(style, fg=None)
        elif code == 49:
            style = replace(style, bg=None)
        elif code is not None and 30 <= code <= 37:
            style = replace(style, fg=code - 30)
        elif code is not None and 90 <= code <= 97:
            style = replace(style, fg=code - 90 + 8)
        elif code is not None and 40 <= code <= 47:
            style = replace(style, bg=code - 40)
        elif code is not None and 100 <= code <= 107:
            style = replace(style, bg=code - 100 + 8)
        elif code in (38, 48):
            if index >= len(parts):
                continue
            mode = _number(parts[index])
            index += 1
            count = {2: 3, 5: 1}.get(mode)
            if count is None:
                # Unsupported colour syntax has no well-defined parameter count.
                break
            components = parts[index:index + count]
            index += count
            colour = _colour(mode, components)
            if colour is not None:
                style = replace(style, **{"fg" if code == 38 else "bg": colour})
    return style


def _string_end(text: str, start: int, allow_bell: bool) -> int:
    """Consume an entire control string, even if its payload looks like text."""
    index = start
    while index < len(text):
        char = text[index]
        if char == "\x9c" or (allow_bell and char == "\x07"):
            return index + 1
        if char == "\x1b" and index + 1 < len(text) and text[index + 1] == "\\":
            return index + 2
        index += 1
    return len(text)


def _escape(text: str, start: int, style: Style) -> tuple[int, Style]:
    """Return the first offset after one ESC or C1 sequence and updated style."""
    char = text[start]
    if char == "\x1b":
        start += 1
        if start >= len(text):
            return start, style
        char = text[start]
        if char in "]P^_X":
            return _string_end(text, start + 1, char == "]"), style
        if char != "[":
            index = start
            while index < len(text) and " " <= text[index] <= "/":
                index += 1
            if index < len(text) and "0" <= text[index] <= "~":
                index += 1
            return index, style
    elif char in "\x9d\x90\x9e\x9f\x98":
        return _string_end(text, start + 1, char == "\x9d"), style
    elif char != "\x9b":
        return start + 1, style

    index = start + 1
    while index < len(text):
        char = text[index]
        if "@" <= char <= "~":
            parameters = text[start + 1:index]
            if char == "m" and all(c in "0123456789;:" for c in parameters):
                style = _sgr(style, parameters)
            return index + 1, style
        if not " " <= char <= "?":
            # A malformed CSI ends here. Process its next character separately,
            # particularly a new ESC that begins another control sequence.
            return index, style
        index += 1
    return index, style


def parse_capture(text: str, width: int, height: int, *, start_row: int = 0,
                  initial_style: Style = Style()) -> list[Cell]:
    """Parse visible non-whitespace cells within a screen rectangle.

    Coordinates are zero-based, relative to start_row. Earlier lines are read
    only for attributes, without storing offscreen cells. Wide characters occupy two columns and are
    omitted if either column would be clipped. Combining marks stay attached to
    their preceding printable cell. Tabs advance to eight-column stops; CR
    returns to column zero and subsequent printable text overwrites earlier text.
    Spaces advance the cursor but are not destructible cells, even with a colour
    background. Unsupported cursor movement sequences are consumed and ignored.
    initial_style carries SGR attributes into an independently indexed row.
    """
    if width <= 0 or height <= 0:
        return []
    start_row = max(0, start_row)
    cells: dict[tuple[int, int], Cell] = {}
    occupied: dict[tuple[int, int], tuple[int, int]] = {}
    x = y = index = 0
    style = initial_style
    previous: tuple[int, int] | None = None

    def erase(column: int) -> None:
        anchor = occupied.get((y, column))
        if anchor is not None:
            cell = cells.pop(anchor)
            for offset in range(cell.width):
                occupied.pop((y, cell.x + offset), None)

    while index < len(text) and y < start_row + height:
        char = text[index]
        if char == "\x1b" or "\x80" <= char <= "\x9f":
            index, style = _escape(text, index, style)
            continue
        index += 1
        if char == "\n":
            x = 0
            y += 1
            previous = None
            continue
        if char == "\r":
            x = 0
            previous = None
            continue
        if char == "\t":
            x = (x // 8 + 1) * 8
            previous = None
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cf", "Cs"):
            continue
        if category in ("Mn", "Me"):
            if previous in cells:
                cell = cells[previous]
                cells[previous] = replace(cell, char=cell.char + char)
            continue
        cell_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        previous = None
        if x < width and y >= start_row:
            for column in range(x, min(x + cell_width, width)):
                erase(column)
            if x + cell_width <= width and not char.isspace():
                previous = (y, x)
                cells[previous] = Cell(x, y - start_row, char, style, cell_width)
                for offset in range(cell_width):
                    occupied[(y, x + offset)] = previous
        x += cell_width
    return [cell for _, cell in sorted(cells.items())]
