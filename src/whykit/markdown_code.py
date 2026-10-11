"""Position-preserving code masking for evidence and link extraction.

Handles CommonMark backtick and tilde fences (including an unfinished fence),
and variable-length inline backtick spans. It deliberately does not implement
all of CommonMark: indented code, HTML blocks and container blocks are separate.
Also excludes HTML comments in source order, including unclosed block comments.
Masked characters become spaces, with original newlines and offsets preserved.
"""
from __future__ import annotations

import re

_BT = chr(96)
_FENCE_OPEN = re.compile(r"^ {0,3}(" + _BT + r"{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(" + _BT + r"+|~+)[ \t]*$")
_BACKTICK_RUN = re.compile(_BT + "+")
# CommonMark line endings are only CR, LF and CRLF. str.splitlines() also
# splits on NEL, vertical tab, form feed and Unicode paragraph separators,
# which would create phantom fences and suppress genuine evidence citations.
_MARKDOWN_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+$")


def mask_code(text: str) -> str:
    """Mask fenced and inline code in linear time, preserving character offsets."""
    # Most notes have no code delimiters. Reuse the original string to avoid
    # one retained allocation per note in large-vault lint/pack runs.
    if _BT not in text and "~~~" not in text and "<!--" not in text:
        return text
    chars = list(text)
    opening: tuple[str, int] | None = None
    at = 0

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    for line_match in _MARKDOWN_LINE.finditer(text):
        line = line_match.group()
        body = line.rstrip("\r\n")
        if opening is None:
            match = _FENCE_OPEN.match(body)
            if match and (match[1][0] != _BT or _BT not in match[2]):
                opening = (match[1][0], len(match[1]))
                blank(at, at + len(line))
        else:
            match = _FENCE_CLOSE.fullmatch(body)
            blank(at, at + len(line))
            if match and match[1][0] == opening[0] and len(match[1]) >= opening[1]:
                opening = None
        at += len(line)

    # Resolve inline spans and comments in lexical order. A comment that opens
    # before a backtick run must close on the first literal '-->', even when
    # that closer contains matching backticks. Conversely an inline code span
    # already open before a comment marker owns that marker as literal code.
    # Precomputed exact-width backtick run matches keep this O(N).
    visible = "".join(chars)
    runs = list(_BACKTICK_RUN.finditer(visible))
    next_of_size: dict[int, int] = {}
    closing: list[int | None] = [None] * len(runs)
    by_start: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        width = len(runs[index].group())
        closing[index] = next_of_size.get(width)
        next_of_size[width] = index
        by_start[runs[index].start()] = index

    cursor = 0
    for token in re.finditer(_BT + r"+|<!--", visible):
        start = token.start()
        if start < cursor:
            continue
        if token.group()[0] == _BT:
            end_index = closing[by_start[start]]
            if end_index is None:
                cursor = token.end()
            else:
                cursor = runs[end_index].end()
                blank(start, cursor)
            continue
        escapes = 0
        while start - escapes - 1 >= 0 and visible[start - escapes - 1] == "\\":
            escapes += 1
        if escapes % 2:
            cursor = token.end()
            continue
        # CommonMark also recognizes the two short empty-comment forms.
        if visible.startswith("<!-->", start):
            cursor = start + 5
        elif visible.startswith("<!--->", start):
            cursor = start + 6
        else:
            closer = visible.find("-->", token.end())
            cursor = len(visible) if closer < 0 else closer + 3
        blank(start, cursor)
    return "".join(chars)
