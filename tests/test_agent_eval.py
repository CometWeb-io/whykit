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

    def test_gate_compares_unrounded_pass_rate(self) -> None:
        cases = [{"id": f"case-{i}", "expected": {"disposition": "answer", "allowed_citations": ["E-001"]}} for i in range(3)]
        responses = [
            {"id": "case-0", "disposition": "answer", "answer": "Grounded result 0."},
            {"id": "case-1", "disposition": "answer", "answer": "Grounded result 1."},
        ]
        left, right = self.fixture(cases, responses)
        result = evaluate(left, right)
        self.assertEqual(result["pass_rate"], 0.666667)
        self.assertEqual(main(["--cases", str(left), "--responses", str(right), "--min-pass-rate", "0.666667"]), 1)
        self.assertEqual(main(["--cases", str(left), "--responses", str(right), "--min-pass-rate", "0.666666"]), 0)

    def test_agent_eval_public_schema_validates_real_output_and_ranges(self) -> None:
        from _jsonschema import validate
        repo = Path(__file__).resolve().parents[1]
        schema = json.loads((repo / "schemas/agent-eval-result.schema.json").read_text(encoding="utf-8"))
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "E-001"}
        ])
        report = evaluate(left, right)
        self.assertEqual(validate(report, schema), [])
        for field in ("pass_rate", "citation_precision", "required_citation_recall"):
            with self.subTest(metric=field):
                corrupted = {**report, field: 1.1}
                self.assertNotEqual(validate(corrupted, schema), [])

    def test_all_requirements_pass(self) -> None:
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "Grounded."}
        ])
        result = evaluate(left, right)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["required_citation_recall"], 1)
        self.assertEqual(result["citation_precision"], 1)
        self.assertEqual(result["false_abstentions"], 0)
        self.assertEqual(main(["--cases", str(left), "--responses", str(right)]), 0)

    def test_answer_without_text_does_not_pass_on_citations_alone(self) -> None:
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "   "}
        ])
        report = evaluate(left, right)
        self.assertEqual(report["passed"], 0)
        self.assertIn("empty_answer", report["results"][0]["violations"])

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

    def test_unlicensed_identifier_in_answer_even_when_structured_citations_are_correct(self) -> None:
        left, right = self.fixture([self.case()], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"],
             "answer": "E-001 supports this, and E-999 apparently confirms it."}
        ])
        actual = evaluate(left, right)
        self.assertEqual(actual["passed"], 0)
        self.assertEqual(actual["unlicensed_citations"], 1)
        self.assertIn("unlicensed_citations", actual["results"][0]["violations"])

    def test_citations_can_be_required_in_the_actual_answer(self) -> None:
        case = self.case()
        case["expected"]["require_inline_citations"] = True
        left, right = self.fixture([case], [
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "No referenced ID."}
        ])
        result = evaluate(left, right)
        self.assertIn("citations_not_in_answer", result["results"][0]["violations"])
        right.write_text('{"id":"test-one","disposition":"answer","citations":["E-001"],"answer":"See E-001"}\n', encoding="utf-8")
        self.assertEqual(evaluate(left, right)["passed"], 1)

    def test_unknown_schema_keys_fail_closed(self) -> None:
        case = self.case()
        case["expected"]["required_citationz"] = ["E-001"]
        left, right = self.fixture([case], [{"id":"test-one","disposition":"answer"}])
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)
        del case["expected"]["required_citationz"]
        left, right = self.fixture([case], [{"id":"test-one","disposition":"answer","citaitons":["E-001"]}])
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)

    def test_normalized_forbidden_phrase_catches_decomposed_unicode(self) -> None:
        case = self.case()
        case["expected"]["forbidden_phrases"] = ["Café"]
        left, right = self.fixture([case], [
            {"id": "test-one", "disposition": "answer", "citations":["E-001"], "answer":"Cafe\u0301"}
        ])
        self.assertGreater(evaluate(left, right)["forbidden_phrase_matches"], 0)

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
            {"id": "test-one", "disposition": "answer", "citations": ["E-001"], "answer": "E-001 supports this case."}
        ])
        self.assertEqual(evaluate(left, right)["passed"], 1)
        self.assertEqual(evaluate(left, right)["results"][1]["violations"], ["missing_response"])
        right.write_text('{"id":"other","disposition":"answer"}\n', encoding="utf-8")
        with self.assertRaises(EvaluationInputError):
            evaluate(left, right)

    def test_line_limit_is_enforced_before_json_parsing(self) -> None:
        from whykit.agent_eval import MAX_LINE
        left, right = self.fixture([self.case()], [{"id": "test-one", "disposition": "answer"}])
        left.write_bytes(b"x" * (MAX_LINE + 100))
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
