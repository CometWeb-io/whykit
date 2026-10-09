"""Shared fail-closed sensitivity classification for readers and exports."""
from __future__ import annotations

from typing import Any
from collections import Counter, defaultdict, deque

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
