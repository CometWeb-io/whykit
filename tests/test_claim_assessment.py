"""Deterministic receipts are constructed locally; this is not authenticated approval."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from _claims import TODAY, claim_vault, read_view
from whykit import claims
from _jsonschema import validate


class ClaimAssessmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.path = claim_vault(self.root)

    def api(self, name):
        method = getattr(claims, name, None)
        self.assertTrue(callable(method), f"Claim assessment requires {name}")
        return method

    def reviewed(self, *, keep=("supports", "contradicts"), extra=False):
        view = read_view(self.root)
        record = view["records"]["C-001"]
        record["relations"] = [r for r in record["relations"] if r["relation"] in keep]
        if extra:
            record["relations"].append(
                {**record["relations"][0], "evidence_id": "E-003"}
            )
            view["evidence"]["E-003"] = dict(view["evidence"]["E-001"])
        record["front"].update(
            status="approved", last_verified="2026-10-09", review_by="2027-01-07"
        )
        # Static evaluator tests bind a constructed captured view, not a file mutation workflow.
        receipt = self.api("encode_claim_receipt")(
            view, "C-001", previous_review=None, next_review="2027-01-07"
        )
        view["review_rows"] = [
            [
                "2026-10-09",
                "[[00-context/claims/c-001-offline-reads]]",
                "Ada Example",
                "approved",
                "—",
                "2027-01-07",
                receipt,
            ]
        ]
        return view

    def result(self, view, today=TODAY):
        return self.api("evaluate_claims")(view, today=today)["C-001"]

    def test_state_table(self):
        for keep, expected in (
            (("supports",), "supported"),
            (("contradicts",), "unsupported"),
            (("supports", "contradicts"), "disputed"),
        ):
            with self.subTest(expected=expected):
                result = self.result(self.reviewed(keep=keep))
                self.assertEqual(result["verification_status"], expected)
                self.assertTrue(result["binding_valid"])
                self.assertFalse(result["history_reconstructed"])
                schema = json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "schemas/claim-assessment.schema.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(validate(result, schema), [])
        result = self.result(read_view(self.root))
        self.assertEqual(result["verification_status"], "unknown")
        self.assertIn("not_reviewed", result["reasons"])

    def test_conflict_with_unresolved_third_relation(self):
        view = self.reviewed(extra=True)
        del view["evidence"]["E-003"]
        result = self.result(view)
        self.assertEqual(result["verification_status"], "disputed")
        self.assertFalse(result["binding_valid"])
        self.assertIn("missing_evidence", result["reasons"])

    def test_lost_contradiction_is_unknown(self):
        for problem in ("missing", "retired", "stale", "changed"):
            with self.subTest(problem=problem):
                view = self.reviewed()
                if problem == "missing":
                    del view["evidence"]["E-002"]
                elif problem == "retired":
                    view["evidence"]["E-002"]["state"] = "retired"
                elif problem == "stale":
                    view["config"]["evidence_access_age_days"] = {"report": 0}
                else:
                    view["evidence"]["E-002"]["source"] = "Changed observation"
                result = self.result(
                    view, TODAY + dt.timedelta(days=1) if problem == "stale" else TODAY
                )
                self.assertEqual(result["verification_status"], "unknown")
                self.assertIn(
                    "evidence_binding_changed"
                    if problem == "changed"
                    else problem + "_evidence",
                    result["reasons"],
                )

    def test_dates_are_inclusive(self):
        view = self.reviewed(keep=("supports",))
        self.assertEqual(
            self.result(view, dt.date(2026, 12, 31))["verification_status"], "supported"
        )
        self.assertIn(
            "outside_validity", self.result(view, dt.date(2027, 1, 1))["reasons"]
        )
        view["records"]["C-001"]["front"].pop("valid_to")
        self.assertEqual(
            self.result(view, dt.date(2027, 1, 7))["verification_status"], "supported"
        )
        self.assertIn(
            "review_overdue", self.result(view, dt.date(2027, 1, 8))["reasons"]
        )

    def test_future_observation_and_invalid_fragment_are_unknown(self):
        view = self.reviewed(keep=("supports",))
        view["records"]["C-001"]["relations"][0]["observed_at"] = "2026-10-10"
        self.assertIn("future_observation", self.result(view)["reasons"])
        view = self.reviewed(keep=("supports",))
        snapshot = view["records"]["C-001"]["relations"][0]["snapshot"]
        view["snapshots"][snapshot]["hash"] = "0" * 64
        self.assertEqual(self.result(view)["verification_status"], "unknown")

    def test_invalid_or_duplicate_receipt_cannot_fall_back(self):
        for problem in ("invalid", "duplicate", "wrong_record", "wrong_target"):
            view = self.reviewed(keep=("supports",))
            if problem == "duplicate":
                view["review_rows"].append(list(view["review_rows"][0]))
            elif problem == "wrong_record":
                view["records"]["C-001"]["text"] += "\nchanged\n"
            elif problem == "wrong_target":
                view["review_rows"][0][1] = "[[00-context/claims/c-002-other]]"
            else:
                view["review_rows"].append(
                    [*view["review_rows"][0][:6], "claim-receipt/v1:!"]
                )
            with self.subTest(problem=problem):
                self.assertEqual(self.result(view)["verification_status"], "unknown")
