"""Synthetic byte-change observations preserve version history and scoped reads."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _jsonschema import validate
from test_artifact_store import register, tree_bytes
from whykit import artifact_store as store, io
from whykit.artifact_store import capture_artifact, observation_path, read_source_observation

DAY = dt.date(2026, 10, 27)


class SourceObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "vault"
        self.root.mkdir()
        register(self.root, "public")
        self.source = self.root.parent / "source.bin"

    def capture(self, body: bytes, when: str, *, write=True) -> dict:
        self.source.write_bytes(body)
        plan = capture_artifact(self.root, "E-001", self.source, recorded_at=when)
        return capture_artifact(self.root, "E-001", self.source, recorded_at=when,
                                write=True, expected_sha256=plan["expected_sha256"]) if write else plan

    def read(self, pinned: dict, **kwargs) -> dict | None:
        return read_source_observation(self.root, "E-001", pinned["source_version"], today=DAY, **kwargs)

    def test_capture_and_canonical_observation_are_one_transaction_and_preview_is_read_only(self) -> None:
        before = tree_bytes(self.root)
        plan = self.capture(b"A\x00\xff\r\n", "2026-10-27T10:00:00Z", write=False)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertFalse((self.root / ".whykit").exists())
        with patch.object(store, "apply_transaction", wraps=io.apply_transaction) as transaction:
            result = capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T10:00:00Z",
                                      write=True, expected_sha256=plan["expected_sha256"])
            self.assertEqual(transaction.call_count, 1)
            self.assertEqual(len(transaction.call_args.args[1]), 2)
        ledger = observation_path(self.root, "E-001")
        value = json.loads(ledger.read_text(encoding="utf-8"))
        schema = json.loads((Path(__file__).parents[1] / "schemas/source-observations.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(value, schema), [])
        self.assertEqual(value["observations"], [{"source_version": result["source_version"],
                                                "recorded_at": "2026-10-27T10:00:00Z", "sensitivity": "public"}])
        self.assertEqual(ledger.read_bytes(), store._json_text(value).encode("utf-8"))
        report = self.read(result)
        self.assertEqual(report["state"], "current")
        self.assertEqual(report["source_versions"], [result["source_version"]])
        self.assertEqual(report["observation_count"], 1)
        self.assertNotIn("content_base64", json.dumps(report))
        self.assertNotIn(str(self.source), json.dumps(report))

    def test_a_b_a_dedup_records_return_to_original_without_rewriting_captures(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        old_bytes = (self.root / first["path"]).read_bytes()
        second = self.capture(b"B", "2026-10-27T11:00:00Z")
        self.assertEqual(self.read(first)["state"], "changed")
        again = self.capture(b"A", "2026-10-27T12:00:00Z")
        self.assertTrue(again["already_exists"])
        self.assertTrue(again["observation_added"])
        self.assertEqual(again["recorded_at"], first["recorded_at"])
        self.assertEqual(again["observation_recorded_at"], "2026-10-27T12:00:00Z")
        self.assertEqual((self.root / first["path"]).read_bytes(), old_bytes)
        self.assertEqual(self.read(first)["state"], "current")
        self.assertEqual(self.read(second)["state"], "changed")
        self.assertEqual(self.read(first)["observation_count"], 3)

    def test_exact_retry_is_noop_but_old_a_retry_after_b_is_stale(self) -> None:
        plan = self.capture(b"A", "2026-10-27T10:00:00Z", write=False)
        capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T10:00:00Z",
                         write=True, expected_sha256=plan["expected_sha256"])
        before = observation_path(self.root, "E-001").read_bytes()
        with patch.object(store, "apply_transaction") as transaction:
            capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T10:00:00Z",
                             write=True, expected_sha256=plan["expected_sha256"])
            transaction.assert_not_called()
        self.assertEqual(observation_path(self.root, "E-001").read_bytes(), before)
        second = self.capture(b"B", "2026-10-27T11:00:00Z")
        self.source.write_bytes(b"A")
        with self.assertRaisesRegex(ValueError, "stale"):
            capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T10:00:00Z",
                             write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(self.read(second)["state"], "current")

    def test_preview_binds_intervening_ledger_changes_even_when_target_capture_is_absent(self) -> None:
        self.capture(b"A", "2026-10-27T10:00:00Z")
        plan = self.capture(b"B", "2026-10-27T11:00:00Z", write=False)
        newest = self.capture(b"C", "2026-10-27T12:00:00Z")
        self.source.write_bytes(b"B")
        with self.assertRaisesRegex(ValueError, "stale"):
            capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T11:00:00Z",
                             write=True, expected_sha256=plan["expected_sha256"])
        self.assertFalse((self.root / plan["path"]).exists())
        self.assertEqual(self.read(newest)["state"], "current")

    def test_latest_uses_datetime_not_text_order_and_backfill_does_not_replace_it(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        self.capture(b"B", "2026-10-27T10:00:00.09Z")
        latest = self.capture(b"C", "2026-10-27T10:00:00.1Z")
        self.capture(b"D", "2026-10-26T12:00:00Z")
        result = self.read(first)
        self.assertEqual(result["state"], "changed")
        self.assertEqual(result["source_versions"], [latest["source_version"]])
        self.assertEqual(result["observed_at"], "2026-10-27T10:00:00.100000Z")

    def test_tied_distinct_versions_are_ambiguous_and_future_is_explicit(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        second = self.capture(b"B", "2026-10-27T10:00:00+00:00")
        result = self.read(first)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(set(result["source_versions"]), {first["source_version"], second["source_version"]})
        self.capture(b"C", "2026-10-28T00:00:00Z")
        self.assertEqual(self.read(first)["state"], "future")

    def test_legacy_capture_without_observation_is_unknown_not_current(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        observation_path(self.root, "E-001").unlink()
        result = self.read(first)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["ledger_sha256"])
        self.assertEqual(result["source_versions"], [])

    def test_all_history_labels_gate_before_ledger_hash_or_artifact_reads(self) -> None:
        register(self.root, "restricted")
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        register(self.root, "public")
        self.capture(b"B", "2026-10-27T11:00:00Z")
        with patch.object(store, "_hash", side_effect=AssertionError("hidden hash")), patch.object(
            store, "_load_artifact", side_effect=AssertionError("hidden capture read")
        ):
            self.assertIsNone(self.read(first, allowed_sensitivities={"public"}))
        self.assertEqual(self.read(first, allowed_sensitivities={"public", "restricted"})["state"], "changed")

    def test_known_denied_evidence_never_reads_a_ledger_or_capture(self) -> None:
        register(self.root, "restricted")
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        with patch.object(store, "_load_observations", side_effect=AssertionError("hidden ledger read")), patch.object(
            store, "_load_artifact", side_effect=AssertionError("hidden capture read")
        ):
            self.assertIsNone(self.read(first, allowed_sensitivities={"public"}))
            self.assertIsNone(store.read_artifact(self.root, "E-001", first["source_version"], allowed_sensitivities={"public"}))

    def test_legacy_private_capture_dedup_persists_its_floor_in_history(self) -> None:
        register(self.root, "restricted")
        self.capture(b"A", "2026-10-27T10:00:00Z")
        observation_path(self.root, "E-001").unlink()
        register(self.root, "public")
        self.capture(b"A", "2026-10-27T11:00:00Z")
        newest = self.capture(b"B", "2026-10-27T12:00:00Z")
        ledger = json.loads(observation_path(self.root, "E-001").read_text(encoding="utf-8"))
        self.assertEqual(ledger["observations"][0]["sensitivity"], "restricted")
        with patch.object(store, "_hash", side_effect=AssertionError("hidden history hash")), patch.object(
            store, "_load_artifact", side_effect=AssertionError("hidden history capture read")
        ):
            self.assertIsNone(self.read(newest, allowed_sensitivities={"public"}))

    def test_pinned_capture_floor_is_checked_before_decode_or_source_hash(self) -> None:
        register(self.root, "restricted")
        first = self.capture(b"PRIVATE_CAPTURE_CANARY", "2026-10-27T10:00:00Z")
        observation_path(self.root, "E-001").unlink()
        register(self.root, "public")
        self.capture(b"B", "2026-10-27T11:00:00Z")
        with patch.object(store, "_hash", side_effect=AssertionError("hidden hash")), patch.object(
            store.base64, "b64decode", side_effect=AssertionError("hidden decode")
        ):
            self.assertIsNone(self.read(first, allowed_sensitivities={"public"}))
            self.assertIsNone(store.read_artifact(self.root, "E-001", first["source_version"], allowed_sensitivities={"public"}))

    def test_all_capture_headers_gate_before_any_source_decode_or_hash(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        latest = self.capture(b"B", "2026-10-27T11:00:00Z")
        path = self.root / latest["path"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["sensitivity"] = "restricted"
        path.write_text(store._json_text(value), encoding="utf-8")
        with patch.object(store, "_hash", side_effect=AssertionError("hash before complete floor gate")), patch.object(
            store.base64, "b64decode", side_effect=AssertionError("decode before complete floor gate")
        ):
            self.assertIsNone(self.read(first, allowed_sensitivities={"public"}))

    def test_sparse_allowed_set_is_not_a_ceiling(self) -> None:
        register(self.root, "internal")
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        with patch.object(store, "_hash", side_effect=AssertionError("denied hash")):
            self.assertIsNone(self.read(first, allowed_sensitivities={"public", "restricted"}))

    def test_missing_or_tampered_latest_capture_refuses_and_never_claims_current(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        latest = self.capture(b"B", "2026-10-27T11:00:00Z")
        path = self.root / latest["path"]
        path.unlink()
        with self.assertRaises(ValueError):
            self.read(first)
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.read(first)

    def test_invalid_noncanonical_duplicate_or_oversized_ledger_is_refused(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        path = observation_path(self.root, "E-001")
        original = path.read_bytes()
        value = json.loads(original)
        cases = [original + b" ", original.replace(b'"format":', b'"format":"wrong", "format":', 1)]
        duplicate = {**value, "observations": value["observations"] * 2}
        cases.append(store._json_text(duplicate).encode())
        for key, replacement in (("recorded_at", "2026-02-30T10:00:00Z"), ("source_version", True),
                                 ("sensitivity", 0)):
            altered = json.loads(original)
            altered["observations"][0][key] = replacement
            cases.append(store._json_text(altered).encode())
        for raw in cases:
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.read(first)
            with self.assertRaises(ValueError):
                self.capture(b"B", "2026-10-27T11:00:00Z", write=False)
        path.write_bytes(original)
        with patch.object(store, "MAX_OBSERVATIONS", 1), self.assertRaises(ValueError):
            self.capture(b"B", "2026-10-27T11:00:00Z", write=False)

    def test_crash_between_capture_and_ledger_is_recovered_together(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        original = (self.root / first["path"]).read_bytes()
        plan = self.capture(b"B", "2026-10-27T11:00:00Z", write=False)
        write_bytes = io.atomic_write_bytes
        ledger = observation_path(self.root, "E-001")
        def fail_ledger(path, raw):
            if path == ledger:
                raise OSError("synthetic crash before observation target")
            write_bytes(path, raw)
        with patch.object(io, "atomic_write_bytes", side_effect=fail_ledger), self.assertRaises(OSError):
            capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T11:00:00Z",
                             write=True, expected_sha256=plan["expected_sha256"])
        with self.assertRaisesRegex(OSError, "recovery required"):
            self.read(first)
        with io.vault_mutation_lock(self.root):
            pass
        self.assertEqual(self.read(first)["state"], "changed")
        self.assertTrue((self.root / plan["path"]).exists())
        self.assertEqual((self.root / first["path"]).read_bytes(), original)
        retry = capture_artifact(self.root, "E-001", self.source, recorded_at="2026-10-27T11:00:00Z",
                                 write=True, expected_sha256=plan["expected_sha256"])
        self.assertFalse(retry["observation_added"])
        self.assertEqual(self.read(first)["observation_count"], 2)

    @unittest.skipIf(os.name == "nt", "symlink creation may require privileges")
    def test_symlink_ledger_never_reads_or_rewrites_the_external_target(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        path = observation_path(self.root, "E-001")
        outside = self.root.parent / "outside.json"
        outside.write_bytes(path.read_bytes())
        before = outside.read_bytes()
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.read(first)
        with self.assertRaises(ValueError):
            self.capture(b"B", "2026-10-27T11:00:00Z", write=False)
        self.assertEqual(outside.read_bytes(), before)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_ledger_is_refused_without_hanging(self) -> None:
        first = self.capture(b"A", "2026-10-27T10:00:00Z")
        path = observation_path(self.root, "E-001")
        path.unlink()
        os.mkfifo(path)
        code = """
from pathlib import Path
import sys
from whykit.artifact_store import read_source_observation
try:
    read_source_observation(Path(sys.argv[1]),'E-001',sys.argv[2])
except (OSError,ValueError):
    raise SystemExit(0)
raise SystemExit('FIFO accepted')
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.root), first["source_version"]],
                               capture_output=True, text=True, encoding="utf-8", timeout=3)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
