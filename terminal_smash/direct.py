"""Temporarily replace one tmux client with a game on its original terminal."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import sys
import tempfile
import termios
import time

HANDOFF_TIMEOUT = 8.0
BUNDLE_PREFIX = "terminal-smash-direct-"


def _cleanup(directory: Path) -> None:
    """Remove only our two files, never recursively remove a supplied path."""
    for name in ("scene.txt", "state.json"):
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
    try:
        directory.rmdir()
    except FileNotFoundError:
        pass


def _read_private(directory: Path, name: str) -> str:
    from .cli import MAX_FILE_BYTES, UserError

    descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise UserError("Direct-mode snapshot files must be private regular files owned by you.")
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise UserError("File is too large. The maximum snapshot size is 2 MiB.")
    return raw.decode("utf-8", errors="replace")


def _client(pane: str, requested: str | None) -> tuple[str, str]:
    from .cli import UserError, _tmux

    rows = _tmux(["list-clients", "-F", "#{client_name}\t#{client_tty}\t#{session_id}\t#{pane_id}\t#{client_control_mode}"]).stdout.splitlines()
    clients = [row.split("\t") for row in rows if len(row.split("\t")) == 5]
    matches = [row for row in clients if requested in row[:2]] if requested else [row for row in clients if row[3] == pane]
    if len(matches) != 1:
        raise UserError("Cannot choose one terminal client safely. Use the Tower shortcut in that client, or pass its exact --client name (tmux list-clients).")
    name, tty_name, session, _, control = matches[0]
    if not tty_name or control == "1" or not re.fullmatch(r"\$\d+", session):
        raise UserError("Direct mode requires an interactive tmux terminal client.")
    return name, session


def _check_detach_policy(session: str) -> None:
    from .cli import UserError, _tmux

    for name, arguments in (
        ("destroy-unattached", ["show-options", "-A", "-v", "-t", session, "destroy-unattached"]),
        ("exit-unattached", ["show-options", "-s", "-v", "exit-unattached"]),
    ):
        if _tmux(arguments).stdout.strip() not in ("off", "0"):
            raise UserError(f"Direct mode cannot detach safely while tmux {name} is enabled. Use --tower --popup instead.")


def launch(pane: str | None, client: str | None, label: str, *,
           challenge: bool = False, tower: bool = False,
           falling_enabled: bool = False) -> int:
    from . import cli

    if not os.environ.get("TMUX"):
        raise cli.UserError("Direct history capture requires tmux. Use the Tower shortcut inside tmux, or --demo/--file outside tmux.")
    cli._tmux_version()
    target = pane or os.environ.get("TMUX_PANE")
    if not target or not re.fullmatch(r"%\d+", target):
        raise cli.UserError("Direct mode needs an explicit tmux pane ID, such as --pane %0.")
    chosen, session = _client(target, client)
    _check_detach_policy(session)
    socket = cli._tmux(["display-message", "-p", "-t", target, "#{socket_path}"]).stdout.strip()
    if not os.path.isabs(socket):
        raise cli.UserError("Cannot determine the tmux server socket for returning to the session.")
    arguments = ["capture-pane", "-p", "-e", "-t", target]
    if tower:
        arguments += ["-S", "-"]
    captured = cli._tmux(arguments).stdout
    if len(captured.encode("utf-8")) > cli.MAX_FILE_BYTES:
        raise cli.UserError("Terminal history is too large. The maximum snapshot size is 2 MiB.")
    launcher = Path(cli.__file__).resolve().parent.parent / "terminal-smash"
    tmux = shutil.which("tmux")
    if not launcher.is_file() or tmux is None:
        raise cli.UserError("The Terminal Smash launcher or tmux is missing. Run install.sh again.")
    directory = Path(tempfile.mkdtemp(prefix=BUNDLE_PREFIX))
    try:
        values = {"label": label, "challenge": challenge, "tower": tower, "falling_enabled": falling_enabled}
        for name, value in (("scene.txt", captured), ("state.json", json.dumps(values))):
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
        # The client's original environment supplies its TERM. Use absolute
        # executables because that environment may have a different PATH.
        start = shlex.join([sys.executable, str(launcher), "--direct-handoff", str(directory), socket, session])
        return_to_session = shlex.join([tmux, "-S", socket, "attach-session", "-E", "-t", session])
        # The fallback also covers an interpreter/import failure before our
        # supervisor starts. The supervisor normally execs attach itself.
        fallback = start + "; if test -d " + shlex.quote(str(directory)) + "; then exec " + return_to_session + "; fi"
        command = "exec " + shlex.join(["/bin/sh", "-c", fallback])
        cli._tmux(["detach-client", "-t", chosen, "-E", command])
        deadline = time.monotonic() + HANDOFF_TIMEOUT
        while directory.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if directory.exists():
            raise cli.UserError("The direct game did not start. Its private snapshot was removed; return with tmux attach if needed.")
    finally:
        _cleanup(directory)
    return 0


def supervise(directory: Path, socket: str, session: str) -> int:
    """Consume a private snapshot, restore the terminal, then reattach."""
    from . import cli

    if not directory.name.startswith(BUNDLE_PREFIX):
        raise cli.UserError("Invalid direct-mode snapshot directory.")
    if not os.path.isabs(socket) or not re.fullmatch(r"\$\d+", session):
        raise cli.UserError("Invalid tmux return target.")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise cli.UserError("The direct-mode snapshot directory must be private and owned by you.")
    saved_terminal = termios.tcgetattr(sys.stdin.fileno())
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, interrupt)
        options = json.loads(_read_private(directory, "state.json"))
        keys = {"label", "challenge", "tower", "falling_enabled"}
        if (not isinstance(options, dict) or set(options) != keys
                or not isinstance(options["label"], str)
                or any(type(options[name]) is not bool for name in keys - {"label"})):
            raise cli.UserError("Invalid direct-mode launch options.")
        text = _read_private(directory, "scene.txt")
        # Removing this bundle acknowledges the handoff to the launching job.
        # Terminal history exists only in the renderer's memory during play.
        _cleanup(directory)
        return cli._render(text, options["label"], challenge=options["challenge"],
                           tower=options["tower"], falling_enabled=options["falling_enabled"])
    except KeyboardInterrupt:
        return 130
    finally:
        _cleanup(directory)
        signal.signal(signal.SIGTERM, previous_term)
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved_terminal)
        except (OSError, termios.error):
            # A closed terminal cannot accept a replacement tmux client.
            pass
        else:
            environment = os.environ.copy()
            environment.pop("TMUX", None)
            environment.pop("TMUX_PANE", None)
            tmux = shutil.which("tmux")
            if tmux:
                os.execvpe(tmux, [tmux, "-S", socket, "attach-session", "-E", "-t", session], environment)
