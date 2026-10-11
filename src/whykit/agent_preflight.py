"""Read-only structural preflight for proposed agent actions.

The result never grants execution authority or checks semantic policy compliance.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .io import vault_read_lock
from .lint import DECISION_ID_RE
from .trace import build_trace
from .vault_index import VaultIndex

FORMAT = "whykit.agent-preflight/v1"
MAX_PROPOSAL_BYTES = 64 * 1024
MAX_DECISIONS = 64
PROPOSAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class PreflightInputError(ValueError):
    """Untrusted input refusal with stable, non-sensitive error codes."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise PreflightInputError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise PreflightInputError("non_finite_number")


def read_proposal(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROPOSAL_BYTES + 1)
    if len(raw) > MAX_PROPOSAL_BYTES:
        raise PreflightInputError("proposal_too_large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise PreflightInputError("invalid_json") from None
    if not isinstance(value, dict) or set(value) - {"proposal_id", "decision_ids", "summary"}:
        raise PreflightInputError("unsupported_proposal_fields")
    if not isinstance(value.get("proposal_id"), str) or not PROPOSAL_ID.fullmatch(value["proposal_id"]):
        raise PreflightInputError("invalid_proposal_id")
    ids = value.get("decision_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DECISIONS
            or any(not isinstance(item, str) or not DECISION_ID_RE.fullmatch(item) for item in ids)
            or len(set(ids)) != len(ids)):
        raise PreflightInputError("invalid_decision_ids")
    summary = value.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 4096:
        raise PreflightInputError("invalid_summary")
    return value, hashlib.sha256(raw).hexdigest()


def assess(proposal: dict[str, Any], trace: dict[str, Any], review_dates: dict[str, object],
           *, today: dt.date, digest: str) -> dict[str, Any]:
    """Check declared IDs only. Absence of a finding does not approve a change."""
    by_id: dict[str, list[dict]] = {}
    for row in trace.get("decisions", []):
        key = row.get("decision_id")
        if isinstance(key, str):
            by_id.setdefault(key, []).append(row)
    findings: list[dict[str, Any]] = []
    for did in sorted(proposal["decision_ids"]):
        problems: set[str] = set()
        rows = by_id.get(did, [])
        if len(rows) != 1:
            problems.add("unknown_or_ambiguous_decision")
        else:
            record = rows[0]
            if record.get("live") is not True or record.get("status") != "approved":
                problems.add("not_binding")
            if record.get("gaps") or record.get("claim_gaps"):
                problems.add("unresolved_evidence_or_claims")
            if record.get("superseded_by"):
                problems.add("superseded")
            date_text = review_dates.get(did)
            if not isinstance(date_text, str) or not DATE.fullmatch(date_text):
                problems.add("missing_review_date")
            else:
                try:
                    if dt.date.fromisoformat(date_text) < today:
                        problems.add("review_overdue")
                except ValueError:
                    problems.add("invalid_review_date")
        findings.append({"decision_id": did, "problems": sorted(problems)})
    return {
        "format": FORMAT, "proposal_id": proposal["proposal_id"], "proposal_sha256": digest,
        "as_of": today.isoformat(), "review_scope": "explicit_decision_ids_only",
        "verdict": "blocked" if any(row["problems"] for row in findings) else "review_required",
        "review_required": True, "semantics_checked": False, "execution_authorized": False,
        "referenced_decisions": len(findings), "findings": findings,
    }


def check_proposal(root: Path, proposal_path: Path, *, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    proposal, digest = read_proposal(proposal_path)
    root = root.resolve(strict=True)
    with vault_read_lock(root):
        report = build_trace(root, today=today)
        notes = VaultIndex.load(root).notes
        dates: dict[str, object] = {}
        duplicates: set[str] = set()
        for note in notes:
            did = note.front.get("decision_id")
            if isinstance(did, str) and DECISION_ID_RE.fullmatch(did):
                if did in dates:
                    duplicates.add(did)
                dates[did] = note.front.get("review_by")
        for did in duplicates:
            dates[did] = None
        return assess(proposal, report, dates, today=today, digest=digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only structural proposal preflight; never authorizes execution")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--today", type=str)
    args = parser.parse_args(argv)
    try:
        if args.today:
            if not DATE.fullmatch(args.today):
                raise PreflightInputError("invalid_date")
            today = dt.date.fromisoformat(args.today)
        else:
            today = dt.date.today()
        report = check_proposal(args.root, args.proposal, today=today)
    except (OSError, ValueError, TypeError, KeyError) as error:
        code = error.args[0] if isinstance(error, PreflightInputError) else "unavailable_or_invalid_vault"
        print(json.dumps({"format": FORMAT, "error": str(code)}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 1 if report["verdict"] == "blocked" else 0
