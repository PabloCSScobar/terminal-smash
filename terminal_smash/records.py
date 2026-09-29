"""Private, bounded records for 30-second rounds; never store terminal text."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .capture import Cell

MAX_RECORDS = 256
MAX_FILE_BYTES = 256 * 1024
MAX_SCORE = 10**12
_KEY = re.compile(r"[0-9a-f]{64}\Z")


def arena_key(cells: list[Cell], width: int, height: int, *, falling_enabled: bool = True) -> str:
    """Fingerprint the scene and size so different arenas have separate records."""
    digest = hashlib.sha256()
    digest.update(f"terminal-smash:arcade-v2:30:{width}:{height}\n".encode("ascii"))
    if not falling_enabled:
        digest.update(b"gravity:off\n")
    for cell in sorted(cells, key=lambda value: (value.y, value.x)):
        row = [cell.x, cell.y, cell.char, cell.width,
               cell.style.fg, cell.style.bg, cell.style.bold]
        digest.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _path() -> Path:
    configured = os.environ.get("XDG_STATE_HOME", "")
    state = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".local" / "state"
    return state / "terminal-smash" / "records.json"


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("arena key must be a SHA-256 hexadecimal digest")


def _read(path: Path, *, tolerate_io: bool) -> dict[str, dict[str, int]]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return {}
    except OSError:
        if tolerate_io:
            return {}
        raise
    if len(raw) > MAX_FILE_BYTES:
        return {}
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return {}
    if not isinstance(document, dict) or document.get("version") != 1:
        return {}
    entries = document.get("records")
    if not isinstance(entries, dict):
        return {}
    valid = {}
    for key, entry in entries.items():
        if not _KEY.fullmatch(key) or not isinstance(entry, dict):
            continue
        score, updated = entry.get("score"), entry.get("updated")
        if type(score) is int and 0 <= score <= MAX_SCORE and type(updated) is int and 0 <= updated < 10**30:
            valid[key] = {"score": score, "updated": updated}
    return dict(sorted(valid.items(), key=lambda item: (item[1]["updated"], item[0]))[-MAX_RECORDS:])


def load_best(key: str) -> int:
    """Read a record, treating a missing, corrupt or unreadable file as empty."""
    _validate_key(key)
    return _read(_path(), tolerate_io=True).get(key, {}).get("score", 0)


def save_best(key: str, score: int) -> int:
    """Save the larger score atomically, serializing concurrent game instances.

    Filesystem errors propagate so the UI can report unavailable persistence
    without stopping the game. The lock uses a stable file separate from the
    JSON file: replacing the JSON while locking it would race with other games.
    """
    _validate_key(key)
    if type(score) is not int or not 0 <= score <= MAX_SCORE:
        raise ValueError("score must be a nonnegative integer below the record limit")
    path = _path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        entries = _read(path, tolerate_io=False)
        best = max(score, entries.get(key, {}).get("score", 0))
        entries[key] = {"score": best, "updated": time.time_ns()}
        # Refreshing an existing scene keeps it among the most recently played.
        entries = dict(sorted(entries.items(), key=lambda item: (item[1]["updated"], item[0]))[-MAX_RECORDS:])
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="records-", suffix=".tmp", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"version": 1, "records": entries}, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return best
