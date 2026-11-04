"""Byte-bounded, unsigned context compilation over a confined visible vault."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from itertools import chain, repeat
from pathlib import Path
from typing import Any

from .artifact_store import read_source_observation
from .claim_ledger import _parse_claim_text, claim_visibility
from .context_applicability import context_metadata
from .config import parse_config
from .io import open_regular_binary, vault_read_lock
from .knowledge_visibility import canonical_decision_id, visible_knowledge_index
from .lint import _build_index, decision_log_rows, evidence_view, is_vault_root, lint, load_note, path_cache
from .pack import AGENT_PREAMBLES, build_pack
from .query import query_vault
from .retrieval import retrieve_bm25
from .review_history import project_review_rows
from .sensitivity import SENSITIVITY_LEVEL, classified_evidence, evidence_register_notes, is_claim_note, is_outcome_note, note_sensitivity_level
from .vault_index import NoteCache, VaultIndex, reuse_notes
from .scaffold import _table_cell

FORMAT = "whykit.compiled-context/v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = MAX_BYTES
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_DOCS = 64
MIN_MAX_BYTES = 160
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ROW_KEYS = frozenset({"source", "type", "date", "accessed", "location", "claims", "sensitivity",
                       "retired_on", "why", "replaced_by"})
_NATIVE_GLOBAL_LOGS = frozenset({"00-context/review-log.md", "06-decisions/decision-log.md"})
_REPORT_KEYS = frozenset({"format", "contract_version", "status", "as_of", "inputs", "preamble", "content_trust",
                          "execution_authorized", "contexts", "citations", "warnings", "omissions",
                          "budget", "manifest"})


class CompiledContextError(ValueError):
    """A fixed refusal code; never includes vault-derived values."""


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _hash(value: object) -> str:
    return hashlib.sha256(_wire(value).encode("utf-8")).hexdigest()


def _bounded(value: object) -> bool:
    """Bound untrusted in-memory input before making a serialized copy of it."""
    pending = [iter([(value, 0)])]
    size = nodes = 0
    while pending:
        try:
            item, depth = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        nodes += 1
        if depth > 32 or nodes > MAX_BYTES:
            return False
        if isinstance(item, dict):
            if len(item) > MAX_BYTES or any(not isinstance(key, str) for key in item):
                return False
            size += 2
            pending.append(zip(chain.from_iterable(item.items()), repeat(depth + 1), strict=False))
        elif isinstance(item, list):
            if len(item) > MAX_BYTES:
                return False
            size += 2
            pending.append(zip(item, repeat(depth + 1), strict=False))
        elif isinstance(item, str):
            if len(item) > MAX_BYTES:
                return False
            size += len(item.encode("utf-8")) + 2
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
            size += len(str(item))
        elif isinstance(item, int):
            if item.bit_length() > 4096:
                return False
            size += len(str(item))
        elif item is None:
            size += 4
        else:
            return False
        if size > MAX_BYTES:
            return False
    return True


def serialize_compiled(report: dict) -> str:
    """The exact stdout representation, including its single final newline."""
    try:
        text = _wire(report)
        size = len(text.encode("utf-8"))
        maximum = report["budget"]["max_bytes"]
        if type(maximum) is not int or not 1 <= maximum <= MAX_BYTES or size > maximum:
            raise CompiledContextError("budget_too_small")
        return text
    except (KeyError, TypeError, UnicodeError, RecursionError, ValueError) as error:
        if isinstance(error, CompiledContextError):
            raise
        raise CompiledContextError("invalid_report") from None


def _recipe(targets, query, ranking, max_docs, max_bytes, agent, allowed) -> dict:
    if (targets is not None and (not isinstance(targets, list) or len(targets) > MAX_DOCS
            or any(not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in targets))):
        raise CompiledContextError("invalid_inputs")
    if query is not None and (not isinstance(query, str) or len(query) > 4096):
        raise CompiledContextError("invalid_inputs")
    if ranking not in {"substring", "bm25", "hybrid"} or type(max_docs) is not int or not 1 <= max_docs <= MAX_DOCS:
        raise CompiledContextError("invalid_inputs")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise CompiledContextError("invalid_inputs")
    if max_bytes < MIN_MAX_BYTES:
        raise CompiledContextError("budget_too_small")
    if agent not in AGENT_PREAMBLES:
        raise CompiledContextError("invalid_inputs")
    allowed = {"public"} if allowed is None else allowed
    if (not isinstance(allowed, (list, set, frozenset))
            or any(not isinstance(value, str) or value not in SENSITIVITY_LEVEL for value in allowed)):
        raise CompiledContextError("invalid_inputs")
    result = {"targets": [value.strip() for value in (targets or [])], "query": query,
              "ranking": ranking, "max_docs": max_docs, "max_bytes": max_bytes,
              "agent": agent, "allowed_sensitivities": sorted(set(allowed))}
    if not result["targets"] and not (query or "").strip():
        raise CompiledContextError("invalid_inputs")
    try:
        _wire(result).encode("utf-8")
    except UnicodeError:
        raise CompiledContextError("invalid_inputs") from None
    return result


def _safe_path(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
        return (path.resolve().is_relative_to(root)
                and not any((root / Path(*relative.parts[:i])).is_symlink()
                            for i in range(1, len(relative.parts) + 1)))
    except (ValueError, OSError, RuntimeError):
        return False


def _native_global_ledger(root: Path, path: Path) -> bool:
    try:
        return path.relative_to(root).as_posix().casefold() in _NATIVE_GLOBAL_LOGS
    except ValueError:
        return False


def _read(path: Path, *, limit: int | None = None) -> bytes:
    bound = MAX_SOURCE_BYTES if limit is None else min(MAX_SOURCE_BYTES, limit)
    with open_regular_binary(path) as handle:
        raw = handle.read(bound + 1)
    if len(raw) > bound:
        raise CompiledContextError("input_too_large")
    return raw


def _metadata(path: Path):
    """Refresh the label without validating or materializing a denied body."""
    with open_regular_binary(path) as handle:
        raw = handle.read(64 * 1024)
    raw = raw.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not raw.startswith(b"---\n"):
        return load_note(path, text="")
    close = re.compile(rb"^---$", re.MULTILINE).search(raw, 4)
    return load_note(path, text=raw[:close.end()].decode("utf-8") if close else "---\n")



def _impact_header_grant(root: Path, paths: list[Path], allowed: set[str], inherited, *, reader=None, headers=None,
                         captured_rows: tuple | None = None):
    """Admit owned declarations before arbitrary card bodies or capture limits.

    C/O JSON bodies are bounded native dependency metadata. Ordinary document
    bodies are unnecessary here; final capture still revalidates full bytes.
    This helper never discovers a path outside the supplied path grant, apart
    from the existing compiler's explicit granted-ID register refresh contract.
    ``captured_rows`` opts internal snapshot readers out of that refresh, even
    when the supplied row grant is empty.
    """
    from .impact_contract import is_impact_note, parse_impact_note
    from .claim_ledger import MAX_CLAIM_BYTES
    from .outcome_ledger import MAX_OUTCOME_BYTES, _parse_text
    from .lint import _decision_own_id
    read = _read if reader is None else reader
    levels = {SENSITIVITY_LEVEL[name] for name in allowed}
    if captured_rows is not None:
        paths = [path for path in paths if path.relative_to(root).as_posix().casefold() != "00-context/evidence-register.md"]
    if headers is None:
        headers = {}
        for path in paths:
            if not _safe_path(root, path):
                continue
            try:
                headers[path] = _metadata(path)
            except (FileNotFoundError, UnicodeError):
                continue
    if captured_rows is None and not any(is_impact_note(note) for note in headers.values()):
        return None
    early = {}
    register_paths = []
    if captured_rows is not None:
        active, retired, occurrences = captured_rows
        per_row = True
        ids = set(active) | set(retired)
    else:
        register_notes = []
        register_paths = [path for path in paths if path.relative_to(root).as_posix().casefold() == "00-context/evidence-register.md"]
        if inherited is not None and not register_paths and (inherited[0] or inherited[1]):
            register_paths = [root / "00-context/evidence-register.md"]
        for path in register_paths:
            if not _safe_path(root, path):
                continue
            try:
                header = headers.get(path) or _metadata(path)
                if note_sensitivity_level(header) > max(levels, default=-1):
                    continue
                raw = read(path)
                register = load_note(path, text=raw.decode("utf-8-sig"))
                register_notes.append(register)
                early[path] = raw
            except (FileNotFoundError, UnicodeError):
                continue
        registers = VaultIndex(root, register_notes, {note.path: note for note in register_notes}, {}, confined=True)
        active, retired, occurrences, per_row = classified_evidence(registers)
        ids = set(inherited[0]) | set(inherited[1]) if inherited is not None else set(active) | set(retired)
    active = {key: row for key, row in active.items() if key in ids and row.get("sensitivity") in allowed}
    retired = {key: row for key, row in retired.items() if key in ids and row.get("sensitivity") in allowed}
    permitted = set(active) | set(retired)
    rows = (active, retired, [(key, line) for key, line in occurrences if key in permitted])
    notes = []
    for path in paths:
        current = headers.get(path)
        if current is None or note_sensitivity_level(current) not in levels:
            continue
        if (per_row or inherited is not None) and path in register_paths:
            continue
        legacy_body_id = current.front.get("type") == "decision" and _decision_own_id(current) is None
        if is_impact_note(current) and not legacy_body_id:
            try:
                parse_impact_note(current, root)
            except ValueError:
                # Keep the identity conflict in the pure closure, without ever
                # treating a dual/malformed native holder as a C/O payload.
                notes.append(current)
                continue
        # A strict C's structured source_ids equals its eventual JSON links.
        # Deny known omitted sources before reading quoted native payloads.
        if is_claim_note(current):
            sources = current.front.get("source_ids")
            if not isinstance(sources, list) or any(not isinstance(eid, str) or eid not in permitted for eid in sources):
                notes.append(current)
                continue
        needs_body = is_claim_note(current) or is_outcome_note(current)
        needs_body = needs_body or legacy_body_id
        if needs_body:
            try:
                bound = MAX_CLAIM_BYTES if is_claim_note(current) else MAX_OUTCOME_BYTES if is_outcome_note(current) else MAX_SOURCE_BYTES
                raw = read(path, limit=bound)
                current = load_note(path, text=raw.decode("utf-8-sig"))
                if note_sensitivity_level(current) not in levels:
                    continue
                if is_claim_note(current) and not claim_visibility(current, permitted, root=root):
                    continue
                if is_outcome_note(current):
                    _parse_text(current.text)
                early[path] = raw
            except (ValueError, FileNotFoundError, UnicodeError):
                continue
        notes.append(current)
    index = VaultIndex(root, notes, {note.path: note for note in notes}, _build_index(notes), confined=True)
    index.derived["evidence_register"] = rows
    admitted = visible_knowledge_index(index, permitted)
    retained = {note.path for note in admitted.notes}
    return admitted, {path: raw for path, raw in early.items() if path in retained}, rows

def _capture(root: Path, allowed: set[str], grant: VaultIndex | None, today: dt.date):
    # Do not inherit a host's persistent parse-cache writes.
    with reuse_notes(NoteCache()):
        source = grant if grant is not None else VaultIndex.load(root)
    if source.root.resolve() != root:
        raise CompiledContextError("invalid_grant")
    levels = {SENSITIVITY_LEVEL[name] for name in allowed}
    from .impact_contract import is_impact_note
    preflight = None
    if any(is_impact_note(note) for note in source.notes):
        preflight = _impact_header_grant(root, [note.path for note in source.notes], allowed,
                                         source.derived.get("evidence_register"))
        if preflight is not None:
            source = preflight[0]
    notes, captured_bytes = [], {}
    for note in source.notes:
        if _native_global_ledger(root, note.path) and note.path.relative_to(root).as_posix() not in _NATIVE_GLOBAL_LOGS:
            # Noncanonical aliases are withheld, never treated as receipt sources.
            continue
        if grant is None and note_sensitivity_level(note) not in levels:
            continue
        if not _safe_path(root, note.path):
            continue
        try:
            if grant is not None and note_sensitivity_level(_metadata(note.path)) not in levels:
                continue
            raw = preflight[1].get(note.path) if preflight is not None else None
            raw = _read(note.path) if raw is None else raw
            current = load_note(note.path, text=raw.decode("utf-8").removeprefix("\ufeff"))
        except (FileNotFoundError, UnicodeError):
            continue
        if note_sensitivity_level(current) not in levels:
            continue
        notes.append(current)
        captured_bytes[note.path] = raw
    captured = VaultIndex(root=root, notes=notes, by_path={note.path.resolve(): note for note in notes},
                          link_index=_build_index(notes), confined=True)
    # Classify before dropping raw per-row registers; preserve a caller's row grant.
    inherited = source.derived.get("evidence_register") if grant is not None else None
    if preflight is not None:
        active, retired, occurrences = preflight[2]
        per_row = True
    elif inherited is None:
        active, retired, occurrences, per_row = classified_evidence(captured)
    else:
        active, retired, occurrences = inherited
        per_row = True
        # Refresh only the explicitly granted row IDs, never the whole caller grant.
        ids = set(active) | set(retired)
        path = root / "00-context/evidence-register.md"
        if ids and path.exists():
            if not _safe_path(root, path):
                raise CompiledContextError("unavailable_inputs")
            register = load_note(path, text=_read(path).decode("utf-8").removeprefix("\ufeff"))
            register_index = VaultIndex(root=root, notes=[register], by_path={path.resolve(): register}, link_index={})
            fresh_active, fresh_retired, _, _ = classified_evidence(register_index)
            active = {key: row for key, row in fresh_active.items() if key in ids}
            retired = {key: row for key, row in fresh_retired.items() if key in ids}
        else:
            active, retired = {}, {}
    def visible_rows(rows):
        return {key: {field: value for field, value in row.items() if field in _ROW_KEYS}
                for key, row in rows.items() if row.get("sensitivity") in allowed}
    active, retired = visible_rows(active), visible_rows(retired)
    register_paths = {note.path for note in evidence_register_notes(captured)}
    permitted_evidence = set(active) | set(retired)
    visible = captured.subset(lambda note: not (per_row and note.path in register_paths)
                             and (not is_claim_note(note) or claim_visibility(note, permitted_evidence, root=root)))
    dependencies, claim_states, denied = [], {}, set()
    for note in visible.notes:
        if not is_claim_note(note):
            continue
        record, payload = _parse_claim_text(note.text)
        observations = []
        for evidence_id, version in sorted({(link["evidence_id"], link["source_version"]) for link in payload["links"]}):
            observation = read_source_observation(root, evidence_id, version, today=today,
                                                  allowed_sensitivities=allowed)
            if observation is None:
                denied.add(note.path)
                break
            observations.append([evidence_id, version, observation])
        if note.path in denied:
            continue
        claim_states[record["claim_id"]] = {item[2]["state"] for item in observations}
        dependencies.append([visible.relative(note.path),
                             [[eid, version, state["ledger_sha256"], sorted(state["capture_sha256"].items())]
                              for eid, version, state in observations]])
    visible = visible.subset(lambda note: note.path not in denied)
    visible.derived["evidence_register"] = (active, retired, [(key, 0) for key in sorted(set(active) | set(retired))])
    visible = visible_knowledge_index(visible, permitted_evidence)
    review_log = visible.note_for(root / "00-context/review-log.md")
    reviews, history_available, incomplete = project_review_rows(review_log, visible, scoped=True)
    decision_log = visible.note_for(root / "06-decisions/decision-log.md")
    decision_rows = []
    if decision_log is not None:
        for row in decision_log_rows(root, text=decision_log.text):
            target, ambiguous = visible.resolve_link(str(row["record"]))
            ledger_note = visible.note_for(target) if target is not None and not ambiguous else None
            if (ledger_note is not None and canonical_decision_id(ledger_note, root) == row["id"]
                    and re.sub(r"(?i)\.md$", "", str(row["record"]).strip())
                    == re.sub(r"(?i)\.md$", "", visible.relative(ledger_note.path))):
                decision_rows.append({key: value for key, value in row.items() if key != "line"})
    visible = visible.subset(lambda note: not _native_global_ledger(root, note.path))
    hashes = {note.path: hashlib.sha256(captured_bytes[note.path]).hexdigest() for note in visible.notes}
    # Synthetic line numbers cannot disclose insertion/deletion of hidden register rows.
    rows = ({key: {**row, "line": "0"} for key, row in active.items()},
            {key: {**row, "line": "0"} for key, row in retired.items()},
            [(key, 0) for key in sorted(set(active) | set(retired))])
    snapshot = {"documents": [[visible.relative(note.path), hashes[note.path]] for note in visible.notes],
                "active_evidence": active, "retired_evidence": retired, "claim_sources": sorted(dependencies),
                "review_history": {"available": history_available,
                                   "incomplete": sorted(visible.relative(path) for path in incomplete),
                                   "rows": [[visible.relative(event["target"]), event["sha256"], event["occurrence"]] for event in reviews]},
                "decision_ledger": {"available": decision_log is not None, "rows": decision_rows}}
    snapshot["documents"].sort()
    return visible, rows, _hash(snapshot), hashes, claim_states, (reviews, history_available, incomplete, decision_rows, decision_log is not None)


def _seal(report: dict) -> dict:
    report["manifest"]["report_sha256"] = "0" * 64
    for _ in range(8):
        size = len(_wire(report).encode("utf-8"))
        if report["budget"]["used_bytes"] == size:
            break
        report["budget"]["used_bytes"] = size
    unsigned = {**report, "manifest": {key: value for key, value in report["manifest"].items()
                                       if key != "report_sha256"}}
    report["manifest"]["report_sha256"] = _hash(unsigned)
    return report


def _refusal(max_bytes: int, error: str = "budget_too_small") -> dict:
    report = {"format": FORMAT, "contract_version": 1, "status": "refused", "error": error,
              "budget": {"max_bytes": max_bytes}}
    serialize_compiled(report)
    return report


def _compile(root: Path, inputs: dict, today: dt.date, grant: VaultIndex | None) -> dict:
    if not is_vault_root(root) or (root / "whykit.toml").is_symlink():
        raise CompiledContextError("unavailable_inputs")
    policy_path = root / "whykit.toml"
    config = parse_config(_read(policy_path).decode("utf-8") if policy_path.exists() else "")
    visible, rows, snapshot_hash, hashes, claim_states, review_data = _capture(root, set(inputs["allowed_sensitivities"]), grant, today)
    metadata = context_metadata(visible, review_data[0], review_data[1], review_data[2], rows, claim_states, config, today, hashes)
    # Legacy context/lint must see captured scoped rows, never fall back to a raw log.
    scoped_text = ("---\ntitle: Scoped review history\ntype: reference\nstatus: draft\nowner: Local reader\n"
                   "created: 2000-01-01\nlast_updated: 2000-01-01\nsource_of_truth: false\nsensitivity: public\n---\n"
                   "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |\n|---|---|---|---|---|---|---|\n")
    scoped_text += "".join("| " + " | ".join(_table_cell(cell) for cell in event["cells"]) + " |\n" for event in review_data[0])
    notes = [*visible.notes, load_note(root / "00-context/review-log.md", text=scoped_text)]
    decision_text = "---\ntitle: Scoped decision log\n---\n| ID | Title | Date | Owner | Status | Record |\n|---|---|---|---|---|---|\n"
    decision_text += "".join("| " + " | ".join(_table_cell(str(row[key])) for key in ("id", "title", "date", "owner", "status"))
                             + " | [[" + _table_cell(str(row["record"])) + "]] |\n" for row in review_data[3])
    notes.append(load_note(root / "06-decisions/decision-log.md", text=decision_text))
    safe_view = VaultIndex(root, notes, {note.path.resolve(): note for note in notes}, _build_index(notes), confined=True)
    query_view = visible.subset(lambda note: metadata[note.path]["applicability"]["state"] == "not_decision"
                                or metadata[note.path]["applicability"]["premise"])
    withheld = []
    hybrid = None
    hybrid_matches: set[str] = set()
    with evidence_view(root, rows):
        hits = []
        if inputs["query"]:
            if inputs["ranking"] == "hybrid":
                from .hybrid_retrieval import rank_prepared

                hybrid = rank_prepared(query_view, inputs["query"], rows=rows, metadata=metadata,
                                       document_hashes=hashes, valid_on=today,
                                       allowed_sensitivities=set(inputs["allowed_sensitivities"]), limit=100,
                                       _matched_paths=hybrid_matches)
                if hybrid["status"] == "refused":
                    return _refusal(inputs["max_bytes"], "scope_too_large")
                hits = hybrid["results"]
            elif inputs["ranking"] == "bm25":
                hits = retrieve_bm25(root, inputs["query"], limit=len(visible.notes), vault=query_view)
            else:
                hits = query_vault(root, text=inputs["query"], limit=len(visible.notes), vault=query_view)["results"]
        selected = []
        for target in inputs["targets"]:
            resolved, _ = safe_view.resolve_link(target)
            if resolved is not None and _native_global_ledger(root, resolved):
                withheld.append({"target": target, "origin": "explicit", "reason": "missing"})
            else:
                selected.append(target)
        selected += [item["path"] for item in hits]
        pack = build_pack(root, targets=selected, max_docs=inputs["max_docs"],
                          max_chars=inputs["max_bytes"], agent=inputs["agent"], vault=safe_view)
        # Context v1 uses the wall clock. Recompute its warnings with the explicit compiler date.
        _, findings = lint(root, orphans=False, secrets=False, today=today, vault=safe_view, config=config)
    contexts, citations, paths = [], {}, set()
    for item in pack["contexts"]:
        record = item.get("record") or {}
        path = record.get("path")
        if path:
            paths.add(path)
            note = visible.note_for(root / path)
            citations["document:" + path] = {"kind": "document", "target": path, "sha256": hashes[note.path]}
            decision_id = metadata[note.path]["decision_id"]
            if isinstance(decision_id, str) and re.fullmatch(r"D-[0-9]{3,}", decision_id):
                record = {**record, "decision_id": decision_id}
            claim_id = note.front.get("claim_id")
            if is_claim_note(note) and isinstance(claim_id, str):
                record = {**record, "claim_id": claim_id}
        evidence = item.get("evidence", [])
        evidence = [evidence] if isinstance(evidence, dict) else evidence
        evidence = [{**entry, "record": {key: value for key, value in entry["record"].items() if key != "line"}
                     if entry.get("record") is not None else None} for entry in evidence]
        for entry in evidence:
            if entry.get("record") is not None:
                citations["evidence:" + entry["id"]] = {
                    "kind": "evidence", "target": entry["id"], "sha256": _hash(entry["record"])}
        details = metadata.get(note.path) if path else None
        if details is None:
            details = {"applicability": {"as_of": today.isoformat(), "state": "not_decision", "premise": False,
                                       "local_approval": "not_applicable", "declared_validity": {"state": "unknown", "valid_from": None, "valid_until": None},
                                       "reasons": ["not_decision"]},
                       "history": {"supersedes": [], "superseded_by": [], "reviews": [], "unavailable": not review_data[1], "truncated": False}}
        for event in details["history"]["reviews"]:
            key = "review:" + event["row_sha256"] + ":" + str(event["occurrence"])
            citations[key] = {"kind": "review", "target": "00-context/review-log.md#" + key[7:], "sha256": event["row_sha256"]}
        for relation in [*details["history"]["supersedes"], *details["history"]["superseded_by"]]:
            citations["document:" + relation["path"]] = {"kind": "document", "target": relation["path"], "sha256": hashes[root / relation["path"]]}
        contexts.append({"target": item["target"], "origin": "explicit" if item["target"] in inputs["targets"] else "query",
                         "kind": item["kind"], "record": record, "content": item.get("content", ""),
                         "content_truncated": bool(item.get("content_truncated")), "evidence": evidence,
                         "applicability": details["applicability"], "history": details["history"]})
        hit = next((hit for hit in hits if hit["path"] == path), None) if hybrid is not None else None
        if hit is not None and contexts[-1]["origin"] == "query":
            contexts[-1]["retrieval"] = {key: hit[key] for key in (
                "score", "lexical_score", "lexical_normalized", "graph_support", "explanation")}
            explanation = hit["explanation"]
            if explanation is not None:
                for node_id in explanation["nodes"]:
                    kind, target = node_id.split(":", 1)
                    if kind == "document":
                        citations[node_id] = {"kind": kind, "target": target, "sha256": hashes[root / target]}
                    elif kind == "evidence":
                        row = rows[0].get(target) or rows[1][target]
                        citations[node_id] = {"kind": kind, "target": target,
                                              "sha256": _hash({key: value for key, value in row.items() if key != "line"})}
    log_available = review_data[4]
    warnings = [{"path": item.path, "code": item.code, "level": item.level, "line": item.line}
                for item in findings if item.path in paths
                and (log_available or not item.code.startswith("decision_log."))]
    if not log_available:
        # An unavailable captured ledger cannot establish indexing diagnostics.
        warnings.extend({"path": item["record"]["path"], "code": "compiled.decision_log_unavailable",
                         "level": "warning", "line": None}
                        for item in contexts if item["record"].get("decision_id"))
    for path in sorted(paths):
        note = visible.note_for(root / path)
        if is_outcome_note(note):
            warnings.append({"path": path, "code": "compiled.outcome_values_unverified",
                             "level": "warning", "line": None})
        claim_ids = note.front.get("claim_ids", [])
        claim_ids = claim_ids if isinstance(claim_ids, list) else []
        claim_ids = [item for item in claim_ids if isinstance(item, str)]
        if is_claim_note(note):
            claim_ids.append(note.front["claim_id"])
        states = {state for claim_id in claim_ids for state in claim_states.get(claim_id, set())}
        warnings.extend({"path": path, "code": "compiled.source_observation_" + state,
                         "level": "warning", "line": None} for state in sorted(states - {"current"}))
    selected_evidence = {entry["target"] for entry in citations.values() if entry["kind"] == "evidence"}
    for finding in findings:
        if finding.path.casefold() != "00-context/evidence-register.md":
            continue
        match = re.match(r"E-[0-9]{3,}(?![0-9])", finding.message)
        if match and match.group() in selected_evidence:
            warnings.append({"path": "evidence:" + match.group(), "code": finding.code,
                             "level": finding.level, "line": None})
    warnings.sort(key=lambda item: (item["path"], item["code"], item["level"],
                                    -1 if item["line"] is None else item["line"]))
    report = {"format": FORMAT, "contract_version": 1, "status": "compiled", "as_of": today.isoformat(), "inputs": inputs,
              "preamble": pack["preamble"], "content_trust": "untrusted_data", "execution_authorized": False,
              "contexts": contexts, "citations": [citations[key] for key in sorted(citations)], "warnings": warnings,
              "omissions": {"unresolved": [*pack["missing"], *withheld],
                            "explicit_targets": [target for target in inputs["targets"] if target not in pack["selected"]],
                            "query_matches": sum(item["path"] not in paths for item in hits)},
              "budget": {"max_bytes": inputs["max_bytes"], "used_bytes": 0},
              "manifest": {"snapshot_sha256": snapshot_hash, "policy_sha256": _hash(config), "report_sha256": ""}}
    if inputs["ranking"] == "hybrid":
        # Empty query recipes still bind the same declared retrieval limits.
        if hybrid is None:
            from .hybrid_retrieval import rank_prepared

            hybrid = rank_prepared(query_view, "", rows=rows, metadata=metadata, document_hashes=hashes,
                                   valid_on=today, allowed_sensitivities=set(inputs["allowed_sensitivities"]), limit=100,
                                   _matched_paths=hybrid_matches)
            if hybrid["status"] == "refused":
                return _refusal(inputs["max_bytes"], "scope_too_large")
        report["retrieval"] = {key: hybrid[key] for key in ("limits", "truncation")}
        report["omissions"]["query_matches"] = len(hybrid_matches - paths)
    bodies = [item["content"] for item in contexts]
    for item, body in zip(contexts, bodies, strict=True):
        item["content"] = ""
        item["content_truncated"] = item["content_truncated"] or bool(body)
    _seal(report)
    if report["budget"]["used_bytes"] > inputs["max_bytes"]:
        return _refusal(inputs["max_bytes"])
    for item, body, original in zip(contexts, bodies, pack["contexts"], strict=True):
        low, high = 0, len(body)
        while low < high:
            middle = (low + high + 1) // 2
            item["content"] = body[:middle]
            item["content_truncated"] = bool(original.get("content_truncated")) or middle < len(body)
            _seal(report)
            if report["budget"]["used_bytes"] <= inputs["max_bytes"]:
                low = middle
            else:
                high = middle - 1
        item["content"] = body[:low]
        item["content_truncated"] = bool(original.get("content_truncated")) or low < len(body)
        _seal(report)
    return report


def compile_context(root: Path, *, targets: list[str] | None = None, query: str | None = None,
                    ranking: str = "substring", max_docs: int = 8, max_bytes: int = 32768,
                    today: dt.date | None = None, agent: str = "generic",
                    allowed_sensitivities: set[str] | None = None, vault: VaultIndex | None = None) -> dict:
    """Compile current visible inputs; the byte cap includes metadata and newline."""
    try:
        inputs = _recipe(targets, query, ranking, max_docs, max_bytes, agent, allowed_sensitivities)
        today = dt.date.today() if today is None else today
        if type(today) is not dt.date:
            raise CompiledContextError("invalid_inputs")
        root = Path(root).resolve(strict=True)
        with vault_read_lock(root), path_cache(fresh=True):
            return _compile(root, inputs, today, vault)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError) as error:
        if isinstance(error, CompiledContextError):
            raise
        raise CompiledContextError("unavailable_inputs") from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CompiledContextError("invalid_report")
        result[key] = value
    return result


def _nonfinite(_value):
    raise CompiledContextError("invalid_report")


def verify_compiled(root: Path, supplied: Any, *, today: dt.date | None = None,
                    allowed_sensitivities: set[str] | None = None, vault: VaultIndex | None = None,
                    **expected) -> dict:
    """Recompile the unsigned recipe; no signature, identity or action authority."""
    result = {"format": "whykit.compiled-context-verification/v1", "contract_version": 1, "status": "refused",
              "reason": "invalid_report", "execution_authorized": False, "signature_verified": False}
    try:
        if isinstance(supplied, (str, bytes)):
            raw = supplied.encode("utf-8") if isinstance(supplied, str) else supplied
            if len(raw) > MAX_BYTES:
                return result
            supplied = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if not isinstance(supplied, dict) or set(supplied) not in (_REPORT_KEYS, _REPORT_KEYS | {"retrieval"}):
            return result
        if not _bounded(supplied):
            return result
        if len(_wire(supplied).encode("utf-8")) > MAX_BYTES:
            return result
        if (supplied["format"] != FORMAT or type(supplied["contract_version"]) is not int
                or supplied["contract_version"] != 1 or supplied["status"] != "compiled"
                or supplied["execution_authorized"] is not False):
            return result
        inputs = supplied["inputs"]
        if not isinstance(inputs, dict) or set(inputs) != {"targets", "query", "ranking", "max_docs", "max_bytes", "agent", "allowed_sensitivities"}:
            return result
        if (inputs.get("ranking") == "hybrid") != ("retrieval" in supplied):
            return result
        normalized = _recipe(inputs["targets"], inputs["query"], inputs["ranking"], inputs["max_docs"],
                             inputs["max_bytes"], inputs["agent"], inputs["allowed_sensitivities"])
        if normalized != inputs or set(supplied["manifest"]) != {"snapshot_sha256", "policy_sha256", "report_sha256"}:
            return result
        if any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in supplied["manifest"].values()):
            return result
        if set(supplied["budget"]) != {"max_bytes", "used_bytes"} or supplied["budget"]["max_bytes"] != inputs["max_bytes"]:
            return result
        if type(supplied["budget"]["used_bytes"]) is not int or len(serialize_compiled(supplied).encode("utf-8")) != supplied["budget"]["used_bytes"]:
            return result
        unsigned = {**supplied, "manifest": {key: value for key, value in supplied["manifest"].items() if key != "report_sha256"}}
        if _hash(unsigned) != supplied["manifest"]["report_sha256"]:
            return {**result, "status": "stale", "reason": "report_changed"}
        allowed = {"public"} if allowed_sensitivities is None else allowed_sensitivities
        if sorted(allowed) != inputs["allowed_sensitivities"]:
            return {**result, "reason": "scope_mismatch"}
        if set(expected) - {"targets", "query", "ranking", "max_docs", "max_bytes", "agent"}:
            return result
        if any(inputs[key] != value for key, value in expected.items()):
            return {**result, "status": "stale", "reason": "parameters_changed"}
        today = dt.date.today() if today is None else today
        if type(today) is not dt.date:
            return result
        if supplied["as_of"] != today.isoformat():
            return {**result, "status": "stale", "reason": "as_of_changed"}
        current = compile_context(root, today=today, allowed_sensitivities=set(allowed), vault=vault,
                                  **{key: value for key, value in inputs.items() if key != "allowed_sensitivities"})
        return {**result, "status": "current" if current == supplied else "stale",
                "reason": "matches_current_visible_inputs" if current == supplied else "inputs_changed"}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError):
        return result
