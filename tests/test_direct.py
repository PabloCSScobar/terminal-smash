"""Direct-terminal handoff privacy, client selection and real PTY round trips."""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
import types
import unittest
from unittest.mock import patch

from terminal_smash import cli, direct

ROOT = Path(__file__).resolve().parent.parent


def completed(stdout=""):
    return subprocess.CompletedProcess(["tmux"], 0, stdout, "")


class DirectTests(unittest.TestCase):
    def test_explicit_direct_and_legacy_popup_routing(self):
        with patch.object(direct, "launch", return_value=0) as launch, patch.object(cli, "_launch_popup", return_value=0) as popup:
            self.assertEqual(cli.main(["--tower", "--direct", "--pane", "%3", "--client", "/dev/pts/5"]), 0)
            launch.assert_called_once_with("%3", "/dev/pts/5", "terminal", tower=True)
            popup.assert_not_called()
            self.assertEqual(cli.main(["--tower", "--popup"]), 0)
            popup.assert_called_once_with(None, None, "terminal", tower=True)

    def test_direct_demo_still_runs_without_tmux(self):
        with patch.object(cli, "_render", return_value=0) as render, patch.object(direct, "launch") as launch:
            self.assertEqual(cli.main(["--demo", "--tower", "--direct"]), 0)
            render.assert_called_once_with(cli.DEMO_TEXT, "demo", demo=True, tower=True)
            launch.assert_not_called()

    def test_diagnostic_requires_tty_and_rejects_game_options(self):
        for arguments in (["--direct", "--popup"], ["--direct", "--session"], ["--popup", "--doctor"], ["--keyboard-check", "--tower"], ["--keyboard-check", "--gravity", "off"], ["--keyboard-check", "--pane", "%0"], ["--keyboard-check", "--direct"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    cli.main(arguments)
                self.assertEqual(result.exception.code, 2)
        with patch.object(cli.sys.stdin, "isatty", return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--keyboard-check"]), 1)
        module = types.ModuleType("terminal_smash.keyboard_check")
        module.run = lambda: 7
        with patch.dict(sys.modules, {module.__name__: module}), patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli.sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli.main(["--keyboard-check"]), 7)

    def test_client_selection_never_guesses_between_clients(self):
        listing = "one\t/dev/pts/1\t$0\t%7\t0\ntwo\t/dev/pts/2\t$1\t%7\t0\n"
        with patch.object(cli, "_tmux", return_value=completed(listing)):
            with self.assertRaisesRegex(cli.UserError, "choose one"):
                direct._client("%7", None)
            with self.assertRaisesRegex(cli.UserError, "choose one"):
                direct._client("%7", "/dev/pts")
            self.assertEqual(direct._client("%7", "/dev/pts/2"), ("two", "$1"))
        with patch.object(cli, "_tmux", return_value=completed("control\t\t$0\t%7\t1\n")):
            with self.assertRaisesRegex(cli.UserError, "interactive"):
                direct._client("%7", "control")

    def test_handoff_quotes_paths_and_keeps_snapshot_private_until_consumed(self):
        self._launch(failure=None)

    def test_failed_detach_removes_private_snapshot(self):
        self._launch(failure="detach")

    def test_failed_exec_timeout_removes_private_snapshot(self):
        self._launch(failure="timeout")

    def _launch(self, failure):
        with tempfile.TemporaryDirectory(prefix="direct test '$ ") as temporary:
            root = Path(temporary)
            (root / "terminal-smash").touch()
            source = "\x1b[31mold history\x1b[0m\nlatest\n"
            bundle = []
            commands = []

            def tmux(arguments):
                commands.append(arguments)
                if arguments == ["-V"]:
                    return completed("tmux 3.4")
                if arguments[0] == "list-clients":
                    return completed("/dev/pts/3\t/dev/pts/3\t$7\t%4\t0\n")
                if arguments[0] == "show-options":
                    return completed("off")
                if arguments[0] == "display-message":
                    return completed(str(root / "socket '$"))
                if arguments[0] == "capture-pane":
                    self.assertEqual(arguments, ["capture-pane", "-p", "-e", "-t", "%4", "-S", "-"])
                    return completed(source)
                self.assertEqual(arguments[:4], ["detach-client", "-t", "/dev/pts/3", "-E"])
                outer = shlex.split(arguments[4])
                self.assertEqual(outer[:3], ["exec", "/bin/sh", "-c"])
                lexer = shlex.shlex(outer[3], posix=True, punctuation_chars=True)
                lexer.whitespace_split = True
                tokens = list(lexer)
                self.assertEqual(tokens[:3], [sys.executable, str(root / "terminal-smash"), "--direct-handoff"])
                directory = Path(tokens[3])
                bundle.append(directory)
                self.assertEqual((directory / "scene.txt").read_text(), source)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((directory / "scene.txt").stat().st_mode), 0o600)
                state = json.loads((directory / "state.json").read_text())
                self.assertEqual(state["label"], "label ' $(echo nope)")
                self.assertTrue(state["tower"])
                self.assertEqual(tokens[4], str(root / "socket '$"))
                self.assertEqual(tokens[5], "$7")
                self.assertEqual(tokens[-7:-2], [str(root / "socket '$"), "attach-session", "-E", "-t", "$7"])
                if failure == "detach":
                    raise cli.UserError("detach failed")
                if failure != "timeout":
                    direct._cleanup(directory)
                return completed()

            with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%4"}), patch.object(cli, "__file__", str(root / "terminal_smash" / "cli.py")), patch.object(cli, "_tmux", side_effect=tmux), patch.object(direct, "HANDOFF_TIMEOUT", 0):
                if failure:
                    with self.assertRaisesRegex(cli.UserError, "detach failed|did not start"):
                        direct.launch(None, None, "label ' $(echo nope)", tower=True)
                else:
                    self.assertEqual(direct.launch(None, None, "label ' $(echo nope)", tower=True), 0)
            self.assertEqual(len(bundle), 1)
            self.assertFalse(bundle[0].exists())

    def test_detach_policy_blocks_before_capture_and_bundle_creation(self):
        for session_value, server_value in (("on", "off"), ("keep-last", "off"), ("keep-group", "off"), ("off", "on")):
            with self.subTest(session=session_value, server=server_value):
                replies = [completed("tmux 3.4"), completed(session_value), completed(server_value)]
                with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%0"}), patch.object(direct, "_client", return_value=("client", "$9")), patch.object(cli, "_tmux", side_effect=replies) as tmux, patch.object(direct.tempfile, "mkdtemp") as temporary:
                    with self.assertRaisesRegex(cli.UserError, "--tower --popup"):
                        direct.launch(None, None, "terminal", tower=True)
                    temporary.assert_not_called()
                    commands = [call.args[0] for call in tmux.call_args_list]
                    self.assertNotIn("capture-pane", [command[0] for command in commands])
                    self.assertNotIn("detach-client", [command[0] for command in commands])
                    self.assertEqual(commands[1], ["show-options", "-A", "-v", "-t", "$9", "destroy-unattached"])

    def test_private_reader_rejects_symlinks_and_public_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            original.write_text("private history")
            original.chmod(0o600)
            scene = root / "scene.txt"
            scene.symlink_to(original)
            with self.assertRaises(OSError):
                direct._read_private(root, "scene.txt")
            scene.unlink()
            scene.write_text("private history")
            scene.chmod(0o644)
            with self.assertRaisesRegex(cli.UserError, "private regular"):
                direct._read_private(root, "scene.txt")
            self.assertEqual(original.read_text(), "private history")

    def test_bad_option_schema_still_cleans_bundle_and_returns_to_session(self):
        with tempfile.TemporaryDirectory(prefix=direct.BUNDLE_PREFIX) as temporary:
            directory = Path(temporary)
            (directory / "state.json").write_text(json.dumps({"label": "test", "tower": "false", "challenge": False, "falling_enabled": False}))
            (directory / "state.json").chmod(0o600)
            (directory / "scene.txt").write_text("original")
            (directory / "scene.txt").chmod(0o600)
            with patch.object(direct.termios, "tcgetattr", return_value=[]), patch.object(direct.termios, "tcsetattr") as restore, patch.object(cli.sys.stdin, "fileno", return_value=0), patch.object(direct.os, "execvpe") as execute, patch.object(cli, "_render") as render:
                with self.assertRaisesRegex(cli.UserError, "launch options"):
                    direct.supervise(directory, "/tmp/socket", "$9")
                render.assert_not_called()
                restore.assert_called_once()
                self.assertEqual(execute.call_args.args[1][-4:], ["attach-session", "-E", "-t", "$9"])
                self.assertNotIn("TMUX", execute.call_args.args[2])
                self.assertFalse(directory.exists())

    def test_oversized_history_never_detaches_or_creates_bundle(self):
        with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%0"}), patch.object(cli, "MAX_FILE_BYTES", 3), patch.object(direct, "_client", return_value=("client", "$0")), patch.object(cli, "_tmux", side_effect=[completed("tmux 3.4"), completed("off"), completed("off"), completed("/tmp/socket"), completed("éé")]) as tmux, patch.object(direct.tempfile, "mkdtemp") as temporary:
            with self.assertRaisesRegex(cli.UserError, "2 MiB"):
                direct.launch(None, None, "terminal", tower=True)
            self.assertEqual(tmux.call_count, 5)
            temporary.assert_not_called()


@unittest.skipUnless(shutil.which("tmux") and hasattr(pty, "fork"), "tmux and PTYs are required")
class DirectPtyTests(unittest.TestCase):
    def test_direct_input_and_return_preserve_real_terminal_and_history(self):
        self._round_trip(mode="input")

    def test_renderer_failure_restores_terminal_and_reattaches(self):
        self._round_trip(mode="error")

    def test_signal_restores_terminal_and_reattaches(self):
        self._round_trip(mode="signal")

    def test_installed_tower_shortcut_quits_real_game_and_can_detach_after_return(self):
        self._round_trip(mode="game")

    def test_policy_guard_preserves_real_client_and_session(self):
        for name, value in (("destroy-unattached", "on"), ("destroy-unattached", "keep-last"), ("destroy-unattached", "keep-group"), ("exit-unattached", "on")):
            with self.subTest(name=name, value=value):
                self._round_trip(mode=f"policy:{name}:{value}")

    def _round_trip(self, mode):
        with tempfile.TemporaryDirectory(prefix="direct pty '$ ") as temporary:
            root = Path(temporary)
            socket = str(root / "test.sock")
            binary = shutil.which("tmux")
            environment = os.environ.copy()
            environment.pop("TMUX", None)
            environment.pop("TMUX_PANE", None)
            environment["TERM"] = "xterm-256color"
            private = root / "private"
            private.mkdir(mode=0o700)
            environment["TMPDIR"] = str(private)
            base = [binary, "-S", socket, "-f", "/dev/null"]

            def tmux(*arguments, check=True):
                return subprocess.run(base + list(arguments), env=environment, check=check, text=True, capture_output=True, timeout=5)

            installation = subprocess.run([str(ROOT / "install.sh"), "--prefix", str(root / "local"), "--config-dir", str(root / "config"), "--tmux-config", str(root / "tmux.conf"), "--no-reload"], text=True, capture_output=True, check=True)
            self.assertIn("Installed:", installation.stdout)
            launcher = root / "local" / "share" / "terminal-smash" / "terminal-smash"
            probe_script = ("#!/usr/bin/env python3\n" + "import os, sys, tty\n"
                                f"sys.path.insert(0, {str(launcher.parent)!r})\n"
                                "from terminal_smash import cli\n"
                                "def render(text, label, **options):\n"
                                "    tty.setraw(0)\n"
                                "    print('DIRECT_READY:'+str(os.getpid())+':'+str(os.environ.get('TERM'))+':'+str(os.environ.get('TMUX')),flush=True)\n"
                                + ("    raise RuntimeError('renderer failed')\n" if mode == "error" else
                                   "    data=os.read(0,1024)\n    print('DIRECT_BYTES:'+data.hex(),flush=True)\n    return 0\n")
                                + "cli._render=render\nraise SystemExit(cli.main())\n")
            if mode != "game":
                launcher.write_text(probe_script)
            pid = master = None
            output = bytearray()
            try:
                producer = "import time; print('\\n'.join('HISTORY%03d'%i for i in range(100)),flush=True); time.sleep(60)"
                tmux("new-session", "-d", "-s", "test", "-x", "80", "-y", "24", shlex.join([sys.executable, "-c", producer]))
                tmux("set-option", "-g", "status", "off")
                tmux("source-file", str(root / "config" / "tmux.conf"))
                pid, master = pty.fork()
                if pid == 0:
                    os.execvpe(binary, base + ["attach-session", "-E", "-t", "test"], environment)
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

                def drain():
                    readable, _, _ = select.select([master], [], [], .02)
                    if readable:
                        output.extend(os.read(master, 65536))

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    drain()
                    clients = tmux("list-clients", "-F", "#{client_tty}\t#{session_id}\t#{pane_id}").stdout.strip()
                    if clients:
                        break
                tty_name, session, pane = clients.split("\t")
                before = tmux("capture-pane", "-p", "-e", "-S", "-", "-t", pane).stdout
                if mode.startswith("policy:"):
                    _, name, value = mode.split(":")
                    if name == "exit-unattached":
                        tmux("set-option", "-s", name, value)
                    else:
                        tmux("set-option", "-t", session, name, value)
                    tmux_env = tmux("display-message", "-p", "#{socket_path},#{pid},0").stdout.strip()
                    with patch.dict(os.environ, {"TMUX": tmux_env, "TMUX_PANE": pane}):
                        with self.assertRaisesRegex(cli.UserError, "--tower --popup"):
                            direct.launch(pane, tty_name, "history", tower=True)
                    self.assertEqual(tmux("has-session", "-t", session).returncode, 0)
                    self.assertEqual(tmux("list-clients", "-F", "#{client_tty}").stdout.strip(), tty_name)
                    self.assertEqual(tmux("capture-pane", "-p", "-e", "-S", "-", "-t", pane).stdout, before)
                    self.assertFalse(list(private.glob(direct.BUNDLE_PREFIX + "*")))
                    return
                os.write(master, b"\x02T")
                deadline = time.monotonic() + 5
                marker = b"SCROLLBACK TOWER" if mode == "game" else b"DIRECT_READY:"
                while marker not in output and time.monotonic() < deadline:
                    drain()
                self.assertIn(marker, output)
                if mode != "game":
                    self.assertIn(b":xterm-256color:None", output)
                release = b"\x1b[97;1:3u"
                if mode != "error":
                    self.assertEqual(tmux("list-clients").stdout.strip(), "")
                    if mode == "signal":
                        renderer_pid = int(re.search(rb"DIRECT_READY:(\d+)", output).group(1))
                        os.kill(renderer_pid, signal.SIGTERM)
                    else:
                        os.write(master, b"q" if mode == "game" else release)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    drain()
                    clients = tmux("list-clients", "-F", "#{client_tty}\t#{session_id}\t#{pane_id}").stdout.strip()
                    if clients == "\t".join((tty_name, session, pane)):
                        break
                self.assertEqual(clients, "\t".join((tty_name, session, pane)))
                if mode == "input":
                    self.assertIn(b"DIRECT_BYTES:" + release.hex().encode(), output)
                self.assertEqual(tmux("capture-pane", "-p", "-e", "-S", "-", "-t", pane).stdout, before)
                self.assertFalse(list(private.glob(direct.BUNDLE_PREFIX + "*")))
                # A normal user detach after returning must not trigger the
                # supervisor's startup fallback and immediately attach again.
                tmux("detach-client", "-t", tty_name)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    finished, _ = os.waitpid(pid, os.WNOHANG)
                    if finished:
                        pid = None
                        break
                    time.sleep(.02)
                self.assertIsNone(pid, "Detaching the restored client must finish the launcher")
                restored = termios.tcgetattr(master)
                self.assertTrue(restored[3] & termios.ICANON)
                self.assertTrue(restored[3] & termios.ECHO)
            finally:
                tmux("kill-server", check=False)
                if master is not None:
                    os.close(master)
                if pid:
                    for _ in range(40):
                        finished, _ = os.waitpid(pid, os.WNOHANG)
                        if finished:
                            break
                        time.sleep(.01)
                    else:
                        os.kill(pid, signal.SIGTERM)
                        os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
