from __future__ import annotations

import copy
import json
import unittest

from _jsonschema import validate
from test_content_quality import DAY, FILLED, ROOT, _TinyCopy
from whykit.lint import lint, load_note
from whykit.rules import RULE_BY_CODE


class DecisionReviewProvenanceTests(_TinyCopy):
    def test_unreviewed_approved_records_warn_but_drafts_do_not(self) -> None:
        for status, flag, expected in (("approved", "false", True), ("approved", '"true"', True), ("approved", "true", False), ("draft", "false", False), ("in_review", "false", False)):
            with self.subTest(status=status, flag=flag):
                path = self.decision(f"Review state {status} {flag}", status=status)
                self.fill(path, FILLED)
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace("tags: []\n", f"tags: []\nprovenance:\n  human_reviewed: {flag}\n", 1), encoding="utf-8")
                findings = [f for f in lint(self.vault, today=DAY)[1] if f.code == "decision.unreviewed" and f.path.endswith(path.name)]
                self.assertEqual(bool(findings), expected)
                if findings:
                    self.assertEqual(findings[0].level, "warning")
        self.assertTrue(RULE_BY_CODE["decision.unreviewed"].security)

    def test_schema_requires_review_date_and_rejects_denied_review(self) -> None:
        schema = json.loads((ROOT / "schemas/decision-record.schema.json").read_text(encoding="utf-8"))
        schema["properties"]["provenance"]["$ref"] = "#/$defs/provenance"
        schema["$defs"] = {"provenance": json.loads((ROOT / "schemas/provenance.schema.json").read_text(encoding="utf-8"))}
        path = self.decision("Schema review contract")
        front = load_note(path).front
        self.assertEqual(validate(front, schema), [])
        for change in ("missing date", "denied review"):
            bad = copy.deepcopy(front)
            if change == "missing date":
                del bad["review_by"]
            else:
                bad["provenance"] = {"human_reviewed": False}
            with self.subTest(change=change):
                self.assertTrue(validate(bad, schema))
                bad["status"] = "draft"
                self.assertEqual(validate(bad, schema), [])


if __name__ == "__main__":
    unittest.main()
