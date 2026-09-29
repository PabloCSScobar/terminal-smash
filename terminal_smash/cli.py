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
DEMO_TEXT = """\x1b[1;36m  T E R M I N A L   S M A S H\x1b[0m           \x1b[33mASCII ARCADE\x1b[0m

\x1b[36m      +====================+\x1b[0m                 \x1b[35m+================+\x1b[0m
\x1b[36m      |\x1b[0m  BUILD SUCCESSFUL  \x1b[36m|\x1b[0m                 \x1b[35m|\x1b[0m CACHE OVERFLOW \x1b[35m|\x1b[0m
\x1b[36m      +====================+\x1b[0m                 \x1b[35m+================+\x1b[0m
\x1b[36m             |      |\x1b[0m                       \x1b[35m    |      |\x1b[0m
\x1b[36m             |      |\x1b[0m                       \x1b[35m    |      |\x1b[0m
\x1b[33m   ================\x1b[0m    \x1b[1;31mERROR\x1b[0m    \x1b[33m=============================\x1b[0m
\x1b[33m         |     |                         |             |\x1b[0m
\x1b[33m         |     |                         |             |\x1b[0m
\x1b[32m  +======================+\x1b[0m       \x1b[34m+========================+\x1b[0m
\x1b[32m  |\x1b[0m git push --force-smash\x1b[32m|\x1b[0m       \x1b[34m|\x1b[0m make clean && make BOOM \x1b[34m|\x1b[0m
\x1b[32m  +======================+\x1b[0m       \x1b[34m+========================+\x1b[0m
\x1b[32m       |           |\x1b[0m             \x1b[34m      |           |\x1b[0m
\x1b[32m       |           |\x1b[0m             \x1b[34m      |           |\x1b[0m
\x1b[1;31m            ERROR\x1b[0m                          \x1b[1;31mERROR\x1b[0m
\x1b[36m  ====================================================================\x1b[0m
  \x1b[1mL\x1b[0m DASH   \x1b[1mX\x1b[0m AIR SLAM   \x1b[1mJ\x1b[0m PUNCH   \x1b[1mK\x1b[0m BLAST   \x1b[1mC\x1b[0m 30-SECOND CHALLENGE
  A/D MOVE   SPACE JUMP   R REBUILD   ? HELP   ESC RETURN TO TERMINAL
"""


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
        raise UserError("Brak tmux. W Ubuntu/WSL zainstaluj go: sudo apt install tmux") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise UserError(f"tmux: {detail or 'polecenie zakończyło się błędem'}") from exc


def _tmux_version() -> str:
    version = _tmux(["-V"]).stdout.strip()
    match = re.search(r"\btmux\s+(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_TMUX:
        raise UserError(f"Potrzebny jest tmux 3.4 lub nowszy (wykryto: {version or 'nieznany'}).")
    return version


def _render(text: str, label: str, *, challenge: bool = False) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise UserError("Animacja wymaga interaktywnego terminala. Uruchom ją w oknie WSL.")
    try:
        import curses
        from .ui import run
    except ImportError as exc:
        raise UserError("Brak modułu curses. Uruchom program w Pythonie 3 na Linuxie/WSL.") from exc
    try:
        if challenge:
            run(text, label=label, challenge=True)
        else:
            run(text, label=label)
    except curses.error as exc:
        raise UserError(f"Nie można uruchomić ekranu terminala: {exc}") from exc
    return 0


def _read_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise UserError("Plik jest za duży. Maksymalny rozmiar migawki to 2 MiB.")
        return raw.decode("utf-8", errors="replace")
    except OSError as exc:
        raise UserError(f"Nie można odczytać pliku {path}: {exc.strerror or exc}") from exc


def _launch_popup(
    pane: str | None, client: str | None, label: str, *, challenge: bool = False
) -> int:
    if not os.environ.get("TMUX"):
        raise UserError(
            "Aby rozbijać tekst swojego terminala, wejdź do tmux:\n"
            "  terminal-smash --session\n"
            "Następnie naciśnij Ctrl+b, potem Shift+s (po instalacji),\n"
            "albo wpisz: terminal-smash\n"
            "Pokaz bez tmux: terminal-smash --demo"
        )
    _tmux_version()
    target = pane or os.environ.get("TMUX_PANE")
    if not target:
        target = _tmux(["display-message", "-p", "#{pane_id}"]).stdout.strip()
    if not re.fullmatch(r"%\d+", target):
        raise UserError("Panel musi mieć identyfikator tmux, np. %0 (sprawdź: tmux list-panes).")

    # Capture before displaying the popup: the original pane continues to exist.
    captured = _tmux(["capture-pane", "-p", "-e", "-t", target]).stdout
    launcher = Path(__file__).resolve().parent.parent / "terminal-smash"
    if not launcher.is_file():
        raise UserError(f"Brak launchera {launcher}. Uruchom ponownie install.sh.")
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
    print(f"Interaktywny terminal: {'tak' if sys.stdin.isatty() and sys.stdout.isatty() else 'nie'}")
    print(f"Sesja tmux: {'tak' if os.environ.get('TMUX') else 'nie'}")
    print(f"Launcher w PATH: {shutil.which('terminal-smash') or 'nie (uruchom ./install.sh)'}")
    try:
        import curses  # noqa: F401
        print("curses: OK")
    except ImportError:
        print("curses: BRAK — potrzebny Python na Linuxie/WSL")
        return 1
    try:
        print(f"tmux: {_tmux_version()}")
    except UserError as exc:
        print(str(exc))
        return 1
    print("Test animacji: terminal-smash --demo")
    print("Sesja z własnym tekstem: terminal-smash --session")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terminal-smash",
        description="Ludzik ASCII rozbija tekst widocznego panelu tmux (Linux/WSL).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="kolorowy pokaz bez tmux")
    mode.add_argument("--file", type=Path, metavar="PLIK", help="animuj tekst z pliku, bez tmux")
    mode.add_argument("--snapshot", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--doctor", action="store_true", help="sprawdź środowisko")
    mode.add_argument("--session", action="store_true", help="otwórz/przyłącz sesję tmux o nazwie smash")
    parser.add_argument("--challenge", action="store_true", help="demolka na czas: 30 sekund i lokalny rekord")
    parser.add_argument("--pane", metavar="ID", help="identyfikator panelu tmux, np. %%0")
    parser.add_argument("--client", metavar="TTY", help="klient tmux, w którym otworzyć animację")
    parser.add_argument("--label", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.challenge and (args.doctor or args.session):
        parser.error("--challenge wymaga planszy: --demo, --file albo panelu tmux")
    if (args.pane or args.client) and any((args.demo, args.file, args.snapshot, args.doctor, args.session)):
        parser.error("--pane i --client dotyczą tylko przechwytywania panelu tmux")
    render_options = {"challenge": True} if args.challenge else {}
    try:
        if args.doctor:
            return _doctor()
        if args.session:
            _tmux_version()
            if os.environ.get("TMUX"):
                raise UserError("Jesteś już w tmux. Naciśnij Ctrl+b, potem Shift+s, albo wpisz terminal-smash.")
            os.execvp("tmux", ["tmux", "new-session", "-A", "-s", "smash"])
            return 0
        if args.demo:
            return _render(DEMO_TEXT, args.label or "demo", **render_options)
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
