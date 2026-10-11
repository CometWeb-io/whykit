"""Deterministic and fail-closed preflight input and decision-state checks."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from whykit.agent_preflight import PreflightInputError, assess, read_proposal, check_proposal
from _jsonschema import validate

TODAY = dt.date(2026, 10, 11)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "proposal.json"
        self.valid = {"proposal_id": "agent-change-1", "decision_ids": ["D-001"]}

    def load(self, data: object) -> tuple[dict, str]:
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return read_proposal(self.path)

    def trace(self, **overrides: object) -> dict:
        return {"decisions": [{"decision_id": "D-001", "status": "approved", "live": True,
                 "superseded_by": [], "gaps": [], "claim_gaps": [], "evidence": [], **overrides}]}

    def assess(self, trace: dict, dates: dict[str, object] | None = None) -> dict:
        proposal, digest = self.load(self.valid)
        return assess(proposal, trace, dates if dates is not None else {"D-001": "2026-11-01"}, today=TODAY, digest=digest)

    def test_green_evidence_still_requires_human_review(self) -> None:
        result = self.assess(self.trace())
        self.assertEqual(result["verdict"], "review_required")
        self.assertTrue(result["review_required"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["semantics_checked"])
        self.assertFalse(result["trusted_history_checked"])

    def test_missing_or_ambiguous_decision_blocks(self) -> None:
        for report in ({"decisions": []}, {"decisions": [*self.trace()["decisions"], *self.trace()["decisions"]]}):
            checked = self.assess(report)
            self.assertEqual(checked["verdict"], "blocked")
            self.assertEqual(checked["findings"][0]["problems"], ["unknown_or_ambiguous_decision"])

    def test_evidence_claims_status_and_supersession_block(self) -> None:
        cases = [
            ({"gaps": ["stale_evidence"]}, "unresolved_evidence_or_claims"),
            ({"claim_gaps": ["decision_review_binding_changed"]}, "unresolved_evidence_or_claims"),
            ({"status": "superseded", "live": False}, "not_binding"),
            ({"superseded_by": ["D-002"], "live": False}, "superseded"),
        ]
        for update, reason in cases:
            with self.subTest(reason=reason):
                report = self.assess(self.trace(**update))
                self.assertEqual(report["verdict"], "blocked")
                self.assertIn(reason, report["findings"][0]["problems"])

    def test_conflicted_or_unsupported_claims_require_manual_resolution(self) -> None:
        for status in ("disputed", "unsupported", "unknown"):
            with self.subTest(status=status):
                record = self.trace(claims=[{"claim_id": "C-001",
                                            "verification_status": status,
                                            "binding_valid": True}])
                output = self.assess(record)
                self.assertEqual(output["verdict"], "blocked")
                self.assertIn("unresolved_claim_verification", output["findings"][0]["problems"])

    def test_supported_bound_claim_preserves_review_only_outcome(self) -> None:
        record = self.trace(claims=[{"claim_id": "C-001",
                                     "verification_status": "supported",
                                     "binding_valid": True}])
        self.assertEqual(self.assess(record)["verdict"], "review_required")

    def test_malformed_claim_projection_fails_closed(self) -> None:
        for claims in (None, "supported", [{"verification_status": "supported"}]):
            with self.subTest(claims=claims):
                record = self.trace(claims=claims)
                result = self.assess(record)
                self.assertEqual(result["verdict"], "blocked")

    def test_active_evidence_requires_a_valid_non_future_access_date(self) -> None:
        for age in (None, -1, "0"):
            with self.subTest(age=age):
                item = {"id": "E-001", "state": "active", "age_days": age, "stale": False}
                report = self.assess(self.trace(evidence=[item]))
                self.assertEqual(report["verdict"], "blocked")
                self.assertIn("unverifiable_evidence_access", report["findings"][0]["problems"])

    def test_missing_trace_projection_refuses_to_greenlight(self) -> None:
        for invalid in (None, {}, "invalid"):
            with self.subTest(value=invalid):
                report = self.assess(self.trace(evidence=invalid))
                self.assertIn("incomplete_trace_projection", report["findings"][0]["problems"])

    def test_symlinked_review_log_is_refused_before_read(self) -> None:
        import shutil
        base = Path(__file__).resolve().parents[1] / "examples" / "tiny"
        root = self.path.parent / "unsafe-vault"
        shutil.copytree(base, root)
        link = root / "00-context/review-log.md"
        if link.exists():
            link.unlink()
        secret = self.path.parent / "local-secret.txt"
        secret.write_text("SYNTHETIC_SECRET_MARKER", encoding="utf-8")
        try:
            link.symlink_to(secret)
        except (NotImplementedError, OSError):
            self.skipTest("filesystem does not permit symlink creation")
        self.load(self.valid)
        with self.assertRaises(PreflightInputError) as caught:
            check_proposal(root, self.path, today=TODAY)
        self.assertEqual(str(caught.exception), "unsafe_review_log_path")

    def test_malformed_configuration_is_not_ignored(self) -> None:
        import shutil
        from whykit.config import ConfigError
        base = Path(__file__).resolve().parents[1] / "examples" / "tiny"
        vault = self.path.parent / "private-vault"
        shutil.copytree(base, vault)
        self.load(self.valid)
        config = vault / "whykit.toml"
        config.write_text("this is deliberately invalid [", encoding="utf-8")
        with self.assertRaises((ConfigError, ValueError)):
            check_proposal(vault, self.path, today=TODAY)

    def test_locally_approved_real_claim_decision_has_a_matching_approval_event(self) -> None:
        from _claims import TODAY as REVIEW_DATE, approved_claim_vault
        root = self.path.parent / "approved-claim-vault"
        _cid, _claim_path, decision_id, _decision_path = approved_claim_vault(root, today=REVIEW_DATE)
        self.valid["decision_ids"] = [decision_id]
        self.load(self.valid)
        report = check_proposal(root, self.path, today=REVIEW_DATE)
        self.assertEqual(report["referenced_decisions"], 1)
        self.assertNotIn("invalid_decision_record", report["findings"][0]["problems"])
        self.assertNotIn("unverified_local_approval", report["findings"][0]["problems"])

    def test_claims_without_explicit_opt_in_cannot_pass_preflight(self) -> None:
        import shutil
        root = self.path.parent / "legacy-claims"
        shutil.copytree(Path(__file__).resolve().parents[1] / "examples" / "tiny", root)
        # This fake note is intentionally not a canonical decision; it also
        # contains a claim reference without enabling the claim contract.
        note = root / "notes" / "legacy-claim-reference.md"
        note.write_text(
            "---\ntype: reference\ndecision_id: D-998\n"
            "status: approved\nclaim_ids: [C-001]\n"
            "review_by: 2026-12-01\nlast_updated: 2026-10-09\n"
            "created: 2026-10-09\nsensitivity: public\n---\n"
            "Legacy claim without opt-in.\n",
            encoding="utf-8",
        )
        self.valid["decision_ids"] = ["D-998"]
        self.load(self.valid)
        # Either an explicit refusal or a blocked outcome is acceptable;
        # the preflight must never grant a positive structural verdict.
        try:
            result = check_proposal(root, self.path, today=TODAY)
        except ValueError:
            return
        self.assertEqual(result["verdict"], "blocked")
        self.assertIn("missing_claim_assessment", result["findings"][0]["problems"])

    def test_invalid_canonical_record_and_missing_approval_receipt_block(self) -> None:
        for finding in ("invalid_decision_record", "unverified_local_approval", "future_record_update"):
            with self.subTest(finding=finding):
                checked = self.load(self.valid)
                report = assess(checked[0], self.trace(), {"D-001": "2026-11-01"},
                                today=TODAY, digest=checked[1],
                                integrity_findings={"D-001": {finding}})
                self.assertEqual(report["verdict"], "blocked")
                self.assertIn(finding, report["findings"][0]["problems"])

    def test_forged_record_outside_decision_directory_cannot_be_binding(self) -> None:
        import shutil
        base = Path(__file__).resolve().parents[1] / "examples" / "tiny"
        root = self.path.parent / "spoofed"
        shutil.copytree(base, root)
        fake = root / "notes" / "rogue.md"
        fake.write_text(
            "---\ntype: reference\ndecision_id: D-999\ntitle: Rogue\n"
            "status: approved\nreview_by: 2026-11-01\nlast_updated: 2026-10-10\n"
            "created: 2026-10-09\nsensitivity: public\n---\n\nSee E-001.\n",
            encoding="utf-8",
        )
        self.valid["decision_ids"] = ["D-999"]
        self.load(self.valid)
        checked = check_proposal(root, self.path, today=TODAY)
        self.assertEqual(checked["verdict"], "blocked")
        self.assertIn("invalid_decision_record", checked["findings"][0]["problems"])
        self.assertIn("unverified_local_approval", checked["findings"][0]["problems"])

    def test_review_due_today_vs_yesterday(self) -> None:
        self.assertEqual(self.assess(self.trace(), {"D-001": "2026-10-11"})["verdict"], "review_required")
        self.assertEqual(self.assess(self.trace(), {"D-001": "2026-10-10"})["findings"][0]["problems"], ["review_overdue"])

    def test_invalid_manifest_and_duplicate_json_keys_fail(self) -> None:
        invalid = [
            {"proposal_id": "bad", "decision_ids": ["D-001", "D-001"]},
            {"proposal_id": "bad", "decision_ids": []},
            {"proposal_id": "bad", "decision_ids": ["D-١٠١"]},
            {"proposal_id": "bad", "decision_ids": ["D-001"], "unexpected": True},
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(PreflightInputError):
                self.load(candidate)
        self.path.write_text('{"proposal_id":"a","proposal_id":"b","decision_ids":["D-001"]}', encoding="utf-8")
        with self.assertRaises(PreflightInputError):
            read_proposal(self.path)

    def test_bounded_read_and_hash(self) -> None:
        self.path.write_bytes(b"{" + b"x" * (64 * 1024 + 1))
        with self.assertRaises(PreflightInputError):
            read_proposal(self.path)
        _, first = self.load(self.valid)
        _, second = self.load(self.valid)
        self.assertEqual(first, second)

    def test_real_example_vault_does_not_mutate_any_files(self) -> None:
        from hashlib import sha256
        root = Path(__file__).resolve().parents[1] / "examples" / "tiny"
        before = {p.relative_to(root): sha256(p.read_bytes()).hexdigest()
                  for p in root.rglob("*") if p.is_file()}
        self.load(self.valid)
        report = check_proposal(root, self.path, today=TODAY)
        self.assertIn(report["verdict"], {"blocked", "review_required"})
        self.assertFalse(report["execution_authorized"])
        self.assertTrue(report["review_required"])
        after = {p.relative_to(root): sha256(p.read_bytes()).hexdigest()
                 for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


    def test_manifest_schema_enforces_runtime_limits(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        schema = json.loads((repo / "schemas/agent-preflight-proposal.schema.json").read_text(encoding="utf-8"))
        for payload in (
            {"proposal_id": "x" * 81, "decision_ids": ["D-001"]},
            {"proposal_id": "x", "decision_ids": [f"D-{i:03d}" for i in range(65)]},
            {"proposal_id": "x", "decision_ids": ["D-001"], "summary": "x" * 4097},
        ):
            with self.subTest(keys=sorted(payload)):
                self.assertNotEqual(validate(payload, schema), [])
                with self.assertRaises(PreflightInputError):
                    self.load(payload)

    def test_schema_validator_rejects_boolean_number_and_nonfinite_confusion(self) -> None:
        import math

        report = self.assess(self.trace())
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/agent-preflight-result.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        for field in ("execution_authorized", "trusted_history_checked", "semantics_checked"):
            with self.subTest(field=field):
                # Python treats False as equal to integer zero; JSON Schema does not.
                tampered = {**report, field: 0}
                self.assertNotEqual(validate(tampered, schema), [])
        self.assertNotEqual(validate(False, {"enum": [0]}), [])
        self.assertNotEqual(validate(0, {"enum": [False]}), [])
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(nonfinite=repr(invalid)):
                self.assertNotEqual(validate(invalid, {"type": "number"}), [])

    def test_result_and_proposal_match_public_schemas(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        def read(name: str) -> dict:
            return json.loads((repo / "schemas" / name).read_text(encoding="utf-8"))
        report = self.assess(self.trace())
        proposal, _ = self.load(self.valid)
        self.assertEqual(validate(report, read("agent-preflight-result.schema.json")), [])
        self.assertEqual(validate(proposal, read("agent-preflight-proposal.schema.json")), [])
        report["execution_authorized"] = True
        self.assertNotEqual(validate(report, read("agent-preflight-result.schema.json")), [])

    def test_private_summary_not_emitted(self) -> None:
        self.valid["summary"] = "PRIVATE SENSITIVE PROJECT"
        report = self.assess(self.trace())
        self.assertNotIn("PRIVATE", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
