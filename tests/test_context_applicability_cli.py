"""Preflight context coverage admits premises, not explicit nonbinding references."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
import tempfile
import unittest

from _vaults import ROOT, fill_decision
from test_workflow_cli import call
from whykit.compiled_context import compile_context, serialize_compiled
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision, create_evidence

DAY = dt.date(2026, 9, 17)


class ContextApplicabilityCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "vault"
        shutil.copytree(ROOT / "examples/approval", self.root)
        (self.root / "whykit.toml").write_text("[evidence_access_age_days]\nfixture = 90\n", encoding="utf-8")
        self.evidence = create_evidence(self.root, source="Synthetic local source", kind="fixture",
                                       location="https://example.com/fixture", claims="Fixture premise only",
                                       sensitivity="public", today=DAY)

    def decision(self, field, value):
        did, path = create_decision(self.root, "Declared decision interval", owner="Ada Example",
                                    source_ids=[self.evidence], today=DAY, sensitivity="public")
        fill_decision(path)
        if field is not None:
            path.write_text(_frontmatter_replace(path.read_text(encoding="utf-8"), field, value), encoding="utf-8")
        plan = approve_decision(self.root, did, reviewer="Ada Example", today=DAY,
                                next_review="2026-12-17")
        approve_decision(self.root, did, reviewer="Ada Example", today=DAY, next_review="2026-12-17",
                         write=True, expected_sha256=plan["expected_sha256"])
        proposal = self.base / f"proposal-{did}.json"
        proposal.write_text(json.dumps({"proposal_id": "synthetic-interval", "decision_ids": [did]}), encoding="utf-8")
        return did, proposal

    def test_explicit_future_or_expired_reference_does_not_cover_a_premise(self):
        for field, value in (("valid_from", "2026-09-18"), ("valid_until", "2026-09-17")):
            with self.subTest(field=field):
                did, proposal = self.decision(field, value)
                code, baseline = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                      "--today", DAY.isoformat(), "--json")
                self.assertEqual(code, 1, baseline)
                self.assertEqual(baseline["verdict"], "blocked")
                compiled = compile_context(self.root, targets=[did], today=DAY)
                self.assertEqual(compiled["status"], "compiled")
                report = self.base / f"context-{did}.json"
                report.write_text(serialize_compiled(compiled), encoding="utf-8")
                code, guarded = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                     "--context", str(report), "--today", DAY.isoformat(), "--json")
                self.assertEqual(code, 1, guarded)
                self.assertEqual(guarded["verdict"], "blocked")
                self.assertEqual(guarded["context_verification"]["status"], "current")
                self.assertIn("context_missing_decision", guarded["findings"][0]["problems"])
                self.assertIs(guarded["execution_authorized"], False)

    def test_applicable_and_legacy_approved_contexts_cover_a_premise(self):
        for field, value in (("valid_from", "2026-09-10"), (None, None)):
            with self.subTest(field=field):
                did, proposal = self.decision(field, value)
                compiled = compile_context(self.root, targets=[did], today=DAY)
                self.assertEqual(compiled["status"], "compiled")
                context = next(item for item in compiled["contexts"] if item["record"].get("decision_id") == did)
                self.assertIs(context["applicability"]["premise"], True)
                self.assertEqual(context["applicability"]["state"], "applicable")
                if field is None:
                    self.assertEqual(context["applicability"]["declared_validity"]["state"], "unknown")
                report = self.base / f"context-{did}.json"
                report.write_text(serialize_compiled(compiled), encoding="utf-8")
                code, result = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                    "--context", str(report), "--today", DAY.isoformat(), "--json")
                self.assertEqual(code, 0, result)
                self.assertEqual(result["verdict"], "pass")
                self.assertIs(result["execution_authorized"], False)

    def test_explicit_decision_interval_is_checked_without_context(self):
        cases = (("valid_from", "2026-09-18", "decision_not_yet_valid"),
                 ("valid_until", "2026-09-17", "decision_expired"),
                 ("valid_from", "not-a-day", "invalid_decision_validity"))
        for field, value, reason in cases:
            with self.subTest(value=value):
                _, proposal = self.decision(field, value)
                code, result = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                    "--today", DAY.isoformat(), "--json")
                self.assertEqual(code, 1, result)
                self.assertEqual(result["verdict"], "blocked")
                self.assertIn(reason, result["findings"][0]["problems"])
                self.assertIs(result["execution_authorized"], False)

    def test_backdated_confirmation_does_not_clear_a_newer_review_request(self):
        for outcome, reason in (("update-required", "review_update_required"),
                                ("supersede-required", "review_supersede_required")):
            with self.subTest(outcome=outcome):
                did, proposal = self.decision(None, None)
                record_review(self.root, did, reviewer="Ada Example", outcome=outcome,
                              today=dt.date(2026, 9, 30))
                record_review(self.root, did, reviewer="Ada Example", outcome="confirmed",
                              next_review="2026-12-20", today=dt.date(2026, 9, 20))
                code, result = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                    "--today", "2026-10-01", "--json")
                self.assertEqual(code, 1, result)
                self.assertEqual(result["verdict"], "needs_review")
                self.assertIn(reason, result["findings"][0]["problems"])
                self.assertIs(result["execution_authorized"], False)

    def test_effective_later_confirmation_and_same_day_order(self):
        cases = ((dt.date(2026, 9, 20), dt.date(2026, 9, 30), False, "pass"),
                 (dt.date(2026, 9, 30), dt.date(2026, 9, 30), False, "pass"),
                 (dt.date(2026, 9, 30), dt.date(2026, 9, 30), True, "needs_review"))
        for requested, confirmed, reverse, verdict in cases:
            with self.subTest(reverse=reverse, requested=requested):
                did, proposal = self.decision(None, None)
                actions = [("update-required", requested), ("confirmed", confirmed)]
                for outcome, when in reversed(actions) if reverse else actions:
                    record_review(self.root, did, reviewer="Ada Example", outcome=outcome,
                                  next_review="2026-12-20" if outcome == "confirmed" else None, today=when)
                code, result = call("agent", "preflight", "--root", str(self.root), "--proposal", str(proposal),
                                    "--today", "2026-10-01", "--json")
                self.assertEqual(result["verdict"], verdict, result)
                self.assertEqual(code, 0 if verdict == "pass" else 1, result)
                compiled = compile_context(self.root, targets=[did], today=dt.date(2026, 10, 1))
                context = next(item for item in compiled["contexts"] if item["record"].get("decision_id") == did)
                self.assertEqual(context["applicability"]["premise"], verdict == "pass")
