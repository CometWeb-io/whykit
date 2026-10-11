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
                 "superseded_by": [], "gaps": [], "claim_gaps": [], **overrides}]}

    def assess(self, trace: dict, dates: dict[str, object] | None = None) -> dict:
        proposal, digest = self.load(self.valid)
        return assess(proposal, trace, dates if dates is not None else {"D-001": "2026-11-01"}, today=TODAY, digest=digest)

    def test_green_evidence_still_requires_human_review(self) -> None:
        result = self.assess(self.trace())
        self.assertEqual(result["verdict"], "review_required")
        self.assertTrue(result["review_required"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["semantics_checked"])

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
        self.path.write_text('{"proposal_id":"a","proposal_id":"b","decision_ids":["D-001"]}')
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
