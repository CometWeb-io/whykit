"""Offline evaluation of evidence-grounded agent outputs from JSONL fixtures.

This module never contacts models or vaults. Holdout prompts, candidate answers
and sensitive markers remain local. Reports expose case IDs and violation codes,
not prompts, answers, withheld markers or source text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

FORMAT = "whykit.agent-eval/v1"
MAX_BYTES = 10 * 1024 * 1024
MAX_LINE = 64 * 1024
MAX_CASES = 10_000
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
RECORD_ID = re.compile(r"[ECD]-[0-9]{3,}\Z")
RECORD_MENTION = re.compile(r"(?<![A-Za-z0-9_-])[ECD]-[0-9]{3,}(?![A-Za-z0-9_-])", re.IGNORECASE)
CLAIM_ID = re.compile(r"C-[0-9]{3,}\Z")
CLAIM_STATES = {"supported", "disputed", "unsupported", "unknown"}


class EvaluationInputError(ValueError):
    """A malformed or unsupported input; no private values are echoed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationInputError("duplicate JSON property")
        result[key] = value
    return result


def _read(path: Path, role: str) -> tuple[dict[str, dict], str]:
    records: dict[str, dict] = {}
    sha = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        line_number = 0
        while True:
            # Never materialize an unbounded JSONL line before checking its size.
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            line_number += 1
            total += len(line)
            if total > MAX_BYTES or len(line) > MAX_LINE or line_number > MAX_CASES:
                raise EvaluationInputError(role + ": size or record limit exceeded")
            sha.update(line)
            if not line.strip():
                continue
            try:
                row = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object)
            except (UnicodeError, ValueError, RecursionError):
                raise EvaluationInputError(role + ": invalid JSON at line " + str(line_number)) from None
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not CASE_ID.fullmatch(row["id"]):
                raise EvaluationInputError(role + ": invalid case ID at line " + str(line_number))
            allowed_keys = {"id", "expected", "prompt"} if role == "cases" else {"id", "disposition", "citations", "claim_states", "conflict", "answer"}
            if set(row) - allowed_keys:
                raise EvaluationInputError(role + ": unsupported field at line " + str(line_number))
            if role == "cases" and "prompt" in row and (not isinstance(row["prompt"], str) or len(row["prompt"]) > MAX_LINE):
                raise EvaluationInputError(role + ": invalid prompt at line " + str(line_number))
            if row["id"] in records:
                raise EvaluationInputError(role + ": duplicate case ID at line " + str(line_number))
            records[row["id"]] = row
    if not records:
        raise EvaluationInputError(role + ": empty input")
    return records, sha.hexdigest()


def _ids(value: object, field: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not RECORD_ID.fullmatch(item) for item in value):
        raise EvaluationInputError(field + ": invalid record identifier list")
    if len(set(value)) != len(value):
        raise EvaluationInputError(field + ": duplicated record identifier")
    return set(value)


def _states(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not CLAIM_ID.fullmatch(key)
        or not isinstance(state, str) or state not in CLAIM_STATES
        for key, state in value.items()
    ):
        raise EvaluationInputError(field + ": invalid claim state mapping")
    return value


def _expected(row: dict) -> dict:
    value = row.get("expected")
    if not isinstance(value, dict) or not isinstance(value.get("disposition"), str) or value["disposition"] not in {"answer", "abstain"}:
        raise EvaluationInputError("case: invalid expected disposition")
    allowed_keys = {"disposition", "allowed_citations", "required_citations", "forbidden_citations",
                    "claim_states", "conflict", "forbidden_phrases", "require_inline_citations"}
    if set(value) - allowed_keys:
        raise EvaluationInputError("case: unknown expected field")
    require_inline = value.get("require_inline_citations", False)
    if type(require_inline) is not bool:
        raise EvaluationInputError("case: require_inline_citations must be boolean")
    allowed = _ids(value.get("allowed_citations", []), "allowed_citations")
    required = _ids(value.get("required_citations", []), "required_citations")
    forbidden = _ids(value.get("forbidden_citations", []), "forbidden_citations")
    if not required <= allowed or forbidden & allowed:
        raise EvaluationInputError("case: contradictory citation policy")
    conflict = value.get("conflict", False)
    if type(conflict) is not bool:
        raise EvaluationInputError("case: conflict must be a boolean")
    phrases = value.get("forbidden_phrases", [])
    if not isinstance(phrases, list) or len(phrases) > 50 or any(not isinstance(p, str) or not p or len(p) > 512 for p in phrases):
        raise EvaluationInputError("case: invalid forbidden phrases")
    return {
        "disposition": value["disposition"], "allowed": allowed, "required": required,
        "forbidden": forbidden, "claims": _states(value.get("claim_states", {}), "claim_states"),
        "conflict": conflict, "phrases": phrases, "require_inline": require_inline,
    }


def _response(row: dict) -> dict:
    if not isinstance(row.get("disposition"), str) or row["disposition"] not in {"answer", "abstain"}:
        raise EvaluationInputError("response: invalid disposition")
    answer, conflict = row.get("answer", ""), row.get("conflict", False)
    if not isinstance(answer, str) or len(answer) > MAX_LINE or type(conflict) is not bool:
        raise EvaluationInputError("response: invalid answer or conflict")
    return {
        "disposition": row["disposition"],
        "citations": _ids(row.get("citations", []), "citations"),
        "claims": _states(row.get("claim_states", {}), "claim_states"),
        "conflict": conflict, "answer": answer,
    }


def evaluate(cases_path: Path, responses_path: Path) -> dict:
    cases, corpus_sha256 = _read(cases_path, "cases")
    predictions, responses_sha256 = _read(responses_path, "responses")
    if set(predictions) - set(cases):
        raise EvaluationInputError("unknown response case ID")
    results = []
    overconfident = false_abstentions = unknown_citations = leaked = required_count = recovered = licensed = presented = 0
    for case_id in sorted(cases):
        expected = _expected(cases[case_id])
        response = _response(predictions[case_id]) if case_id in predictions else None
        violations: set[str] = set()
        if response is None:
            violations.add("missing_response")
        else:
            # Correct metadata without an actual answer is not a successful
            # grounded answer. The evaluator still cannot judge semantic truth.
            if response["disposition"] == "answer" and not response["answer"].strip():
                violations.add("empty_answer")
            if response["disposition"] != expected["disposition"]:
                violations.add("wrong_disposition")
                if response["disposition"] == "answer" and expected["disposition"] == "abstain":
                    overconfident += 1
                elif response["disposition"] == "abstain" and expected["disposition"] == "answer":
                    false_abstentions += 1
            # A model can mention E-999 in prose but leave it out of the structured
            # citations array. Evaluate both surfaces; do not trust self-reporting.
            inline = {identifier.upper() for identifier in RECORD_MENTION.findall(response["answer"])}
            claimed = response["citations"] | inline
            presented += len(claimed)
            licensed += len(claimed & expected["allowed"])
            invalid = claimed - expected["allowed"]
            if invalid:
                violations.add("unlicensed_citations")
                unknown_citations += len(invalid)
            if claimed & expected["forbidden"]:
                violations.add("forbidden_citations")
            if expected["require_inline"] and response["citations"] - inline:
                violations.add("citations_not_in_answer")
            if expected["required"] - response["citations"]:
                violations.add("missing_citations")
            if response["claims"] != expected["claims"]:
                violations.add("incorrect_claim_states")
            if response["conflict"] != expected["conflict"]:
                violations.add("incorrect_conflict_assessment")
            normalized_answer = unicodedata.normalize("NFC", response["answer"]).casefold()
            count = sum(unicodedata.normalize("NFC", p).casefold() in normalized_answer for p in expected["phrases"])
            if count:
                leaked += count
                violations.add("forbidden_phrase_in_answer")
            if expected["disposition"] == "abstain" and claimed:
                violations.add("abstention_with_citations")
            recovered += len(expected["required"] & response["citations"])
        required_count += len(expected["required"])
        results.append({"id": case_id, "pass": not violations, "violations": sorted(violations)})
    passed = sum(1 for item in results if item["pass"] is True)
    return {
        "format": FORMAT, "cases_sha256": corpus_sha256, "responses_sha256": responses_sha256,
        "count": len(results), "passed": passed, "pass_rate": round(passed / len(results), 6),
        "false_answers": overconfident, "false_abstentions": false_abstentions,
        "citation_precision": round(licensed / presented, 6) if presented else None,
        "unlicensed_citations": unknown_citations,
        "forbidden_phrase_matches": leaked,
        "required_citation_recall": round(recovered / required_count, 6) if required_count else None,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate locally supplied agent answers against a sealed evidence corpus")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--responses", required=True, type=Path)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not (0 <= args.min_pass_rate <= 1):
        parser.error("--min-pass-rate must be between 0 and 1")
    try:
        result = evaluate(args.cases, args.responses)
    except (OSError, EvaluationInputError) as exc:
        print(json.dumps({"format": FORMAT, "error": "invalid_input", "kind": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if (result["passed"] / result["count"] >= args.min_pass_rate
                 and result["false_answers"] == 0 and result["forbidden_phrase_matches"] == 0) else 1


