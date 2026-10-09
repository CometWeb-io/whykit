"""Restart the writer at real filesystem boundaries, including legacy journals."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whykit import io

ROOT = Path(__file__).resolve().parents[1]

CRASH_COMMIT = r'''
import json, os, sys
from pathlib import Path
from whykit import io
root, stop = Path(sys.argv[1]), int(sys.argv[2])
events = []
def boundary(label):
    events.append(label)
    if len(events) == stop:
        os._exit(77)
replace, fsync, unlink = os.replace, os.fsync, Path.unlink
def replaced(*args, **kwargs):
    result = replace(*args, **kwargs)
    boundary("replace")
    return result
def synced(*args, **kwargs):
    result = fsync(*args, **kwargs)
    boundary("fsync")
    return result
def removed(self, *args, **kwargs):
    result = unlink(self, *args, **kwargs)
    if self.name.endswith(".new"):
        boundary("unlink")
    return result
os.replace, os.fsync, Path.unlink = replaced, synced, removed
if stop == 0:
    os._exit(77)
with io.vault_mutation_lock(root):
    pass
print(json.dumps(events))
'''


class TransactionRecoveryTests(unittest.TestCase):
    def _stage(self, root: Path, count: int = 2) -> tuple[Path, dict[Path, str]]:
        root.mkdir()
        (root / "notes").mkdir()
        updates = {root / "notes" / f"{n}.md": f"new contents {n}\n" for n in range(count)}
        return io.stage_transaction(root, updates), updates

    def _child(self, code: str, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, *map(str, args)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, timeout=30,
        )

    def _recovered(self, root: Path, tx: Path, updates: dict[Path, str]) -> None:
        for _ in range(2):
            with io.vault_mutation_lock(root, timeout=2):
                pass
            for path, expected in updates.items():
                self.assertEqual(path.read_text(encoding="utf-8"), expected)
            self.assertTrue((tx / "COMMITTED").exists())
            self.assertEqual(list(tx.glob("*.new")), [])

    def test_process_death_after_every_replace_fsync_and_cleanup(self) -> None:
        for count in (2, 5):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                tx, updates = self._stage(base / "reference", count)
                reference = self._child(CRASH_COMMIT, base / "reference", -1)
                self.assertEqual(reference.returncode, 0, reference.stderr)
                events = json.loads(reference.stdout)
                self.assertIn("replace", events)
                self.assertIn("fsync", events)
                self.assertEqual(events.count("unlink"), count)
                self._recovered(base / "reference", tx, updates)
                for stop in range(len(events) + 1):
                    with self.subTest(files=count, boundary=stop):
                        root = base / f"stop-{stop}"
                        tx, updates = self._stage(root, count)
                        child = self._child(CRASH_COMMIT, root, stop)
                        self.assertEqual(child.returncode, 77, child.stderr)
                        self._recovered(root, tx, updates)

    def test_legacy_missing_source_is_recovered_only_from_matching_target(self) -> None:
        for matching in (True, False):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "vault"
                tx, updates = self._stage(root)
                first = next(iter(updates))
                first.write_bytes((tx / "0.new").read_bytes() if matching else b"unrelated data\n")
                (tx / "0.new").unlink()
                if matching:
                    self._recovered(root, tx, updates)
                else:
                    with self.assertRaisesRegex(RuntimeError, "missing staged content"):
                        with io.vault_mutation_lock(root):
                            pass
                    self.assertEqual(first.read_text(encoding="utf-8"), "unrelated data\n")
                    self.assertFalse((tx / "COMMITTED").exists())
                    self.assertTrue((tx / "1.new").exists())

    def test_write_and_commit_marker_failures_leave_recoverable_sources(self) -> None:
        for failure_target in ("0.md", "1.md", "COMMITTED"):
            with self.subTest(target=failure_target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "vault"
                tx, updates = self._stage(root)
                original = os.replace

                def fail(source, target, failure_target=failure_target, original=original):
                    if Path(target).name == failure_target:
                        raise OSError(errno.ENOSPC, "synthetic disk full")
                    return original(source, target)

                with patch.object(os, "replace", fail), self.assertRaises(OSError):
                    io.commit_transaction(root, tx)
                self.assertTrue((tx / "0.new").exists())
                self.assertTrue((tx / "1.new").exists())
                self._recovered(root, tx, updates)

    @unittest.skipIf(os.name == "nt", "directory fsync is not exposed on Windows")
    def test_directory_fsync_io_error_is_reported_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            tx, updates = self._stage(root)
            with patch.object(io, "_sync_directory", side_effect=OSError(errno.EIO, "synthetic sync failure")):
                with self.assertRaises(OSError):
                    io.commit_transaction(root, tx)
            self.assertFalse((tx / "COMMITTED").exists())
            self.assertTrue((tx / "0.new").exists())
            self._recovered(root, tx, updates)

    def test_cleanup_failure_does_not_rewrite_committed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            tx, updates = self._stage(root)
            original = Path.unlink

            def fail(path, *args, **kwargs):
                if path.name == "0.new":
                    raise OSError(errno.EACCES, "synthetic cleanup failure")
                return original(path, *args, **kwargs)

            with patch.object(Path, "unlink", fail), self.assertRaises(OSError):
                io.commit_transaction(root, tx)
            self.assertTrue((tx / "COMMITTED").exists())
            first = next(iter(updates))
            first.write_text("a later edit\n", encoding="utf-8")
            with io.vault_mutation_lock(root):
                pass
            self.assertEqual(first.read_text(encoding="utf-8"), "a later edit\n")
            self.assertEqual(list(tx.glob("*.new")), [])

    def test_invalid_manifest_is_rejected_before_writing_any_target(self) -> None:
        for changed in ({"staged": "../0.new"}, {"sha256": "invalid"}, {"target": ".whykit/transactions/other/READY"}):
            with self.subTest(entry=changed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "vault"
                tx, updates = self._stage(root)
                manifest = json.loads((tx / "manifest.json").read_text(encoding="utf-8"))
                manifest[1].update(changed)
                (tx / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    io.commit_transaction(root, tx)
                self.assertFalse(any(path.exists() for path in updates))

    def test_corrupt_staged_bytes_are_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            tx, updates = self._stage(root)
            (tx / "0.new").write_text("corrupt\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                io.commit_transaction(root, tx)
            self.assertFalse(any(path.exists() for path in updates))

    def test_staging_refuses_to_replace_transaction_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaises(ValueError):
                io.stage_transaction(root, {root / ".whykit/READY": "not vault content\n"})

    def test_note_and_map_are_recovered_together_after_each_target_write(self) -> None:
        sys.path.insert(0, str(ROOT / "tests"))
        from _vaults import fresh_vault

        code = r'''
import os, sys
from pathlib import Path
from whykit import io, scaffold
root, stop = Path(sys.argv[1]), int(sys.argv[2])
original = io.atomic_write_bytes
count = 0
def write(path, data):
    global count
    original(path, data)
    count += 1
    if count == stop:
        os._exit(77)
io.atomic_write_bytes = write
scaffold.create_note(root, "Crash probe", workstream="notes", owner="Example reviewer", link_from="Home.md")
'''
        for stop in (1, 2):
            with self.subTest(write=stop), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "vault"
                fresh_vault(root)
                child = self._child(code, root, stop)
                self.assertEqual(child.returncode, 77, child.stderr)
                with io.vault_mutation_lock(root):
                    pass
                self.assertTrue((root / "notes/crash-probe.md").is_file())
                self.assertIn("[[notes/crash-probe|Crash probe]]", (root / "Home.md").read_text(encoding="utf-8"))
                tx = next((root / ".whykit/transactions").iterdir())
                manifest = json.loads((tx / "manifest.json").read_text(encoding="utf-8"))
                for entry in manifest:
                    self.assertEqual(hashlib.sha256((root / entry["target"]).read_bytes()).hexdigest(), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
