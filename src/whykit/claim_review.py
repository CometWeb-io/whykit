"""Claim-bound review plans reuse the existing vault transaction and decision gate."""

from __future__ import annotations

from .io import consistent_read
import datetime as dt
import base64
import copy
import difflib
import hashlib
import json
import re
from pathlib import Path
from .claims import (
    CLAIM_ID_RE,
    capture_claims,
    claim_record_hash,
    encode_claim_receipt,
    decode_claim_receipt,
    evaluate_claims,
    _canonical,
    _date,
)
from .contract import TargetNotFound
from .config import load_config
from .io import apply_transaction, safe_vault_target, vault_mutation_lock
from .lint import load_note
from .scaffold import _frontmatter_replace
from .vault_index import VaultIndex

DECISION_RECEIPT_PREFIX = "decision-claim-receipt/v1:"


def resolve_record(index: VaultIndex, target: str):
    if re.fullmatch(r"[CD]-[0-9]{3,}", target):
        key = "claim_id" if target.startswith("C-") else "decision_id"
        matches = [n for n in index.notes if n.front.get(key) == target]
        return matches[0] if len(matches) == 1 else None
    path, ambiguous = index.resolve_link(target)
    return index.note_for(path) if path is not None and not ambiguous else None


def decision_claim_binding(
    view: dict, note, *, today: dt.date
) -> tuple[list[dict], list[dict]]:
    ids = note.front.get("claim_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or len(set(map(str, ids))) != len(ids)
        or any(
            not isinstance(cid, str) or not CLAIM_ID_RE.fullmatch(cid) for cid in ids
        )
    ):
        raise ValueError("decision claim_ids must contain unique C-NNN identifiers")
    assessments = evaluate_claims(view, today=today)
    selected = [assessments[cid] for cid in ids if cid in assessments]
    if len(selected) != len(ids) or any(
        a["verification_status"] == "unknown" or not a["binding_valid"]
        or any(not row["usable"] for row in a["relations"])
        for a in selected
    ):
        raise ValueError(
            "decision approval requires approved, bound, resolved claims; unknown claims block approval"
        )
    from .placeholders import split_sections

    sections = [
        (start, stop)
        for name, start, stop in split_sections(note.masked.splitlines())
        if name == "Claim assessment"
    ]
    explanations = {}
    if sections:
        if len(sections) != 1:
            raise ValueError("exactly one Claim assessment section is allowed")
        start, stop = sections[0]
        for line in note.masked.splitlines()[start:stop]:
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*- (C-[0-9]{3,}):\s*(.+)", line)
            if (
                match is None
                or match[1] not in ids
                or match[1] in explanations
                or re.fullmatch(
                    r"(?i)(TODO|TBD|—|-|placeholder)[.! ]*", match[2].strip()
                )
            ):
                raise ValueError(
                    "Claim assessment requires unique referenced IDs and non-placeholder rationales"
                )
            explanations[match[1]] = match[2].strip()
    if any(
        a["verification_status"] in {"disputed", "unsupported"}
        and a["claim_id"] not in explanations
        for a in selected
    ):
        raise ValueError(
            "disputed/unsupported claims require a Claim assessment rationale"
        )
    manifests = []
    for cid in sorted(ids):
        record = view["records"][cid]
        events = [
            row
            for row in view["review_rows"]
            if row[1] == "[[" + record["path"].removesuffix(".md") + "]]"
            and row[3] in {"approved", "confirmed"}
        ]
        manifests.append(
            {
                "claim_id": cid,
                "record_sha256": claim_record_hash(record["text"]),
                "receipt_sha256": hashlib.sha256(
                    events[-1][6].encode("utf-8")
                ).hexdigest(),
            }
        )
    return selected, manifests


def decode_decision_receipt(value: str) -> dict:
    from .claims import _unique_object

    try:
        token = value.split(DECISION_RECEIPT_PREFIX, 1)[1]
        if len(token) > 128 * 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise ValueError
        data = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        decoded = json.loads(data, object_pairs_hook=_unique_object)
        if data != _canonical(decoded) or set(decoded) != {
            "format",
            "decision_id",
            "record_sha256",
            "claims",
            "previous_review",
            "next_review",
        }:
            raise ValueError
        if (
            decoded["format"] != "whykit.decision-claim-receipt/v1"
            or not re.fullmatch(r"D-[0-9]{3,}", decoded["decision_id"])
            or not re.fullmatch(r"[0-9a-f]{64}", decoded["record_sha256"])
        ):
            raise ValueError
        from .claims import _date

        if (
            _date(decoded["next_review"]) is None
            or decoded["previous_review"] is not None
            and _date(decoded["previous_review"]) is None
        ):
            raise ValueError
        rows = decoded["claims"]
        if not isinstance(rows, list) or not rows or len(rows) > 256:
            raise ValueError
        for row in rows:
            if (
                set(row) != {"claim_id", "record_sha256", "receipt_sha256"}
                or not CLAIM_ID_RE.fullmatch(row["claim_id"])
                or any(
                    not re.fullmatch(r"[0-9a-f]{64}", row[k])
                    for k in ("record_sha256", "receipt_sha256")
                )
            ):
                raise ValueError
        if len({row["claim_id"] for row in rows}) != len(rows) or rows != sorted(
            rows, key=lambda row: row["claim_id"]
        ):
            raise ValueError
        return decoded
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, UnicodeError):
        raise ValueError("invalid decision claim receipt") from None


def decision_claim_review_current(view: dict, note, *, today: dt.date) -> bool:
    from .immutability import approval_record_hash

    if note.front.get("status") != "approved":
        return False
    try:
        _, bindings = decision_claim_binding(view, note, today=today)
        events = [
            row
            for row in view["review_rows"]
            if row[1]
            == "[["
            + note.path.relative_to(view.get("root", note.path.parents[1]))
            .as_posix()
            .removesuffix(".md")
            + "]]"
        ]
        day = _date(events[-1][0]) if events else None
        if not events or events[-1][3] not in {"approved", "confirmed"} or day is None or day > today:
            return False
        receipt = decode_decision_receipt(events[-1][6])
        return (
            receipt["claims"] == bindings
            and receipt["decision_id"] == note.front["decision_id"]
            and receipt["record_sha256"] == approval_record_hash(note.text)
            and receipt["next_review"] == note.front.get("review_by")
        )
    except (ValueError, KeyError):
        return False


@consistent_read
def _plan(
    root: Path,
    target: str,
    reviewer: str,
    today: dt.date,
    next_review: str | None,
    outcome: str,
    note_text: str,
):
    from .review import (
        _approval_plan,
        _append_review_row,
        _review_log_template,
        _touch_last_updated,
        _reviewed_provenance,
        OUTCOMES,
    )
    from .immutability import approval_record_hash, _review_log_parts

    root = root.resolve(strict=True)
    if (
        not reviewer.strip()
        or reviewer.strip().casefold() == "todo"
        or any(ord(c) < 32 for c in reviewer)
        or reviewer != reviewer.strip()
    ):
        raise ValueError("--reviewer must name a reviewer on one line")
    if outcome not in {"approved", *OUTCOMES}:
        raise ValueError("unsupported review outcome")
    index = VaultIndex.load(root)
    note = resolve_record(index, target)
    if note is None:
        raise TargetNotFound("review target is missing or ambiguous")
    config, _ = load_config(root)
    view = capture_claims(index, config)
    if view["errors"]:
        raise ValueError(
            "claim dependencies are malformed or changed; resolve vault checks before review"
        )
    path = safe_vault_target(root, index.relative(note.path), create_parents=False)
    inputs = dict(view["raw_inputs"])
    inputs[path] = path.read_bytes()
    before = inputs[path].decode("utf-8").replace("\r\n", "\n")
    if load_note(path, text=before).text != note.text:
        raise ValueError("review target changed during preview")
    kind = "claim" if note.front.get("type") == "claim" else "decision"
    if (
        note.front_error
        or not isinstance(note.front.get("owner"), str)
        or not note.front["owner"].strip()
        or note.front["owner"].strip().casefold() == "todo"
    ):
        raise ValueError("review requires readable metadata and a real owner")
    from .claims import _date

    created, verified = (
        _date(note.front.get("created")),
        _date(note.front.get("last_verified")),
    )
    if created is None or created > today or verified is not None and verified > today:
        raise ValueError("review date must not be retroactive")
    if outcome == "approved" and note.front.get("status") not in {"draft", "in_review"}:
        raise ValueError("only draft/in_review records can be approved")
    if outcome == "confirmed" and note.front.get("status") != "approved":
        raise ValueError("only approved records can be confirmed")
    relative = index.relative(path)
    target_link = "[[" + relative.removesuffix(".md") + "]]"
    events = [
        row
        for row in view["review_rows"]
        if row[1] == target_link and row[3] in {"approved", "confirmed"}
    ]
    if outcome == "approved" and events:
        raise ValueError(
            "this record already has an approval event; never reset an accepted record"
        )
    previous = note.front.get("review_by") or None
    deadline = (
        next_review
        or (
            today + dt.timedelta(days=int(config["defaults"]["decision_review_days"]))
        ).isoformat()
    )
    if _date(deadline) is None or dt.date.fromisoformat(deadline) <= today:
        raise ValueError("--next-review must be after the review date")
    log = root / "00-context/review-log.md"
    log_before = (inputs[log] or b"").decode("utf-8").replace(
        "\r\n", "\n"
    ) or _review_log_template(today, reviewer)
    updates: dict[Path, str] = {}
    base_result: dict = {}
    if kind == "decision" and outcome == "approved":
        base_result, updates, legacy_inputs = _approval_plan(
            root, target, reviewer, today, next_review
        )
        if any(path in inputs and inputs[path] != data for path, data in legacy_inputs.items()):
            raise ValueError("approval captures changed; retry with one consistent view")
        inputs.update(legacy_inputs)
        updated = updates[path]
    else:
        updated = before
        if outcome == "archived":
            updated = _touch_last_updated(_frontmatter_replace(updated, "status", "archived"), today)
            updates[path] = updated
            if kind == "decision":
                from .scaffold import _update_decision_log_status_text
                decision_log = safe_vault_target(root, "06-decisions/decision-log.md", create_parents=False)
                inputs[decision_log] = decision_log.read_bytes()
                updates[decision_log] = _update_decision_log_status_text(inputs[decision_log].decode("utf-8"), note.front["decision_id"], "archived", today)
        if outcome in {"approved", "confirmed"}:
            updated = _frontmatter_replace(updated, "status", "approved")
            updated = _frontmatter_replace(updated, "review_by", deadline)
            if kind == "claim":
                updated = _frontmatter_replace(
                    updated, "last_verified", today.isoformat()
                )
            updated = _touch_last_updated(_reviewed_provenance(updated), today)
            updates[path] = updated
    digest = (
        claim_record_hash(updated) if kind == "claim" else approval_record_hash(updated)
    )
    selected = []
    receipt = note_text or "—"
    if outcome in {"approved", "confirmed"}:
        if kind == "claim":
            cid = str(note.front["claim_id"])
            if cid not in view["records"]:
                raise ValueError("claim record is missing")
            candidate = copy.deepcopy(view)
            candidate["records"][cid]["text"] = updated
            candidate["records"][cid]["front"] = load_note(path, text=updated).front
            receipt = encode_claim_receipt(
                candidate, cid, previous_review=previous, next_review=deadline
            )
            candidate["review_rows"].append(
                [
                    today.isoformat(),
                    target_link,
                    reviewer,
                    outcome,
                    previous or "—",
                    deadline,
                    receipt,
                ]
            )
            assessment = evaluate_claims(candidate, today=today)[cid]
            if (
                assessment["verification_status"] == "unknown"
                or not assessment["binding_valid"]
                or any(not row["usable"] for row in assessment["relations"])
            ):
                raise ValueError(
                    "claim review requires valid, current local fragments and active evidence"
                )
            if (
                events
                and claim_record_hash(before)
                != decode_claim_receipt(events[-1][6])["record_sha256"]
            ):
                raise ValueError(
                    "accepted claim semantics changed; create a superseding claim"
                )
            selected = [assessment]
            predecessor = (
                note.front.get("supersedes") if outcome == "approved" else None
            )
            if predecessor:
                old = view["records"].get(predecessor)
                if (
                    old is None
                    or old["front"].get("status") != "approved"
                    or predecessor == cid
                ):
                    raise ValueError(
                        "supersedes must name a currently approved predecessor"
                    )
                seen = {cid}
                current = predecessor
                while current:
                    if current in seen or current not in view["records"]:
                        raise ValueError("claim supersedes cycle or foreign ID")
                    seen.add(current)
                    current = view["records"][current]["front"].get("supersedes")
                old_path = root / old["path"]
                old_text = _frontmatter_replace(old["text"], "status", "superseded")
                old_text = _frontmatter_replace(old_text, "superseded_by", cid)
                updates[old_path] = _touch_last_updated(old_text, today)
        else:
            if (
                events
                and decode_decision_receipt(events[-1][6])["record_sha256"] != digest
            ):
                raise ValueError(
                    "accepted decision semantics changed; create a superseding decision"
                )
            selected, bindings = decision_claim_binding(
                view, load_note(path, text=updated), today=today
            )
            data = {
                "format": "whykit.decision-claim-receipt/v1",
                "decision_id": note.front["decision_id"],
                "record_sha256": digest,
                "claims": bindings,
                "previous_review": previous,
                "next_review": deadline,
            }
            token = DECISION_RECEIPT_PREFIX + base64.urlsafe_b64encode(
                _canonical(data)
            ).decode("ascii").rstrip("=")
            receipt = f"record-sha256:{digest}; snapshot-sha256:{hashlib.sha256(inputs[path]).hexdigest()}; {token}"
    updates[log] = _append_review_row(
        log_before,
        (
            today.isoformat(),
            target_link,
            reviewer,
            outcome,
            previous or "—",
            deadline if outcome in {"approved", "confirmed"} else next_review or "—",
            receipt,
        ),
        today,
    )
    if _review_log_parts(updates[log]) is None:
        raise ValueError("review log is malformed")
    binding = {
        "root": str(root),
        "target": relative,
        "reviewer": reviewer,
        "date": today.isoformat(),
        "outcome": outcome,
        "files": {
            index.relative(p): hashlib.sha256(data).hexdigest()
            if data is not None
            else None
            for p, data in inputs.items()
        },
        "updates": {
            index.relative(p): hashlib.sha256(text.encode("utf-8")).hexdigest()
            for p, text in updates.items()
        },
    }
    expected = hashlib.sha256(_canonical(binding)).hexdigest()
    for p, data in inputs.items():
        if (p.read_bytes() if p.exists() else None) != data:
            raise ValueError("review inputs changed during preview")
    changes = [
        {
            "path": index.relative(p),
            "diff": "".join(
                difflib.unified_diff(
                    (inputs[p] or b"").decode("utf-8").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=index.relative(p),
                    tofile=index.relative(p),
                )
            ),
        }
        for p, text in updates.items()
    ]
    result = {
        **base_result,
        "contract_version": 2,
        "applied": False,
        "expected_sha256": expected,
        "target": relative,
        "claim_id": note.front.get("claim_id"),
        "decision_id": note.front.get("decision_id"),
        "record_kind": kind,
        "snapshot_sha256": hashlib.sha256(inputs[path]).hexdigest(),
        "record_sha256": digest,
        "reviewer": reviewer,
        "date": today.isoformat(),
        "next_review": deadline,
        "previous_review": previous,
        "outcome": outcome,
        "reviewed_record": before,
        "claims": selected,
        "changes": changes,
        "note": note_text or None,
        "log": "00-context/review-log.md",
        "evidence": base_result.get("evidence", []),
    }
    return result, updates, inputs


def bound_review(
    root: Path,
    target: str,
    *,
    reviewer: str,
    today: dt.date,
    next_review: str | None,
    outcome: str,
    write: bool,
    expected_sha256: str | None,
    note_text: str = "",
) -> dict:
    if write and (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise ValueError("--write requires --expect-hash from a reviewed preview")
    if not write:
        return _plan(root, target, reviewer, today, next_review, outcome, note_text)[0]
    with vault_mutation_lock(root):
        result, updates, inputs = _plan(
            root, target, reviewer, today, next_review, outcome, note_text
        )
        if result["expected_sha256"] != expected_sha256:
            raise ValueError("review preview is stale; review a fresh preview")
        for path, before in inputs.items():
            if (path.read_bytes() if path.exists() else None) != before:
                raise ValueError("review inputs changed before apply")
        apply_transaction(root, updates)
        return {**result, "applied": True}
