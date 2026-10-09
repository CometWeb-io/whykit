from __future__ import annotations
import datetime as dt
import json
import unittest
from pathlib import Path
from _jsonschema import validate
from whykit.lint import lint
from whykit.trace import build_trace

ROOT = Path(__file__).resolve().parents[1]


class ClaimExampleTests(unittest.TestCase):
    def test_claims_example_exposes_reviewed_conflict(self):
        root = ROOT / "examples/claims"
        self.assertTrue(root.is_dir(), "F1 needs a complete generic reviewed example")
        trace = build_trace(root, today=dt.date(2026, 10, 9), decision="D-001")
        self.assertEqual(
            trace["decisions"][0]["claims"][0]["verification_status"], "disputed"
        )
        self.assertEqual(trace["summary"]["live_with_gaps"], 0)
        schema = json.loads(
            (ROOT / "schemas/trace-report-v2.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validate(trace, schema), [])
        self.assertEqual(lint(root, today=dt.date(2026, 10, 9))[1], [])

    def test_docs_explain_opt_in_limits_and_attribution(self):
        text = (ROOT / "docs/claims.md").read_text(encoding="utf-8")
        for phrase in (
            "format_version = 1",
            "1 MiB",
            "256",
            "claim-receipt/v1",
            "not authenticated",
            "not truth",
            "history_reconstructed",
            "--expect-hash",
        ):
            self.assertIn(phrase, text)
