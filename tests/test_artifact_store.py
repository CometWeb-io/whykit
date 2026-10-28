"""Offline immutable captures; all sources and registers below are synthetic."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from whykit.artifact_store import capture_artifact, read_artifact

STAMP = "2026-10-27T10:00:00Z"


def register(root: Path, label: str = "internal", *, duplicate: bool = False) -> Path:
    path = root / "00-context/evidence-register.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = f"| E-001 | Synthetic source | file | 2026-10-27 | 2026-10-27 | example.invalid/source | Example | {label} |\n"
    path.write_text(
        "---\ntitle: Evidence\ntype: reference\nstatus: draft\nowner: Ada Example\n"
        "created: 2026-10-27\nlast_updated: 2026-10-27\nsource_of_truth: false\nsensitivity: public\n---\n\n"
        "# Evidence\n\n| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n" + row + (row if duplicate else ""),
        encoding="utf-8",
    )
    return path


def tree_bytes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "vault"
        self.root.mkdir()
        self.register = register(self.root)
        self.source = self.root.parent / "source.bin"
        self.source.write_bytes(b"\xef\xbb\xbfRaw\r\n" + "żółw".encode() + b"\x00\xff")

    def capture(self, **kwargs) -> dict:
        return capture_artifact(self.root, "E-001", self.source, recorded_at=STAMP, **kwargs)

    def apply(self) -> dict:
        plan = self.capture()
        return self.capture(write=True, expected_sha256=plan["expected_sha256"])

    def test_preview_no_writes_and_binary_round_trip(self) -> None:
        before = tree_bytes(self.root)
        plan = self.capture()
        self.assertEqual(tree_bytes(self.root), before)
        self.assertFalse((self.root / ".whykit").exists())
        self.assertFalse((self.root / "00-context/artifacts").exists())
        self.assertFalse(plan["applied"])
        self.assertNotIn("content_base64", plan)
        self.assertNotIn("source", plan)
        with patch("whykit.artifact_store.apply_transaction", wraps=__import__("whykit.io", fromlist=["apply_transaction"]).apply_transaction) as transaction:
            result = self.capture(write=True, expected_sha256=plan["expected_sha256"])
            self.assertEqual(transaction.call_count, 1)
        self.assertTrue(result["applied"])
        raw = self.source.read_bytes()
        self.assertEqual(result["source_version"], "sha256:" + hashlib.sha256(raw).hexdigest())
        envelope = json.loads((self.root / result["path"]).read_text(encoding="utf-8"))
        schema = json.loads((Path(__file__).parents[1] / "schemas/evidence-artifact.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(envelope, schema), [])
        self.assertEqual(base64.b64decode(envelope["content_base64"]), raw)
        self.assertEqual(read_artifact(self.root, "E-001", result["source_version"]), raw)
        self.assertEqual(self.register.read_bytes(), before["00-context/evidence-register.md"])

    def test_dedup_is_idempotent_and_prior_versions_are_immutable(self) -> None:
        preview = self.capture()
        first = self.capture(write=True, expected_sha256=preview["expected_sha256"])
        original = (self.root / first["path"]).read_bytes()
        with patch("whykit.artifact_store.apply_transaction") as transaction:
            replay = self.capture(write=True, expected_sha256=preview["expected_sha256"])
            self.assertTrue(replay["already_exists"])
            transaction.assert_not_called()
        plan = capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-28T10:00:00Z")
        self.assertTrue(plan["already_exists"])
        with patch("whykit.artifact_store.apply_transaction") as transaction:
            capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-28T10:00:00Z",
                             write=True, expected_sha256=plan["expected_sha256"])
            self.assertEqual(transaction.call_count, 1)
        self.assertEqual((self.root / first["path"]).read_bytes(), original)
        self.source.write_bytes(b"new version\n")
        second = self.apply()
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual((self.root / first["path"]).read_bytes(), original)
        self.assertEqual(read_artifact(self.root, "E-001", first["source_version"]), b"\xef\xbb\xbfRaw\r\n" + "żółw".encode() + b"\x00\xff")
        self.assertEqual(len(list((self.root / "00-context/artifacts/E-001").glob("*.json"))), 2)

    def test_effective_register_floor_and_utc_normalization(self) -> None:
        self.register.write_text(self.register.read_text(encoding="utf-8").replace("sensitivity: public", "sensitivity: confidential"), encoding="utf-8")
        result = capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T10:00:00+00:00")
        self.assertEqual(result["sensitivity"], "confidential")
        self.assertEqual(result["recorded_at"], STAMP)

    def test_stale_source_register_or_parameters_are_refused(self) -> None:
        for changed in ("source", "register", "timestamp"):
            with self.subTest(changed=changed):
                register(self.root)
                self.source.write_bytes(b"reviewed")
                plan = self.capture()
                stamp = STAMP
                if changed == "source":
                    self.source.write_bytes(b"changed!")
                elif changed == "register":
                    register(self.root, "restricted")
                else:
                    stamp = "2026-10-28T10:00:00Z"
                with self.assertRaisesRegex(ValueError, "stale"):
                    capture_artifact(self.root, "E-001", self.source, recorded_at=stamp,
                                     write=True, expected_sha256=plan["expected_sha256"])
                self.assertFalse((self.root / "00-context/artifacts").exists())
        with self.assertRaises(ValueError):
            self.capture(write=True)

    def test_invalid_unknown_duplicate_and_unreadable_evidence_fail_closed(self) -> None:
        for eid in ("../E-001", "E-001\n", "E-００１", "E-999"):
            with self.subTest(eid=eid), self.assertRaises(ValueError):
                capture_artifact(self.root, eid, self.source, recorded_at=STAMP)
        register(self.root, duplicate=True)
        with self.assertRaises(ValueError):
            self.capture()
        register(self.root, "unclassified")
        with self.assertRaises(ValueError):
            self.capture()
        register(self.root)
        for stamp in ("", "2026-10-27", "2026-10-27T10:00:00+02:00", "2026-02-30T10:00:00Z"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                capture_artifact(self.root, "E-001", self.source, recorded_at=stamp)

    def test_capture_and_current_evidence_floor_both_restrict_reads(self) -> None:
        register(self.root, "restricted")
        result = self.apply()
        register(self.root, "public")
        self.assertIsNone(read_artifact(self.root, "E-001", result["source_version"], allowed_sensitivities={"public"}))
        self.assertEqual(read_artifact(self.root, "E-001", result["source_version"], allowed_sensitivities={"restricted"}), self.source.read_bytes())
        self.source.write_bytes(b"another")
        public = self.apply()
        register(self.root, "restricted")
        self.assertIsNone(read_artifact(self.root, "E-001", public["source_version"], allowed_sensitivities={"public"}))

    def test_tampered_or_duplicate_key_capture_never_counts_as_dedup(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        original = path.read_bytes()
        value = json.loads(original)
        for key, replacement in (("evidence_id", "E-002"), ("content_base64", "eA=="), ("size_bytes", True), ("sensitivity", "unknown")):
            with self.subTest(key=key):
                altered = {**value, key: replacement}
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_artifact(self.root, "E-001", result["source_version"])
                with self.assertRaises(ValueError):
                    self.capture()
        path.write_text(original.decode().replace('"format":', '"format":"wrong", "format":', 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_artifact(self.root, "E-001", result["source_version"])

    def test_limits_and_no_symlink_sources_or_capture_paths(self) -> None:
        with self.assertRaises(ValueError):
            self.capture(max_bytes=2)
        with self.assertRaises(ValueError):
            self.capture(max_bytes=True)
        if os.name == "nt":
            return
        alias = self.root.parent / "alias"
        alias.symlink_to(self.source)
        with self.assertRaises((ValueError, OSError)):
            capture_artifact(self.root, "E-001", alias, recorded_at=STAMP)
        destination = self.root / "00-context/artifacts"
        destination.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.capture()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_source_and_capture_are_refused_in_bounded_subprocess(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        for operation in ("capture", "read"):
            with self.subTest(operation=operation):
                if operation == "capture":
                    self.source.unlink()
                    os.mkfifo(self.source)
                else:
                    path.unlink()
                    os.mkfifo(path)
                script = """
from pathlib import Path
import sys
from whykit.artifact_store import capture_artifact, read_artifact
try:
    if sys.argv[1] == 'capture':
        capture_artifact(Path(sys.argv[2]), 'E-001', Path(sys.argv[3]), recorded_at='2026-10-27T10:00:00Z')
    else:
        read_artifact(Path(sys.argv[2]), 'E-001', sys.argv[4])
except (OSError, ValueError):
    raise SystemExit(0)
raise SystemExit('FIFO accepted')
"""
                child = subprocess.run([sys.executable, "-c", script, operation, str(self.root), str(self.source), result["source_version"]],
                                       capture_output=True, text=True, timeout=3)
                self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
