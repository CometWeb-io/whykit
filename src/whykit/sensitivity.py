"""Shared fail-closed sensitivity classification for readers and exports."""
from __future__ import annotations

from typing import Any
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import functools

SENSITIVITY_LEVEL = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}

UNREADABLE_LEVEL = 99


def is_claim_note(note: Any) -> bool:
    """Pure conservative marker for native claim metadata or its reserved directory."""
    parts = tuple(part.casefold() for part in getattr(getattr(note, "path", None), "parts", ()))
    return (note.front.get("type") == "claim" or "claim_id" in note.front
            or any(parts[index:index + 2] == ("00-context", "claims") for index in range(len(parts) - 1)))


def is_outcome_note(note: Any) -> bool:
    """Native operational measurements remain restricted despite metadata downgrades."""
    parts = tuple(part.casefold() for part in getattr(getattr(note, "path", None), "parts", ()))
    return (note.front.get("type") == "outcome" or "outcome_id" in note.front
            or any(parts[index:index + 2] == ("00-context", "outcomes") for index in range(len(parts) - 1)))


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
    # Persisted claim JSON contains source quotes; a metadata downgrade must not export them.
    from .impact_contract import is_impact_note
    if is_claim_note(note) or is_outcome_note(note) or is_impact_note(note):
        level = max(level, SENSITIVITY_LEVEL["restricted"])
    return level



def note_sensitivity_label(note: Any, default: str = "") -> str:
    """Expose a native impact floor without changing its stored approved bytes."""
    from .impact_contract import is_impact_note
    if is_impact_note(note):
        level = note_sensitivity_level(note)
        return next((name for name, value in SENSITIVITY_LEVEL.items() if value == level), "unclassified")
    return str(note.front.get("sensitivity") or default)

def evidence_register_notes(index: Any) -> list[Any]:
    """Match captured register paths, including .MD and case aliases."""
    return [note for note in index.notes
            if index.relative(note.path).casefold() == "00-context/evidence-register.md"]


def classified_evidence(index: Any) -> tuple[dict, dict, list, bool]:
    """Classify rows from the same captured bytes as their register's label."""
    registers = evidence_register_notes(index)
    if len(registers) != 1:
        # Ambiguous registers must not expose any rows or raw candidate bodies.
        return {}, {}, [], bool(registers)
    return classify_evidence_register(registers[0])


def scoped_report(handler: Any = None, *, project_notes: bool = True) -> Any:
    """Opt compatible report readers into captured evidence scope propagation."""
    if handler is None:
        return lambda report: scoped_report(report, project_notes=project_notes)

    @functools.wraps(handler)
    def guarded(root: Path, *args: Any, **kwargs: Any) -> Any:
        with scoped_read(root, kwargs.get("vault"), kwargs.get("allowed_sensitivities"), project_notes=project_notes) as vault:
            if vault is not None:
                kwargs["vault"] = vault
            return handler(root, *args, **kwargs)

    return guarded


@contextmanager
def scoped_read(root: Path, vault: Any, allowed: set[str] | None, *, project_notes: bool = True) -> Iterator[Any]:
    """Bind existing scoped report APIs to captured notes and evidence together."""
    from .lint import _captured_evidence, _intersect_evidence, _real, evidence_view, path_cache
    from .vault_index import NoteCache, VaultIndex, reuse_notes

    if vault is not None and not isinstance(vault, VaultIndex):
        raise ValueError("unsupported vault index")
    with path_cache():
        parent = _captured_evidence(root)
        supplied = vault if isinstance(vault, VaultIndex) else None
        if allowed is None and parent is None and (supplied is None or not supplied.confined and "evidence_register" not in supplied.derived):
            if not project_notes:
                yield vault
                return
            from .impact_contract import is_impact_note
            with reuse_notes(NoteCache()):
                supplied = supplied if supplied is not None else VaultIndex.load(root)
            if not any(is_impact_note(note) for note in supplied.notes):
                yield supplied
                return
        # A caller's persistent parse-cache setting must not turn a scoped read
        # or its nested report calls into writes to the vault.
        with reuse_notes(NoteCache()):
            index = supplied if supplied is not None else VaultIndex.load(root)
            if _real(index.root) != _real(root):
                raise ValueError("vault index belongs to a different root")
            active, retired, occurrences, per_row = classified_evidence(index)
            captured = (active, retired, occurrences)
            rows = index.derived.get("evidence_register", captured if evidence_register_notes(index) or parent is None else parent)
            if parent is not None:
                rows = _intersect_evidence(parent, rows)
            permitted = set(SENSITIVITY_LEVEL) if allowed is None else set(SENSITIVITY_LEVEL).intersection(allowed)
            levels = {SENSITIVITY_LEVEL[name] for name in permitted}
            active = {key: dict(row) for key, row in rows[0].items() if str(row.get("sensitivity") or "internal").lower() in permitted}
            retired = {key: dict(row) for key, row in rows[1].items() if str(row.get("sensitivity") or "internal").lower() in permitted}
            ids = set(active) | set(retired)
            rows = (active, retired, [(key, line) for key, line in rows[2] if key in ids])
            registers = {note.path for note in evidence_register_notes(index)}
            # Even a homogeneous register body is outside a sparse row grant.
            # Row payload equality also prevents captured A from refreshing to B.
            withhold_register = per_row or any(
                {key: {field: value for field, value in row.items() if field != "line"}
                 for key, row in original.items()}
                != {key: {field: value for field, value in row.items() if field != "line"}
                    for key, row in granted.items()}
                for original, granted in ((captured[0], active), (captured[1], retired))
            )

            def keep(note: Any) -> bool:
                path = index.relative(note.path).replace("\\", "/").casefold()
                if path in {"06-decisions/decision-log.md", "00-context/review-log.md"}:
                    return False
                if note.path in registers and withhold_register:
                    return False
                return allowed is None or note_sensitivity_level(note) in levels

            # Lint also accepts deliberate projected/synthetic log notes. It
            # needs the row snapshot, without changing that supplied note view.
            scoped = index.subset(keep if project_notes else lambda note: True)
            scoped.derived["evidence_register"] = rows
            if project_notes:
                from .knowledge_visibility import visible_knowledge_index
                scoped = visible_knowledge_index(scoped, ids)
            with evidence_view(root, rows):
                yield scoped


def classify_evidence_register(note: Any) -> tuple[dict, dict, list, bool]:
    """Classify one captured register with duplicate and replacement-chain floors."""
    from .lint import _parse_evidence_register_text
    from .tables import split_table_row

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
