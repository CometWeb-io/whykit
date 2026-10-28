"""Preflight exercises current vault bytes and real local approval receipts."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _jsonschema import validate, unsupported_keywords
from _vaults import approved_decision, fill_decision
from whykit.agent_preflight import MAX_PROPOSAL_BYTES, PreflightInputError, check_proposal, main, read_proposal
from whykit.artifact_store import capture_artifact
from whykit.claim_ledger import create_claim
from whykit.review import approve_decision
from whykit.scaffold import _frontmatter_replace, create_decision
from whykit.parse_cache import persistent

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 9, 17)
DAY_ISO = DAY.isoformat()


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)
        self.vault = self.parent / "vault"
        shutil.copytree(ROOT / "examples/approval", self.vault)
        self.config = self.vault / "whykit.toml"
        self.config.write_text('[evidence_access_age_days]\nfixture = 90\n', encoding="utf-8")
        self.record = next((self.vault / "06-decisions").glob("d-001-*.md"))
        self.proposal = self.parent / "proposal.json"
        self.manifest = {"proposal_id": "dependency-upgrade", "decision_ids": ["D-001"]}
        self.write_manifest()

    def write_manifest(self) -> None:
        self.proposal.write_text(json.dumps(self.manifest), encoding="utf-8")

    def check(self, *, today: dt.date = DAY) -> dict:
        return check_proposal(self.vault, self.proposal, today=today)

    def change(self, key: str, value: str) -> None:
        self.record.write_text(_frontmatter_replace(self.record.read_text(encoding="utf-8"), key, value), encoding="utf-8")

    def codes(self, report: dict) -> set[str]:
        return {code for finding in report["findings"] for code in finding["problems"]}

    def claimed_decision(self, *, kind="human", relation="supports", pin=True, linked=True,
                         evidence_id="E-001", pin_case=None, quote="Reviewed evidence.",
                         valid_from=DAY_ISO, valid_until=None):
        source = self.parent.resolve() / "captured-source.bin"
        source.write_bytes(b"Reviewed evidence.")
        capture = capture_artifact(self.vault, evidence_id, source, recorded_at="2026-09-17T10:00:00Z")
        capture_artifact(self.vault, evidence_id, source, recorded_at="2026-09-17T10:00:00Z",
                         write=True, expected_sha256=capture["expected_sha256"])
        links = [{"evidence_id": evidence_id, "source_version": capture["source_version"],
                  "location": {"start_byte": 0, "end_byte": 18, "quote": quote},
                  "assessment": {"kind": kind, "by": "Ada Example" if kind == "human" else "example-model/v1", "relation": relation}}] if linked else []
        claim = create_claim(self.vault, "The captured text says reviewed evidence.", links,
                             title="Review evidence claim", owner="Ada Example", today=DAY,
                             valid_from=valid_from, valid_until=valid_until)
        create_claim(self.vault, "The captured text says reviewed evidence.", links,
                     title="Review evidence claim", owner="Ada Example", today=DAY,
                     valid_from=valid_from, valid_until=valid_until,
                     write=True, expected_sha256=claim["expected_sha256"])
        claim_path = self.vault / claim["path"]
        claim_hash = "sha256:" + hashlib.sha256(claim_path.read_bytes()).hexdigest()
        did, decision = create_decision(self.vault, "Use a pinned claim", owner="Ada Example", source_ids=["E-001"], today=DAY)
        fill_decision(decision)
        text = _frontmatter_replace(decision.read_text(encoding="utf-8"), "claim_ids", "[" + claim["claim_id"] + "]")
        if pin:
            pin_map = json.dumps({claim["claim_id"]: claim_hash}, separators=(",", ":"))
            if pin_case == "duplicate":
                pin_map = '{"' + claim["claim_id"] + '":"' + claim_hash + '","' + claim["claim_id"] + '":"' + claim_hash + '"}'
            elif pin_case == "mismatched_keys":
                pin_map = json.dumps({"C-999": claim_hash})
            elif pin_case == "invalid_hash":
                pin_map = json.dumps({claim["claim_id"]: "sha256:" + "A" * 64})
            elif pin_case == "wrong_hash":
                pin_map = json.dumps({claim["claim_id"]: "sha256:" + "0" * 64})
            elif pin_case == "oversize":
                pin_map = " " * (16 * 1024) + pin_map
            scalar = json.dumps(pin_map)
            text = _frontmatter_replace(text, "provenance", "\n  claim_versions: " + scalar)
        decision.write_text(text, encoding="utf-8")
        approval = approve_decision(self.vault, did, reviewer="Ada Example", today=DAY, next_review="2026-12-16")
        approve_decision(self.vault, did, reviewer="Ada Example", today=DAY, next_review="2026-12-16",
                         write=True, expected_sha256=approval["expected_sha256"])
        self.record = decision
        self.manifest["decision_ids"] = [did]
        self.write_manifest()
        return claim, claim_path, capture

    def test_pass_remains_structural_and_requires_review(self) -> None:
        report = self.check()
        self.assertEqual(report["verdict"], "pass", report)
        self.assertEqual(self.codes(report), set())
        self.assertTrue(report["review_required"])
        for field in ("execution_authorized", "semantics_checked", "trusted_history_checked"):
            self.assertIs(report[field], False)

    def test_real_preview_apply_approval_is_recognized(self) -> None:
        did, _ = approved_decision(self.vault, "Use the reviewed process", owner="Ada Example",
                                  source_ids=["E-001"], today=DAY, review_by="2026-12-16")
        self.manifest["decision_ids"] = [did]
        self.write_manifest()
        self.assertEqual(self.check()["verdict"], "pass")

    def test_missing_approval_receipt_and_rewritten_reasoning_block(self) -> None:
        log = self.vault / "00-context/review-log.md"
        original = log.read_text(encoding="utf-8")
        log.write_text("", encoding="utf-8")
        self.assertIn("unverified_local_approval", self.codes(self.check()))
        log.write_text(original, encoding="utf-8")
        self.record.write_text(self.record.read_text(encoding="utf-8") + "\nChanged reasoning.\n", encoding="utf-8")
        self.assertIn("unverified_local_approval", self.codes(self.check()))
        self.assertEqual(self.check()["verdict"], "blocked")

    def test_reviewer_label_is_not_authenticated_identity(self) -> None:
        log = self.vault / "00-context/review-log.md"
        log.write_text(log.read_text(encoding="utf-8").replace("Ada Example", "Mallory Example"), encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "pass")
        self.assertFalse(report["execution_authorized"])
        self.assertFalse(report["trusted_history_checked"])

    def test_spoofed_decision_outside_canonical_directory_blocks(self) -> None:
        rogue = self.vault / "notes/rogue.md"
        rogue.parent.mkdir(exist_ok=True)
        rogue.write_text(self.record.read_text(encoding="utf-8"), encoding="utf-8")
        self.record.unlink()
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("invalid_decision_record", self.codes(report))

    def test_duplicate_or_unknown_decision_blocks(self) -> None:
        twin = self.record.with_name("d-001-twin.md")
        twin.write_bytes(self.record.read_bytes())
        self.assertIn("unknown_or_ambiguous_decision", self.codes(self.check()))
        twin.unlink()
        self.manifest["decision_ids"] = ["D-999"]
        self.write_manifest()
        self.assertEqual(self.check()["verdict"], "blocked")

    def test_filename_mismatch_and_non_decision_type_block(self) -> None:
        self.change("type", "reference")
        self.assertIn("invalid_decision_record", self.codes(self.check()))
        self.change("type", "decision")
        self.record.rename(self.record.with_name("d-099-wrong.md"))
        self.assertIn("invalid_decision_record", self.codes(self.check()))

    def test_nested_record_path_cannot_spoof_canonical_scope(self) -> None:
        nested = self.vault / "06-decisions/d-001-nested/record.md"
        nested.parent.mkdir()
        self.record.rename(nested)
        log = self.vault / "00-context/review-log.md"
        log.write_text(log.read_text(encoding="utf-8").replace(
            "06-decisions/" + self.record.stem, "06-decisions/d-001-nested/record"), encoding="utf-8")
        self.assertEqual(self.check()["verdict"], "blocked")
        self.assertIn("invalid_decision_record", self.codes(self.check()))

    def test_non_binding_and_superseded_records_block(self) -> None:
        self.change("status", "draft")
        self.assertIn("not_binding", self.codes(self.check()))
        self.change("status", "approved")
        successor = self.record.with_name("d-002-successor.md")
        successor.write_text(self.record.read_text(encoding="utf-8").replace("D-001", "D-002").replace(
            "status: approved", "supersedes: D-001\nstatus: draft"), encoding="utf-8")
        report = self.check()
        self.assertIn("superseded", self.codes(report))
        self.assertEqual(report["verdict"], "blocked")

    def test_due_today_passes_and_overdue_needs_review(self) -> None:
        self.config.write_text('[evidence_access_age_days]\nfixture = 365\n', encoding="utf-8")
        self.assertEqual(self.check(today=dt.date(2026, 12, 16))["verdict"], "pass")
        report = self.check(today=dt.date(2026, 12, 17))
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"review_overdue"})

    def test_stale_and_unbounded_evidence_need_review(self) -> None:
        self.config.write_text('[evidence_access_age_days]\nfixture = 0\n', encoding="utf-8")
        report = self.check(today=DAY + dt.timedelta(days=1))
        self.assertEqual(report["verdict"], "needs_review")
        self.assertIn("stale_evidence", self.codes(report))
        self.config.unlink()
        self.assertEqual(self.codes(self.check()), {"evidence_age_unbounded"})

    def test_invalid_future_missing_and_ambiguous_evidence_block(self) -> None:
        register = self.vault / "00-context/evidence-register.md"
        original = register.read_text(encoding="utf-8")
        cases = [
            (original.replace("| 2026-09-17 | https://example.com/review-policy", "| invalid | https://example.com/review-policy"), "unverifiable_evidence_access"),
            (original.replace("| 2026-09-17 | https://example.com/review-policy", "| 2027-09-17 | https://example.com/review-policy"), "unverifiable_evidence_access"),
            (original.replace("| E-001 |", "| E-099 |"), "missing_evidence"),
            (original + next(line for line in original.splitlines() if line.startswith("| E-001 |")) + "\n", "ambiguous_evidence"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                register.write_text(text, encoding="utf-8")
                report = self.check()
                self.assertEqual(report["verdict"], "blocked")
                self.assertIn(expected, self.codes(report))

    def test_missing_claim_without_pin_never_passes(self) -> None:
        self.change("claim_ids", "[C-001]")
        self.assertIn("missing_claim", self.codes(self.check()))
        self.assertIn("claim_snapshot_unpinned", self.codes(self.check()))

    def test_pinned_claim_and_real_approval_pass_only_structural_checks_without_writes(self) -> None:
        self.claimed_decision()
        before = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("no subprocess")), patch("socket.socket", side_effect=AssertionError("no network")):
            report = self.check()
        after = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(report["verdict"], "pass", report)
        self.assertEqual(before, after)
        self.assertFalse(report["execution_authorized"])
        self.assertFalse(report["semantics_checked"])
        self.assertFalse(report["trusted_history_checked"])

    def observe_source(self, raw: bytes, stamp: str) -> dict:
        source = self.parent.resolve() / "observed-source.bin"
        source.write_bytes(raw)
        preview = capture_artifact(self.vault, "E-001", source, recorded_at=stamp)
        return capture_artifact(self.vault, "E-001", source, recorded_at=stamp,
                                write=True, expected_sha256=preview["expected_sha256"])

    def test_new_observation_requires_review_without_rewriting_approved_inputs(self) -> None:
        claim, claim_path, first = self.claimed_decision()
        self.assertEqual(self.check()["verdict"], "pass")
        retained = {path: path.read_bytes() for path in (self.record, claim_path,
                    self.vault / "00-context/review-log.md", self.vault / first["path"])}
        self.observe_source(b"Replacement evidence.", "2026-09-17T11:00:00Z")
        before = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        with persistent(), patch("subprocess.run", side_effect=AssertionError("no subprocess")), patch("socket.socket", side_effect=AssertionError("no network")):
            report = self.check()
        self.assertEqual(report["verdict"], "needs_review", report)
        self.assertEqual(self.codes(report), {"claim_source_changed"})
        after = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        for path, raw in retained.items():
            self.assertEqual(path.read_bytes(), raw)
        for flag in ("execution_authorized", "semantics_checked", "trusted_history_checked"):
            self.assertIs(report[flag], False)
        self.observe_source(b"Reviewed evidence.", "2026-09-17T12:00:00Z")
        self.assertEqual(self.check()["verdict"], "pass")
        for path, raw in retained.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(claim["claim_id"], "C-001")

    def test_source_state_mapping_is_conservative_and_uses_the_preflight_date(self) -> None:
        claim, path, _ = self.claimed_decision()
        expected_pin = hashlib.sha256(path.read_bytes()).hexdigest()
        schema = json.loads((ROOT / "schemas/agent-preflight-result.schema.json").read_text(encoding="utf-8"))
        for state in ("current", "changed", "unknown", "ambiguous", "future", None, "invalid", []):
            with self.subTest(state=state):
                verification = {"exists": True, "links": [{"status": "matched", "source_state": state}]}
                with patch("whykit.agent_preflight.verify_claim", return_value=verification) as verify:
                    report = self.check()
                self.assertEqual(validate(report, schema), [])
                verify.assert_called_once_with(self.vault.resolve(), claim["claim_id"],
                                                expected_sha256=expected_pin, today=DAY)
                if state == "current":
                    self.assertEqual(report["verdict"], "pass")
                elif isinstance(state, str) and state in {"changed", "unknown", "ambiguous", "future"} or state is None:
                    self.assertEqual(report["verdict"], "needs_review")
                    self.assertEqual(self.codes(report), {"claim_source_" + (state or "unknown")})
                else:
                    self.assertEqual(report["verdict"], "blocked")
                    self.assertEqual(self.codes(report), {"claim_verification_failed"})

    def test_observation_unknown_ambiguous_future_and_invalid_have_distinct_gates(self) -> None:
        self.claimed_decision()
        observation = self.vault / "00-context/observations/E-001.json"
        observation.unlink(missing_ok=True)
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"claim_source_unknown"})
        self.observe_source(b"Reviewed evidence.", "2026-09-17T10:00:00Z")
        self.observe_source(b"Other equally timed evidence.", "2026-09-17T10:00:00Z")
        self.assertEqual(self.codes(self.check()), {"claim_source_ambiguous"})
        self.observe_source(b"Tomorrow's evidence.", "2026-09-18T00:00:00Z")
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"claim_source_future"})
        observation.write_text("{}", encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("claim_verification_failed", self.codes(report))

    def test_missing_latest_capture_blocks_without_losing_the_pinned_capture(self) -> None:
        _, _, first = self.claimed_decision()
        latest = self.observe_source(b"Latest bytes.", "2026-09-17T11:00:00Z")
        (self.vault / latest["path"]).unlink()
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("claim_verification_failed", self.codes(report))
        self.assertTrue((self.vault / first["path"]).is_file())

    def test_new_source_observations_do_not_change_decisions_without_claims(self) -> None:
        original = self.check()
        self.observe_source(b"New bytes A", "2026-09-17T10:00:00Z")
        self.observe_source(b"New bytes B", "2026-09-17T11:00:00Z")
        self.assertEqual(original, self.check())

    def test_claim_mutation_after_approval_blocks_even_when_anchors_still_match(self) -> None:
        _, path, _ = self.claimed_decision()
        path.write_text(path.read_text(encoding="utf-8").replace("The captured text says reviewed evidence.", "A different claim using the same bytes."), encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("claim_snapshot_changed", self.codes(report))
        self.assertNotIn("unverified_local_approval", self.codes(report))
        path.write_text("Malformed changed bytes", encoding="utf-8")
        self.assertIn("claim_snapshot_changed", self.codes(self.check()))

    def test_missing_pin_requires_review_without_inventing_approval(self) -> None:
        self.claimed_decision(pin=False)
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review", report)
        self.assertEqual(self.codes(report), {"claim_snapshot_unpinned"})

    def test_claim_without_valid_from_requires_review_without_inferring_created(self) -> None:
        self.claimed_decision(valid_from=None)
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"claim_validity_unknown"})

    def test_claim_before_explicit_valid_from_requires_review(self) -> None:
        self.claimed_decision(valid_from=(DAY + dt.timedelta(days=1)).isoformat())
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"claim_not_current"})

    def test_claim_valid_until_is_exclusive(self) -> None:
        self.claimed_decision(valid_until=(DAY + dt.timedelta(days=1)).isoformat())
        self.assertEqual(self.check()["verdict"], "pass")
        report = self.check(today=DAY + dt.timedelta(days=1))
        self.assertEqual(report["verdict"], "needs_review")
        self.assertEqual(self.codes(report), {"claim_not_current"})

    def test_model_contradicted_and_unlinked_claims_require_review(self) -> None:
        for options, expected in (({"kind": "model"}, "claim_model_assessment"),
                                  ({"relation": "contradicts"}, "claim_contradicted"),
                                  ({"linked": False}, "claim_unlinked")):
            with self.subTest(options=options):
                self.claimed_decision(**options)
                report = self.check()
                self.assertEqual(report["verdict"], "needs_review", report)
                self.assertIn(expected, self.codes(report))

    def test_invalid_claim_ids_missing_file_and_tampered_capture_block(self) -> None:
        for value in ("C-001", "null", "[C-001, C-001]", "[C-0001]", "[../C-001]", "[true]"):
            with self.subTest(value=value):
                self.change("claim_ids", value)
                report = self.check()
                self.assertEqual(report["verdict"], "blocked")
                self.assertIn("invalid_claim_ids", self.codes(report))
        self.change("claim_ids", "[]")
        _, path, capture = self.claimed_decision()
        raw = path.read_bytes()
        path.unlink()
        self.assertIn("missing_claim", self.codes(self.check()))
        path.write_bytes(raw)
        (self.vault / capture["path"]).write_text("{}", encoding="utf-8")
        self.assertEqual(self.check()["verdict"], "blocked")
        self.assertIn("invalid_claim_record", self.codes(self.check()))

    def test_strict_bounded_pin_map_and_wrong_hash_are_bound_before_approval(self) -> None:
        for pin_case in ("duplicate", "mismatched_keys", "invalid_hash", "wrong_hash", "oversize"):
            with self.subTest(pin_case=pin_case):
                self.claimed_decision(pin_case=pin_case)
                report = self.check()
                self.assertEqual(report["verdict"], "blocked")
                expected = "claim_snapshot_changed" if pin_case == "wrong_hash" else "invalid_claim_versions"
                self.assertIn(expected, self.codes(report))
                self.assertNotIn("unverified_local_approval", self.codes(report))

    def test_unverified_anchor_or_legacy_missing_capture_blocks_with_exact_pin(self) -> None:
        self.claimed_decision(quote="Reviewed evidence?")
        self.assertIn("unverified_claim_anchor", self.codes(self.check()))
        _, _, capture = self.claimed_decision()
        # Legacy captures have no observation asserting the missing version is latest.
        (self.vault / "00-context/observations/E-001.json").unlink()
        (self.vault / capture["path"]).unlink()
        self.assertIn("unverified_claim_anchor", self.codes(self.check()))

    def test_indirect_claim_evidence_still_uses_current_lifecycle_and_age_policy(self) -> None:
        self.claimed_decision(evidence_id="E-003")
        self.assertEqual(self.check()["verdict"], "pass")
        register = self.vault / "00-context/evidence-register.md"
        original = register.read_text(encoding="utf-8")
        row = next(line for line in original.splitlines() if line.startswith("| E-003 |"))
        register.write_text(original.replace(row, row.replace("| 2026-09-17 | https://", "| 2026-01-01 | https://")), encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "needs_review")
        self.assertIn("stale_evidence", self.codes(report))
        register.write_text(original.replace(row + "\n", "") + "| E-003 | Synthetic classification contract | 2026-09-17 | Retired | E-001 | public |\n", encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("retired_evidence", self.codes(report))

    def test_future_record_and_unreviewed_provenance_block(self) -> None:
        self.change("last_updated", "2027-01-01")
        self.assertIn("invalid_record_timestamp", self.codes(self.check()))
        self.change("last_updated", DAY.isoformat())
        self.change("provenance", "\n  human_reviewed: false")
        self.assertIn("unreviewed_decision", self.codes(self.check()))

    def test_invalid_config_and_wrong_root_fail_closed(self) -> None:
        self.config.write_text("invalid = [", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check()
        with self.assertRaisesRegex(PreflightInputError, "vault_not_found"):
            check_proposal(self.parent, self.proposal, today=DAY)

    def test_pending_transaction_requires_recovery_without_mutation(self) -> None:
        ready = self.vault / ".whykit/transactions/synthetic/READY"
        ready.parent.mkdir(parents=True)
        ready.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(OSError, "recovery required"):
            self.check()
        self.assertTrue(ready.exists())

    def test_only_declared_ids_are_assessed_and_blockers_take_precedence(self) -> None:
        unrelated = self.vault / "06-decisions/d-999-private.md"
        unrelated.write_text(self.record.read_text(encoding="utf-8").replace("D-001", "D-999"), encoding="utf-8")
        report = self.check()
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual([item["decision_id"] for item in report["findings"]], ["D-001"])
        self.assertNotIn("D-999", json.dumps(report))
        self.config.unlink()
        self.manifest["decision_ids"] = ["D-001", "D-999"]
        self.write_manifest()
        report = self.check()
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("evidence_age_unbounded", self.codes(report))
        self.assertIn("unverified_local_approval", self.codes(report))

    def test_symlinked_sensitive_paths_are_refused_before_inventory_load(self) -> None:
        for relative in ("00-context/review-log.md", "00-context/evidence-register.md", "whykit.toml"):
            with self.subTest(relative=relative):
                path = self.vault / relative
                original = path.read_bytes()
                outside = self.parent / "outside.txt"
                outside.write_bytes(original)
                path.unlink()
                path.symlink_to(outside)
                try:
                    with patch("whykit.agent_preflight.VaultIndex.load", side_effect=AssertionError("must not load")):
                        with self.assertRaisesRegex(PreflightInputError, "unsafe_vault_path"):
                            self.check()
                finally:
                    path.unlink()
                    path.write_bytes(original)

    def test_read_only_without_network_git_or_unrelated_output(self) -> None:
        self.manifest["summary"] = "PRIVATE_SUMMARY_MARKER"
        self.write_manifest()
        private = self.vault / "notes/private.md"
        private.parent.mkdir(exist_ok=True)
        private.write_text("PRIVATE_NOTE_MARKER", encoding="utf-8")
        before = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("no subprocess")), patch("socket.socket", side_effect=AssertionError("no network")):
            report = self.check()
        after = {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        output = json.dumps(report)
        for private_value in ("PRIVATE_SUMMARY_MARKER", "PRIVATE_NOTE_MARKER", "Ada Example", str(self.vault), self.record.name, "E-003"):
            self.assertNotIn(private_value, output)

    def test_inherited_persistent_scope_creates_no_cache_or_other_vault_entries(self) -> None:
        before_entries = {path.relative_to(self.vault) for path in self.vault.rglob("*")}
        before_bytes = {path.relative_to(self.vault): path.read_bytes()
                        for path in self.vault.rglob("*") if path.is_file()}
        self.assertFalse((self.vault / ".whykit/cache").exists())
        with persistent(True):
            report = self.check()
        self.assertEqual(report["verdict"], "pass")
        self.assertFalse((self.vault / ".whykit/cache").exists())
        self.assertEqual({path.relative_to(self.vault) for path in self.vault.rglob("*")}, before_entries)
        self.assertEqual({path.relative_to(self.vault): path.read_bytes()
                          for path in self.vault.rglob("*") if path.is_file()}, before_bytes)

    def test_input_limits_unknown_fields_and_spoofed_authorization(self) -> None:
        invalid = [[], {}, {**self.manifest, "execution_authorized": True},
                   {**self.manifest, "decision_ids": []}, {**self.manifest, "decision_ids": ["D-001", "D-001"]},
                   {**self.manifest, "decision_ids": ["D-١٠١"]}, {**self.manifest, "decision_ids": ["../D-001"]},
                   {**self.manifest, "decision_ids": [True]}, {**self.manifest, "summary": "x" * 4097},
                   {**self.manifest, "proposal_id": "x" * 81},
                   {**self.manifest, "decision_ids": [f"D-{i:03d}" for i in range(65)]}]
        for payload in invalid:
            with self.subTest(payload=str(payload)[:100]):
                self.proposal.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(PreflightInputError):
                    read_proposal(self.proposal)
        for raw in (b'{"proposal_id":"a","proposal_id":"b","decision_ids":["D-001"]}',
                    b'{"summary":NaN}', b'{"summary":Infinity}', b'\xff', b'[' * 2000,
                    b' ' * (MAX_PROPOSAL_BYTES + 1)):
            with self.subTest(raw=raw[:50]):
                self.proposal.write_bytes(raw)
                with self.assertRaises(PreflightInputError):
                    read_proposal(self.proposal)

    def test_digest_binds_exact_bytes_and_output_is_deterministic(self) -> None:
        self.assertEqual(self.check(), self.check())
        self.assertEqual(self.check()["proposal_sha256"], hashlib.sha256(self.proposal.read_bytes()).hexdigest())
        previous = self.check()["proposal_sha256"]
        self.proposal.write_bytes(self.proposal.read_bytes() + b"\n")
        self.assertNotEqual(self.check()["proposal_sha256"], previous)

    def test_cli_exit_codes_and_error_redaction(self) -> None:
        args = ["--root", str(self.vault), "--proposal", str(self.proposal), "--today", DAY.isoformat()]
        for change, verdict, exit_code in ((None, "pass", 0), ("unbounded", "needs_review", 1), ("draft", "blocked", 1)):
            if change == "unbounded":
                self.config.unlink()
            if change == "draft":
                self.change("status", "draft")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(args), exit_code)
            self.assertEqual(json.loads(output.getvalue())["verdict"], verdict)
        for date in ("2026-02-30", "20260917"):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([*args[:-1], date]), 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "invalid_date")
        with patch("whykit.agent_preflight.check_proposal", side_effect=OSError("PRIVATE_ERROR_MARKER")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(args), 2)
        self.assertNotIn("PRIVATE_ERROR_MARKER", output.getvalue())

    def test_source_script_runs_without_installed_package(self) -> None:
        result = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/agent_preflight.py"),
                                 "--root", str(self.vault), "--proposal", str(self.proposal), "--today", DAY.isoformat()],
                                capture_output=True, text=True, timeout=20, cwd=self.parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["verdict"], "pass")

    def test_result_schema_covers_verdicts_and_forbids_authorization(self) -> None:
        schema = json.loads((ROOT / "schemas/agent-preflight-result.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(unsupported_keywords(schema), [])
        report = self.check()
        self.assertEqual(validate(report, schema), [])
        self.config.unlink()
        self.assertEqual(validate(self.check(), schema), [])
        self.change("status", "draft")
        self.assertEqual(validate(self.check(), schema), [])
        for field in ("execution_authorized", "semantics_checked", "trusted_history_checked"):
            self.assertNotEqual(validate({**report, field: True}, schema), [])
            self.assertNotEqual(validate({**report, field: 0}, schema), [])

    def test_claim_preflight_problem_codes_follow_the_published_result_schema(self) -> None:
        schema = json.loads((ROOT / "schemas/agent-preflight-result.schema.json").read_text(encoding="utf-8"))
        for options in ({"kind": "model"}, {"pin": False}, {"pin_case": "wrong_hash"},
                        {"pin_case": "duplicate"}, {"quote": "Reviewed evidence?"}):
            with self.subTest(options=options):
                self.claimed_decision(**options)
                self.assertEqual(validate(self.check(), schema), [])


if __name__ == "__main__":
    unittest.main()
