"""Exercise ncurses and real tmux popups without touching the user's server."""

from __future__ import annotations

import errno
import fcntl
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


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    for variable in ("TMUX", "TMUX_PANE", "COLUMNS", "LINES"):
        env.pop(variable, None)
    env.update(TERM="xterm-256color", LC_ALL="C.UTF-8")
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
        self._size(rows, columns)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=self.slave,
                stdout=self.slave,
                stderr=self.slave,
                cwd=ROOT,
                env=_environment(),
                start_new_session=True,
                preexec_fn=_claim_terminal,
                close_fds=True,
            )
        except BaseException:
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
            terminal.send(b"dd wjk")
            terminal.until(b"charging", after=offset)
            offset = len(terminal.output)
            terminal.send(b"?")
            terminal.until(b"Only a copy of the screen is destroyed.", after=offset)
            terminal.send(b"?r")

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
            env = _environment()

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
                offset = len(terminal.output)
                terminal.send(b"dd wjk")
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
                terminal.send(b"k")
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
