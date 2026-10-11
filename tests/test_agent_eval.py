"""Deterministic correctness and safety checks for the provider-neutral agent evaluator."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from whykit.agent_eval import EvaluationInputError, evaluate, main


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, cases: list[dict], responses: list[dict]) -> tuple[Path, Path]:
        left, right = self.root / "cases.jsonl", self.root / "responses.jsonl"
        left.write_text("".join(json.dumps(item) + "\n" for item in cases), encoding="utf-8")
        right.write_text("".join(json.dumps(item) + "\n" for item in responses), encoding="utf-8")
        return left, right

    def case(self, disposition="answer") -> dict:
        return {"id": "test-one", "expected": {
            "disposition": disposition, "allowed_citations": ["E-001"] if disposition == "answer" else [],
            "required_citations": ["E-001"] if disposition == "answer" else [],
            "forbidden_phrases": ["SENSITIVE-HOLDOUT"],
        }}

    def test_all_requirements_pass(self) -> None:
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "Grounded."}
        ])
        result = evaluate(left, right)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["required_citation_recall"], 1)
        self.assertEqual(main(["--cases", str(left), "--responses", str(right)]), 0)

    def test_overconfident_answer_and_leak_never_pass(self) -> None:
        left, right = self.fixture([self.case("abstain")], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "SENSITIVE-HOLDOUT"}
        ])
        result = evaluate(left, right)
        self.assertEqual(result["false_answers"], 1)
        self.assertEqual(result["forbidden_phrase_matches"], 1)
        self.assertIn("unlicensed_citations", result["results"][0]["violations"])
        self.assertEqual(main(["--cases", str(left), "--responses", str(right), "--min-pass-rate", "0"]), 1)
        self.assertNotIn("SENSITIVE-HOLDOUT", json.dumps(result))

    def test_claims_and_conflict_are_typed(self) -> None:
        case = self.case()
        case["expected"]["claim_states"] = {"C-001": "disputed"}
        case["expected"]["conflict"] = True
        left, right = self.fixture([case], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"],
             "claim_states": {"C-001": "supported"}, "conflict": False}
        ])
        problems = evaluate(left, right)["results"][0]["violations"]
        self.assertIn("incorrect_claim_states", problems)
        self.assertIn("incorrect_conflict_assessment", problems)

    def test_missing_and_unknown_predictions(self) -> None:
        left, right = self.fixture([self.case(), {"id": "test-two", "expected": {"disposition": "abstain"}}], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"]}
        ])
        self.assertEqual(evaluate(left, right)["passed"], 1)
        self.assertEqual(evaluate(left, right)["results"][1]["violations"], ["missing_response"])
        right.write_text('{"id":"other","disposition":"answer"}\n', encoding="utf-8")
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)

    def test_duplicate_ids_and_unhashable_fields_fail_closed(self) -> None:
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"]}
        ])
        right.write_text('{"id":"test-one","disposition":"answer"}\n{"id":"test-one","disposition":"answer"}\n', encoding="utf-8")
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)
        right.write_text('{"id":"test-one","disposition":[],"citations":[]}\n', encoding="utf-8")
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)
        right.write_text('{"id":"test-one","disposition":"answer","claim_states":{"C-001":[]}}\n', encoding="utf-8")
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)


if __name__ == "__main__":
    unittest.main()
