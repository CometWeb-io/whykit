"""Local claim records and their captured evidence; no network or write side effects."""
from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .config import CONFIG_FILE, parse_config
from .io import safe_vault_target
from .sensitivity import classified_evidence
from .snapshot import normalize_content
from .tables import split_table_row

if TYPE_CHECKING:
    from .vault_index import VaultIndex

CLAIM_ID_RE = re.compile(r"C-[0-9]{3,}")
CLAIM_PATH_RE = re.compile(r"00-context/claims/c-[0-9]{3,}-.+\.md", re.IGNORECASE)
SNAPSHOT_RE = re.compile(r"00-context/claim-snapshots/([0-9a-f]{64})\.txt")
CLAIM_COLUMNS = ("evidence_id", "relation", "snapshot", "source_snapshot_hash", "fragment", "observed_at", "rationale")
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_RELATIONS = 256
CLAIM_STATUSES = {"draft", "in_review", "approved", "superseded", "archived"}
RECEIPT_PREFIX = "claim-receipt/v1:"
MAX_RECEIPT_BYTES = 128 * 1024


def claims_enabled(config: dict[str, Any]) -> bool:
    return config.get("claims") == {"format_version": 1}


def _date(value: object) -> dt.date | None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _read_snapshot(root: Path, relative: str) -> bytes:
    if SNAPSHOT_RE.fullmatch(relative) is None:
        raise ValueError("snapshot must have a hash-named local path")
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("snapshot path must not pass through a symlink")
    path = safe_vault_target(root, relative, create_parents=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("snapshot must be a regular file")
        data = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 1 MiB")
    return data


def capture_claims(vault: VaultIndex, config: dict[str, Any]) -> dict[str, Any]:
    from .lint import load_note
    from .immutability import _review_log_parts

    root = vault.root
    view: dict[str, Any] = {"root": root, "config": config, "records": {}, "evidence": {},
                            "snapshots": {}, "review_rows": [], "errors": [], "raw_inputs": {}}

    def error(path: str, code: str, message: str, record: dict | None = None) -> None:
        finding = {"path": path, "code": "claim." + code, "message": message}
        view["errors"].append(finding)
        if record is not None:
            record["errors"].append(finding)

    transactions = root / ".whykit/transactions"
    if (root / ".whykit").is_symlink() or transactions.is_symlink() or any(
        tx.is_symlink() or (tx / "READY").exists() and not (tx / "COMMITTED").exists()
        for tx in transactions.iterdir()
    ) if transactions.exists() else False:
        error(".whykit/transactions", "capture_changed", "claim review transaction is pending or unsafe; recover under the vault mutation lock")

    def capture(relative: str) -> bytes | None:
        path = safe_vault_target(root, relative, create_parents=False)
        data = path.read_bytes() if path.exists() else None
        view["raw_inputs"][path] = data
        note = vault.note_for(path)
        if note is not None and (data is None or load_note(path, text=data.decode("utf-8")).text != note.text):
            error(relative, "capture_changed", "captured record changed; retry the read")
        return data

    try:
        config_data = capture(CONFIG_FILE)
        if parse_config(config_data.decode("utf-8") if config_data is not None else "") != config:
            error(CONFIG_FILE, "capture_changed", "configuration changed; retry the read")
        capture("00-context/evidence-register.md")
        log = capture("00-context/review-log.md")
        if log is not None:
            parts = _review_log_parts(log.decode("utf-8"))
            if parts is None:
                error("00-context/review-log.md", "invalid_receipt", "review log cannot be parsed")
            else:
                view["review_rows"] = parts[1]
    except (OSError, ValueError, RuntimeError, UnicodeError):
        error(CONFIG_FILE, "capture_changed", "could not capture claim dependencies")

    active, retired, occurrences, _ = classified_evidence(vault)
    view["evidence"] = {**{eid: {**row, "state": "active"} for eid, row in active.items()},
                        **{eid: {**row, "state": "retired"} for eid, row in retired.items()}}
    view["duplicate_evidence"] = {eid for eid, count in Counter(eid for eid, _ in occurrences).items() if count > 1}
    candidates = [note for note in vault.notes if note.front.get("type") == "claim"
                  or "claim_id" in note.front or CLAIM_PATH_RE.fullmatch(vault.relative(note.path))]
    candidate_paths = {note.path for note in candidates}
    ids = Counter(str(note.front.get("claim_id") or "") for note in candidates)
    for note in vault.notes:
        relative = vault.relative(note.path)
        if note.path not in candidate_paths and "claim_ids" in note.front:
            values = note.front["claim_ids"]
            if not claims_enabled(config):
                error(relative, "requires_opt_in", "claim_ids requires [claims] format_version = 1")
            if not isinstance(values, list) or any(not isinstance(v, str) or CLAIM_ID_RE.fullmatch(v) is None for v in values) or len(set(map(str, values))) != len(values):
                error(relative, "invalid_metadata", "claim_ids must contain unique C-NNN identifiers")
    for note in candidates:
        relative = vault.relative(note.path)
        front = dict(note.front)
        cid = str(front.get("claim_id") or "")
        record: dict[str, Any] = {"path": relative, "text": note.text, "front": front, "relations": [], "errors": []}
        if ids[cid] > 1:
            error(relative, "duplicate_id", "claim ID must be unique", record)
        if not claims_enabled(config):
            error(relative, "requires_opt_in", "claim records require [claims] format_version = 1", record)
        if cid not in view["records"]:
            view["records"][cid] = record
        try:
            capture(relative)
        except (OSError, ValueError, RuntimeError, UnicodeError):
            error(relative, "capture_changed", "could not capture the claim record", record)
        required = ("claim_id", "title", "statement", "scope", "owner", "created", "last_updated", "valid_from", "sensitivity")
        invalid = note.front_error or any(not isinstance(front.get(key), str) or not front[key].strip() for key in required)
        invalid = invalid or front.get("type") != "claim" or CLAIM_ID_RE.fullmatch(cid) is None
        invalid = invalid or CLAIM_PATH_RE.fullmatch(relative) is None or not note.path.name.lower().startswith(cid.lower() + "-")
        invalid = invalid or front.get("status") not in CLAIM_STATUSES or "verification_status" in front
        invalid = invalid or front.get("sensitivity") not in {"public", "internal", "confidential", "restricted"}
        for key in ("created", "last_updated", "valid_from", "valid_to", "last_verified", "review_by"):
            if key in front and _date(front[key]) is None:
                invalid = True
        valid_to, valid_from = _date(front.get("valid_to")), _date(front.get("valid_from"))
        if valid_to is not None and valid_from is not None and valid_to < valid_from:
            invalid = True
        for key in ("supersedes", "superseded_by"):
            if key in front and (not isinstance(front[key], str) or CLAIM_ID_RE.fullmatch(front[key]) is None or front[key] == cid):
                invalid = True
        if invalid:
            error(relative, "invalid_metadata", "claim metadata, dates or record path are invalid", record)
        lines = note.masked.splitlines()
        headings = [i for i, line in enumerate(lines) if line.strip() == "## Evidence"]
        if len(headings) != 1:
            error(relative, "invalid_relation", "claim requires exactly one Evidence table", record)
            continue
        start = headings[0] + 1
        stop = next((i for i in range(start, len(lines)) if re.match(r"^#{1,2} ", lines[i])), len(lines))
        rows = [split_table_row(line) for line in lines[start:stop] if line.lstrip().startswith("|")]
        if len(rows) < 2 or tuple(rows[0]) != CLAIM_COLUMNS or len(rows[1]) != len(CLAIM_COLUMNS) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
            error(relative, "invalid_relation", "Evidence table columns are malformed", record)
            continue
        if len(rows) - 2 > MAX_RELATIONS:
            error(relative, "invalid_relation", "claim exceeds 256 relations", record)
            continue
        seen: set[tuple[str, str, str]] = set()
        for cells in rows[2:]:
            if len(cells) != len(CLAIM_COLUMNS):
                error(relative, "invalid_relation", "Evidence row has the wrong number of cells", record)
                continue
            row = dict(zip(CLAIM_COLUMNS, cells, strict=True))
            row["fragment_text"] = ""
            record["relations"].append(row)
            edge_key = (row["evidence_id"], row["snapshot"], row["fragment"])
            if edge_key in seen or re.fullmatch(r"E-[0-9]{3,}", row["evidence_id"]) is None or row["relation"] not in {"supports", "contradicts"} or not row["rationale"] or _date(row["observed_at"]) is None:
                error(relative, "invalid_relation", "relation must identify unique evidence and fragment with valid observation and rationale", record)
            seen.add(edge_key)
            target = row["snapshot"]
            if target not in view["snapshots"]:
                try:
                    data = _read_snapshot(root, target)
                    normalized = normalize_content(data)
                    normalized.decode("utf-8")
                    view["raw_inputs"][root / target] = data
                    view["snapshots"][target] = {"normalized": normalized, "hash": hashlib.sha256(normalized).hexdigest(), "error": None}
                except (OSError, ValueError, RuntimeError, UnicodeError):
                    view["snapshots"][target] = {"normalized": b"", "hash": None, "error": "invalid_snapshot"}
            snapshot = view["snapshots"][target]
            match = SNAPSHOT_RE.fullmatch(target)
            if snapshot["error"] or re.fullmatch(r"[0-9a-f]{64}", row["source_snapshot_hash"]) is None or snapshot["hash"] != row["source_snapshot_hash"] or match is None or match[1] != snapshot["hash"]:
                error(relative, "invalid_snapshot", "snapshot is missing, unsafe, oversized, unreadable or has a different hash", record)
                continue
            fragment = re.fullmatch(r"lines:([0-9]+)-([0-9]+)", row["fragment"])
            source_lines = snapshot["normalized"].decode("utf-8").splitlines()
            if fragment is None or not 1 <= int(fragment[1]) <= int(fragment[2]) <= len(source_lines):
                error(relative, "invalid_fragment", "fragment must select existing lines, numbered from one", record)
            else:
                row["fragment_text"] = "\n".join(source_lines[int(fragment[1]) - 1:int(fragment[2])])
    targets = {"[[" + record["path"].removesuffix(".md") + "]]" for record in view["records"].values()}
    events = [tuple(row) for row in view["review_rows"] if row[1] in targets]
    if len(set(events)) != len(events):
        error("00-context/review-log.md", "invalid_receipt", "duplicate claim review event")
    return view


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def claim_record_hash(text: str) -> str:
    from .immutability import _without_frontmatter_keys
    normalized = normalize_content(text.encode("utf-8")).decode("utf-8")
    semantic = _without_frontmatter_keys(normalized, {"status", "review_by", "last_updated", "last_verified", "superseded_by"})
    if semantic is None:
        raise ValueError("claim requires readable front matter")
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()


def _evidence_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({key: value for key, value in row.items() if key not in {"state", "line"}})).hexdigest()


def _relation_manifest(view: dict[str, Any], record: dict[str, Any]) -> list[dict[str, str]]:
    manifest = []
    for row in record["relations"]:
        evidence = view["evidence"].get(row["evidence_id"])
        if evidence is None:
            raise ValueError("receipt requires existing evidence")
        manifest.append({"evidence_id": row["evidence_id"], "evidence_sha256": _evidence_hash(evidence),
                         "source_snapshot_hash": row["source_snapshot_hash"], "fragment": row["fragment"], "relation": row["relation"]})
    return sorted(manifest, key=_canonical)


def encode_claim_receipt(view: dict[str, Any], claim_id: str, *, previous_review: str | None, next_review: str) -> str:
    record = view["records"].get(claim_id)
    if record is None:
        raise ValueError("receipt requires an existing claim")
    receipt = {"format": "claim-receipt/v1", "claim_id": claim_id, "record_sha256": claim_record_hash(record["text"]),
               "relations": _relation_manifest(view, record), "previous_review": previous_review, "next_review": next_review}
    value = RECEIPT_PREFIX + base64.urlsafe_b64encode(_canonical(receipt)).decode("ascii").rstrip("=")
    decode_claim_receipt(value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt key")
        result[key] = value
    return result


def decode_claim_receipt(value: str) -> dict[str, Any]:
    try:
        if not isinstance(value, str) or len(value) > MAX_RECEIPT_BYTES or not value.startswith(RECEIPT_PREFIX):
            raise ValueError
        encoded = value[len(RECEIPT_PREFIX):]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError
        data = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        keys = {"format", "claim_id", "record_sha256", "relations", "previous_review", "next_review"}
        if not isinstance(receipt, dict) or set(receipt) != keys or receipt["format"] != "claim-receipt/v1":
            raise ValueError
        if not isinstance(receipt["claim_id"], str) or CLAIM_ID_RE.fullmatch(receipt["claim_id"]) is None:
            raise ValueError
        if not isinstance(receipt["record_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", receipt["record_sha256"]) is None:
            raise ValueError
        if _date(receipt["next_review"]) is None or (receipt["previous_review"] is not None and _date(receipt["previous_review"]) is None):
            raise ValueError
        relations = receipt["relations"]
        if not isinstance(relations, list) or not 1 <= len(relations) <= MAX_RELATIONS:
            raise ValueError
        seen: set[tuple[str, str, str]] = set()
        for row in relations:
            if not isinstance(row, dict) or set(row) != {"evidence_id", "evidence_sha256", "source_snapshot_hash", "fragment", "relation"} or any(not isinstance(item, str) for item in row.values()):
                raise ValueError
            if re.fullmatch(r"E-[0-9]{3,}", row["evidence_id"]) is None or row["relation"] not in {"supports", "contradicts"}:
                raise ValueError
            if any(re.fullmatch(r"[0-9a-f]{64}", row[key]) is None for key in ("evidence_sha256", "source_snapshot_hash")):
                raise ValueError
            fragment = re.fullmatch(r"lines:([1-9][0-9]*)-([1-9][0-9]*)", row["fragment"])
            if fragment is None or int(fragment[1]) > int(fragment[2]):
                raise ValueError
            key = row["evidence_id"], row["source_snapshot_hash"], row["fragment"]
            if key in seen:
                raise ValueError
            seen.add(key)
        if relations != sorted(relations, key=_canonical) or data != _canonical(receipt) or base64.urlsafe_b64encode(data).decode("ascii").rstrip("=") != encoded:
            raise ValueError
        return receipt
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise ValueError("invalid claim receipt") from None


def evaluate_claims(view: dict[str, Any], *, today: dt.date) -> dict[str, dict[str, Any]]:
    results = {}
    for cid, record in view["records"].items():
        front = record["front"]
        reasons: set[str] = set()
        target = "[[" + record["path"].removesuffix(".md") + "]]"
        events = [row for row in view["review_rows"] if len(row) == 7 and row[1] == target]
        receipt = None
        review_date = None
        if events:
            event = events[-1]
            try:
                if event[3] not in {"approved", "confirmed"}:
                    if event[3] in {"update-required", "supersede-required", "archived"}:
                        reasons.add("review_required")
                    raise ValueError
                receipt = decode_claim_receipt(event[6])
                review_date = _date(event[0])
                if receipt["claim_id"] != cid or receipt["record_sha256"] != claim_record_hash(record["text"]) or review_date is None or review_date > today or _date(front.get("last_verified")) != review_date or event[5] != front.get("review_by") or receipt["next_review"] != event[5] or (receipt["previous_review"] or "—") != event[4] or len({tuple(e) for e in events}) != len(events) or not event[2].strip():
                    raise ValueError
            except ValueError:
                receipt = None
                if "review_required" not in reasons:
                    reasons.add("invalid_receipt")
        if receipt is None:
            reasons.add("not_reviewed")
        if front.get("status") != "approved":
            reasons.add("not_reviewed")
        start, end, deadline = _date(front.get("valid_from")), _date(front.get("valid_to")), _date(front.get("review_by"))
        if start is None or today < start or (end is not None and today > end):
            reasons.add("outside_validity")
        if deadline is not None and today > deadline:
            reasons.add("review_overdue")
        if any(item["code"] == "claim.capture_changed" for item in view["errors"]):
            reasons.add("capture_changed")
        if any(item["code"] in {"claim.invalid_metadata", "claim.duplicate_id", "claim.invalid_relation", "claim.requires_opt_in"} for item in record["errors"]):
            reasons.add("invalid_claim")
        relations = []
        all_bound = receipt is not None
        expected = {(r["evidence_id"], r["source_snapshot_hash"], r["fragment"], r["relation"]): r for r in receipt["relations"]} if receipt else {}
        for row in record["relations"]:
            problems: set[str] = set()
            evidence = view["evidence"].get(row["evidence_id"])
            binding = expected.get((row["evidence_id"], row["source_snapshot_hash"], row["fragment"], row["relation"]))
            bound = binding is not None
            if row["evidence_id"] in view["duplicate_evidence"]:
                problems.add("duplicate_evidence")
                bound = False
            if evidence is None:
                problems.add("missing_evidence")
                bound = False
            else:
                if evidence["state"] != "active":
                    problems.add("retired_evidence")
                if binding is not None and binding["evidence_sha256"] != _evidence_hash(evidence):
                    problems.add("evidence_binding_changed")
                    bound = False
                accessed = _date(evidence.get("accessed"))
                max_age = view["config"]["evidence_access_age_days"].get(evidence.get("type"))
                if accessed is None or accessed > today:
                    problems.add("future_observation" if accessed is not None else "stale_evidence")
                elif max_age is not None and (today - accessed).days > max_age:
                    problems.add("stale_evidence")
            snapshot = view["snapshots"].get(row["snapshot"])
            if snapshot is None or snapshot["error"] or snapshot["hash"] != row["source_snapshot_hash"]:
                problems.add("missing_snapshot" if snapshot is None else "changed_snapshot")
                bound = False
            if not row.get("fragment_text"):
                problems.add("invalid_fragment")
            observed = _date(row["observed_at"])
            if observed is None or observed > today or (review_date is not None and observed > review_date):
                problems.add("future_observation")
            if not bound:
                problems.add("evidence_binding_changed")
            all_bound = all_bound and bound
            reasons.update(problems)
            relations.append({**row, "usable": not problems and receipt is not None, "reasons": sorted(problems)})
        if len(expected) != len(relations):
            all_bound = False
            reasons.add("evidence_binding_changed")
        usable = {row["relation"] for row in relations if row["usable"]}
        unreviewable = reasons & {"not_reviewed", "invalid_receipt", "invalid_claim", "outside_validity", "review_overdue", "capture_changed"}
        if unreviewable:
            state = "unknown"
        elif usable == {"supports", "contradicts"}:
            state = "disputed"
            reasons.add("conflicting_evidence")
        elif any(not row["usable"] for row in relations) or not relations:
            state = "unknown"
        elif usable == {"supports"}:
            state = "supported"
        else:
            state = "unsupported"
            reasons.add("no_support")
        results[cid] = {"claim_id": cid, "path": record["path"], "status": front.get("status"), "verification_status": state,
                        "as_of": today.isoformat(), "last_verified": front.get("last_verified"), "binding_valid": all_bound,
                        "history_reconstructed": False, "reasons": sorted(reasons), "relations": relations}
    return results
