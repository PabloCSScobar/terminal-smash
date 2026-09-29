import dataclasses
import unittest

from terminal_smash.capture import Cell, Style, parse_capture


class CaptureTests(unittest.TestCase):
    def test_plain_text_positions_and_no_spaces(self):
        self.assertEqual(
            parse_capture(" a b\n c", 10, 5),
            [Cell(1, 0, "a"), Cell(3, 0, "b"), Cell(1, 1, "c")],
        )

    def test_ansi_colours_bold_and_resets(self):
        cells = parse_capture("\x1b[1;31;104mA\x1b[22;39mB\x1b[49mC\x1b[92mD\x1b[mE", 20, 2)
        self.assertEqual(
            [cell.style for cell in cells],
            [Style(1, 12, True), Style(None, 12), Style(), Style(10), Style()],
        )

    def test_indexed_and_truecolour(self):
        cells = parse_capture("\x1b[38;5;201;48;2;95;135;175mA\x1b[38;2;128;128;128mB", 10, 2)
        self.assertEqual(cells[0].style, Style(201, 67))
        self.assertEqual(cells[1].style, Style(244, 67))

    def test_colon_colours_with_and_without_colourspace(self):
        cells = parse_capture(
            "\x1b[38:2::95:135:175;48:5:17;1mA"
            "\x1b[38:2:0:255:0:0mB\x1b[48:2:0:255:0mC",
            10, 2,
        )
        self.assertEqual(cells[0].style, Style(67, 17, True))
        self.assertEqual(cells[1].style, Style(196, 17, True))
        self.assertEqual(cells[2].style, Style(196, 46, True))

    def test_invalid_colours_do_not_inject_style_components(self):
        cells = parse_capture("\x1b[31mA\x1b[38;2;999;1;0mB\x1b[48;5;256mC", 10, 2)
        self.assertTrue(all(cell.style == Style(1) for cell in cells))
        self.assertEqual(parse_capture("\x1b[38;2;1mX", 10, 1)[0].style, Style())

    def test_unicode_width_and_combining(self):
        self.assertEqual(
            parse_capture("e\u0301界🙂x", 10, 1),
            [Cell(0, 0, "e\u0301"), Cell(1, 0, "界", width=2), Cell(3, 0, "🙂", width=2), Cell(5, 0, "x")],
        )
        self.assertEqual(parse_capture("\u0301x", 10, 1), [Cell(0, 0, "x")])

    def test_combining_marks_survive_sgr(self):
        self.assertEqual(parse_capture("e\x1b[31m\u0301x", 10, 1), [Cell(0, 0, "e\u0301"), Cell(1, 0, "x", Style(1))])

    def test_no_line_wrapping_and_clipped_wide_glyph(self):
        self.assertEqual(parse_capture("ab界z\nZ", 3, 2), [Cell(0, 0, "a"), Cell(1, 0, "b"), Cell(0, 1, "Z")])
        self.assertEqual(parse_capture("abcdef\nx\ny", 2, 2), [Cell(0, 0, "a"), Cell(1, 0, "b"), Cell(0, 1, "x")])

    def test_tabs_and_carriage_returns(self):
        self.assertEqual(parse_capture("a\tb\rXY", 20, 1), [Cell(0, 0, "X"), Cell(1, 0, "Y"), Cell(8, 0, "b")])
        self.assertEqual(parse_capture("界x\r a", 10, 1), [Cell(1, 0, "a"), Cell(2, 0, "x")])
        self.assertEqual(parse_capture("ab\r \nZ", 10, 2), [Cell(1, 0, "b"), Cell(0, 1, "Z")])

    def test_osc_titles_links_and_clipboard_are_discarded(self):
        text = "A\x1b]0;evil title\x07B\x1b]52;c;c2VjcmV0\x1b\\C\x1b]8;;https://example.test\x1b\\D\x1b]8;;\x1b\\E"
        self.assertEqual("".join(cell.char for cell in parse_capture(text, 10, 1)), "ABCDE")

    def test_dcs_apc_pm_sos_and_c1_controls_are_discarded(self):
        text = "A\x1bPgarbage\x1b[31m\x1b\\B\x1b_secret\x1b\\C\x1b^hidden\x1b\\D\x1bXhidden\x1b\\E\x9d52;hidden\x9cF\x90hidden\x9cG"
        cells = parse_capture(text, 20, 1)
        self.assertEqual("".join(cell.char for cell in cells), "ABCDEFG")
        self.assertTrue(all(cell.style == Style() for cell in cells))

    def test_cursor_moves_charset_sequences_and_controls_never_replay(self):
        text = "A\x1b[2J\x1b[20;20H\x1b[?1049hB\x1b(B\x1b7C\x07\x08\x00\u202eD"
        self.assertEqual(parse_capture(text, 20, 1), [Cell(i, 0, char) for i, char in enumerate("ABCD")])

    def test_c1_sgr(self):
        self.assertEqual(parse_capture("\x9b31mA", 10, 1), [Cell(0, 0, "A", Style(1))])

    def test_unterminated_control_strings_discard_the_payload(self):
        for escape in ("\x1b]", "\x1bP", "\x9d", "\x90"):
            with self.subTest(escape=repr(escape)):
                self.assertEqual(parse_capture("A" + escape + "secret\nmore", 10, 2), [Cell(0, 0, "A")])
        self.assertEqual(parse_capture("A\x1b[123;", 10, 1), [Cell(0, 0, "A")])
        self.assertEqual(parse_capture("A\x1b", 10, 1), [Cell(0, 0, "A")])

    def test_malformed_and_huge_parameters_are_safe(self):
        self.assertEqual(parse_capture("\x1b[" + "9" * 6000 + "mX", 10, 1), [Cell(0, 0, "X")])
        self.assertEqual(parse_capture("A\x1b[\x1b]52;secret\x07B", 10, 1), [Cell(0, 0, "A"), Cell(1, 0, "B")])

    def test_spaces_with_background_are_not_destructible(self):
        self.assertEqual(parse_capture("\x1b[41m \t\n ", 20, 2), [])

    def test_empty_screen_and_immutable_cells(self):
        for width, height in ((0, 5), (5, 0), (-1, 5), (5, -1)):
            self.assertEqual(parse_capture("hello", width, height), [])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            Cell(0, 0, "x").x = 1
        with self.assertRaises(dataclasses.FrozenInstanceError):
            Style().fg = 1



class ViewportTests(unittest.TestCase):
    def test_offset_keeps_recent_rows_and_inherited_color(self):
        cells = parse_capture("\x1b[31mold\nolder\nnew\nlast\n", 20, 2, start_row=2)
        self.assertEqual("".join(c.char for c in cells), "newlast")
        self.assertEqual([(c.x, c.y) for c in cells], [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (3, 1)])
        self.assertTrue(all(c.style.fg == 1 for c in cells))

    def test_offset_carriage_return_overwrites_without_stale_cells(self):
        cells = parse_capture("skip\n界\rX ", 20, 1, start_row=1)
        self.assertEqual([(c.x, c.y, c.char) for c in cells], [(0, 0, "X")])

if __name__ == "__main__":
    unittest.main()
