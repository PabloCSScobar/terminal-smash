"""Persistent records stay private, bounded and safe across game instances."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from terminal_smash.capture import Cell, Style
from terminal_smash import records


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="terminal-smash-records-")
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, {"XDG_STATE_HOME": self.temporary.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = Path(self.temporary.name) / "terminal-smash" / "records.json"
        self.cells = [Cell(1, 2, "A"), Cell(3, 4, "B", Style(fg=31, bold=True))]
        self.key = records.arena_key(self.cells, 80, 24)

    def write_raw(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(value)

    def test_scene_key_is_canonical_and_changes_with_layout_or_size(self):
        self.assertEqual(self.key, records.arena_key(list(reversed(self.cells)), 80, 24))
        self.assertNotEqual(self.key, records.arena_key(self.cells, 81, 24))
        self.assertNotEqual(self.key, records.arena_key(self.cells, 80, 25))
        self.assertNotEqual(self.key, records.arena_key([Cell(1, 2, "C"), *self.cells[1:]], 80, 24))
        self.assertNotEqual(self.key, records.arena_key([Cell(2, 2, "A"), *self.cells[1:]], 80, 24))
        self.assertNotEqual(self.key, records.arena_key([Cell(1, 2, "A", Style(bold=True)), *self.cells[1:]], 80, 24))

    def test_missing_file_reads_zero_without_creating_anything(self):
        self.assertEqual(records.load_best(self.key), 0)
        self.assertFalse(self.path.parent.exists())

    def test_only_highest_score_is_kept_and_arenas_are_separate(self):
        other = records.arena_key(self.cells, 100, 30)
        self.assertEqual(records.save_best(self.key, 150), 150)
        self.assertEqual(records.save_best(self.key, 20), 150)
        self.assertEqual(records.save_best(other, 200), 200)
        self.assertEqual(records.load_best(self.key), 150)
        self.assertEqual(records.load_best(other), 200)
        self.assertEqual(records.save_best(self.key, 300), 300)

    def test_corrupt_or_unbounded_files_do_not_crash(self):
        malformed = ["{", "[]", "null", '{"version":2}', '{"version":1,"records":[]}', '[' * 2000]
        with patch.object(records, "MAX_FILE_BYTES", 4096):
            malformed.append("x" * 4097)
            for document in malformed:
                with self.subTest(document=document[:40]):
                    self.write_raw(document)
                    self.assertEqual(records.load_best(self.key), 0)
        self.assertEqual(records.save_best(self.key, 42), 42)
        self.assertEqual(records.load_best(self.key), 42)

    def test_malformed_individual_records_are_ignored(self):
        invalid = [True, -1, "123", records.MAX_SCORE + 1, None]
        for score in invalid:
            with self.subTest(score=score):
                self.write_raw(json.dumps({"version": 1, "records": {self.key: {"score": score, "updated": 1}}}))
                self.assertEqual(records.load_best(self.key), 0)

    def test_records_only_store_hashes_scores_and_timestamps(self):
        sensitive = "private terminal capture"
        key = records.arena_key([Cell(index, 1, char) for index, char in enumerate(sensitive)], 80, 24)
        records.save_best(key, 123)
        raw = self.path.read_text()
        self.assertNotIn(sensitive, raw)
        document = json.loads(raw)
        self.assertEqual(set(document), {"version", "records"})
        self.assertEqual(set(document["records"][key]), {"score", "updated"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_oldest_record_is_evicted_and_replayed_arena_is_refreshed(self):
        keys = [records.arena_key(self.cells, width, 24) for width in range(80, 84)]
        with patch.object(records, "MAX_RECORDS", 3), patch.object(records.time, "time_ns", side_effect=range(1, 6)):
            for index in range(3):
                records.save_best(keys[index], 10 + index)
            records.save_best(keys[0], 1)
            records.save_best(keys[3], 30)
            self.assertEqual(records.load_best(keys[0]), 10)
            self.assertEqual(records.load_best(keys[1]), 0)
            self.assertEqual(records.load_best(keys[2]), 12)
            self.assertEqual(records.load_best(keys[3]), 30)
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 3)

    def test_concurrent_writers_preserve_largest_score_and_other_arenas(self):
        other = records.arena_key(self.cells, 100, 30)
        writes = [(self.key if index % 2 else other, index) for index in reversed(range(24))]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda pair: records.save_best(*pair), writes))
        self.assertEqual(len(results), 24)
        self.assertEqual(records.load_best(self.key), 23)
        self.assertEqual(records.load_best(other), 22)

    def test_failed_atomic_replace_preserves_previous_file_and_removes_temp(self):
        records.save_best(self.key, 100)
        original = self.path.read_bytes()
        with patch.object(records.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaises(OSError):
                records.save_best(self.key, 200)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_storage_failure_propagates_only_from_save(self):
        with patch.object(Path, "open", side_effect=PermissionError("unavailable")):
            self.assertEqual(records.load_best(self.key), 0)
            with self.assertRaises(OSError):
                records.save_best(self.key, 100)

    def test_invalid_keys_and_scores_are_rejected(self):
        for score in (-1, True, 1.5, records.MAX_SCORE + 1):
            with self.subTest(score=score), self.assertRaises(ValueError):
                records.save_best(self.key, score)
        with self.assertRaises(ValueError):
            records.load_best("../escape")
        self.assertFalse(self.path.parent.exists())


if __name__ == "__main__":
    unittest.main()
