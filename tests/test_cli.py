"""Regression checks for quoting, snapshot privacy and user-local installation."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from terminal_smash import cli

ROOT = Path(__file__).resolve().parent.parent


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["tmux"], returncode, stdout, stderr)


class CliTests(unittest.TestCase):
    def test_outside_tmux_shows_session_and_demo_guidance(self):
        error = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(error):
            self.assertEqual(cli.main([]), 1)
        self.assertIn("--session", error.getvalue())
        self.assertIn("--demo", error.getvalue())

    def test_demo_does_not_require_tmux(self):
        with patch.object(cli, "_render", return_value=0) as render, patch.object(cli, "_tmux") as tmux:
            self.assertEqual(cli.main(["--demo"]), 0)
        render.assert_called_once_with(cli.DEMO_TEXT, "demo", demo=True)
        tmux.assert_not_called()

    def test_challenge_demo_does_not_require_tmux(self):
        with patch.object(cli, "_render", return_value=0) as render, patch.object(cli, "_tmux") as tmux:
            self.assertEqual(cli.main(["--demo", "--challenge"]), 0)
        render.assert_called_once_with(cli.DEMO_TEXT, "demo", demo=True, challenge=True)
        tmux.assert_not_called()

    def test_challenge_reads_file_and_internal_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scene.txt"
            source.write_text("arena")
            for argument in ("--file", "--snapshot"):
                with self.subTest(argument=argument), patch.object(cli, "_render", return_value=0) as render:
                    self.assertEqual(cli.main([argument, str(source), "--challenge"]), 0)
                    render.assert_called_once_with("arena", "scene.txt", challenge=True)

    def test_challenge_passes_through_tmux_launch(self):
        with patch.object(cli, "_launch_popup", return_value=0) as launch:
            self.assertEqual(cli.main(["--challenge", "--pane", "%7"]), 0)
        launch.assert_called_once_with("%7", None, "terminal", challenge=True)

    def test_challenge_rejects_non_game_commands(self):
        for argument in ("--doctor", "--session"):
            with self.subTest(argument=argument), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.main([argument, "--challenge"])
                self.assertEqual(error.exception.code, 2)

    def test_render_passes_challenge_to_ui(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli.sys.stdout, "isatty", return_value=True), patch("terminal_smash.ui.run") as run:
            self.assertEqual(cli._render("arena", "demo", challenge=True), 0)
        run.assert_called_once_with("arena", label="demo", challenge=True)

    def test_challenge_popup_keeps_flag_with_private_snapshot(self):
        self._check_popup(fail=False, challenge=True)

    def test_gravity_off_reaches_demo_ui_and_popup(self):
        with patch.object(cli, "_render", return_value=0) as render:
            self.assertEqual(cli.main(["--demo", "--gravity", "off"]), 0)
        render.assert_called_once_with(cli.DEMO_TEXT, "demo", demo=True, falling_enabled=False)
        with patch.object(cli, "_launch_popup", return_value=0) as launch:
            self.assertEqual(cli.main(["--gravity", "off", "--challenge"]), 0)
        launch.assert_called_once_with(None, None, "terminal", challenge=True, falling_enabled=False)
        self._check_popup(fail=False, challenge=True, falling_enabled=False)

    def test_render_forwards_responsive_demo_and_gravity_setting(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli.sys.stdout, "isatty", return_value=True), patch("terminal_smash.ui.run") as run:
            self.assertEqual(cli._render("arena", "demo", demo=True, falling_enabled=False), 0)
        run.assert_called_once_with("arena", label="demo", demo=True, falling_enabled=False)

    def test_gravity_setting_rejects_non_game_commands(self):
        for argument in ("--doctor", "--session"):
            with self.subTest(argument=argument), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.main([argument, "--gravity", "off"])
                self.assertEqual(error.exception.code, 2)

    def test_snapshot_is_private_safely_quoted_and_removed(self):
        self._check_popup(fail=False)

    def test_snapshot_is_removed_after_popup_error(self):
        self._check_popup(fail=True)

    def _check_popup(self, fail, challenge=False, falling_enabled=True):
        real_temporary_directory = tempfile.TemporaryDirectory
        with real_temporary_directory(prefix="smash tests '$ ") as directory:
            root = Path(directory)
            (root / "terminal-smash").write_text("#!/usr/bin/env python3\n")
            source = "\x1b[31mREAL terminal output\x1b[0m\n"
            seen = []
            snapshot_paths = []

            def mock_tmux(arguments, **kwargs):
                seen.append(arguments)
                if arguments == ["-V"]:
                    return completed("tmux 3.4\n")
                if arguments[0] == "capture-pane":
                    self.assertEqual(arguments, ["capture-pane", "-p", "-e", "-t", "%7"])
                    return completed(source)
                self.assertEqual(arguments[:10], ["display-popup", "-E", "-B", "-w", "100%", "-h", "100%", "-t", "%7", "-c"])
                self.assertEqual(arguments[10], "/dev/pts/123")
                shell = shlex.split(arguments[-1])
                self.assertEqual(shell[0], str(root / "terminal-smash"))
                self.assertEqual(shell[1], "--snapshot")
                self.assertEqual(shell[3:], ["--label", "a label ' $()"] + (["--challenge"] if challenge else []) + ([] if falling_enabled else ["--gravity", "off"]))
                snapshot = Path(shell[2])
                snapshot_paths.append(snapshot)
                self.assertEqual(snapshot.read_text(), source)
                self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(snapshot.parent.stat().st_mode), 0o700)
                if fail:
                    raise cli.UserError("popup failed")
                return completed()

            with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%2"}), patch.object(cli, "__file__", str(root / "terminal_smash" / "cli.py")), patch.object(cli, "_tmux", side_effect=mock_tmux), patch.object(cli.tempfile, "TemporaryDirectory", side_effect=lambda **kw: real_temporary_directory(dir=root, **kw)):
                if fail:
                    with self.assertRaisesRegex(cli.UserError, "popup failed"):
                        cli._launch_popup("%7", "/dev/pts/123", "a label ' $()", challenge=challenge, falling_enabled=falling_enabled)
                else:
                    self.assertEqual(cli._launch_popup("%7", "/dev/pts/123", "a label ' $()", challenge=challenge, falling_enabled=falling_enabled), 0)
            self.assertEqual([command[0] for command in seen], ["-V", "capture-pane", "display-popup"])
            self.assertEqual(len(snapshot_paths), 1)
            self.assertFalse(snapshot_paths[0].parent.exists())

    def test_capture_error_does_not_open_popup(self):
        with patch.dict(os.environ, {"TMUX": "socket", "TMUX_PANE": "%0"}), patch.object(cli, "_tmux", side_effect=[completed("tmux 3.4"), cli.UserError("capture failed")]) as tmux:
            with self.assertRaisesRegex(cli.UserError, "capture failed"):
                cli._launch_popup(None, None, "terminal")
            self.assertEqual(tmux.call_count, 2)

    def test_version_requirement_is_clear(self):
        with patch.object(cli, "_tmux", return_value=completed("tmux 3.1c")):
            with self.assertRaisesRegex(cli.UserError, "3.4"):
                cli._tmux_version()

    def test_file_is_size_bounded_and_invalid_utf8_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            source.write_bytes(b"test\xff")
            self.assertEqual(cli._read_file(source), "test\ufffd")
            with patch.object(cli, "MAX_FILE_BYTES", 4):
                with self.assertRaisesRegex(cli.UserError, "za duży"):
                    cli._read_file(source)

    def test_render_requires_real_terminal(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(cli.UserError, "interaktywnego"):
                cli._render("text", "test")

    def test_session_executes_tmux_attach_or_create(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "_tmux_version", return_value="tmux 3.4"), patch.object(cli.os, "execvp") as execute:
            self.assertEqual(cli.main(["--session"]), 0)
        execute.assert_called_once_with("tmux", ["tmux", "new-session", "-A", "-s", "smash"])


class InstallerTests(unittest.TestCase):
    def install(self, directory, *arguments):
        return subprocess.run(
            [str(ROOT / "install.sh"), "--prefix", str(directory / "local '$"), "--config-dir", str(directory / "config"), "--tmux-config", str(directory / ".tmux.conf"), "--no-reload", *arguments],
            check=False, text=True, capture_output=True,
        )

    def test_default_config_selection_preserves_existing_xdg_configuration(self):
        installer = (ROOT / "install.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        for selected in ("dotfile", "xdg", "fallback"):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                user_dir = root / "user"
                xdg_dir = root / "xdg"
                dotfile = user_dir / ".tmux.conf"
                xdg_config = xdg_dir / "tmux" / "tmux.conf"
                fallback = user_dir / ".config" / "tmux" / "tmux.conf"
                existing = [fallback]
                if selected in ("dotfile", "xdg"):
                    existing.append(xdg_config)
                if selected == "dotfile":
                    existing.append(dotfile)
                for config in existing:
                    config.parent.mkdir(parents=True, exist_ok=True)
                    config.write_text("# user tmux settings\n")
                chosen = {"dotfile": dotfile, "xdg": xdg_config, "fallback": fallback}[selected]
                with patch.object(Path, "home", return_value=user_dir), patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_dir)}), patch.object(sys, "argv", [str(ROOT / "install.sh"), str(ROOT), "--no-reload"]), contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(installer, str(ROOT / "install.sh"), "exec"), {"__name__": "__main__"})
                self.assertIn("# >>> terminal-smash", chosen.read_text())
                for other in existing:
                    if other != chosen:
                        self.assertEqual(other.read_text(), "# user tmux settings\n")
                if selected != "dotfile":
                    self.assertFalse(dotfile.exists(), "Installer must not shadow an existing XDG config")

    def test_install_is_idempotent_and_uninstall_preserves_user_config(self):
        with tempfile.TemporaryDirectory(prefix="terminal-smash-install-") as temporary:
            root = Path(temporary)
            config = root / ".tmux.conf"
            original = "# existing user settings\nset -g history-limit 10000\n"
            config.write_text(original)
            first = self.install(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            installed_config = config.read_text()
            launcher = root / "local '$" / "bin" / "terminal-smash"
            self.assertTrue(launcher.is_symlink())
            help_result = subprocess.run([str(launcher), "--help"], text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            second = self.install(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(config.read_text(), installed_config)
            self.assertEqual(installed_config.count("# >>> terminal-smash"), 1)
            removal = self.install(root, "--uninstall")
            self.assertEqual(removal.returncode, 0, removal.stderr)
            self.assertEqual(config.read_text(), original)
            self.assertFalse(launcher.exists())
            self.assertFalse((root / "config" / "tmux.conf").exists())
            self.assertTrue(list(root.glob(".tmux.conf.terminal-smash.bak-*")))

    def test_installer_refuses_foreign_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "local '$" / "bin" / "terminal-smash"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("my unrelated tool")
            result = self.install(root)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(launcher.read_text(), "my unrelated tool")
            self.assertFalse((root / ".tmux.conf").exists())

    def test_uninstall_preserves_modified_installed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.install(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            source = root / "local '$" / "share" / "terminal-smash" / "terminal_smash" / "cli.py"
            source.write_text("# my changes\n")
            removal = self.install(root, "--uninstall")
            self.assertEqual(removal.returncode, 0, removal.stderr)
            self.assertEqual(source.read_text(), "# my changes\n")


if __name__ == "__main__":
    unittest.main()
