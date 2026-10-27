"""Pure scoped review rows shared by history consumers; never a whole-log digest."""
from __future__ import annotations

from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

from .lint import Note
from .markdown_code import mask_code
from .tables import REVIEW_COLUMNS, review_table_header, split_table_row
from .vault_index import VaultIndex


def latest_review_event(events: list[dict], today: dt.date) -> dict | None:
    """Latest declared date among projected rows; visible append order breaks ties."""
    current = [(at, event) for at, event in enumerate(events) if event["cells"][0] <= today.isoformat()]
    return max(current, key=lambda item: (item[1]["cells"][0], item[0]))[1] if current else None


def _scoped_header(lines: list[str]) -> int | None:
    masked = mask_code("\n".join(lines)).split("\n")
    for at in range(len(lines) - 1):
        if (tuple(cell.casefold() for cell in split_table_row(lines[at])) == REVIEW_COLUMNS
                and split_table_row(lines[at]) == split_table_row(masked[at])
                and split_table_row(lines[at + 1]) == split_table_row(masked[at + 1])
                and review_table_header(lines[at:at + 2]) == 0):
            return at
    return None


def project_review_rows(log: Note | None, index: VaultIndex, *, strict: bool = False,
                        scoped: bool = False) -> tuple[list[dict], bool, set[Path]]:
    """Resolve/filter before validation, hash or occurrence; no filesystem access.

    Strict mode preserves the knowledge bundle's refusal of invalid/unresolved
    rows. The incomplete set contains only resolved permitted targets.
    """
    if log is None:
        return [], False, set()
    lines = log.text.splitlines()
    at = _scoped_header(lines) if scoped else review_table_header(lines)
    if at is None:
        if strict:
            raise ValueError("unrecognized knowledge review table")
        return [], False, set()
    rows, incomplete = [], set()
    occurrences: Counter[str] = Counter()
    for line in lines[at + 2:]:
        if not line.lstrip().startswith("|"):
            break
        cells = split_table_row(line)
        match = re.fullmatch(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", cells[1]) if len(cells) >= 2 else None
        target, ambiguous = index.resolve_link(match.group(1)) if match else (None, False)
        if scoped and target is not None and not ambiguous and match is not None:
            spelling = re.sub(r"(?i)\.md$", "", match.group(1).strip())
            canonical = re.sub(r"(?i)\.md$", "", index.relative(target))
            if spelling != canonical:
                # Aliases can become unambiguous only after hidden notes vanish.
                continue
        if len(cells) != 7:
            if strict:
                raise ValueError("invalid knowledge review event")
            if target is not None and not ambiguous:
                incomplete.add(target)
            continue
        if target is None or ambiguous:
            if strict:
                raise ValueError("unresolved knowledge review target")
            continue
        try:
            if scoped and review_table_header([lines[at], lines[at + 1], line]) is None:
                raise ValueError("masked review row")
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", cells[0]) is None:
                raise ValueError("noncanonical review date")
            dt.date.fromisoformat(cells[0])
            if cells[3] not in {"approved", "confirmed", "update-required", "supersede-required", "archived"} or not cells[2].strip():
                raise ValueError("invalid review event")
        except ValueError:
            if strict:
                raise ValueError("invalid knowledge review event") from None
            incomplete.add(target)
            continue
        wire = (json.dumps(cells, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        digest = hashlib.sha256(wire).hexdigest()
        occurrences[digest] += 1
        rows.append({"cells": cells, "target": target, "sha256": digest, "occurrence": occurrences[digest]})
    return rows, True, incomplete
