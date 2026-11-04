"""Read-only checks of explicitly declared decision dependencies, never authority."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .claim_ledger import ClaimSnapshotChanged, _claim_id, read_claim, verify_claim
from .config import load_config
from .immutability import _approval_event_matches
from .io import open_regular_binary, vault_read_lock
from .lint import (
    Note, _parse_date, _parse_evidence_register_text, check_front_matter,
    is_vault_root, path_cache,
)
from .trace import _evidence_state, build_trace
from .temporal_graph import _validity
from .review_history import latest_review_event, project_review_rows
from .vault_index import NoteCache, VaultIndex, reuse_notes

FORMAT = "whykit.agent-preflight/v1"
MAX_PROPOSAL_BYTES = 64 * 1024
MAX_DECISIONS = 64
PROPOSAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
DECISION_ID = re.compile(r"D-[0-9]{3,}\Z")
MAX_CLAIMS = 64
MAX_CLAIM_PIN_BYTES = 16 * 1024
REVIEW_CODES = frozenset({"review_overdue", "stale_evidence", "evidence_age_unbounded",
                          "claim_snapshot_unpinned", "claim_unlinked", "claim_contradicted", "claim_model_assessment",
                          "claim_validity_unknown", "claim_not_current", "claim_source_changed", "claim_source_unknown",
                          "claim_source_ambiguous", "claim_source_future", "review_update_required", "review_supersede_required"})


class PreflightInputError(ValueError):
    """A stable refusal code without vault-derived text."""


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
    """Read a bounded manifest; the digest binds bytes, not an agent's identity."""
    with open_regular_binary(path) as stream:
        raw = stream.read(MAX_PROPOSAL_BYTES + 1)
    if len(raw) > MAX_PROPOSAL_BYTES:
        raise PreflightInputError("proposal_too_large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except PreflightInputError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise PreflightInputError("invalid_json") from None
    if not isinstance(value, dict) or set(value) - {"proposal_id", "decision_ids", "summary"}:
        raise PreflightInputError("unsupported_proposal_fields")
    if not isinstance(value.get("proposal_id"), str) or not PROPOSAL_ID.fullmatch(value["proposal_id"]):
        raise PreflightInputError("invalid_proposal_id")
    ids = value.get("decision_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DECISIONS
            or any(not isinstance(item, str) or not DECISION_ID.fullmatch(item) for item in ids)
            or len(set(ids)) != len(ids)):
        raise PreflightInputError("invalid_decision_ids")
    if not isinstance(value.get("summary", ""), str) or len(value.get("summary", "")) > 4096:
        raise PreflightInputError("invalid_summary")
    return value, hashlib.sha256(raw).hexdigest()


def _evidence_problems(entries: list[dict], duplicates: set[str]) -> set[str]:
    problems = set()
    for entry in entries:
        if entry["id"] in duplicates:
            problems.add("ambiguous_evidence")
        if entry["state"] == "missing":
            problems.add("missing_evidence")
        elif entry["state"] == "retired":
            problems.add("retired_evidence")
        elif entry["state"] == "active":
            if type(entry["age_days"]) is not int or entry["age_days"] < 0:
                problems.add("unverifiable_evidence_access")
            if entry["max_age_days"] is None:
                problems.add("evidence_age_unbounded")
            if entry["stale"]:
                problems.add("stale_evidence")
    return problems


def _claim_problems(root: Path, note: Note, duplicates: set[str], state_of: Callable[[str], dict], today: dt.date) -> set[str]:
    ids = note.front.get("claim_ids", [])
    if not isinstance(ids, list) or len(ids) > MAX_CLAIMS:
        return {"invalid_claim_ids"}
    try:
        for claim_id in ids:
            _claim_id(claim_id)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate claim IDs")
    except ValueError:
        return {"invalid_claim_ids"}
    problems: set[str] = set()
    provenance = note.front.get("provenance")
    pins = None
    if isinstance(provenance, dict) and "claim_versions" in provenance:
        try:
            value = provenance["claim_versions"]
            if isinstance(value, str):
                if len(value.encode("utf-8")) > MAX_CLAIM_PIN_BYTES:
                    raise ValueError("pin map too large")
                value = json.loads(value, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            if not isinstance(value, dict) or set(value) != set(ids):
                raise ValueError("pin map must exactly match claim_ids")
            if any(not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) for digest in value.values()):
                raise ValueError("invalid claim pin")
            pins = value
        except (ValueError, UnicodeError, RecursionError):
            problems.add("invalid_claim_versions")
    elif ids:
        problems.add("claim_snapshot_unpinned")
    for claim_id in ids:
        expected = pins[claim_id][7:] if pins is not None else None
        try:
            claim = read_claim(root, claim_id, expected_sha256=expected)
        except ClaimSnapshotChanged:
            problems.add("claim_snapshot_changed")
            continue
        except (OSError, ValueError):
            problems.add("invalid_claim_record")
            continue
        if claim is None:
            problems.add("missing_claim")
            continue
        valid_from = _parse_date(claim["record"].get("valid_from"))
        valid_until = _parse_date(claim["record"].get("valid_until"))
        if valid_from is None:
            problems.add("claim_validity_unknown")
        if (valid_from is not None and today < valid_from) or (valid_until is not None and today >= valid_until):
            problems.add("claim_not_current")
        links = claim["payload"]["links"]
        entries = [{"id": evidence_id, **state_of(evidence_id)} for evidence_id in sorted({link["evidence_id"] for link in links})]
        problems.update(_evidence_problems(entries, duplicates))
        try:
            verification = verify_claim(root, claim_id, expected_sha256=expected, today=today)
        except ClaimSnapshotChanged:
            problems.add("claim_snapshot_changed")
            continue
        except (OSError, ValueError):
            problems.add("claim_verification_failed")
            continue
        if not verification["exists"] or any(link["status"] != "matched" for link in verification["links"]):
            problems.add("unverified_claim_anchor")
        for link in verification["links"]:
            state = link.get("source_state")
            if state is None:
                problems.add("claim_source_unknown")
            elif isinstance(state, str) and state in {"changed", "unknown", "ambiguous", "future"}:
                problems.add("claim_source_" + state)
            elif state != "current":
                problems.add("claim_verification_failed")
        if not links:
            problems.add("claim_unlinked")
        if any(link["assessment"]["relation"] == "contradicts" for link in links):
            problems.add("claim_contradicted")
        def assessed_anchor(link):
            location = link["location"]
            return (link["evidence_id"], link["source_version"], location["start_byte"], location["end_byte"],
                    location["quote"], link["assessment"]["relation"])
        declared_human = {assessed_anchor(link) for link in links if link["assessment"]["kind"] == "human"}
        if any(link["assessment"]["kind"] == "model" and assessed_anchor(link) not in declared_human for link in links):
            problems.add("claim_model_assessment")
    return problems


def _problems(root: Path, note: Note, row: dict, log_text: str, duplicates: set[str], today: dt.date,
              state_of: Callable[[str], dict], review_outcome: str | None = None) -> set[str]:
    problems: set[str] = set()
    did = row["decision_id"]
    relative = note.path.relative_to(root).as_posix()
    canonical = (
        note.front_error is None and note.front.get("type") == "decision"
        and re.fullmatch(r"06-decisions/" + did.lower() + r"-[^/]+\.[mM][dD]", relative) is not None
    )
    findings: list = []
    check_front_matter(root, note, findings, today)
    if (not canonical or any(item.level == "error" for item in findings) or note.decision_placeholders
            or not isinstance(note.front.get("owner"), str)
            or note.front["owner"].strip().casefold() in {"", "todo"}):
        problems.add("invalid_decision_record")
    validity_problem = {"not_yet": "decision_not_yet_valid", "expired": "decision_expired",
                        "invalid": "invalid_decision_validity"}.get(_validity(note, today)["state"])
    if validity_problem:
        problems.add(validity_problem)
    if review_outcome in {"update-required", "supersede-required"}:
        problems.add("review_" + review_outcome.replace("-", "_"))
    elif review_outcome == "archived":
        problems.add("not_binding")
    if row["status"] != "approved" or row["live"] is not True:
        problems.add("not_binding")
    if row["superseded_by"]:
        problems.add("superseded")
    if not canonical or not _approval_event_matches(note.text, relative, None, log_text):
        problems.add("unverified_local_approval")
    updated = _parse_date(note.front.get("last_updated"))
    if updated is None or updated > today:
        problems.add("invalid_record_timestamp")
    deadline = _parse_date(note.front.get("review_by"))
    if deadline is None:
        problems.add("invalid_review_date")
    elif deadline < today:
        problems.add("review_overdue")
    provenance = note.front.get("provenance")
    if isinstance(provenance, dict) and provenance.get("human_reviewed", True) is not True:
        problems.add("unreviewed_decision")
    problems.update(_claim_problems(root, note, duplicates, state_of, today))
    problems.update(row["gaps"])
    problems.update(_evidence_problems(row["evidence"], duplicates))
    return problems


def check_proposal(root: Path, proposal_path: Path, *, today: dt.date | None = None) -> dict:
    """Check current vault bytes under one advisory read lock, without Git or writes."""
    today = today or dt.date.today()
    proposal, digest = read_proposal(proposal_path)
    root = root.resolve(strict=True)
    with vault_read_lock(root), path_cache(), reuse_notes(NoteCache()):
        for relative in ("Home.md", "00-context", "06-decisions", "whykit.toml",
                         "00-context/review-log.md", "00-context/evidence-register.md"):
            if (root / relative).is_symlink():
                raise PreflightInputError("unsafe_vault_path")
        if not is_vault_root(root):
            raise PreflightInputError("vault_not_found")
        config, _ = load_config(root)
        vault = VaultIndex.load(root)
        trace = build_trace(root, today=today, vault=vault,
                            access_age_days=dict(config["evidence_access_age_days"]))
        log = vault.note_for(root / "00-context/review-log.md")
        reviews, _, _ = project_review_rows(log, vault)
        reviews_by_target: dict[Path, list[dict]] = {}
        for event in reviews:
            reviews_by_target.setdefault(event["target"], []).append(event)
        register = vault.note_for(root / "00-context/evidence-register.md")
        if register is None or register.front_error:
            raise PreflightInputError("invalid_evidence_register")
        active, retired, occurrences = _parse_evidence_register_text(register.text)
        duplicates = {key for key, count in Counter(key for key, _ in occurrences).items() if count > 1}
        def state_of(evidence_id: str) -> dict:
            return _evidence_state(evidence_id, active, retired, dict(config["evidence_access_age_days"]), None, today)
        by_id: dict[str, list[dict]] = {}
        for row in trace["decisions"]:
            by_id.setdefault(row["decision_id"], []).append(row)
        findings = []
        for did in sorted(proposal["decision_ids"]):
            rows = by_id.get(did, [])
            if len(rows) != 1:
                problems = {"unknown_or_ambiguous_decision"}
            else:
                row = rows[0]
                note = vault.note_for(root / row["path"])
                if note is None or note.path.is_symlink():
                    problems = {"invalid_decision_record"}
                else:
                    latest = latest_review_event(reviews_by_target.get(note.path, []), today)
                    problems = _problems(root, note, row, log.text if log else "", duplicates, today, state_of,
                                         latest["cells"][3] if latest else None)
            findings.append({"decision_id": did, "problems": sorted(problems)})
        codes = {code for finding in findings for code in finding["problems"]}
        verdict = "blocked" if codes - REVIEW_CODES else "needs_review" if codes else "pass"
        return {
            "contract_version": 1,
            "format": FORMAT, "proposal_id": proposal["proposal_id"], "proposal_sha256": digest,
            "as_of": today.isoformat(), "review_scope": "explicit_decision_ids_only",
            "verdict": verdict, "review_required": True, "execution_authorized": False,
            "semantics_checked": False, "trusted_history_checked": False,
            "referenced_decisions": len(findings), "findings": findings,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only decision preflight; never authorizes execution")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--today", help="evaluate current vault bytes as of YYYY-MM-DD")
    args = parser.parse_args(argv)
    try:
        today = dt.date.today()
        if args.today:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args.today):
                raise PreflightInputError("invalid_date")
            try:
                today = dt.date.fromisoformat(args.today)
            except ValueError:
                raise PreflightInputError("invalid_date") from None
        report = check_proposal(args.root, args.proposal, today=today)
    except (OSError, ValueError) as error:
        code = str(error) if isinstance(error, PreflightInputError) else "unavailable_or_invalid_vault"
        print(json.dumps({"format": FORMAT, "error": code}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["verdict"] == "pass" else 1
