"""Bounded offline evidence captures; no URL resolution or factual verdicts."""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .io import apply_transaction, consistent_read, open_regular_binary, safe_vault_target, vault_mutation_lock, vault_read_lock
from .lint import load_note
from .sensitivity import SENSITIVITY_LEVEL, classify_evidence_register

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
MAX_REGISTER_BYTES = 2 * 1024 * 1024
ARTIFACT_FORMAT = "whykit.evidence-artifact/v1"
OBSERVATION_FORMAT = "whykit.source-observations/v1"
MAX_OBSERVATIONS = 256
MAX_OBSERVATION_BYTES = 64 * 1024


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("non-finite JSON value")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid bounded JSON envelope") from exc


def _confined_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe capture or claim path")
    target = root / path
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlinked capture or claim path refused")
    return target


def _read_regular(path: Path, *, max_bytes: int, missing_ok: bool = False) -> bytes | None:
    """Reject static symlinks/devices and validate the descriptor after no-follow open."""
    path = path.absolute()
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("symlinked input path refused")
    try:
        before = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if before.st_size > max_bytes:
        raise ValueError("input exceeds byte limit")
    with open_regular_binary(path) as handle:
        opened = os.fstat(handle.fileno())
        if opened.st_size > max_bytes:
            raise ValueError("input exceeds byte limit")
        raw = handle.read(max_bytes + 1)
        after = os.fstat(handle.fileno())
        current = path.lstat()
        def signature(info: os.stat_result) -> tuple[int, ...]:
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        if len(raw) > max_bytes:
            raise ValueError("input exceeds byte limit")
        if signature(before) != signature(opened) or signature(opened) != signature(after) or signature(after) != signature(current):
            raise ValueError("input changed during read; retry")
        return raw


def _evidence_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"E-[0-9]{3,}", value) is None:
        raise ValueError("evidence ID must be E-NNN")


def _version(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("source version must be sha256:<64 lowercase hex digits>")
    return value[7:]


def _utc(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)", value) is None:
        raise ValueError("recorded_at must be a UTC ISO timestamp with seconds")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.isoformat().replace("+00:00", "Z")


def _evidence_info(root: Path, evidence_id: str) -> tuple[str, bytes]:
    _evidence_id(evidence_id)
    path = _confined_path(root, "00-context/evidence-register.md")
    if any(item.name.casefold() == "evidence-register.md" and item.name != "evidence-register.md" for item in path.parent.iterdir()):
        raise ValueError("ambiguous evidence register path")
    raw = _read_regular(path, max_bytes=MAX_REGISTER_BYTES)
    assert raw is not None
    try:
        note = load_note(path, text=raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("evidence register must be UTF-8") from exc
    active, retired, occurrences, _ = classify_evidence_register(note)
    row = active.get(evidence_id) or retired.get(evidence_id)
    if row is None or sum(key == evidence_id for key, _ in occurrences) != 1:
        raise ValueError("evidence ID must identify exactly one populated register row")
    label = row.get("sensitivity", "unclassified")
    if label not in SENSITIVITY_LEVEL:
        raise ValueError("evidence sensitivity is unreadable")
    return label, raw


def _artifact_path(root: Path, evidence_id: str, source_version: str) -> Path:
    _evidence_id(evidence_id)
    digest = _version(source_version)
    return _confined_path(root, f"00-context/artifacts/{evidence_id}/{digest}.json")


def _artifact_envelope(root: Path, evidence_id: str, source_version: str, *,
                       max_envelope_bytes: int = MAX_ARTIFACT_BYTES) -> tuple[dict, bytes] | None:
    if type(max_envelope_bytes) is not int or not 1 <= max_envelope_bytes <= MAX_ARTIFACT_BYTES:
        raise ValueError("invalid artifact read budget")
    path = _artifact_path(root, evidence_id, source_version)
    raw = _read_regular(path, max_bytes=max_envelope_bytes, missing_ok=True)
    if raw is None:
        return None
    return _parse_artifact_envelope(raw, evidence_id, source_version), raw


def _parse_artifact_envelope(raw: bytes, evidence_id: str, source_version: str) -> dict:
    """Parse captured envelope metadata without decoding or hashing source bytes."""
    try:
        value = _strict_json(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("artifact envelope must be UTF-8") from exc
    keys = {"format", "evidence_id", "source_version", "recorded_at", "sensitivity", "size_bytes", "content_base64"}
    if not isinstance(value, dict) or set(value) != keys or value["format"] != ARTIFACT_FORMAT:
        raise ValueError("invalid artifact envelope")
    if value["evidence_id"] != evidence_id or value["source_version"] != source_version:
        raise ValueError("artifact identity or digest collision")
    if not isinstance(value["sensitivity"], str) or value["sensitivity"] not in SENSITIVITY_LEVEL:
        raise ValueError("invalid artifact classification or timestamp")
    return value


def _load_artifact(root: Path, evidence_id: str, source_version: str, *,
                   allowed_sensitivities: set[str] | None = None,
                   sensitivity_floor: str = "public", max_envelope_bytes: int = MAX_ARTIFACT_BYTES,
                   max_source_bytes: int = MAX_SOURCE_BYTES) -> tuple[dict, bytes, bytes] | None:
    if type(max_source_bytes) is not int or not 0 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError("invalid artifact source budget")
    envelope = _artifact_envelope(root, evidence_id, source_version, max_envelope_bytes=max_envelope_bytes)
    if envelope is None:
        return None
    value, raw = envelope
    return _decode_artifact_envelope(value, raw, source_version, allowed_sensitivities=allowed_sensitivities,
                                     sensitivity_floor=sensitivity_floor, max_source_bytes=max_source_bytes)


def _decode_artifact_envelope(value: dict, raw: bytes, source_version: str, *,
                              allowed_sensitivities: set[str] | None = None,
                              sensitivity_floor: str = "public", max_source_bytes: int = MAX_SOURCE_BYTES) -> tuple[dict, bytes, bytes]:
    """Validate/decode one already captured envelope; no second filesystem read."""
    if type(max_source_bytes) is not int or not 0 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError("invalid artifact source budget")
    effective = max((sensitivity_floor, value["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)
    if allowed_sensitivities is not None and effective not in allowed_sensitivities:
        raise PermissionError("artifact is outside the supplied sensitivity scope")
    if _utc(value["recorded_at"]) != value["recorded_at"]:
        raise ValueError("invalid artifact classification or timestamp")
    if type(value["size_bytes"]) is not int or not 0 <= value["size_bytes"] <= max_source_bytes or not isinstance(value["content_base64"], str):
        raise ValueError("invalid artifact byte envelope")
    encoded = value["content_base64"]
    padding = (-value["size_bytes"]) % 3
    if (len(encoded) != 4 * ((value["size_bytes"] + 2) // 3)
            or encoded.count("=") != padding or not encoded.endswith("=" * padding)):
        raise ValueError("invalid artifact base64 length")
    try:
        content = base64.b64decode(value["content_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid artifact base64") from exc
    if len(content) != value["size_bytes"] or _hash(content) != _version(source_version) or base64.b64encode(content).decode("ascii") != value["content_base64"]:
        raise ValueError("tampered artifact bytes or digest")
    return value, content, raw


@consistent_read
def read_artifact(root: Path, evidence_id: str, source_version: str, *, allowed_sensitivities: set[str] | None = None) -> bytes | None:
    """Internal bytes API; never resolves a register location or source URL."""
    root = Path(root).resolve(strict=True)
    _version(source_version)
    label, _ = _evidence_info(root, evidence_id)
    if allowed_sensitivities is not None and not any(
        SENSITIVITY_LEVEL.get(value, -1) >= SENSITIVITY_LEVEL[label] for value in allowed_sensitivities
    ):
        return None
    try:
        artifact = _load_artifact(root, evidence_id, source_version, allowed_sensitivities=allowed_sensitivities,
                                  sensitivity_floor=label)
    except PermissionError:
        return None
    if artifact is None:
        return None
    envelope, content, _ = artifact
    effective = max((label, envelope["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)
    return content if allowed_sensitivities is None or effective in allowed_sensitivities else None


def observation_path(root: Path, evidence_id: str) -> Path:
    """Confined path only; never resolve an external source location."""
    _evidence_id(evidence_id)
    return _confined_path(Path(root).resolve(strict=True), f"00-context/observations/{evidence_id}.json")


def _load_observations(root: Path, evidence_id: str, label: str,
                       allowed_sensitivities: set[str] | None = None, *,
                       max_bytes: int = MAX_OBSERVATION_BYTES) -> tuple[dict | None, bytes | None, str]:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_OBSERVATION_BYTES:
        raise ValueError("invalid observation read budget")
    raw = _read_regular(observation_path(root, evidence_id), max_bytes=max_bytes, missing_ok=True)
    if raw is None:
        if allowed_sensitivities is not None and label not in allowed_sensitivities:
            raise PermissionError("observations are outside the supplied sensitivity scope")
        return None, None, label
    try:
        value = _strict_json(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("observation ledger must be UTF-8") from exc
    if not isinstance(value, dict) or set(value) != {"format", "evidence_id", "observations"}:
        raise ValueError("invalid source observation envelope")
    events = value["observations"]
    if value["format"] != OBSERVATION_FORMAT or value["evidence_id"] != evidence_id or not isinstance(events, list) or not 1 <= len(events) <= MAX_OBSERVATIONS:
        raise ValueError("invalid source observation identity or count")
    for event in events:
        if not isinstance(event, dict) or set(event) != {"source_version", "recorded_at", "sensitivity"}:
            raise ValueError("invalid source observation event")
        if not isinstance(event["sensitivity"], str) or event["sensitivity"] not in SENSITIVITY_LEVEL:
            raise ValueError("unreadable source observation sensitivity")
        if allowed_sensitivities is not None and event["sensitivity"] not in allowed_sensitivities:
            raise PermissionError("observations are outside the supplied sensitivity scope")
        label = max((label, event["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)
    # History is one classification unit: never reveal/hash a public subset of it.
    if allowed_sensitivities is not None and label not in allowed_sensitivities:
        raise PermissionError("observations are outside the supplied sensitivity scope")
    seen = set()
    for event in events:
        _version(event["source_version"])
        if _utc(event["recorded_at"]) != event["recorded_at"]:
            raise ValueError("observation timestamp must be canonical UTC")
        key = (event["source_version"], event["recorded_at"], event["sensitivity"])
        if key in seen:
            raise ValueError("duplicate source observation")
        seen.add(key)
    if _json_text(value).encode("utf-8") != raw:
        raise ValueError("observation ledger must use canonical JSON")
    return value, raw, label


@consistent_read
def read_source_observation(root: Path, evidence_id: str, pinned_version: str, *,
                             today: dt.date | None = None,
                             allowed_sensitivities: set[str] | None = None) -> dict | None:
    """Compare a pinned capture with latest declared UTC-day observations, not truth."""
    root = Path(root).resolve(strict=True)
    _version(pinned_version)
    today = dt.datetime.now(dt.timezone.utc).date() if today is None else today
    if type(today) is not dt.date:
        raise ValueError("today must be a calendar date")
    label, _ = _evidence_info(root, evidence_id)
    if allowed_sensitivities is not None and label not in allowed_sensitivities:
        return None
    try:
        ledger, raw, label = _load_observations(root, evidence_id, label, allowed_sensitivities)
        events = ledger["observations"] if ledger else []
        latest = max((dt.datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")) for event in events), default=None)
        versions = sorted({event["source_version"] for event in events
                           if dt.datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")) == latest})
        requested = list(dict.fromkeys([pinned_version, *versions]))
        missing_latest = False
        # Gate every header before decoding/hashing any dependent source payload.
        for version in requested:
            envelope = _artifact_envelope(root, evidence_id, version)
            if envelope is None:
                missing_latest = missing_latest or version in versions
                continue
            capture_label = envelope[0]["sensitivity"]
            if allowed_sensitivities is not None and capture_label not in allowed_sensitivities:
                return None
            label = max((label, capture_label), key=SENSITIVITY_LEVEL.__getitem__)
        if missing_latest:
            raise ValueError("latest observed capture is missing")
        captures = {}
        for version in requested:
            captured = _load_artifact(root, evidence_id, version, allowed_sensitivities=allowed_sensitivities,
                                       sensitivity_floor="public")
            if captured is None:
                if version in versions:
                    raise ValueError("latest observed capture is missing")
                continue
            label = max((label, captured[0]["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)
            captures[version] = _hash(captured[2])
    except PermissionError:
        return None
    state = "unknown" if latest is None else "future" if latest.date() > today else "ambiguous" if len(versions) > 1 else "current" if versions == [pinned_version] else "changed"
    return {"format": "whykit.source-observation/v1", "evidence_id": evidence_id, "pinned_version": pinned_version,
            "state": state, "observed_at": latest.isoformat().replace("+00:00", "Z") if latest else None,
            "source_versions": versions, "ledger_sha256": _hash(raw) if raw is not None else None,
            "capture_sha256": dict(sorted(captures.items())),
            "sensitivity": label, "observation_count": len(events)}


def _capture_plan(root: Path, evidence_id: str, source: Path, recorded_at: str, max_bytes: int, *,
                  source_bytes: bytes | None = None) -> tuple[dict, dict[str, str], list[tuple[Path, bytes | None, int]], set[str]]:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_SOURCE_BYTES:
        raise ValueError("max_bytes must be between 1 and 8388608")
    recorded_at = _utc(recorded_at)
    label, register = _evidence_info(root, evidence_id)
    # Adapters may validate one bounded snapshot and plan these exact same bytes.
    raw = _read_regular(Path(source), max_bytes=max_bytes) if source_bytes is None else source_bytes
    assert raw is not None
    if type(raw) is not bytes or len(raw) > max_bytes:
        raise ValueError("input exceeds byte limit or is not a byte snapshot")
    source_version = "sha256:" + _hash(raw)
    target = _artifact_path(root, evidence_id, source_version)
    existing = _load_artifact(root, evidence_id, source_version)
    ledger, ledger_raw, history_label = _load_observations(root, evidence_id, label)
    events = ledger["observations"] if ledger else []
    if events:
        latest = max(dt.datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")) for event in events)
        for version in {event["source_version"] for event in events
                        if dt.datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")) == latest}:
            if _load_artifact(root, evidence_id, version) is None:
                raise ValueError("latest observed capture is missing")
    envelope: dict[str, Any] = {"format": ARTIFACT_FORMAT, "evidence_id": evidence_id, "source_version": source_version,
                "recorded_at": recorded_at, "sensitivity": label, "size_bytes": len(raw),
                "content_base64": base64.b64encode(raw).decode("ascii")}
    text = _json_text(envelope)
    if existing is not None:
        if existing[1] != raw:
            raise ValueError("artifact hash collision")
        envelope = existing[0]
    event = {"source_version": source_version, "recorded_at": recorded_at,
             "sensitivity": max((label, envelope["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)}
    added = event not in events
    next_ledger = {"format": OBSERVATION_FORMAT, "evidence_id": evidence_id,
                   "observations": [*events, event] if added else events}
    ledger_text = _json_text(next_ledger)
    if len(next_ledger["observations"]) > MAX_OBSERVATIONS or len(ledger_text.encode("utf-8")) > MAX_OBSERVATION_BYTES:
        raise ValueError("source observation ledger limit exceeded")
    ledger_path = observation_path(root, evidence_id)
    binding = {"root": str(root), "source": str(Path(source).absolute()), "source_version": source_version,
               "recorded_at": recorded_at, "sensitivity": label, "max_bytes": max_bytes,
               "register": _hash(register), "existing": _hash(existing[2]) if existing else None,
               "observations": _hash(ledger_raw) if ledger_raw is not None else None}
    replay_hashes: set[str] = set()
    if existing is not None and not added and events[-1] == event:
        # Reconstruct only the state immediately before this final append, never before later observations.
        previous = {**next_ledger, "observations": events[:-1]}
        previous_hash = _hash(_json_text(previous).encode("utf-8")) if events[:-1] else None
        prior_binding = {**binding, "observations": previous_hash}
        replay_hashes.add(_hash(_json_text(prior_binding).encode("utf-8")))
        if existing[2] == text.encode("utf-8"):
            replay_hashes.add(_hash(_json_text({**prior_binding, "existing": None}).encode("utf-8")))
    report = {"format": "whykit.artifact-capture/v1", "applied": False,
              "expected_sha256": _hash(_json_text(binding).encode()), "evidence_id": evidence_id,
              "source_version": source_version, "path": target.relative_to(root).as_posix(),
              "size_bytes": len(raw), "sensitivity": max((history_label, event["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__),
              "recorded_at": envelope["recorded_at"], "already_exists": existing is not None,
              "observation_path": ledger_path.relative_to(root).as_posix(),
              "observation_recorded_at": recorded_at, "observation_added": added}
    updates = {}
    if existing is None:
        updates[report["path"]] = text
    if added:
        updates[report["observation_path"]] = ledger_text
    inputs = [(root / "00-context/evidence-register.md", register, MAX_REGISTER_BYTES),
              (target, existing[2] if existing else None, MAX_ARTIFACT_BYTES),
              (ledger_path, ledger_raw, MAX_OBSERVATION_BYTES)]
    return report, updates, inputs, replay_hashes


def capture_artifact(root: Path, evidence_id: str, source: Path, *, recorded_at: str,
                     write: bool = False, expected_sha256: str | None = None,
                     max_bytes: int = MAX_SOURCE_BYTES) -> dict:
    """Preview, then capture the same bounded local bytes under a content-bound hash."""
    root = Path(root).resolve(strict=True)
    if not write:
        with vault_read_lock(root):
            return _capture_plan(root, evidence_id, source, recorded_at, max_bytes)[0]
    if not expected_sha256:
        raise ValueError("capture write requires a reviewed expected_sha256")
    with vault_mutation_lock(root):
        plan, updates, inputs, replay_hashes = _capture_plan(root, evidence_id, source, recorded_at, max_bytes)
        if plan["expected_sha256"] != expected_sha256 and expected_sha256 not in replay_hashes:
            raise ValueError("capture preview is stale; review a fresh preview")
        if updates:
            if any(_read_regular(path, max_bytes=limit, missing_ok=True) != before for path, before, limit in inputs):
                raise ValueError("capture inputs changed; review a fresh preview")
            apply_transaction(root, {safe_vault_target(root, relative): text for relative, text in updates.items()})
        return {**plan, "expected_sha256": expected_sha256, "applied": True}
