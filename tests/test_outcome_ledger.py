"""Synthetic outcome plans and historical measurements never certify value or cause."""
from __future__ import annotations

import concurrent.futures
import copy
import datetime as dt
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
from _vaults import approved_decision, fresh_vault
from whykit.artifact_store import capture_artifact
from whykit.claim_ledger import create_claim
from whykit.lint import load_note
from whykit.parse_cache import persistent
from whykit.outcome_ledger import create_outcome, list_outcomes, outcome_references, outcome_visibility, read_outcome, verify_outcome
from whykit.scaffold import create_decision, create_evidence
from whykit.sensitivity import note_sensitivity_level

DAY = dt.date(2026, 10, 27)


class OutcomeLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "vault"
        fresh_vault(self.root)
        self.did, self.decision = create_decision(self.root, "Synthetic bounded decision", owner="Ada Example", today=DAY)
        self.data = {"decision_id": self.did,
                     "metric": {"name": "Latency", "unit": "ms", "definition": "Declared p95 for the explicitly described sample."},
                     "baseline": {"value": "1.00", "as_of": "2026-10-26", "claim": None},
                     "target": {"value": "-0", "by": None}, "observed": None,
                     "implementation_refs": ["issue:EXAMPLE-42", "https://example.invalid/change"]}

    def create(self, data=None, **kwargs) -> dict:
        return create_outcome(self.root, self.data if data is None else data, owner="Ada Example", today=DAY, **kwargs)

    def apply(self, data=None, **kwargs) -> dict:
        plan = self.create(data, **kwargs)
        return self.create(data, write=True, expected_sha256=plan["expected_sha256"], **kwargs)

    def pinned_sample(self) -> tuple[dict, dict, Path]:
        eid = create_evidence(self.root, source="Synthetic measurement", location="https://example.invalid/measurement",
                                 kind="fixture", claims="Captured text is retained.", today=DAY)
        source = self.root.parent / "measurement.bin"
        source.write_bytes(b"Declared sample 12.00 ms\r\n")
        capture = capture_artifact(self.root, eid, source, recorded_at="2026-10-27T10:00:00Z")
        capture_artifact(self.root, eid, source, recorded_at="2026-10-27T10:00:00Z", write=True,
                         expected_sha256=capture["expected_sha256"])
        links = [{"evidence_id": eid, "source_version": capture["source_version"],
                  "location": {"start_byte": 0, "end_byte": 25, "quote": "Declared sample 12.00 ms\r"},
                  "assessment": {"kind": "model", "by": "example-model/v1", "relation": "supports"}}]
        claim = create_claim(self.root, "A captured declaration exists.", links, title="Measurement statement",
                             owner="Ada Example", today=DAY)
        create_claim(self.root, "A captured declaration exists.", links, title="Measurement statement",
                     owner="Ada Example", today=DAY, write=True, expected_sha256=claim["expected_sha256"])
        claim_path = self.root / claim["path"]
        sample = {"value": "12.00", "as_of": DAY.isoformat(),
                  "claim": {"claim_id": claim["claim_id"], "sha256": hashlib.sha256(claim_path.read_bytes()).hexdigest()}}
        return sample, capture, claim_path

    def test_preview_no_writes_and_roundtrip_preserve_exact_decimals_and_legacy_records(self) -> None:
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        original = copy.deepcopy(self.data)
        plan = self.create()
        self.assertFalse((self.root / "00-context/outcomes").exists())
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        result = self.create(write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(result["outcome_id"], "O-001")
        self.assertEqual(result["path"], "00-context/outcomes/o-001.md")
        row = read_outcome(self.root, "O-001")
        self.assertEqual(row["payload"]["baseline"]["value"], "1.00")
        self.assertEqual(row["payload"]["target"]["value"], "-0")
        self.assertIsNone(row["payload"]["observed"])
        self.assertEqual(row["record"]["decision_ref"], self.did)
        self.assertNotIn("decision_id", row["record"])
        self.assertEqual(row["record"]["sensitivity"], "restricted")
        self.assertEqual(list_outcomes(self.root), [row])
        self.assertEqual(self.data, original)
        for path, raw in before.items():
            if not path.startswith(".whykit/"):
                self.assertEqual((self.root / path).read_bytes(), raw, path)
        schema = json.loads((Path(__file__).parents[1] / "schemas/outcome-record.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(row["payload"], schema), [])

    def test_target_only_baseline_only_and_unlinked_observed_never_verify_a_measurement(self) -> None:
        for baseline, target, observed in ((None, {"value": "0", "by": "2027-01-01"}, None),
                                           (self.data["baseline"], None, None),
                                           (None, None, {"value": "0", "as_of": DAY.isoformat(), "claim": None})):
            with self.subTest(baseline=baseline, target=target, observed=observed):
                result = self.apply({**self.data, "baseline": baseline, "target": target, "observed": observed})
                report = verify_outcome(self.root, result["outcome_id"], today=DAY)
                self.assertFalse(report["observed_source_verified"])
                for key in ("values_checked", "causality_checked", "execution_authorized"):
                    self.assertIs(report[key], False)

    def test_pinned_anchor_is_historical_and_changed_latest_source_is_only_information(self) -> None:
        sample, captured, _ = self.pinned_sample()
        result = self.apply({**self.data, "observed": sample})
        report = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertTrue(report["observed_source_verified"], report)
        self.assertTrue(report["observed"]["pin_and_anchor_matched"])
        source = self.root.parent / "measurement.bin"
        source.write_bytes(b"A later different sample\n")
        plan = capture_artifact(self.root, captured["evidence_id"], source, recorded_at="2026-10-27T11:00:00Z")
        capture_artifact(self.root, captured["evidence_id"], source, recorded_at="2026-10-27T11:00:00Z",
                         write=True, expected_sha256=plan["expected_sha256"])
        changed = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertTrue(changed["observed_source_verified"], changed)
        self.assertIn("changed", changed["observed"]["source_states"])
        (self.root / plan["path"]).unlink()
        invalid_latest = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertTrue(invalid_latest["observed_source_verified"])
        self.assertIn("invalid", invalid_latest["observed"]["source_states"])

    def test_approved_decision_receipt_and_old_outcome_bytes_remain_unchanged(self) -> None:
        sample, capture, _ = self.pinned_sample()
        did, decision = approved_decision(self.root, "A reviewed synthetic decision", owner="Ada Example", today=DAY,
                                          source_ids=[capture["evidence_id"]])
        first = self.apply({**self.data, "decision_id": did, "observed": sample})
        paths = [decision, self.root / "00-context/review-log.md", self.root / first["path"], self.root / capture["path"]]
        before = {path: path.read_bytes() for path in paths}
        self.apply({**self.data, "decision_id": did})
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_preview_and_verification_do_not_inherit_persistent_disk_cache_writes(self) -> None:
        sample, _, _ = self.pinned_sample()
        result = self.apply({**self.data, "observed": sample})
        def snapshot():
            return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                    for path in self.root.rglob("*")}
        before = snapshot()
        with persistent(True):
            self.create()
            verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertEqual(snapshot(), before)

    def test_wrong_claim_pin_rejected_on_create_and_postwrite_mutation_is_reported(self) -> None:
        sample, _, claim_path = self.pinned_sample()
        bad = {**sample, "claim": {**sample["claim"], "sha256": "0" * 64}}
        with self.assertRaises(ValueError):
            self.create({**self.data, "observed": bad})
        result = self.apply({**self.data, "observed": sample})
        claim_path.write_text("changed malformed claim", encoding="utf-8")
        report = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertEqual(report["observed"]["state"], "claim_changed")
        self.assertFalse(report["observed_source_verified"])

    def test_decimal_types_precision_scale_and_future_dates_are_strict(self) -> None:
        for value in (0, 1.0, True, "NaN", "inf", "1e2", "+1", "00", "١", "1" * 33, "0." + "1" * 13):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.create({**self.data, "observed": {"value": value, "as_of": DAY.isoformat(), "claim": None}})
        for as_of in ("2026-02-30", "20261027", "2026-10-28"):
            with self.subTest(as_of=as_of), self.assertRaises(ValueError):
                self.create({**self.data, "observed": {"value": "1", "as_of": as_of, "claim": None}})
        with self.assertRaises(ValueError):
            self.create({**self.data, "baseline": None, "target": None, "observed": None})
        data = {**self.data, "metric": {"name": "Żółw", "unit": "ms", "definition": "Pomiar określonej próby.\nBez twierdzenia o przyczynie."},
                "observed": {"value": "12345678901234567890.123456789012", "as_of": DAY.isoformat(), "claim": None}}
        result = self.apply(data)
        self.assertEqual(read_outcome(self.root, result["outcome_id"])["payload"]["observed"]["value"], data["observed"]["value"])

    def test_safe_allocation_stale_parameters_and_dependency_changes(self) -> None:
        plan = self.create()
        def apply():
            try:
                return self.create(write=True, expected_sha256=plan["expected_sha256"])["outcome_id"]
            except ValueError:
                return "stale"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: apply(), range(2))), ["O-001", "stale"])
        plan = self.create()
        self.decision.write_text(self.decision.read_text(encoding="utf-8") + "\nChanged draft.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.create(write=True, expected_sha256=plan["expected_sha256"])

    def test_missing_ambiguous_and_noncanonical_decisions_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.create({**self.data, "decision_id": "D-999"})
        duplicate = self.root / "06-decisions/d-999-duplicate.md"
        duplicate.write_bytes(self.decision.read_bytes())
        with self.assertRaises(ValueError):
            self.create()
        duplicate.unlink()
        moved = self.root / "notes/rogue.md"
        moved.parent.mkdir(exist_ok=True)
        self.decision.rename(moved)
        with self.assertRaises(ValueError):
            self.create()

    def test_malformed_decision_metadata_cannot_become_a_valid_outcome_dependency(self) -> None:
        original = self.decision.read_text(encoding="utf-8")
        for text in (original.replace("created: " + DAY.isoformat(), "created: invalid-date"),
                     original.replace("status: draft", "status: [draft]")):
            with self.subTest(text=text):
                self.decision.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.create()

    def test_legacy_body_decision_id_is_accepted_without_rewriting_its_bytes(self) -> None:
        text = self.decision.read_text(encoding="utf-8")
        self.decision.write_text(text.replace(f"decision_id: {self.did}\n", ""), encoding="utf-8")
        before = self.decision.read_bytes()
        self.assertNotIn("decision_id", load_note(self.decision).front)
        self.assertEqual(load_note(self.decision).section_decision_id, self.did)
        result = self.apply()
        report = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertEqual(report["decision_state"], "available")
        self.assertEqual(self.decision.read_bytes(), before)

    def test_pure_visibility_and_classifier_resist_metadata_downgrades(self) -> None:
        sample, _, _ = self.pinned_sample()
        result = self.apply({**self.data, "observed": sample})
        path = self.root / result["path"]
        note = load_note(path)
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("pure helper read")):
            self.assertEqual(outcome_references(note.text), (self.did, {sample["claim"]["claim_id"]}))
            self.assertTrue(outcome_visibility(note, {self.did}, {sample["claim"]["claim_id"]}, root=self.root))
            self.assertFalse(outcome_visibility(note, set(), {sample["claim"]["claim_id"]}, root=self.root))
            self.assertFalse(outcome_visibility(note, {self.did}, set(), root=self.root))
        changed = note.text.replace('type: "outcome"', 'type: "reference"').replace('sensitivity: "restricted"', 'sensitivity: "public"')
        altered = load_note(path, text=changed)
        self.assertEqual(note_sensitivity_level(altered), 3)
        self.assertFalse(outcome_visibility(altered, {self.did}, {sample["claim"]["claim_id"]}, root=self.root))
        markerless = load_note(path, text=changed.replace('outcome_id: "O-001"\n', ""))
        self.assertEqual(note_sensitivity_level(markerless), 3)
        nested = load_note(self.root / "notes/nested/00-context/outcomes/o-001.md", text=note.text)
        self.assertFalse(outcome_visibility(nested, {self.did}, {sample["claim"]["claim_id"]}, root=self.root))

    def test_actual_case_alias_is_refused_consistently_by_read_list_and_host(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        uppercase = path.with_name("O-001.MD")
        path.rename(uppercase)
        with self.assertRaises(ValueError):
            read_outcome(self.root, "O-001")
        with self.assertRaises(ValueError):
            list_outcomes(self.root)
        self.assertFalse(outcome_visibility(load_note(uppercase), {self.did}, set(), root=self.root))

    def test_corrupt_pinned_capture_is_not_a_verified_observation(self) -> None:
        sample, capture, _ = self.pinned_sample()
        result = self.apply({**self.data, "observed": sample})
        (self.root / capture["path"]).write_text("{}", encoding="utf-8")
        report = verify_outcome(self.root, result["outcome_id"], today=DAY)
        self.assertFalse(report["observed_source_verified"])

    def test_opaque_implementation_references_are_not_opened_fetched_or_executed(self) -> None:
        result = self.apply({**self.data, "implementation_refs": ["../../secret", "https://example.invalid/never-fetch", "$(never-execute)"]})
        with patch("subprocess.run", side_effect=AssertionError("no process")), patch("socket.socket", side_effect=AssertionError("no network")):
            verify_outcome(self.root, result["outcome_id"], today=DAY)

    def test_filtered_reads_never_open_hash_or_verify_dependencies(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        path.write_text("malformed hidden outcome", encoding="utf-8")
        with patch("whykit.outcome_ledger._read_regular", side_effect=AssertionError("hidden I/O")):
            self.assertIsNone(read_outcome(self.root, result["outcome_id"], allowed_sensitivities={"public"}))
            self.assertEqual(list_outcomes(self.root, allowed_sensitivities={"public"}), [])
            self.assertFalse(verify_outcome(self.root, result["outcome_id"], allowed_sensitivities={"public"})["exists"])

    def test_strict_envelope_duplicate_keys_identity_and_bounds(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        text = path.read_text(encoding="utf-8")
        for invalid in (text.replace('"outcome_id": "O-001"', '"outcome_id": "O-002"'),
                        text.replace('"format":', '"format":"wrong", "format":', 1),
                        text + "\n```json\n{}\n```\n"):
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_outcome(self.root, "O-001")
        path.write_text(text, encoding="utf-8")
        with patch("whykit.outcome_ledger.MAX_OUTCOME_BYTES", 10), self.assertRaises(ValueError):
            read_outcome(self.root, "O-001")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_outcome_read_is_refused_without_hanging(self) -> None:
        result = self.apply()
        path = self.root / result["path"]
        path.unlink()
        os.mkfifo(path)
        child = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from whykit.outcome_ledger import read_outcome
try:
    read_outcome(Path(sys.argv[1]), 'O-001')
except (OSError, ValueError):
    raise SystemExit(0)
raise SystemExit('FIFO accepted')
""", str(self.root)], capture_output=True, text=True, encoding="utf-8", timeout=3)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
