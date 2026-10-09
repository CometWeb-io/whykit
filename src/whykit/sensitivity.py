"""Shared fail-closed sensitivity classification for readers and exports."""
from __future__ import annotations

from typing import Any
from collections import Counter, defaultdict, deque
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vault_index import VaultIndex

SENSITIVITY_LEVEL = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}

UNREADABLE_LEVEL = 99


def _sensitivity_level(value: object) -> int:
    key = str(value or "internal").lower() or "internal"
    return SENSITIVITY_LEVEL.get(key, 99)


def note_sensitivity_level(note: Any) -> int:
    """The level a note is filtered at, failing closed when its label is unreadable.

    A note without front matter, or with valid front matter that omits the
    key, is `internal` (the documented default). Front matter that does not
    parse, or a label spelt with different case or spacing (`Sensitivity:`),
    may hide a stricter label WhyKit cannot read, so such a note is treated as
    above every ceiling.
    """
    if note.front_error:
        return UNREADABLE_LEVEL
    front = note.front
    value = front.get("sensitivity")
    level = SENSITIVITY_LEVEL["internal"]
    if "sensitivity" in front:
        level = _sensitivity_level(value) if isinstance(value, str) and value else UNREADABLE_LEVEL
    for key in front:
        if key != "sensitivity" and str(key).strip().casefold() == "sensitivity":
            level = UNREADABLE_LEVEL
    return level


def classified_evidence(index: Any) -> tuple[dict, dict, list, bool]:
    """Classify rows from the same captured bytes as their register's label."""
    from .lint import _parse_evidence_register_text
    from .tables import split_table_row

    note = index.note_for(index.root / "00-context/evidence-register.md")
    if note is None:
        return {}, {}, [], False
    floor = note_sensitivity_level(note)
    names = {level: name for name, level in SENSITIVITY_LEVEL.items()}
    active, retired, occurrences = _parse_evidence_register_text(note.text)
    per_row = any("sensitivity" in row for row in (*active.values(), *retired.values()))
    for line in note.text.splitlines():
        if line.lstrip().startswith("|"):
            cells = [cell.casefold() for cell in split_table_row(line)]
            if cells and cells[0] == "id" and ("sensitivity" in cells or len(cells) not in {5, 7}):
                per_row = True
    duplicates = {key for key, count in Counter(key for key, _ in occurrences).items() if count > 1}
    per_row = per_row or bool(duplicates)
    for rows in (active, retired):
        for key, row in rows.items():
            level = max(floor, SENSITIVITY_LEVEL.get(row["sensitivity"], UNREADABLE_LEVEL)) if "sensitivity" in row else floor
            if key in duplicates:
                level = UNREADABLE_LEVEL
            row["sensitivity"] = names.get(level, "unclassified")
    rows = {**active, **retired}
    dependents: dict[str, list[str]] = defaultdict(list)
    for key, row in retired.items():
        replacement = row.get("replaced_by")
        if replacement in rows:
            dependents[replacement].append(key)
    pending = deque(rows)
    while pending:
        key = pending.popleft()
        level = SENSITIVITY_LEVEL.get(rows[key]["sensitivity"], UNREADABLE_LEVEL)
        for dependent in dependents[key]:
            if level > SENSITIVITY_LEVEL.get(rows[dependent]["sensitivity"], UNREADABLE_LEVEL):
                rows[dependent]["sensitivity"] = names.get(level, "unclassified")
                pending.append(dependent)
    return active, retired, occurrences, per_row


def _claim_marker_paths(view: dict[str, Any], text: str) -> tuple[set[Path], bool]:
    paths: set[Path] = set()
    unknown = False
    for cid in re.findall(r"(?<!\w)C-[0-9]{3,}(?!\w)", text, re.IGNORECASE):
        record = view["records"].get(cid.upper())
        if record is None:
            unknown = True
        else:
            paths.add(view["root"] / record["path"])
    digests = {value.lower() for value in re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text, re.IGNORECASE)}
    if "_snapshot_marker_paths" not in view:
        view["_snapshot_marker_paths"] = {Path(relative).stem: view["root"] / relative for relative in view["snapshots"]}
    paths.update(view["_snapshot_marker_paths"][digest] for digest in digests if digest in view["_snapshot_marker_paths"])
    return paths, unknown


def claim_dependency_levels(vault: VaultIndex, view: dict[str, Any]) -> dict[Path, int]:
    """Propagate the strictest dependency label before any filtered assessment."""
    levels = {note.path: note_sensitivity_level(note) for note in vault.notes}
    dependencies: dict[Path, set[Path]] = defaultdict(set)
    claim_paths = {cid: vault.root / record["path"] for cid, record in view["records"].items()}
    snapshots = {relative: vault.root / relative for relative in view["snapshots"]}
    for relative, snapshot in view["snapshots"].items():
        path = snapshots[relative]
        levels[path] = UNREADABLE_LEVEL if snapshot["error"] else 0
    for cid, record in view["records"].items():
        path = claim_paths[cid]
        if record["errors"] or any(error["code"] == "claim.capture_changed" for error in view["errors"]):
            levels[path] = UNREADABLE_LEVEL
        for row in record["relations"]:
            evidence = view["evidence"].get(row["evidence_id"])
            level = _sensitivity_level(evidence.get("sensitivity")) if evidence else UNREADABLE_LEVEL
            levels[path] = max(levels.get(path, UNREADABLE_LEVEL), level)
            if evidence:
                markers, unknown = _claim_marker_paths(view, "\n".join(map(str, evidence.values())))
                dependencies[path].update(markers)
                if unknown:
                    levels[path] = UNREADABLE_LEVEL
            target = snapshots.get(row["snapshot"])
            if target is None:
                levels[path] = UNREADABLE_LEVEL
            else:
                dependencies[path].add(target)
                dependencies[target].add(path)  # A shared snapshot takes every user's floor.
    for note in vault.notes:
        path = note.path
        markers, unknown = _claim_marker_paths(view, note.text)
        dependencies[path].update(markers)
        if unknown:
            levels[path] = UNREADABLE_LEVEL
        for eid in note.cited_evidence:
            row = view["evidence"].get(eid)
            levels[path] = max(levels[path], _sensitivity_level(row.get("sensitivity")) if row else UNREADABLE_LEVEL)
            if row:
                markers, unknown = _claim_marker_paths(view, "\n".join(map(str, row.values())))
                dependencies[path].update(markers)
                if unknown:
                    levels[path] = UNREADABLE_LEVEL
        for target, _, _ in note.wikilink_hits:
            if target.startswith(("https://", "http://")):
                continue
            resolved, ambiguous = vault.resolve_link(target)
            if resolved is not None and not ambiguous and resolved in levels:
                dependencies[path].add(resolved)
            else:
                levels[path] = UNREADABLE_LEVEL
    dependents: dict[Path, set[Path]] = defaultdict(set)
    for path, targets in dependencies.items():
        for target in targets:
            dependents[target].add(path)
    pending = deque(levels)
    while pending:
        target = pending.popleft()
        for dependent in dependents[target]:
            if levels[target] > levels[dependent]:
                levels[dependent] = levels[target]
                pending.append(dependent)
    return levels


def visible_claim_view(vault: VaultIndex, view: dict[str, Any], *, ceiling: str,
                       allowed_sensitivities: set[str] | None = None) -> tuple[VaultIndex, dict[str, Any]]:
    if ceiling not in SENSITIVITY_LEVEL:
        raise ValueError("unsupported sensitivity ceiling")
    levels = claim_dependency_levels(vault, view)
    maximum = SENSITIVITY_LEVEL[ceiling]
    allowed_levels = {level for name, level in SENSITIVITY_LEVEL.items()
                      if level <= maximum and (allowed_sensitivities is None or name in allowed_sensitivities)}
    visible = vault.subset(lambda note: levels.get(note.path, UNREADABLE_LEVEL) in allowed_levels)
    allowed = {note.path for note in visible.notes}
    records = {cid: record for cid, record in view["records"].items() if vault.root / record["path"] in allowed}
    evidence = {}
    for eid, row in view["evidence"].items():
        markers, unknown = _claim_marker_paths(view, "\n".join(map(str, row.values())))
        level = max([_sensitivity_level(row.get("sensitivity")), *(levels.get(path, UNREADABLE_LEVEL) for path in markers)])
        if not unknown and level in allowed_levels:
            evidence[eid] = {key: value for key, value in row.items() if key != "line"}
    snapshot_rows = {key: value for key, value in view["snapshots"].items() if levels.get(vault.root / key, UNREADABLE_LEVEL) in allowed_levels}
    allowed_targets = {"[[" + vault.relative(path).removesuffix(".md") + "]]" for path in allowed}
    projected = {"config": view["config"], "records": records, "evidence": evidence,
                 "snapshots": snapshot_rows, "review_rows": [row for row in view["review_rows"] if row[1] in allowed_targets],
                 "errors": [], "duplicate_evidence": view["duplicate_evidence"] & evidence.keys()}
    active = {eid: row for eid, row in evidence.items() if row["state"] == "active"}
    retired = {eid: row for eid, row in evidence.items() if row["state"] == "retired"}
    visible.derived["claims_view"] = projected
    visible.derived["evidence_register"] = (active, retired, [(eid, n + 1) for n, eid in enumerate(sorted(evidence))])
    return visible, projected
