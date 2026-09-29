#!/usr/bin/env bash
# Terminal Smash user-local installer. No sudo and no third-party packages.
set -eu
INSTALL_SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 - "$INSTALL_SOURCE" "$@" <<'PY'
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

SOURCE = Path(sys.argv[1])


def default_tmux_config():
    """Follow tmux's existing user config without shadowing an XDG config."""
    user_dir = Path.home()
    candidates = [user_dir / ".tmux.conf"]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "tmux" / "tmux.conf")
    candidates.append(user_dir / ".config" / "tmux" / "tmux.conf")
    return next((path for path in candidates if path.exists()), candidates[0])


parser = argparse.ArgumentParser(description="Instalacja Terminal Smash dla bieżącego użytkownika")
parser.add_argument("--uninstall", action="store_true")
parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
parser.add_argument("--config-dir", type=Path, default=Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "terminal-smash")
parser.add_argument("--tmux-config", type=Path, default=default_tmux_config())
parser.add_argument("--no-reload", action="store_true", help="nie przeładowuj działającego serwera tmux")
args = parser.parse_args(sys.argv[2:])
prefix = args.prefix.expanduser().resolve()
data = prefix / "share" / "terminal-smash"
launcher = prefix / "bin" / "terminal-smash"
fragment = args.config_dir.expanduser().resolve() / "tmux.conf"
config = args.tmux_config.expanduser().resolve()
marker = data / ".terminal-smash-install.json"
BEGIN = "# >>> terminal-smash (managed by install.sh) >>>"
END = "# <<< terminal-smash <<<"
FORMAT = "terminal-smash-local-install-v1"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def quote_tmux(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'


def atomic_write(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".terminal-smash-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def without_block(text):
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if not starts and not ends:
        return text
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError(f"Niepoprawny oznaczony blok Terminal Smash w {config}; plik pozostaje bez zmian.")
    return "".join(lines[:starts[0]] + lines[ends[0] + 1:])


def update_config(raw):
    previous = config.read_bytes() if config.exists() else b""
    if previous == raw:
        return
    if config.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = config.with_name(config.name + ".terminal-smash.bak-" + stamp)
        shutil.copy2(config, backup)
        print(f"Kopia konfiguracji: {backup}")
    atomic_write(config, raw, config.stat().st_mode & 0o777 if config.exists() else 0o644)


def tmux(arguments):
    return subprocess.run(["tmux", *arguments], text=True, capture_output=True, check=False)


def reload_tmux(uninstall=False):
    if args.no_reload or not shutil.which("tmux") or tmux(["list-sessions"]).returncode:
        return
    if uninstall:
        key = tmux(["list-keys", "-T", "prefix", "S"])
        if str(data / "terminal-smash") in key.stdout:
            tmux(["unbind-key", "-T", "prefix", "S"])
        result = tmux(["source-file", str(config)])
    else:
        result = tmux(["source-file", str(fragment)])
    if result.returncode:
        print("Uwaga: tmux nie przeładował konfiguracji: " + result.stderr.strip(), file=sys.stderr)


try:
    manifest = json.loads(marker.read_text()) if marker.exists() else None
    if manifest is not None and manifest.get("format") != FORMAT:
        raise ValueError(f"Nieznany format instalacji w {data}; niczego nie nadpisuję.")
    old_config = config.read_text(encoding="utf-8") if config.exists() else ""
    bare_config = without_block(old_config)
    owned_launcher = launcher.is_symlink() and launcher.resolve() == data / "terminal-smash"
    if args.uninstall:
        if manifest is None:
            raise ValueError(f"Brak rozpoznanej instalacji w {data}; nie usuwam cudzych plików.")
        update_config(bare_config.encode("utf-8"))
        reload_tmux(uninstall=True)
        if owned_launcher:
            launcher.unlink()
        if fragment.is_file() and digest(fragment.read_bytes()) == manifest.get("fragment_sha256"):
            fragment.unlink()
            try:
                fragment.parent.rmdir()
            except OSError:
                pass
        elif fragment.exists():
            print(f"Zachowuję zmieniony plik: {fragment}")
        for relative, expected in manifest.get("files", {}).items():
            path = data / relative
            if not path.resolve().is_relative_to(data) or path.is_symlink():
                continue
            if path.is_file() and digest(path.read_bytes()) == expected:
                path.unlink()
            elif path.exists():
                print(f"Zachowuję zmieniony plik: {path}")
        # Bytecode is generated by Python and contains no source-only user files.
        cache = data / "terminal_smash" / "__pycache__"
        if cache.is_dir() and not cache.is_symlink():
            for path in cache.glob("*.pyc"):
                if not path.is_symlink():
                    path.unlink()
            try:
                cache.rmdir()
            except OSError:
                pass
        marker.unlink()
        for directory in (data / "terminal_smash", data):
            try:
                directory.rmdir()
            except OSError:
                pass
        print("Terminal Smash odinstalowany. Kopie konfiguracji i zmienione pliki zostały zachowane.")
        raise SystemExit(0)

    if data.exists() and manifest is None:
        raise ValueError(f"Katalog {data} już istnieje i nie należy do instalatora.")
    if (launcher.exists() or launcher.is_symlink()) and not owned_launcher:
        raise ValueError(f"Plik {launcher} już istnieje; nie nadpisuję go.")
    if fragment.exists() and (manifest is None or digest(fragment.read_bytes()) != manifest.get("fragment_sha256")):
        raise ValueError(f"Plik {fragment} zawiera własne zmiany; nie nadpisuję go.")
    files = {"terminal-smash": (SOURCE / "terminal-smash").read_bytes()}
    for source in sorted((SOURCE / "terminal_smash").glob("*.py")):
        files["terminal_smash/" + source.name] = source.read_bytes()
    if not all("terminal_smash/" + name in files for name in ("cli.py", "ui.py", "model.py")):
        raise ValueError("Niekompletne źródła Terminal Smash; przerwano instalację.")
    shell_command = shlex.join([str(data / "terminal-smash")]) + " --pane '#{pane_id}' --client '#{client_name}'"
    fragment_text = "# Terminal Smash: generated by install.sh\n" + "bind-key -T prefix S run-shell -b " + quote_tmux(shell_command) + "\n"
    block = BEGIN + "\nsource-file " + quote_tmux(str(fragment)) + "\n" + END + "\n"
    new_config = bare_config + ("" if not bare_config or bare_config.endswith("\n") else "\n") + block
    for relative, raw in files.items():
        atomic_write(data / relative, raw, 0o755 if relative == "terminal-smash" else 0o644)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not owned_launcher:
        launcher.symlink_to(data / "terminal-smash")
    atomic_write(fragment, fragment_text.encode("utf-8"))
    new_manifest = {"format": FORMAT, "files": {name: digest(raw) for name, raw in files.items()}, "fragment_sha256": digest(fragment_text.encode("utf-8"))}
    atomic_write(marker, json.dumps(new_manifest, indent=2).encode("utf-8"))
    update_config(new_config.encode("utf-8"))
    reload_tmux()
    print(f"Zainstalowano: {launcher}")
    print("Uruchom: terminal-smash --session")
    print("W tmux: Ctrl+b, potem Shift+s. Wyjście z animacji: Esc lub Q.")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print("Dodaj do ~/.bashrc: export PATH=\"" + str(launcher.parent) + ':$PATH"')
        print("Do tego czasu używaj pełnej ścieżki launchera podanej wyżej.")
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f"Instalator: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
