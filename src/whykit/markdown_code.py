"""Position-preserving code masking for evidence and link extraction.

Handles CommonMark backtick and tilde fences (including an unfinished fence),
and variable-length inline backtick spans. It deliberately does not implement
all of CommonMark: indented code, HTML blocks and container blocks are separate.
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
    return "".join(chars)
