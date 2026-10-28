"""Restricted outcome plans and historical samples, without value or causal verdicts."""
from __future__ import annotations

from collections.abc import Collection
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any

from .artifact_store import _confined_path, _hash, _json_text, _read_regular, _strict_json, read_artifact, read_source_observation
from .claim_ledger import ClaimSnapshotChanged, _claim_id, _date, _read_claim, read_claim
from .claim_provenance import verify_anchor
from .io import apply_transaction, consistent_read, safe_vault_target, vault_mutation_lock, vault_read_lock
from .knowledge_visibility import canonical_decision_id
from .lint import ALLOWED_STATUS, REQUIRED_KEYS, Finding, Note, check_front_matter, load_note
from .sensitivity import SENSITIVITY_LEVEL, note_sensitivity_level
from .vault_index import NoteCache, VaultIndex, reuse_notes

MAX_OUTCOME_BYTES = 64 * 1024
MAX_OUTCOME_LEDGER_BYTES = 8 * 1024 * 1024
MAX_OUTCOMES = 10_000
OUTCOME_DIRECTORY = "00-context/outcomes"
INPUT_KEYS = {"decision_id", "metric", "baseline", "target", "observed", "implementation_refs"}


def _outcome_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"O-[0-9]{3,}", value) is None:
        raise ValueError("outcome ID must be canonical O-NNN")
    if len(value) > 18 or int(value[2:]) < 1 or value != f"O-{int(value[2:]):03d}":
        raise ValueError("outcome ID must be canonical O-NNN")
    return value


def _text(value: object, name: str, limit: int, *, multiline: bool = False) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be bounded nonblank text")
    if not multiline and (len(value.splitlines()) != 1 or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{name} must be single-line text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{name} must be UTF-8 encodable") from exc


def _object(value: object, keys: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} requires exactly its documented fields")
    return value


def _decimal(value: object) -> None:
    if (not isinstance(value, str) or len(value) > 46
            or re.fullmatch(r"-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,12})?", value) is None
            or sum(char in "0123456789" for char in value) > 32):
        raise ValueError("value must be an exact decimal string, precision <= 32 and scale <= 12")


def _validate_data(data: object, created: dt.date) -> dict:
    data = _object(data, INPUT_KEYS, "outcome data")
    if not isinstance(data["decision_id"], str) or re.fullmatch(r"D-[0-9]{3,}", data["decision_id"]) is None:
        raise ValueError("decision_id must be D-NNN")
    metric = _object(data["metric"], {"name", "unit", "definition"}, "metric")
    _text(metric["name"], "metric name", 128)
    _text(metric["unit"], "metric unit", 128)
    _text(metric["definition"], "metric definition", 4096, multiline=True)
    if all(data[key] is None for key in ("baseline", "target", "observed")):
        raise ValueError("outcome requires at least one plan or sample")
    for key in ("baseline", "observed"):
        if data[key] is None:
            continue
        sample = _object(data[key], {"value", "as_of", "claim"}, key)
        _decimal(sample["value"])
        day = _date(sample["as_of"], "as_of")
        if day is None or day > created:
            raise ValueError("sample as_of must be a real date no later than outcome creation")
        if sample["claim"] is not None:
            claim = _object(sample["claim"], {"claim_id", "sha256"}, "sample claim")
            _claim_id(claim["claim_id"])
            if not isinstance(claim["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", claim["sha256"]) is None:
                raise ValueError("sample claim sha256 must be the exact raw Markdown digest")
    if data["target"] is not None:
        target = _object(data["target"], {"value", "by"}, "target")
        _decimal(target["value"])
        _date(target["by"], "target by")
    refs = data["implementation_refs"]
    if not isinstance(refs, list) or len(refs) > 32:
        raise ValueError("implementation_refs must contain at most 32 opaque strings")
    for ref in refs:
        _text(ref, "implementation reference", 512)
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate implementation reference")
    return data


def _parse_text(text: str) -> tuple[dict, dict]:
    if not isinstance(text, str) or len(text) > MAX_OUTCOME_BYTES:
        raise ValueError("outcome record exceeds its text bound")
    try:
        if len(text.encode("utf-8")) > MAX_OUTCOME_BYTES:
            raise ValueError("outcome record exceeds its byte bound")
    except UnicodeError as exc:
        raise ValueError("outcome record must be UTF-8") from exc
    note = load_note(Path("outcome.md"), text=text)
    record = note.front
    required = set(REQUIRED_KEYS) | {"outcome_id", "decision_ref"}
    if note.front_error or not note.has_front or set(record) != required:
        raise ValueError("outcome requires exact complete front matter")
    oid = _outcome_id(record["outcome_id"])
    if (record["type"] != "outcome" or record["status"] != "draft"
            or record["source_of_truth"] is not False or record["sensitivity"] != "restricted"):
        raise ValueError("outcome must remain a restricted noncanonical draft")
    _text(record["title"], "title", 256)
    _text(record["owner"], "owner", 256)
    created, updated = _date(record["created"], "created"), _date(record["last_updated"], "last_updated")
    if created is None or updated is None or updated < created:
        raise ValueError("invalid outcome record dates")
    match = re.fullmatch(r"\s*# Outcome " + re.escape(oid) + r"\n\n```whykit-outcome\n(.*?)\n```\n?", note.body, re.DOTALL)
    if match is None:
        raise ValueError("outcome requires exactly one fenced whykit-outcome JSON envelope")
    payload = _object(_strict_json(match.group(1)), INPUT_KEYS | {"format", "outcome_id"}, "outcome payload")
    if payload["format"] != "whykit.outcome/v1" or payload["outcome_id"] != oid:
        raise ValueError("outcome payload identity mismatch")
    _validate_data({key: payload[key] for key in INPUT_KEYS}, created)
    if record["decision_ref"] != payload["decision_id"]:
        raise ValueError("outcome decision reference mismatch")
    return record, payload


def outcome_references(text: str) -> tuple[str, set[str]]:
    """Pure bounded reference parsing; no source reads, hashing or grant expansion."""
    _, payload = _parse_text(text)
    return payload["decision_id"], {payload[key]["claim"]["claim_id"] for key in ("baseline", "observed")
                                    if payload[key] is not None and payload[key]["claim"] is not None}


def outcome_visibility(note: Note, visible_decision_ids: Collection[str], visible_claim_ids: Collection[str], *, root: Path) -> bool:
    try:
        record, payload = _parse_text(note.text)
        if note.path.relative_to(root).parts != ("00-context", "outcomes", record["outcome_id"].lower() + ".md"):
            return False
        decision = payload["decision_id"]
        claims = {payload[key]["claim"]["claim_id"] for key in ("baseline", "observed")
                  if payload[key] is not None and payload[key]["claim"] is not None}
        return decision in visible_decision_ids and all(claim in visible_claim_ids for claim in claims)
    except (ValueError, UnicodeError):
        return False


def _decision(root: Path, decision_id: str, allowed: set[str] | None = None) -> tuple[str, str | None]:
    with reuse_notes(NoteCache()):
        index = VaultIndex.load(root)
    matches = [note for note in index.notes if note.front.get("decision_id") == decision_id
               or canonical_decision_id(note, root) == decision_id]
    if not matches:
        return "missing", None
    if len(matches) != 1:
        return "ambiguous", None
    note = matches[0]
    if allowed is not None and not any(level == note_sensitivity_level(note) for label, level in SENSITIVITY_LEVEL.items() if label in allowed):
        return "unavailable", None
    if (note.front_error or note.front.get("type") != "decision" or not isinstance(note.front.get("status"), str)
            or note.front["status"] not in ALLOWED_STATUS - {"template"}
            or not set(REQUIRED_KEYS).issubset(note.front)
            or canonical_decision_id(note, root) != decision_id):
        return "invalid", None
    findings: list[Finding] = []
    check_front_matter(root, note, findings, dt.date.today())
    if any(item.level == "error" for item in findings):
        return "invalid", None
    raw = _read_regular(note.path, max_bytes=1024 * 1024)
    assert raw is not None
    return "available", _hash(raw)


def _canonical_path(root: Path, relative: str) -> Path:
    path = _confined_path(root, relative)
    current = root
    for component in Path(relative).parts:
        if not current.exists():
            break
        for entry in current.iterdir():
            if entry.name.casefold() == component.casefold() and entry.name != component:
                raise ValueError("noncanonical outcome path alias")
        current = current / component
    return path


def _read(root: Path, outcome_id: str, *, path: Path | None = None) -> dict | None:
    path = path if path is not None else _canonical_path(root, f"{OUTCOME_DIRECTORY}/{outcome_id.lower()}.md")
    raw = _read_regular(path, max_bytes=MAX_OUTCOME_BYTES, missing_ok=True)
    if raw is None:
        return None
    try:
        record, payload = _parse_text(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("outcome record must be UTF-8") from exc
    if record["outcome_id"] != outcome_id:
        raise ValueError("outcome path identity mismatch")
    return {"outcome_id": outcome_id, "path": path.relative_to(root).as_posix(),
            "record": record, "payload": payload, "sha256": _hash(raw)}


@consistent_read
def read_outcome(root: Path, outcome_id: str, *, allowed_sensitivities: set[str] | None = None) -> dict | None:
    _outcome_id(outcome_id)
    if allowed_sensitivities is not None and "restricted" not in allowed_sensitivities:
        return None
    return _read(Path(root).resolve(strict=True), outcome_id)


def _rows(root: Path) -> list[dict]:
    directory = _canonical_path(root, OUTCOME_DIRECTORY)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("outcome directory must be a directory")
    paths: list[Path] = []
    for path in directory.iterdir():
        if len(paths) >= MAX_OUTCOMES or re.fullmatch(r"o-[0-9]{3,}\.md", path.name) is None:
            raise ValueError("noncanonical or over-limit outcome directory")
        paths.append(path)
    rows: list[dict] = []
    total = 0
    for path in sorted(paths):
        oid = _outcome_id(path.stem.upper())
        row = _read(root, oid, path=path)
        if row is None:
            raise ValueError("outcome disappeared during listing")
        total += path.lstat().st_size
        if total > MAX_OUTCOME_LEDGER_BYTES:
            raise ValueError("outcome ledger byte limit exceeded")
        rows.append(row)
    return rows


@consistent_read
def list_outcomes(root: Path, *, allowed_sensitivities: set[str] | None = None) -> list[dict]:
    if allowed_sensitivities is not None and "restricted" not in allowed_sensitivities:
        return []
    return _rows(Path(root).resolve(strict=True))


def _plan(root: Path, data: dict, owner: str, title: str | None, today: dt.date) -> tuple[dict, str]:
    if type(today) is not dt.date:
        raise ValueError("today must be a calendar date")
    _validate_data(data, today)
    _text(owner, "owner", 256)
    title = data["metric"]["name"] if title is None else title
    _text(title, "title", 256)
    state, decision_hash = _decision(root, data["decision_id"])
    if state != "available":
        raise ValueError("outcome requires one canonical existing decision")
    dependencies: dict[str, Any] = {"decision": decision_hash, "claims": {}}
    for key in ("baseline", "observed"):
        sample = data[key]
        if sample is not None and sample["claim"] is not None:
            pin = sample["claim"]
            claim = _read_claim(root, pin["claim_id"], pin["sha256"])
            if claim is None:
                raise ValueError("outcome sample claim is missing")
            dependencies["claims"][pin["claim_id"]] = claim["sha256"]
    previous = _rows(root)
    if len(previous) >= MAX_OUTCOMES:
        raise ValueError("outcome record limit exceeded")
    oid = _outcome_id(f"O-{max((int(row['outcome_id'][2:]) for row in previous), default=0) + 1:03d}")
    record = {"title": title, "type": "outcome", "status": "draft", "owner": owner,
              "created": today.isoformat(), "last_updated": today.isoformat(), "source_of_truth": False,
              "sensitivity": "restricted", "outcome_id": oid, "decision_ref": data["decision_id"]}
    payload = {"format": "whykit.outcome/v1", "outcome_id": oid, **data}
    front = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in record.items())
    text = f"---\n{front}\n---\n\n# Outcome {oid}\n\n```whykit-outcome\n{_json_text(payload).rstrip()}\n```\n"
    _parse_text(text)
    if sum((root / row["path"]).lstat().st_size for row in previous) + len(text.encode("utf-8")) > MAX_OUTCOME_LEDGER_BYTES:
        raise ValueError("outcome ledger byte limit exceeded")
    path = f"{OUTCOME_DIRECTORY}/{oid.lower()}.md"
    _canonical_path(root, path)
    binding = {"root": str(root), "path": path, "content": _hash(text.encode("utf-8")), "dependencies": dependencies,
               "outcomes": {row["path"]: row["sha256"] for row in previous}}
    return {"format": "whykit.outcome-create/v1", "applied": False, "expected_sha256": _hash(_json_text(binding).encode("utf-8")),
            "outcome_id": oid, "path": path, "record": record, "payload": payload}, text


def create_outcome(root: Path, data: dict, *, owner: str, title: str | None = None,
                   today: dt.date | None = None, write: bool = False, expected_sha256: str | None = None) -> dict:
    root = Path(root).resolve(strict=True)
    today = dt.date.today() if today is None else today
    if write and not expected_sha256:
        raise ValueError("outcome write requires a reviewed expected_sha256")
    with vault_mutation_lock(root) if write else vault_read_lock(root):
        plan, text = _plan(root, data, owner, title, today)
        if not write:
            return plan
        if plan["expected_sha256"] != expected_sha256:
            raise ValueError("outcome preview is stale; review a fresh preview")
        target = safe_vault_target(root, plan["path"])
        if target.exists():
            raise ValueError("outcome target appeared; review a fresh preview")
        apply_transaction(root, {target: text})
        return {**plan, "applied": True}


def _verify_sample(root: Path, sample: dict | None, today: dt.date, allowed: set[str] | None) -> dict:
    result: dict[str, Any] = {"state": "absent" if sample is None else "unlinked", "claim_id": None,
              "pin_and_anchor_matched": False, "source_states": []}
    if sample is None or sample["claim"] is None:
        return result
    pin = sample["claim"]
    result["claim_id"] = pin["claim_id"]
    try:
        claim = read_claim(root, pin["claim_id"], expected_sha256=pin["sha256"], allowed_sensitivities=allowed)
    except ClaimSnapshotChanged:
        return {**result, "state": "claim_changed"}
    except (OSError, ValueError):
        return {**result, "state": "invalid_claim"}
    if claim is None:
        return {**result, "state": "unavailable" if allowed is not None else "missing_claim"}
    links = claim["payload"]["links"]
    matched = bool(links)
    states = set()
    for link in links:
        try:
            raw = read_artifact(root, link["evidence_id"], link["source_version"], allowed_sensitivities=allowed)
            matched = (verify_anchor(link, raw) == "matched") and matched
        except (OSError, ValueError):
            matched = False
        try:
            observation = read_source_observation(root, link["evidence_id"], link["source_version"], today=today,
                                                   allowed_sensitivities=allowed)
            states.add(observation["state"] if observation is not None else "unavailable")
        except (OSError, ValueError):
            states.add("invalid")
    return {**result, "state": "verified" if matched else "unverified_anchor",
            "pin_and_anchor_matched": matched, "source_states": sorted(states)}


@consistent_read
def verify_outcome(root: Path, outcome_id: str, *, today: dt.date | None = None,
                    allowed_sensitivities: set[str] | None = None) -> dict:
    today = dt.date.today() if today is None else today
    if type(today) is not dt.date:
        raise ValueError("today must be a calendar date")
    root = Path(root).resolve(strict=True)
    outcome = read_outcome(root, outcome_id, allowed_sensitivities=allowed_sensitivities)
    decision_state = "unavailable"
    baseline = observed = None
    if outcome is not None:
        decision_state, _ = _decision(root, outcome["payload"]["decision_id"], allowed_sensitivities)
        baseline, observed = outcome["payload"]["baseline"], outcome["payload"]["observed"]
    baseline_result = _verify_sample(root, baseline, today, allowed_sensitivities)
    observed_result = _verify_sample(root, observed, today, allowed_sensitivities)
    return {"format": "whykit.outcome-verification/v1", "outcome_id": outcome_id, "exists": outcome is not None,
            "decision_state": decision_state, "baseline": baseline_result, "observed": observed_result,
            "observed_source_verified": decision_state == "available" and observed_result["pin_and_anchor_matched"],
            "values_checked": False, "causality_checked": False, "execution_authorized": False}
