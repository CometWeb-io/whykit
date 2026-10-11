"""Position-preserving code masking for evidence and link extraction.

Handles CommonMark backtick and tilde fences (including an unfinished fence),
and variable-length inline backtick spans. It deliberately does not implement
all of CommonMark: indented code, HTML blocks and container blocks are separate.
Also excludes HTML comments (after code spans), including unclosed block comments.
Masked characters become spaces, with original newlines and offsets preserved.
"""
from __future__ import annotations

import re

_BT = chr(96)
_FENCE_OPEN = re.compile(r"^ {0,3}(" + _BT + r"{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(" + _BT + r"+|~+)[ \t]*$")
_BACKTICK_RUN = re.compile(_BT + "+")


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

    for line in text.splitlines(keepends=True):
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

    # Find matching code-span delimiters in O(N). Each opening delimiter
    # uses the next subsequent run of exactly the same length.
    runs = list(_BACKTICK_RUN.finditer("".join(chars)))
    next_of_size: dict[int, int] = {}
    closing: list[int | None] = [None] * len(runs)
    for i in range(len(runs) - 1, -1, -1):
        size = len(runs[i].group())
        closing[i] = next_of_size.get(size)
        next_of_size[size] = i

    i = 0
    while i < len(runs):
        close = closing[i]
        if close is None:
            i += 1
            continue
        blank(runs[i].start(), runs[close].end())
        i = close + 1

    # Scan comments only after masking code. An HTML comment marker displayed
    # inside a code span must not consume ordinary citations after that span.
    # Only ASCII backslash escapes affect an opening '<' in Markdown.
    remaining = "".join(chars)
    cursor = 0
    while True:
        start = remaining.find("<!--", cursor)
        if start < 0:
            break
        escapes = 0
        while start - escapes - 1 >= 0 and remaining[start - escapes - 1] == "\\":
            escapes += 1
        if escapes % 2:
            cursor = start + 4
            continue
        closer = remaining.find("-->", start + 4)
        end = len(remaining) if closer < 0 else closer + 3
        blank(start, end)
        cursor = end
    return "".join(chars)
