"""Pure local-governance eligibility and scoped history, never execution authority."""
from __future__ import annotations

from collections import defaultdict
import datetime as dt
from pathlib import Path
import re
from typing import Any

from .immutability import approval_event_matches_rows
from .artifact_store import _strict_json
from .knowledge_visibility import canonical_decision_id
from .lint import REQUIRED_KEYS, check_front_matter
from .review_history import latest_review_event
from .temporal_graph import _validity
from .trace import _evidence_state
from .vault_index import VaultIndex

MAX_HISTORY_REVIEWS = 8
MAX_HISTORY_RELATIONS = 8


def _date(value) -> dt.date | None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def context_metadata(index: VaultIndex, reviews: list[dict], history_available: bool, incomplete: set[Path],
                     rows: tuple, claim_states: dict, config: dict, today: dt.date,
                     document_hashes: dict[Path, str]) -> dict[Path, dict]:
    """Consume only captured/granted notes, rows and source states; no I/O."""
    groups: dict[str, list] = defaultdict(list)
    own: dict[Path, str] = {}
    candidates: dict[Path, bool] = {}
    basic: dict[Path, dict[str, Any]] = {}
    events_by_target: dict[Path, list[dict]] = defaultdict(list)
    for event in reviews:
        events_by_target[event["target"]].append(event)
    for note in index.notes:
        did = canonical_decision_id(note, index.root)
        candidate = note.front.get("type") == "decision" or "decision_id" in note.front
        if did is not None:
            groups[did].append(note)
            own[note.path] = did
        elif candidate:
            declared = note.front.get("decision_id")
            if isinstance(declared, str) and re.fullmatch(r"D-[0-9]{3,}", declared):
                groups[declared].append(note)
        candidates[note.path] = candidate
    for note in index.notes:
        did = own.get(note.path)
        valid = _validity(note, today)
        record_dates = [_date(note.front.get(key)) for key in ("created", "last_updated")]
        future = any(day is not None and day > today for day in record_dates)
        unique = did is not None and len(groups[did]) == 1
        findings: list = []
        if candidates[note.path]:
            check_front_matter(index.root, note, findings, today)
        invalid_record = not set(REQUIRED_KEYS).issubset(note.front) or any(item.level == "error" for item in findings)
        available = history_available and note.path not in incomplete
        matched = bool(unique and available and approval_event_matches_rows(
            note.text, index.relative(note.path), [], [event["cells"] for event in events_by_target[note.path]]))
        basic[note.path] = {"did": did, "validity": valid, "future": future,
                            "unique": unique, "available": available, "matched": matched, "invalid_record": invalid_record}
    # Only a visible locally approved, already effective successor proves this edge.
    successors: dict[str, list] = defaultdict(list)
    declared_successors: dict[str, list] = defaultdict(list)
    for note in index.notes:
        state = basic[note.path]
        previous = note.front.get("supersedes")
        if state["unique"] and isinstance(previous, str):
            declared_successors[previous].append(note)
        if (state["unique"] and state["matched"] and not state["future"] and not state["invalid_record"]
                and note.front.get("status") == "approved" and state["validity"]["state"] not in {"not_yet", "invalid"}
                and isinstance(previous, str) and previous in groups and len(groups[previous]) == 1):
            successors[previous].append(note)
    claims: dict[str, list] = defaultdict(list)
    for note in index.notes:
        claim_id = note.front.get("claim_id")
        if isinstance(claim_id, str):
            claims[claim_id].append(note)
    output = {}
    for note in index.notes:
        state = basic[note.path]
        reasons: set[str] = set()
        applicability = {"as_of": today.isoformat(), "state": "not_decision", "premise": False,
                         "local_approval": "not_applicable", "declared_validity": state["validity"], "reasons": []}
        if candidates[note.path]:
            if state["did"] is None or state["invalid_record"]:
                reasons.add("invalid_decision")
            if state["did"] is not None and not state["unique"]:
                reasons.add("ambiguous_decision")
            if note.front.get("status") != "approved":
                reasons.add("not_approved")
            if note.front.get("status") == "superseded" or successors.get(state["did"]):
                reasons.add("superseded")
            applicability["local_approval"] = "matched" if state["matched"] else "unverified" if state["available"] else "unavailable"
            if not state["available"]:
                reasons.add("approval_unavailable")
            elif not state["matched"]:
                reasons.add("approval_unverified")
            if state["future"]:
                reasons.add("record_future")
            validity_state = state["validity"]["state"]
            if validity_state != "active":
                reasons.add("validity_" + validity_state)
            deadline = _date(note.front.get("review_by"))
            if deadline is None:
                reasons.add("review_date_invalid")
            elif deadline < today:
                reasons.add("review_overdue")
            cited = set(note.cited_evidence)
            claim_ids = note.front.get("claim_ids", [])
            if (not isinstance(claim_ids, list) or len(claim_ids) > 64 or any(not isinstance(item, str) for item in claim_ids)
                    or len(set(claim_ids)) != len(claim_ids)):
                reasons.add("claim_unavailable")
                claim_ids = []
            pins: Any = {}
            if claim_ids:
                try:
                    provenance = note.front.get("provenance")
                    pins = provenance.get("claim_versions") if isinstance(provenance, dict) else None
                    pins = _strict_json(pins) if isinstance(pins, str) and len(pins.encode("utf-8")) <= 16 * 1024 else pins
                    if not isinstance(pins, dict) or set(pins) != set(claim_ids):
                        raise ValueError("claim pins unavailable")
                    if any(not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in pins.values()):
                        raise ValueError("invalid claim pins")
                except (ValueError, UnicodeError):
                    reasons.add("claim_unpinned")
                    pins = {}
            for claim_id in claim_ids:
                records = claims.get(claim_id, [])
                if len(records) != 1 or claim_id not in claim_states:
                    reasons.add("claim_unavailable")
                    continue
                from .claim_ledger import _parse_claim_text
                record, payload = _parse_claim_text(records[0].text)
                if claim_id in pins and pins[claim_id] != "sha256:" + document_hashes[records[0].path]:
                    reasons.add("claim_snapshot_changed")
                cited.update(link["evidence_id"] for link in payload["links"])
                if not payload["links"]:
                    reasons.add("claim_unavailable")
                if any(link["assessment"]["relation"] == "contradicts" for link in payload["links"]):
                    reasons.add("claim_contradicted")
                def anchor_key(link):
                    location = link["location"]
                    return (link["evidence_id"], link["source_version"], location["start_byte"], location["end_byte"],
                            location["quote"], link["assessment"]["relation"])
                human = {anchor_key(link) for link in payload["links"] if link["assessment"]["kind"] == "human"}
                if any(link["assessment"]["kind"] == "model" and anchor_key(link) not in human for link in payload["links"]):
                    reasons.add("claim_model_assessment")
                validity = _validity(records[0], today)["state"]
                if validity == "unknown":
                    reasons.add("claim_validity_unknown")
                elif validity != "active":
                    reasons.add("claim_not_current")
                for source_state in claim_states[claim_id] - {"current"}:
                    reasons.add("claim_source_" + source_state)
            if not cited:
                reasons.add("no_evidence")
            for eid in cited:
                evidence = _evidence_state(eid, rows[0], rows[1], dict(config["evidence_access_age_days"]), None, today)
                if evidence["state"] != "active":
                    reasons.add("evidence_" + evidence["state"])
                else:
                    if evidence["age_days"] is None:
                        reasons.add("evidence_date_invalid")
                    elif evidence["age_days"] < 0:
                        reasons.add("evidence_future")
                    if evidence["stale"]:
                        reasons.add("evidence_stale")
                    if evidence["max_age_days"] is None:
                        reasons.add("evidence_unbounded")
            latest_event = latest_review_event(events_by_target[note.path], today)
            if latest_event and latest_event["cells"][3] in {"update-required", "supersede-required"}:
                reasons.add("review_required")
            if latest_event and latest_event["cells"][3] == "archived":
                reasons.add("review_archived")
            unknown = reasons & {"invalid_decision", "ambiguous_decision", "approval_unavailable", "approval_unverified"}
            excluded = reasons & {"not_approved", "superseded", "record_future", "validity_not_yet", "validity_expired", "validity_invalid", "review_archived"}
            review = reasons - {"validity_unknown"} - unknown - excluded
            applicability["state"] = "reference" if excluded else "unknown" if unknown else "needs_review" if review else "applicable"
            applicability["premise"] = applicability["state"] == "applicable"
        else:
            reasons.add("not_decision")
        applicability["reasons"] = sorted(reasons)
        related_from, related_to, unavailable = [], [], not history_available or note.path in incomplete
        previous = note.front.get("supersedes")
        if isinstance(previous, str) and previous:
            matches = groups.get(previous, [])
            if len(matches) == 1 and matches[0].path in own:
                related_from.append({"decision_id": previous, "path": index.relative(matches[0].path)})
            else:
                unavailable = True
        elif previous:
            unavailable = True
        if state["did"] is not None:
            for other in declared_successors[state["did"]]:
                related_to.append({"decision_id": own[other.path], "path": index.relative(other.path)})
        following = note.front.get("superseded_by")
        if isinstance(following, str) and following:
            matches = groups.get(following, [])
            if len(matches) == 1 and matches[0].path in own:
                relation = {"decision_id": following, "path": index.relative(matches[0].path)}
                if relation not in related_to:
                    related_to.append(relation)
            else:
                unavailable = True
        elif following:
            unavailable = True
        events = [{"date": event["cells"][0], "outcome": event["cells"][3],
                   "row_sha256": event["sha256"], "occurrence": event["occurrence"]}
                  for event in events_by_target[note.path]]
        events.sort(key=lambda event: event["date"])
        displayed_events = events[-MAX_HISTORY_REVIEWS:]
        controlling = latest_review_event(events_by_target[note.path], today)
        if controlling is not None:
            control = {"date": controlling["cells"][0], "outcome": controlling["cells"][3],
                       "row_sha256": controlling["sha256"], "occurrence": controlling["occurrence"]}
            if control not in displayed_events:
                displayed_events = [control, *displayed_events[-(MAX_HISTORY_REVIEWS - 1):]]
        related_from.sort(key=lambda relation: (relation["decision_id"], relation["path"]))
        related_to.sort(key=lambda relation: (relation["decision_id"], relation["path"]))
        history = {"supersedes": related_from[:MAX_HISTORY_RELATIONS], "superseded_by": related_to[:MAX_HISTORY_RELATIONS],
                   "reviews": displayed_events, "unavailable": unavailable,
                   "truncated": len(events) > MAX_HISTORY_REVIEWS or len(related_from) > MAX_HISTORY_RELATIONS or len(related_to) > MAX_HISTORY_RELATIONS}
        output[note.path] = {"applicability": applicability, "history": history, "decision_id": state["did"]}
    return output
