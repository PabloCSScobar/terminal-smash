"""Command line entry points and a deliberately small tmux bridge."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

MIN_TMUX = (3, 4)
MAX_FILE_BYTES = 2 * 1024 * 1024
from .demo import build_demo

DEMO_TEXT = build_demo(80, 19)


class UserError(Exception):
    """An expected problem that should not produce a Python traceback."""


def _tmux(arguments: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["tmux", *arguments],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise UserError("tmux is missing. On Ubuntu/WSL, install it with: sudo apt install tmux") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise UserError(f"tmux: {detail or 'command failed'}") from exc


def _tmux_version() -> str:
    version = _tmux(["-V"]).stdout.strip()
    match = re.search(r"\btmux\s+(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_TMUX:
        raise UserError(f"tmux 3.4 or later is required (detected: {version or 'unknown'}).")
    return version


def _render(text: str, label: str, *, challenge: bool = False,
            falling_enabled: bool = True, demo: bool = False) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise UserError("The game requires an interactive terminal. Run it in a WSL terminal window.")
    try:
        import curses
        from .ui import run
    except ImportError as exc:
        raise UserError("The curses module is missing. Run the game with Python 3 on Linux/WSL.") from exc
    try:
        options = {}
        if challenge:
            options['challenge'] = True
        if not falling_enabled:
            options['falling_enabled'] = False
        if demo:
            options['demo'] = True
        run(text, label=label, **options)
    except curses.error as exc:
        raise UserError(f"Cannot initialize the terminal screen: {exc}") from exc
    return 0


def _read_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise UserError("File is too large. The maximum snapshot size is 2 MiB.")
        return raw.decode("utf-8", errors="replace")
    except OSError as exc:
        raise UserError(f"Cannot read file {path}: {exc.strerror or exc}") from exc


def _launch_popup(
    pane: str | None, client: str | None, label: str, *, challenge: bool = False,
    falling_enabled: bool = True
) -> int:
    if not os.environ.get("TMUX"):
        raise UserError(
            "To smash the text in your terminal, enter tmux:\n"
            "  terminal-smash --session\n"
            "Then press Ctrl+b, followed by Shift+s (after installation),\n"
            "or run: terminal-smash\n"
            "Demo without tmux: terminal-smash --demo"
        )
    _tmux_version()
    target = pane or os.environ.get("TMUX_PANE")
    if not target:
        target = _tmux(["display-message", "-p", "#{pane_id}"]).stdout.strip()
    if not re.fullmatch(r"%\d+", target):
        raise UserError("Use a tmux pane ID, such as %0 (check with: tmux list-panes).")

    # Capture before displaying the popup: the original pane continues to exist.
    captured = _tmux(["capture-pane", "-p", "-e", "-t", target]).stdout
    launcher = Path(__file__).resolve().parent.parent / "terminal-smash"
    if not launcher.is_file():
        raise UserError(f"Launcher not found: {launcher}. Run install.sh again.")
    # A private directory keeps terminal contents inaccessible to other users.
    # It lives only as long as the synchronous popup command.
    with tempfile.TemporaryDirectory(prefix="terminal-smash-") as temporary:
        snapshot = Path(temporary) / "screen.txt"
        descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(captured)
        render_arguments = [str(launcher), "--snapshot", str(snapshot), "--label", label]
        if challenge:
            render_arguments.append("--challenge")
        if not falling_enabled:
            render_arguments.extend(["--gravity", "off"])
        renderer = shlex.join(render_arguments)
        command = ["display-popup", "-E", "-B", "-w", "100%", "-h", "100%", "-t", target]
        if client:
            command.extend(["-c", client])
        command.append(renderer)
        _tmux(command)
    return 0


def _doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"System: {sys.platform}")
    print(f"Interactive terminal: {'yes' if sys.stdin.isatty() and sys.stdout.isatty() else 'no'}")
    print(f"tmux session: {'yes' if os.environ.get('TMUX') else 'no'}")
    print(f"Launcher in PATH: {shutil.which('terminal-smash') or 'not found (run ./install.sh)'}")
    try:
        import curses  # noqa: F401
        print("curses: OK")
    except ImportError:
        print("curses: MISSING - Python on Linux/WSL is required")
        return 1
    try:
        print(f"tmux: {_tmux_version()}")
    except UserError as exc:
        print(str(exc))
        return 1
    print("Try the game: terminal-smash --demo")
    print("Session with your own text: terminal-smash --session")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terminal-smash",
        description="An ASCII character smashes text in the visible tmux pane (Linux/WSL).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="play the colorful demo without tmux")
    mode.add_argument("--file", type=Path, metavar="FILE", help="play with text from a file, without tmux")
    mode.add_argument("--snapshot", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--doctor", action="store_true", help="check the environment")
    mode.add_argument("--session", action="store_true", help="create or attach to the tmux session named smash")
    parser.add_argument("--challenge", action="store_true", help="play a 30-second survival challenge with local records")
    parser.add_argument("--gravity", choices=("on", "off"), default=None,
                        help="falling damaged text: on (default) or off")
    parser.add_argument("--pane", metavar="ID", help="tmux pane ID, such as %%0")
    parser.add_argument("--client", metavar="TTY", help="tmux client in which to open the game")
    parser.add_argument("--label", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.gravity is not None and (args.doctor or args.session):
        parser.error("--gravity requires a game: use --demo, --file or a tmux pane")
    if args.challenge and (args.doctor or args.session):
        parser.error("--challenge requires a game: use --demo, --file or a tmux pane")
    if (args.pane or args.client) and any((args.demo, args.file, args.snapshot, args.doctor, args.session)):
        parser.error("--pane and --client apply only to tmux pane capture")
    render_options = {"challenge": True} if args.challenge else {}
    if args.gravity == "off":
        render_options["falling_enabled"] = False
    try:
        if args.doctor:
            return _doctor()
        if args.session:
            _tmux_version()
            if os.environ.get("TMUX"):
                raise UserError("You are already in tmux. Press Ctrl+b, then Shift+s, or run terminal-smash.")
            os.execvp("tmux", ["tmux", "new-session", "-A", "-s", "smash"])
            return 0
        if args.demo:
            return _render(DEMO_TEXT, args.label or "demo", demo=True, **render_options)
        source = args.file or args.snapshot
        if source is not None:
            return _render(_read_file(source), args.label or source.name, **render_options)
        return _launch_popup(args.pane, args.client, args.label or "terminal", **render_options)
    except KeyboardInterrupt:
        return 130
    except (UserError, OSError) as exc:
        print(f"terminal-smash: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
