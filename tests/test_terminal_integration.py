"""Exercise ncurses and real tmux popups without touching the user's server."""

from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "terminal-smash"


def _environment(state_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    for variable in ("TMUX", "TMUX_PANE", "COLUMNS", "LINES"):
        env.pop(variable, None)
    env.update(TERM="xterm-256color", LC_ALL="C.UTF-8", XDG_STATE_HOME=str(state_home))
    return env


def _claim_terminal() -> None:
    # Popen's start_new_session runs setsid before this callback.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    """A child process with its own session and controlling pseudo-terminal."""

    def __init__(self, command: list[str], *, rows: int = 30, columns: int = 100):
        self.master, self.slave = pty.openpty()
        self.original_mode = termios.tcgetattr(self.slave)
        self.output = bytearray()
        self.state_directory = tempfile.TemporaryDirectory(prefix="smash-test-state-")
        self._size(rows, columns)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=self.slave,
                stdout=self.slave,
                stderr=self.slave,
                cwd=ROOT,
                env=_environment(Path(self.state_directory.name)),
                start_new_session=True,
                preexec_fn=_claim_terminal,
                close_fds=True,
            )
        except BaseException:
            self.state_directory.cleanup()
            os.close(self.master)
            os.close(self.slave)
            raise

    def _size(self, rows: int, columns: int) -> None:
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def resize(self, rows: int, columns: int) -> None:
        self._size(rows, columns)
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGWINCH)

    def send(self, keys: bytes) -> None:
        os.write(self.master, keys)

    def _read(self, timeout: float) -> None:
        readable, _, _ = select.select([self.master], [], [], timeout)
        if readable:
            try:
                self.output.extend(os.read(self.master, 65536))
            except OSError as error:
                if error.errno != errno.EIO:
                    raise

    def until(self, needle: bytes, *, after: int = 0, timeout: float = 8) -> bytes:
        deadline = time.monotonic() + timeout
        while needle not in self.output[after:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"Terminal did not display {needle!r}; process={self.process.poll()}, "
                    f"output tail={bytes(self.output[-1800:])!r}"
                )
            self._read(min(0.05, remaining))
        return bytes(self.output[after:])

    def pump(self, duration: float) -> None:
        """Drain frame output while allowing the real event loop to advance."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self._read(min(0.03, max(0, deadline - time.monotonic())))

    def finish(self, timeout: float = 5) -> int:
        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError("Terminal child did not exit after its exit key")
            self._read(0.05)
        # Consume the cleanup sequences written immediately before the exit.
        while select.select([self.master], [], [], 0)[0]:
            self._read(0)
        return self.process.returncode

    def close(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
        os.close(self.master)
        os.close(self.slave)
        self.state_directory.cleanup()


def _eventually(check, *, timeout: float = 8):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the isolated tmux fixture")
        time.sleep(0.04)


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux PTYs and termios")
class TerminalIntegrationTests(unittest.TestCase):
    def test_demo_gameplay_resize_exit_and_terminal_restoration(self):
        terminal = Terminal([sys.executable, str(LAUNCHER), "--demo"])
        try:
            terminal.until(b"TERMINAL SMASH")
            self.assertIsNone(terminal.process.poll())
            running_mode = termios.tcgetattr(terminal.slave)
            self.assertFalse(running_mode[3] & (termios.ECHO | termios.ICANON))

            offset = len(terminal.output)
            terminal.send(b"ddLwXJK")
            terminal.until(b"charging", after=offset)
            offset = len(terminal.output)
            terminal.send(b"?")
            terminal.until(b"Only a copy of the screen is destroyed.", after=offset)
            terminal.send(b"?r")
            offset = len(terminal.output)
            terminal.send(b"C")
            terminal.until(b"CHALLENGE", after=offset)
            offset = len(terminal.output)
            terminal.send(b"c")
            terminal.until(b"FREE PLAY", after=offset)

            offset = len(terminal.output)
            terminal.resize(10, 50)
            terminal.until(b"Resize to at least", after=offset)
            offset = len(terminal.output)
            terminal.resize(30, 100)
            terminal.until(b"TERMINAL SMASH", after=offset)

            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            output = bytes(terminal.output)
            self.assertNotIn(b"Traceback", output)
            self.assertIn(b"\x1b[?1049h", output)
            self.assertIn(b"\x1b[?1049l", output)
            self.assertGreater(output.rfind(b"\x1b[?1049l"), output.find(b"\x1b[?1049h"))
            restored = termios.tcgetattr(terminal.slave)
            self.assertEqual(
                restored[3] & (termios.ECHO | termios.ICANON),
                terminal.original_mode[3] & (termios.ECHO | termios.ICANON),
            )
        finally:
            terminal.close()

    def test_gravity_and_automatic_grip_survive_reset_challenge_and_resize(self):
        # Full repaint makes the actual PTY status readable as complete text;
        # ncurses normally emits only the changed "N" / "FF" bytes on toggles.
        # The CLI, input loop, world, record store and curses renderer are real.
        child = (
            "from terminal_smash import cli, ui\n"
            "draw = ui._draw\n"
            "def redraw(win, *args, **kwargs):\n"
            "    win.redrawwin()\n"
            "    draw(win, *args, **kwargs)\n"
            "ui._draw = redraw\n"
            "raise SystemExit(cli.main(['--demo']))\n"
        )
        terminal = Terminal([sys.executable, "-c", child])
        try:
            terminal.until(b"falling ON")
            terminal.until(b"Grip AUTO")
            offset = len(terminal.output)
            terminal.send(b"G")
            terminal.until(b"falling OFF", after=offset)
            terminal.until(b"Grip AUTO", after=offset)

            # Charging the blast makes the reset observable, even though the
            # gravity status itself is expected to remain unchanged.
            offset = len(terminal.output)
            terminal.send(b"k")
            terminal.until(b"falling OFF | Grip AUTO | K blast recharging", after=offset)
            offset = len(terminal.output)
            terminal.send(b"R")
            terminal.until(b"falling OFF | Grip AUTO | K blast ready", after=offset)

            for key, mode in ((b"C", b"CHALLENGE"), (b"c", b"FREE PLAY")):
                offset = len(terminal.output)
                terminal.send(key)
                terminal.until(mode, after=offset)
                mode_offset = terminal.output.index(mode, offset)
                terminal.until(b"falling OFF", after=mode_offset)
                terminal.until(b"Grip AUTO", after=mode_offset)

            # Passing through an unplayable size removes the old footer, so
            # the next OFF label must come from the rebuilt larger world.
            offset = len(terminal.output)
            terminal.resize(10, 50)
            terminal.until(b"Resize to at least", after=offset)
            offset = len(terminal.output)
            terminal.resize(32, 120)
            terminal.until(b"falling OFF", after=offset)
            terminal.until(b"Grip AUTO", after=offset)
            offset = len(terminal.output)
            terminal.send(b"g")
            terminal.until(b"falling ON", after=offset)
            terminal.until(b"Grip AUTO", after=offset)

            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
            restored = termios.tcgetattr(terminal.slave)
            self.assertEqual(
                restored[3] & (termios.ECHO | termios.ICANON),
                terminal.original_mode[3] & (termios.ECHO | termios.ICANON),
            )
        finally:
            terminal.close()

    def test_cli_can_start_demo_with_gravity_disabled(self):
        terminal = Terminal([sys.executable, str(LAUNCHER), "--demo", "--gravity", "off"])
        try:
            terminal.until(b"falling OFF")
            self.assertIsNone(terminal.process.poll())
            self.assertNotIn(b"falling ON", bytes(terminal.output))
            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
            restored = termios.tcgetattr(terminal.slave)
            self.assertEqual(
                restored[3] & (termios.ECHO | termios.ICANON),
                terminal.original_mode[3] & (termios.ECHO | termios.ICANON),
            )
        finally:
            terminal.close()

    def test_short_terminal_help_pages_and_resumes_gameplay(self):
        terminal = Terminal([sys.executable, str(LAUNCHER), "--demo"], rows=14, columns=60)
        try:
            terminal.until(b"TERMINAL SMASH")
            offset = len(terminal.output)
            terminal.send(b"?")
            terminal.until(b"HELP", after=offset)
            safety = b"Only a copy of the screen is destroyed."
            for _ in range(8):
                if safety in terminal.output[offset:]:
                    break
                terminal.send(b"\x1b[6~")  # PageDown inside the help overlay.
                terminal.pump(0.08)
            self.assertIn(safety, terminal.output[offset:])
            offset = len(terminal.output)
            terminal.send(b"?k")
            terminal.until(b"charging", after=offset)
            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
        finally:
            terminal.close()

    def test_real_challenge_completion_restart_toggle_and_resize(self):
        # Change only the round length; use the actual curses input/render loop,
        # production physics, and persistent record store in a temporary profile.
        child = (
            "from terminal_smash import cli, ui; "
            "ui.ROUND_DURATION = .45; "
            "raise SystemExit(cli.main(['--demo', '--challenge']))"
        )
        terminal = Terminal([sys.executable, "-c", child])
        try:
            terminal.until(b"CHALLENGE")
            terminal.send(b"dLwXJK?")
            terminal.until(b"HELP")
            terminal.pump(0.6)
            self.assertNotIn(b"ROUND OVER", terminal.output)
            terminal.send(b"?")
            terminal.until(b"ROUND OVER")
            self.assertIsNone(terminal.process.poll())
            record_files = sorted(Path(terminal.state_directory.name).rglob("*.json"))
            self.assertTrue(record_files, "Completing a round must persist a local record")
            saved = {path: path.read_bytes() for path in record_files}

            # Actions after the timer expires must leave the final result intact.
            terminal.send(b"ddLwXJKT")
            terminal.pump(0.15)
            self.assertIsNone(terminal.process.poll())
            self.assertEqual({path: path.read_bytes() for path in record_files}, saved)

            offset = len(terminal.output)
            terminal.send(b"R")
            terminal.until(b"ROUND OVER", after=offset)

            offset = len(terminal.output)
            terminal.send(b"C")
            terminal.until(b"FREE PLAY", after=offset)
            offset = len(terminal.output)
            terminal.send(b"c")
            terminal.until(b"CHALLENGE", after=offset)
            offset = len(terminal.output)
            terminal.resize(10, 50)
            terminal.until(b"Resize to at least", after=offset)
            offset = len(terminal.output)
            terminal.resize(28, 96)
            terminal.until(b"CHALLENGE", after=offset)
            terminal.until(b"ROUND OVER", after=offset)

            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
            restored = termios.tcgetattr(terminal.slave)
            self.assertEqual(
                restored[3] & (termios.ECHO | termios.ICANON),
                terminal.original_mode[3] & (termios.ECHO | termios.ICANON),
            )
        finally:
            terminal.close()

    def test_completed_challenge_record_survives_immediate_exit(self):
        # Trigger a fatal real enemy contact during K. K and Esc arrive in one
        # input batch, so saving only once per rendered frame would lose it.
        # Ordinary clear no longer ends a challenge: enemies keep arriving.
        child = (
            "from terminal_smash import ui\n"
            "from terminal_smash.model import World, Enemy\n"
            "blast = World.blast\n"
            "def fatal_contact(world):\n"
            "    blast(world)\n"
            "    world.player.hp = 1\n"
            "    world.time = max(2.0, world.time)\n"
            "    world.hurt_until = world.dash_until = 0\n"
            "    world.enemies[:] = [Enemy(world.player.x, world.player.y)]\n"
            "    world.update(1 / 120)\n"
            "World.blast = fatal_contact\n"
            "ui.run('\\n' + ' ' * 36 + 'X', label='fatal-contact', challenge=True)\n"
        )
        terminal = Terminal([sys.executable, "-c", child])
        try:
            terminal.until(b"TERMINAL SMASH")
            terminal.send(b"k\x1b")
            self.assertEqual(terminal.finish(), 0)
            record_file = Path(terminal.state_directory.name) / "terminal-smash" / "records.json"
            self.assertTrue(record_file.is_file(), "Exit must preserve the just-completed round")
            records = json.loads(record_file.read_text(encoding="utf-8"))["records"]
            self.assertEqual(len(records), 1)
            self.assertGreater(next(iter(records.values()))["score"], 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
        finally:
            terminal.close()

    def test_health_damage_game_over_and_reset_in_real_terminal(self):
        # Put an actual enemy in contact on each K, without waiting through the
        # damage cooldown. The real collision code decides HP, knockback and
        # death; the real input loop saves the result and handles restart.
        child = (
            "import json, os, re\n"
            "from pathlib import Path\n"
            "from terminal_smash import ui\n"
            "from terminal_smash.model import World, Enemy\n"
            "def contact(world):\n"
            "    if world.finished: return\n"
            "    world.time = max(2.0, world.time)\n"
            "    world.hurt_until = world.dash_until = 0\n"
            "    world.player.vx = world.player.vy = 0\n"
            "    world.enemies[:] = [Enemy(world.player.x, world.player.y)]\n"
            "    world.update(1 / 120)\n"
            "World.blast = contact\n"
            "draw = ui._draw\n"
            "def redraw(win, *args, **kwargs):\n"
            "    win.redrawwin()\n"
            "    draw(win, *args, **kwargs)\n"
            "    rows, columns = win.getmaxyx()\n"
            "    lines = [win.instr(row, 0, columns - 1).decode('utf-8', errors='replace') "
            "for row in range(rows)]\n"
            "    state = Path(os.environ['XDG_STATE_HOME']) / 'health-screen.json'\n"
            "    temporary = state.with_suffix('.tmp')\n"
            "    temporary.write_text(json.dumps(dict(columns=columns, header=lines[0], "
            "bar_rows=[row for row, line in enumerate(lines) if re.search(r'\\[[#-]{10,}\\]', line)])))\n"
            "    temporary.replace(state)\n"
            "ui._draw = redraw\n"
            "ui.run('plain session output', label='health', challenge=True)\n"
        )
        terminal = Terminal([sys.executable, "-c", child], rows=30, columns=100)
        state_file = Path(terminal.state_directory.name) / "health-screen.json"

        def check_health_header(columns: int):
            def inspect():
                terminal.pump(0.015)
                if not state_file.exists():
                    return None
                state = json.loads(state_file.read_text())
                if state["columns"] == columns and "HP 5/5" in state["header"]:
                    return state
                return None
            state = _eventually(inspect)
            self.assertRegex(state["header"], r"HP 5/5 \[#{10,}\]  SCORE ")
            self.assertEqual(state["bar_rows"], [0], "Health belongs only in the header")

        try:
            terminal.until(b"HP 5/5")
            check_health_header(100)
            offset = len(terminal.output)
            terminal.send(b"k")
            terminal.until(b"HP 4/5", after=offset)
            offset = len(terminal.output)
            terminal.send(b"kkkk")
            terminal.until(b"GAME OVER - NO HEALTH!", after=offset)
            terminal.until(b"HP 0/5", after=offset)
            record_file = Path(terminal.state_directory.name) / "terminal-smash" / "records.json"
            saved = record_file.read_bytes()
            offset = len(terminal.output)
            terminal.send(b"ddwJkLXT")
            terminal.pump(0.1)
            self.assertEqual(record_file.read_bytes(), saved)
            self.assertNotIn(b"HP 5/5", terminal.output[offset:])

            offset = len(terminal.output)
            terminal.send(b"R")
            terminal.until(b"HP 5/5", after=offset)
            terminal.until(b"CHALLENGE", after=offset)
            self.assertNotIn(b"GAME OVER", terminal.output[offset:])
            self.assertEqual(record_file.read_bytes(), saved)

            # Health stays visible in the minimum supported viewport too.
            offset = len(terminal.output)
            terminal.resize(14, 44)
            terminal.until(b"HP 5/5", after=offset)
            check_health_header(44)
            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
        finally:
            terminal.close()

    def test_real_automatic_grip_climb_hang_move_and_release(self):
        # Choose only a reproducible starting position. Input, traversal,
        # gravity and rendering remain production code; telemetry observes
        # resulting positions rather than replacing the movement methods.
        # No grip toggle or other setup key is sent: attachment is automatic.
        child = (
            "import json, os\n"
            "from pathlib import Path\n"
            "from terminal_smash import ui\n"
            "create = ui._new_world\n"
            "def near_wall(*args, **kwargs):\n"
            "    world = create(*args, **kwargs)\n"
            "    world.player.x, world.player.y = 1.0, 5.0\n"
            "    return world\n"
            "ui._new_world = near_wall\n"
            "draw = ui._draw\n"
            "state = Path(os.environ['XDG_STATE_HOME']) / 'motion.json'\n"
            "def observe(win, world, *args, **kwargs):\n"
            "    draw(win, world, *args, **kwargs)\n"
            "    temporary = state.with_suffix('.tmp')\n"
            "    temporary.write_text(json.dumps(dict(x=world.player.x, y=world.player.y, "
            "surface=world.grip_surface, enabled=world.grip_enabled)))\n"
            "    temporary.replace(state)\n"
            "ui._draw = observe\n"
            "ui.run('plain text', label='grip-control')\n"
        )
        terminal = Terminal([sys.executable, "-c", child])
        state_file = Path(terminal.state_directory.name) / "motion.json"

        def motion(check):
            def inspect():
                terminal.pump(0.015)
                if not state_file.exists():
                    return None
                state = json.loads(state_file.read_text())
                return state if check(state) else None
            return _eventually(inspect, timeout=3)

        try:
            terminal.until(b"TERMINAL SMASH")
            attached = motion(lambda state: state["surface"] == "left")
            self.assertTrue(attached["enabled"])
            terminal.send(b"s")
            descended = motion(lambda state: state["y"] > attached["y"] + 0.5)
            self.assertEqual(descended["surface"], "left")
            terminal.send(b"w")
            climbed = motion(lambda state: state["y"] < descended["y"] - 0.5)
            self.assertEqual(climbed["surface"], "left")

            # Repeated W is deliberate key repeat. A single press must not
            # teleport from the lower wall to the ceiling.
            for _ in range(8):
                terminal.send(b"w")
                terminal.pump(0.06)
                state = json.loads(state_file.read_text())
                if state["surface"] == "ceiling":
                    break
            hanging = motion(lambda state: state["surface"] == "ceiling")
            self.assertEqual(hanging["y"], 2.0)
            terminal.send(b"d")
            moved = motion(lambda state: state["x"] > hanging["x"] + 0.5)
            self.assertEqual(moved["surface"], "ceiling")
            terminal.send(b" ")
            released = motion(lambda state: state["surface"] == "" and state["y"] > 2.1)
            self.assertTrue(released["enabled"], "Jump detaches without disabling automatic grip")
            terminal.send(b"\x1b")
            self.assertEqual(terminal.finish(), 0)
            self.assertNotIn(b"Traceback", bytes(terminal.output))
        finally:
            terminal.close()

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_real_popup_preserves_pane_and_running_process(self):
        version = subprocess.run(["tmux", "-V"], text=True, capture_output=True, check=True).stdout
        parsed = re.search(r"tmux\s+(\d+)\.(\d+)", version)
        if not parsed or tuple(map(int, parsed.groups())) < (3, 4):
            self.skipTest("The popup launcher requires tmux >= 3.4")

        with tempfile.TemporaryDirectory(prefix="smash-integration-") as temporary:
            directory = Path(temporary)
            socket = directory / "tmux.sock"
            heartbeat = directory / "heartbeat"
            fixture = directory / "fixture.py"
            fixture.write_text(
                "from pathlib import Path\n"
                "import sys, time\n"
                "print('SMASH_SENTINEL_UNCHANGED_927461', flush=True)\n"
                "print('A/D move through this captured text; J/K smash it.', flush=True)\n"
                "print('The underlying process keeps working.', flush=True)\n"
                "while True:\n"
                "    Path(sys.argv[1]).write_text(str(time.monotonic()))\n"
                "    time.sleep(0.04)\n",
                encoding="utf-8",
            )
            env = _environment(directory / "state")

            def tmux(*arguments: str, check: bool = True):
                # Every server command explicitly names our temporary socket.
                return subprocess.run(
                    ["tmux", "-S", str(socket), *arguments],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=check,
                    timeout=5,
                )

            terminal = None
            popup = None
            try:
                tmux(
                    "-f", "/dev/null", "new-session", "-d", "-x", "100", "-y", "30",
                    "-s", "integration",
                    shlex.join([sys.executable, "-u", str(fixture), str(heartbeat)]),
                )
                tmux("set-option", "-t", "integration", "status", "off")
                pane = tmux("display-message", "-p", "-t", "integration", "#{pane_id}").stdout.strip()
                server_env = tmux("display-message", "-p", "-t", pane, "#{socket_path},#{pid},0").stdout.strip()
                terminal = Terminal(["tmux", "-S", str(socket), "attach-session", "-t", "integration"])
                terminal.until(b"SMASH_SENTINEL_UNCHANGED_927461")
                client = _eventually(lambda: tmux("list-clients", "-F", "#{client_tty}").stdout.strip())
                before = tmux("capture-pane", "-p", "-e", "-t", pane).stdout
                initial_heartbeat = _eventually(lambda: heartbeat.read_text() if heartbeat.exists() else "")

                popup_env = dict(env, TMUX=server_env, TMUX_PANE=pane)
                offset = len(terminal.output)
                popup = subprocess.Popen(
                    [sys.executable, str(LAUNCHER), "--pane", pane, "--client", client],
                    cwd=ROOT,
                    env=popup_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                terminal.until(b"TERMINAL SMASH", after=offset)
                self.assertIsNone(popup.poll())
                self.assertNotIn("ERROR", before)
                self.assertNotIn(b"[ERROR]", terminal.output[offset:])

                # Challenge must populate a real session snapshot even when no
                # literal ERROR word was captured. The copied source pane and
                # its running process must remain unchanged throughout.
                offset = len(terminal.output)
                terminal.send(b"C")
                terminal.until(b"CHALLENGE", after=offset)
                terminal.until(b"[ERROR]", after=offset)
                offset = len(terminal.output)
                tmux("refresh-client", "-t", client)
                terminal.until(b"ERROR 3", after=offset)
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)

                offset = len(terminal.output)
                terminal.send(b"k?")
                terminal.until(b"HELP", after=offset)
                terminal.until(b"charging", after=offset)
                terminal.send(b"?R")
                terminal.pump(0.1)
                offset = len(terminal.output)
                tmux("refresh-client", "-t", client)
                terminal.until(b"CHALLENGE", after=offset)
                terminal.until(b"ERROR 3", after=offset)
                terminal.until(b"K blast ready", after=offset)
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)

                # Returning to free play restores the snapshot without the
                # challenge's supplemental enemies.
                offset = len(terminal.output)
                terminal.send(b"C")
                terminal.until(b"FREE PLAY", after=offset)
                offset = len(terminal.output)
                tmux("refresh-client", "-t", client)
                terminal.until(b"ERROR 0", after=offset)
                offset = len(terminal.output)
                terminal.send(b"ddLwXJK")
                terminal.until(b"charging", after=offset)
                _eventually(lambda: heartbeat.read_text() not in ("", initial_heartbeat))
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)
                offset = len(terminal.output)
                terminal.send(b"\x1b")
                stdout, stderr = popup.communicate(timeout=8)
                self.assertEqual(popup.returncode, 0, stdout + stderr)
                terminal.until(b"SMASH_SENTINEL_UNCHANGED_927461", after=offset)
                self.assertIsNone(terminal.process.poll())
                self.assertNotIn(b"Traceback", bytes(terminal.output))
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)
                self.assertEqual(tmux("display-message", "-p", "-t", pane, "#{pane_dead}").stdout.strip(), "0")
                after_heartbeat = heartbeat.read_text()
                _eventually(lambda: heartbeat.read_text() not in ("", after_heartbeat))

                # Exercise the shipped installer and actual prefix binding too.
                # All installation paths live inside this test's temp directory.
                config_dir = directory / "config"
                installed = subprocess.run(
                    [
                        "bash", str(ROOT / "install.sh"),
                        "--prefix", str(directory / "prefix"),
                        "--config-dir", str(config_dir),
                        "--tmux-config", str(directory / "user-tmux.conf"),
                        "--no-reload",
                    ],
                    cwd=ROOT, env=env, text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
                tmux("source-file", str(config_dir / "tmux.conf"))
                offset = len(terminal.output)
                terminal.send(b"\x02S")  # Default tmux prefix Ctrl+b, then Shift+s.
                terminal.until(b"TERMINAL SMASH", after=offset)
                offset = len(terminal.output)
                # Freeze the cooldown in help so tmux output coalescing cannot
                # hide its short-lived status on a busy host.
                terminal.send(b"k?")
                terminal.until(b"HELP", after=offset)
                terminal.until(b"charging", after=offset)
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)
                offset = len(terminal.output)
                terminal.send(b"\x1b")
                terminal.until(b"SMASH_SENTINEL_UNCHANGED_927461", after=offset)
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-t", pane).stdout, before)
                self.assertIsNone(terminal.process.poll())
                self.assertNotIn(b"Traceback", bytes(terminal.output))
                after_heartbeat = heartbeat.read_text()
                _eventually(lambda: heartbeat.read_text() not in ("", after_heartbeat))
            finally:
                if popup is not None:
                    if popup.poll() is None:
                        os.killpg(popup.pid, signal.SIGTERM)
                        try:
                            popup.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(popup.pid, signal.SIGKILL)
                            popup.communicate(timeout=2)
                    else:
                        popup.communicate()
                if terminal is not None:
                    terminal.close()
                tmux("kill-server", check=False)


if __name__ == "__main__":
    unittest.main()
