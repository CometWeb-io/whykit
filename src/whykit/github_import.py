"""Saved GitHub REST declarations, captured offline without authenticating a source."""
from __future__ import annotations

import math
from pathlib import Path
import re

from .artifact_store import (
    _capture_plan, _confined_path, _hash, _json_text, _read_regular, _strict_json, _utc,
)
from .io import apply_transaction, safe_vault_target, vault_mutation_lock, vault_read_lock
from .lint import load_note
from .sensitivity import classify_evidence_register
from .vault_index import NoteCache, reuse_notes

MAX_GITHUB_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 20
MAX_JSON_NODES = 20_000
MAX_SCALAR_CHARS = 64 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
KINDS = {"issue", "pull-request", "workflow-run"}
WORKFLOW_STATUSES = {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
WORKFLOW_CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale", "startup_failure"}
FLAGS = {"source_identity_authenticated": False, "remote_state_checked": False,
         "semantics_checked": False, "execution_authorized": False}


def _repository(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}", value) is None:
        raise ValueError("repository must be one bounded owner/repository")
    if value.split("/")[1] in {".", ".."}:
        raise ValueError("invalid repository name")
    return value


def _positive(value: object) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("provider ID/number/attempt must be a bounded positive integer")
    return value


def _sha(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("provider SHA must be 40 lowercase hexadecimal characters")
    return value


def _timestamp(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError("provider timestamp must be UTC text")
    return _utc(value)


def _token(value: object, supported: set[str]) -> str:
    if not isinstance(value, str) or value not in supported:
        raise ValueError("provider state/status is outside the supported saved REST profile")
    return value


def _json(raw: bytes) -> dict:
    try:
        value = _strict_json(raw.decode("utf-8-sig"))
    except UnicodeError as exc:
        raise ValueError("saved GitHub JSON must be UTF-8") from exc
    remaining = MAX_JSON_NODES
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        remaining -= 1
        if remaining < 0 or depth > MAX_JSON_DEPTH:
            raise ValueError("saved GitHub JSON structure exceeds its bound")
        if type(item) is dict:
            if any(len(key) > MAX_SCALAR_CHARS for key in item):
                raise ValueError("saved GitHub JSON key exceeds its bound")
            try:
                for key in item:
                    key.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("saved GitHub JSON key contains invalid Unicode") from exc
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_SCALAR_CHARS:
                raise ValueError("saved GitHub JSON scalar exceeds its bound")
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("saved GitHub JSON contains invalid Unicode") from exc
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("saved GitHub JSON contains a nonfinite number")
    if type(value) is not dict:
        raise ValueError("saved GitHub JSON must be a single REST object")
    return value


def _repo_field(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not dict or "full_name" not in value:
        raise ValueError("provider repository must declare full_name")
    return _repository(value["full_name"])


def _metadata(raw: bytes, kind: str, repository: str) -> dict:
    value = _json(raw)
    try:
        identity = _positive(value["id"])
        created = _timestamp(value["created_at"])
        updated = _timestamp(value["updated_at"])
        if kind == "workflow-run":
            if _repo_field(value["repository"]) != repository:
                raise ValueError("workflow repository differs from expected repository")
            if "head_repository" in value:
                _repo_field(value["head_repository"], nullable=True)
            suffix = f"actions/runs/{identity}"
            result = {"id": identity, "url": f"https://github.com/{repository}/{suffix}",
                      "status": _token(value["status"], WORKFLOW_STATUSES),
                      "conclusion": None if value["conclusion"] is None else _token(value["conclusion"], WORKFLOW_CONCLUSIONS),
                      "head_sha": _sha(value["head_sha"]), "run_attempt": _positive(value["run_attempt"]),
                      "created_at": created, "updated_at": updated}
            api_suffix = suffix
        else:
            number = _positive(value["number"])
            state = value["state"]
            if state not in ("open", "closed"):
                raise ValueError("issue/PR state must be open or closed")
            suffix = f"{'issues' if kind == 'issue' else 'pull'}/{number}"
            api_suffix = f"{'issues' if kind == 'issue' else 'pulls'}/{number}"
            result = {"id": identity, "number": number, "url": f"https://github.com/{repository}/{suffix}",
                      "state": state, "created_at": created, "updated_at": updated,
                      "closed_at": _timestamp(value["closed_at"], nullable=True)}
            if kind == "issue":
                if "pull_request" in value or value["repository_url"] != f"https://api.github.com/repos/{repository}":
                    raise ValueError("issue profile cannot import a PR or another repository")
            else:
                if type(value["base"]) is not dict or type(value["head"]) is not dict:
                    raise ValueError("PR head/base must be objects")
                if _repo_field(value["base"]["repo"]) != repository:
                    raise ValueError("PR base repository differs from expected repository")
                _repo_field(value["head"]["repo"], nullable=True)
                result.update(base_sha=_sha(value["base"]["sha"]), head_sha=_sha(value["head"]["sha"]),
                              merge_commit_sha=_sha(value["merge_commit_sha"], nullable=True),
                              merged_at=_timestamp(value["merged_at"], nullable=True))
        if value["html_url"] != result["url"] or value["url"] != f"https://api.github.com/repos/{repository}/{api_suffix}":
            raise ValueError("provider URL differs from expected canonical resource")
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError("saved GitHub object lacks valid selected REST fields") from exc


def _receipt_path(root: Path, evidence_id: str, version: str) -> Path:
    relative = f"00-context/imports/github/{evidence_id}/{version[7:]}.json"
    path = _confined_path(root, relative)
    current = root
    for component in Path(relative).parts:
        if not current.exists():
            break
        if any(entry.name.casefold() == component.casefold() and entry.name != component for entry in current.iterdir()):
            raise ValueError("noncanonical import receipt path alias")
        current = current / component
    return path


def _binding(capture_hash: str, receipt_hash: str | None, kind: str, repository: str, metadata: dict) -> str:
    return _hash(_json_text({"capture": capture_hash, "receipt": receipt_hash, "kind": kind,
                             "repository": repository, "metadata": metadata, "adapter_version": 1}).encode("utf-8"))


def _plan(root: Path, evidence_id: str, source: Path, kind: str, repository: str, recorded_at: str) -> tuple[dict, dict, list, set[str]]:
    raw = _read_regular(source, max_bytes=MAX_GITHUB_BYTES)
    assert raw is not None
    metadata = _metadata(raw, kind, repository)
    captured, updates, inputs, capture_replays = _capture_plan(root, evidence_id, source, recorded_at, MAX_GITHUB_BYTES, source_bytes=raw)
    register_path, register_raw, _ = inputs[0]
    assert register_raw is not None
    note = load_note(register_path, text=register_raw.decode("utf-8"))
    active, retired, _, _ = classify_evidence_register(note)
    row = active.get(evidence_id) or retired.get(evidence_id)
    if row is None or row.get("sensitivity") != "restricted" or row.get("location") != metadata["url"]:
        raise ValueError("GitHub import requires restricted evidence with the matching canonical resource location")
    prior_capture = inputs[1][1]
    if prior_capture is not None and _strict_json(prior_capture.decode("utf-8"))["sensitivity"] != "restricted":
        raise ValueError("prior immutable capture must already be restricted")
    receipt = {"format": "whykit.github-import-receipt/v1", "adapter_version": 1,
               "evidence_id": evidence_id, "source_version": captured["source_version"], "kind": kind,
               "repository": repository, "metadata": metadata, "sensitivity": "restricted", **FLAGS}
    receipt_raw = _json_text(receipt).encode("utf-8")
    if len(receipt_raw) > MAX_RECEIPT_BYTES:
        raise ValueError("import receipt exceeds its bound")
    path = _receipt_path(root, evidence_id, captured["source_version"])
    old = _read_regular(path, max_bytes=MAX_RECEIPT_BYTES, missing_ok=True)
    if old is not None and old != receipt_raw:
        raise ValueError("existing import receipt is invalid or tampered")
    if old is None:
        updates[path.relative_to(root).as_posix()] = receipt_raw.decode("utf-8")
    inputs.append((path, old, MAX_RECEIPT_BYTES))
    old_hash = _hash(old) if old is not None else None
    expected = _binding(captured["expected_sha256"], old_hash, kind, repository, metadata)
    receipt_hashes = {old_hash}
    if old is not None and capture_replays:
        # A receipt-absent previous state is replayable only for the final event.
        receipt_hashes.add(None)
    replay_hashes = {_binding(capture_hash, receipt_hash, kind, repository, metadata)
                     for capture_hash in {captured["expected_sha256"], *capture_replays}
                     for receipt_hash in receipt_hashes}
    report = {"format": "whykit.github-import/v1", "applied": False, "expected_sha256": expected,
              "evidence_id": evidence_id, "source_version": captured["source_version"], "artifact_path": captured["path"],
              "receipt_path": path.relative_to(root).as_posix(), "already_exists": captured["already_exists"],
              "observation_added": captured["observation_added"], "observation_recorded_at": captured["observation_recorded_at"],
              "size_bytes": len(raw), "sensitivity": "restricted", "metadata": metadata, **FLAGS}
    return report, updates, inputs, replay_hashes


def import_github(root: Path, evidence_id: str, source: Path, *, kind: str, repository: str, recorded_at: str,
                  write: bool = False, expected_sha256: str | None = None) -> dict:
    """Validate one saved snapshot, then capture it with a receipt in one transaction."""
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("unsupported saved GitHub resource kind")
    repository = _repository(repository)
    if (not isinstance(evidence_id, str) or re.fullmatch(r"E-[0-9]{3,15}", evidence_id) is None
            or int(evidence_id[2:]) < 1 or evidence_id != f"E-{int(evidence_id[2:]):03d}"):
        raise ValueError("evidence ID must be canonical E-NNN")
    recorded_at = _utc(recorded_at)
    root = Path(root).resolve(strict=True)
    source = Path(source).absolute()
    if write and not expected_sha256:
        raise ValueError("GitHub import write requires a reviewed expected_sha256")
    with (vault_mutation_lock(root) if write else vault_read_lock(root)), reuse_notes(NoteCache()):
        report, updates, inputs, replay_hashes = _plan(root, evidence_id, source, kind, repository, recorded_at)
        if not write:
            return report
        if expected_sha256 != report["expected_sha256"] and expected_sha256 not in replay_hashes:
            raise ValueError("GitHub import preview is stale; review a fresh preview")
        if updates:
            if any(_read_regular(path, max_bytes=limit, missing_ok=True) != before for path, before, limit in inputs):
                raise ValueError("GitHub import inputs changed; review a fresh preview")
            apply_transaction(root, {safe_vault_target(root, name): text for name, text in updates.items()})
        return {**report, "expected_sha256": expected_sha256, "applied": True}
