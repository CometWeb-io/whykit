"""Markdown table parsing shared by ledger writers, gates and exports."""
from __future__ import annotations

import re
from collections.abc import Sequence

REVIEW_COLUMNS = ("date", "target", "reviewer", "outcome", "previous review", "next review", "note")
EVIDENCE_COLUMNS = {
    "active": ("id", "source", "type", "date", "accessed", "location", "claims it supports"),
    "retired": ("id", "source", "retired on", "why", "replaced by"),
}


def evidence_table_bounds(lines: Sequence[str], mode: str) -> tuple[int, int, bool]:
    """Locate a legacy table or one with the optional final Sensitivity column."""
    expected = EVIDENCE_COLUMNS[mode]
    fence: str | None = None
    for index, line in enumerate(lines):
        opener = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opener:
            mark = opener.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        cells = tuple(cell.casefold() for cell in split_table_row(line))
        if cells not in (expected, (*expected, "sensitivity")):
            continue
        if index + 1 >= len(lines):
            break
        separator = split_table_row(lines[index + 1])
        if len(separator) != len(cells) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            break
        end = index + 2
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        return index, end, len(cells) > len(expected)
    raise ValueError(f"expected a valid {mode} evidence table")


def split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    buf: list[str] = []
    wiki_depth = 0
    # Searching for a closer after every opener made a row of `[[` quadratic;
    # a closer exists later on the line exactly when the last one is further on.
    last_close = text.rfind("]]")
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\\" and nxt == "|":
            buf.append("|")
            i += 2
            continue
        # Only a `[[` that is closed later on the line opens a wikilink. A stray
        # `[[` in free text must not swallow every following cell separator.
        if ch == "[" and nxt == "[" and last_close >= i + 2:
            wiki_depth += 1
            buf.extend((ch, nxt))
            i += 2
            continue
        if ch == "]" and nxt == "]" and wiki_depth:
            wiki_depth -= 1
            buf.extend((ch, nxt))
            i += 2
            continue
        if ch == "|" and wiki_depth == 0:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells



def review_table_header(lines: Sequence[str]) -> int | None:
    """Find a review header with seven valid separator cells, despite padding."""
    fence: str | None = None
    for index, line in enumerate(lines):
        opener = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opener:
            mark = opener.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None or not line.lstrip().startswith("|"):
            continue
        if tuple(cell.lower() for cell in split_table_row(line)) != REVIEW_COLUMNS:
            continue
        if index + 1 >= len(lines):
            return None
        cells = split_table_row(lines[index + 1])
        return index if len(cells) == len(REVIEW_COLUMNS) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells) else None
    return None
