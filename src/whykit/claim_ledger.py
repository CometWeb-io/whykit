"""Canonical Markdown claims, with byte anchors and no automatic approval."""
from __future__ import annotations

import datetime as dt
from collections.abc import Collection
import json
from pathlib import Path
import re

from .artifact_store import _confined_path, _evidence_info, _hash, _json_text, _load_artifact, _read_regular, _strict_json
from .claim_provenance import validate_claim, verify_anchor
from .io import apply_transaction, consistent_read, safe_vault_target, vault_mutation_lock, vault_read_lock
from .lint import REQUIRED_KEYS, Note, load_note
from .sensitivity import SENSITIVITY_LEVEL, note_sensitivity_level

MAX_CLAIM_BYTES = 1024 * 1024
MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_CLAIM_LINKS = 256
MAX_CLAIMS = 10_000
CLAIM_DIRECTORY = "00-context/claims"


class ClaimSnapshotChanged(ValueError):
    """Current claim bytes differ from a caller's explicitly pinned raw digest."""


def _claim_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"C-[0-9]{3,}", value) is None:
        raise ValueError("claim ID must be canonical C-NNN")
    if len(value) > 18 or int(value[2:]) < 1 or value != f"C-{int(value[2:]):03d}":
        raise ValueError("claim ID must be canonical C-NNN")
    return value


def _date(value: str | None, name: str) -> dt.date | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError(f"{name} must be an ISO calendar date")
    return dt.date.fromisoformat(value)


def _interval(record: dict) -> None:
    start = _date(record.get("valid_from"), "valid_from")
    end = _date(record.get("valid_until"), "valid_until")
    if start is not None and end is not None and start >= end:
        raise ValueError("valid_until must be after valid_from (exclusive end)")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) > MAX_CLAIM_BYTES or not value.strip() or len(value.splitlines()) != 1 or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must be nonblank single-line text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{name} must be UTF-8 text") from exc


def _parse_claim_text(text: str) -> tuple[dict, dict]:
    if not isinstance(text, str) or len(text) > MAX_CLAIM_BYTES:
        raise ValueError("claim record must be text")
    try:
        if len(text.encode("utf-8")) > MAX_CLAIM_BYTES:
            raise ValueError("claim exceeds byte limit")
    except UnicodeError as exc:
        raise ValueError("claim record must be UTF-8") from exc
    # load_note parses only supplied text; this sentinel path is never opened.
    note = load_note(Path("claim.md"), text=text)
    record = note.front
    if note.front_error or not note.has_front or not set(REQUIRED_KEYS).issubset(record):
        raise ValueError("claim record requires readable complete front matter")
    allowed = set(REQUIRED_KEYS) | {"claim_id", "source_ids", "valid_from", "valid_until"}
    if not set(record).issubset(allowed):
        raise ValueError("unknown claim front matter field")
    claim_id = _claim_id(record.get("claim_id"))
    if record.get("type") != "claim" or record.get("status") != "draft" or record.get("source_of_truth") is not False:
        raise ValueError("claim type or draft status mismatch")
    if record.get("sensitivity") != "restricted":
        raise ValueError("persisted claim quotes require restricted sensitivity")
    _text(record["title"], "title")
    _text(record["owner"], "owner")
    created = _date(record["created"], "created")
    updated = _date(record["last_updated"], "last_updated")
    if created is None or updated is None or updated < created:
        raise ValueError("claim creation and update dates are required")
    if note_sensitivity_level(note) not in SENSITIVITY_LEVEL.values():
        raise ValueError("claim sensitivity is unreadable")
    _interval(record)
    match = re.fullmatch(r"\s*# Claim " + re.escape(claim_id) + r"\n\n```whykit-claim\n(.*?)\n```\n?", note.body, re.DOTALL)
    if match is None:
        raise ValueError("claim requires exactly one fenced whykit-claim JSON envelope")
    payload = _strict_json(match.group(1))
    validate_claim(payload)
    if len(payload["links"]) > MAX_CLAIM_LINKS:
        raise ValueError("claim link limit exceeded")
    if payload["claim_id"] != claim_id:
        raise ValueError("claim payload identity mismatch")
    expected_sources = sorted({link["evidence_id"] for link in payload["links"]})
    if record.get("source_ids") != expected_sources:
        raise ValueError("claim source_ids must match its payload links")
    return record, payload


def claim_evidence_ids(text: str) -> set[str]:
    """Pure bounded parser for scoped readers; no filesystem reads or source hashes."""
    _, payload = _parse_claim_text(text)
    return {link["evidence_id"] for link in payload["links"]}


def claim_visibility(note: Note, visible_evidence_ids: Collection[str], *, root: Path | None = None) -> bool:
    """Check a native claim against the caller's already-filtered E-ID set.

    Callers must also apply their note sensitivity ceiling. This never expands
    that ceiling, resolves an E-ID or verifies source bytes.
    """
    try:
        record, payload = _parse_claim_text(note.text)
        parts = note.path.relative_to(root).parts if root is not None else note.path.parts[-3:]
        if tuple(parts) != ("00-context", "claims", record["claim_id"].lower() + ".md"):
            return False
        return all(link["evidence_id"] in visible_evidence_ids for link in payload["links"])
    except (ValueError, UnicodeError):
        return False


def _parse_record(root: Path, path: Path, raw: bytes) -> dict:
    try:
        record, payload = _parse_claim_text(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("claim record must be UTF-8") from exc
    claim_id = record["claim_id"]
    if path.relative_to(root).as_posix() != f"{CLAIM_DIRECTORY}/{claim_id.lower()}.md":
        raise ValueError("claim identity or path mismatch")
    return {"claim_id": claim_id, "path": path.relative_to(root).as_posix(), "record": record,
            "payload": payload, "sha256": _hash(raw)}


def _read_claim(root: Path, claim_id: str, expected_sha256: str | None = None) -> dict | None:
    _claim_id(claim_id)
    path = _confined_path(root, f"{CLAIM_DIRECTORY}/{claim_id.lower()}.md")
    raw = _read_regular(path, max_bytes=MAX_CLAIM_BYTES, missing_ok=True)
    if raw is not None and expected_sha256 is not None and _hash(raw) != expected_sha256:
        raise ClaimSnapshotChanged("claim snapshot changed")
    return None if raw is None else _parse_record(root, path, raw)


def _effective_label(root: Path, payload: dict, label: str) -> tuple[str, dict]:
    """Bind visibility to current evidence and original capture classifications."""
    dependencies = {}
    for link in payload["links"]:
        evidence_id, version = link["evidence_id"], link["source_version"]
        key = f"{evidence_id}@{version}"
        if key in dependencies:
            continue
        current, register = _evidence_info(root, evidence_id)
        captured = _load_artifact(root, evidence_id, version)
        labels = (label, current, captured[0]["sensitivity"] if captured else current)
        label = max(labels, key=SENSITIVITY_LEVEL.__getitem__)
        dependencies[key] = {"register": _hash(register), "artifact": _hash(captured[2]) if captured else None}
    return label, dependencies


@consistent_read
def read_claim(root: Path, claim_id: str, *, allowed_sensitivities: set[str] | None = None,
               expected_sha256: str | None = None) -> dict | None:
    root = Path(root).resolve(strict=True)
    _claim_id(claim_id)
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        raise ValueError("expected claim SHA-256 must be 64 lowercase hex digits")
    if allowed_sensitivities is not None and "restricted" not in allowed_sensitivities:
        return None
    claim = _read_claim(root, claim_id, expected_sha256)
    if claim is None:
        return None
    label, _ = _effective_label(root, claim["payload"], claim["record"]["sensitivity"])
    if allowed_sensitivities is not None and label not in allowed_sensitivities:
        return None
    # The returned effective floor may be stricter than the persisted label.
    return {**claim, "record": {**claim["record"], "sensitivity": label}}


def _claims(root: Path) -> list[dict]:
    directory = _confined_path(root, CLAIM_DIRECTORY)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("claim directory must be a directory")
    paths: list[Path] = []
    for path in directory.iterdir():
        if len(paths) >= MAX_CLAIMS:
            raise ValueError("claim record limit exceeded")
        if re.fullmatch(r"c-[0-9]{3,}\.md", path.name) is None:
            raise ValueError("noncanonical file in claim directory")
        paths.append(path)
    claims = []
    total_bytes = 0
    for path in sorted(paths):
        raw = _read_regular(path, max_bytes=MAX_CLAIM_BYTES)
        assert raw is not None
        total_bytes += len(raw)
        if total_bytes > MAX_LEDGER_BYTES:
            raise ValueError("claim ledger byte limit exceeded")
        claims.append(_parse_record(root, path, raw))
    return claims


@consistent_read
def list_claims(root: Path, *, allowed_sensitivities: set[str] | None = None) -> list[dict]:
    root = Path(root).resolve(strict=True)
    if allowed_sensitivities is not None and "restricted" not in allowed_sensitivities:
        return []
    result = []
    for claim in _claims(root):
        label, _ = _effective_label(root, claim["payload"], claim["record"]["sensitivity"])
        if allowed_sensitivities is None or label in allowed_sensitivities:
            result.append({**claim, "record": {**claim["record"], "sensitivity": label}})
    return result


def _creation_plan(root: Path, statement: str, links: list[dict], title: str, owner: str, sensitivity: str,
                   today: dt.date, valid_from: str | None, valid_until: str | None) -> tuple[dict, str]:
    _text(title, "title")
    _text(owner, "owner")
    if not isinstance(sensitivity, str) or sensitivity not in SENSITIVITY_LEVEL:
        raise ValueError("invalid claim sensitivity")
    if type(today) is not dt.date:
        raise ValueError("today must be a calendar date")
    prior = _claims(root)
    if len(prior) >= MAX_CLAIMS:
        raise ValueError("claim record limit exceeded")
    next_id = max((int(claim["claim_id"][2:]) for claim in prior), default=0) + 1
    claim_id = f"C-{next_id:03d}"
    _claim_id(claim_id)
    payload = {"format": "whykit.claim-provenance/v1", "claim_id": claim_id, "statement": statement, "links": links}
    if isinstance(statement, str) and len(statement) > MAX_CLAIM_BYTES:
        raise ValueError("claim exceeds byte limit")
    if isinstance(links, list) and len(links) > MAX_CLAIM_LINKS:
        raise ValueError("claim link limit exceeded")
    validate_claim(payload)
    # Quotes are source content; keep every persisted claim above ordinary reader ceilings.
    label, dependencies = _effective_label(root, payload, "restricted")
    record = {"title": title, "type": "claim", "status": "draft", "owner": owner,
              "created": today.isoformat(), "last_updated": today.isoformat(), "source_of_truth": False,
              "sensitivity": label, "claim_id": claim_id, "source_ids": sorted({link["evidence_id"] for link in links})}
    for key, value in (("valid_from", valid_from), ("valid_until", valid_until)):
        if value is not None:
            record[key] = value
    _interval(record)
    front = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False, allow_nan=False)}" for key, value in record.items())
    text = f"---\n{front}\n---\n\n# Claim {claim_id}\n\n```whykit-claim\n{_json_text(payload).rstrip()}\n```\n"
    if len(text.encode("utf-8")) > MAX_CLAIM_BYTES:
        raise ValueError("claim exceeds byte limit")
    if sum((root / claim["path"]).lstat().st_size for claim in prior) + len(text.encode("utf-8")) > MAX_LEDGER_BYTES:
        raise ValueError("claim ledger byte limit exceeded")
    _parse_claim_text(text)
    path = f"{CLAIM_DIRECTORY}/{claim_id.lower()}.md"
    _confined_path(root, path)
    binding = {"root": str(root), "path": path, "content": _hash(text.encode("utf-8")),
               "requested_sensitivity": sensitivity, "dependencies": dependencies,
               "claims": {claim["path"]: claim["sha256"] for claim in prior}}
    report = {"format": "whykit.claim-create/v1", "applied": False, "expected_sha256": _hash(_json_text(binding).encode("utf-8")),
              "claim_id": claim_id, "path": path, "record": record, "payload": payload}
    return report, text


def create_claim(root: Path, statement: str, links: list[dict], *, title: str, owner: str,
                 sensitivity: str = "internal", today: dt.date | None = None,
                 valid_from: str | None = None, valid_until: str | None = None,
                 write: bool = False, expected_sha256: str | None = None) -> dict:
    """Allocate a draft C-ID and persist only the reviewed content, under one writer lock."""
    root = Path(root).resolve(strict=True)
    today = dt.date.today() if today is None else today
    if write and not expected_sha256:
        raise ValueError("claim write requires a reviewed expected_sha256")
    with vault_mutation_lock(root) if write else vault_read_lock(root):
        report, text = _creation_plan(root, statement, links, title, owner, sensitivity, today, valid_from, valid_until)
        if not write:
            return report
        if report["expected_sha256"] != expected_sha256:
            raise ValueError("claim preview is stale; review a fresh preview")
        target = safe_vault_target(root, report["path"])
        if target.exists():
            raise ValueError("claim target appeared; review a fresh preview")
        apply_transaction(root, {target: text})
        return {**report, "applied": True}


@consistent_read
def verify_claim(root: Path, claim_id: str, *, allowed_sensitivities: set[str] | None = None,
                 expected_sha256: str | None = None, today: dt.date | None = None) -> dict:
    """Separate byte-anchor matches from source observations at UTC-day precision.

    A same-day timestamp is not proof it was observed before the current instant.
    Neither an anchor match nor current bytes establish semantic support or authority.
    """
    today = dt.date.today() if today is None else today
    if type(today) is not dt.date:
        raise ValueError("today must be a calendar date")
    root = Path(root).resolve(strict=True)
    claim = read_claim(root, claim_id, allowed_sensitivities=allowed_sensitivities, expected_sha256=expected_sha256)
    result = {"format": "whykit.claim-verification/v1", "claim_id": claim_id, "exists": claim is not None,
              "links": [], "unlinked": False}
    if claim is None:
        return result
    from .artifact_store import read_artifact, read_source_observation
    links = []
    for link in claim["payload"]["links"]:
        source = read_artifact(root, link["evidence_id"], link["source_version"], allowed_sensitivities=allowed_sensitivities)
        observation = read_source_observation(root, link["evidence_id"], link["source_version"],
                                               today=today, allowed_sensitivities=allowed_sensitivities)
        links.append({"evidence_id": link["evidence_id"], "source_version": link["source_version"],
                      "status": verify_anchor(link, source),
                      "source_state": observation["state"] if observation is not None else None,
                      "assessment": dict(link["assessment"])})
    return {**result, "links": links, "unlinked": not links}
