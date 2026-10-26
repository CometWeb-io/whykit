"""Table rows and review-log boundaries shared by writers, lint and history."""
from __future__ import annotations

import re
from collections.abc import Sequence

from .markdown_code import mask_code

REVIEW_COLUMNS = ("date", "target", "reviewer", "outcome", "previous review", "next review", "note")


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
    """Find a review table only when its boundaries and event cells stay visible."""
    raw = [line.rstrip("\r\n") for line in lines]
    text = "\n".join(raw)
    masked = mask_code(text).splitlines()
    for index, line in enumerate(masked):
        if not line.lstrip().startswith("|") or tuple(cell.casefold() for cell in split_table_row(line)) != REVIEW_COLUMNS:
            continue
        if index + 1 >= len(masked):
            return None
        if tuple(cell.casefold() for cell in split_table_row(raw[index])) != REVIEW_COLUMNS:
            return None
        cells = split_table_row(raw[index + 1])
        if (len(cells) != len(REVIEW_COLUMNS) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
                or cells != split_table_row(masked[index + 1])):
            return None
        uncommented = mask_code(text, keep_code=True).splitlines()
        for at in range(index + 2, len(raw)):
            if not raw[at].lstrip().startswith("|"):
                break
            row = split_table_row(raw[at])
            visible = split_table_row(masked[at])
            # Notes may contain code or closed comments; event fields and pipes may not vanish.
            if (not masked[at].lstrip().startswith("|") or len(row) != len(visible)
                    or split_table_row(uncommented[at])[:6] != row[:6]
                    or (raw[at].rstrip().endswith("|") and not masked[at].rstrip().endswith("|"))):
                return None
        return index
    return None


EVIDENCE_COLUMNS = {
    "active": ("id", "source", "type", "date", "accessed", "location", "claims it supports"),
    "retired": ("id", "source", "retired on", "why", "replaced by"),
}


def evidence_table_bounds(lines: Sequence[str], mode: str) -> tuple[int, int, bool]:
    """Locate a legacy table or one with the optional final Sensitivity column."""
    expected = EVIDENCE_COLUMNS[mode]
    raw = [line.rstrip("\r\n") for line in lines]
    text = "\n".join(raw)
    masked = mask_code(text).splitlines()
    uncommented = mask_code(text, keep_code=True).splitlines()
    for index, line in enumerate(masked):
        if not line.lstrip().startswith("|"):
            continue
        cells = tuple(cell.casefold() for cell in split_table_row(line))
        if cells not in (expected, (*expected, "sensitivity")):
            continue
        if tuple(cell.casefold() for cell in split_table_row(raw[index])) != cells or index + 1 >= len(raw):
            break
        separator = split_table_row(raw[index + 1])
        if (len(separator) != len(cells) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
                or separator != split_table_row(masked[index + 1])):
            break
        end = index + 2
        while end < len(raw) and raw[end].lstrip().startswith("|"):
            row = split_table_row(raw[end])
            if (len(row) != len(cells) or not masked[end].lstrip().startswith("|")
                    or len(split_table_row(masked[end])) != len(row)
                    or split_table_row(uncommented[end]) != row):
                raise ValueError(f"cannot write an ambiguous {mode} evidence table")
            end += 1
        return index, end, len(cells) > len(expected)
    raise ValueError(f"expected a valid {mode} evidence table")
