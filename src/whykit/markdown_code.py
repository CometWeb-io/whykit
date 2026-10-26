"""Position-preserving masking of code and comments for link extraction.

shortcut: this is not a full CommonMark parser (containers, indented code and
other HTML blocks are unsupported); use a full parser when those are required.
"""
from __future__ import annotations

import re

_BT = chr(96)
_FENCE_OPEN = re.compile(r"^ {0,3}(" + _BT + r"{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(" + _BT + r"+|~+)[ \t]*$")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_THEMATIC_BREAK = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_TOKENS = re.compile(_BT + r"+|<!--")
# Unicode separators are ordinary content, not Markdown physical line endings.
_MARKDOWN_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+$")


def mask_code(text: str, *, keep_code: bool = False) -> str:
    """Mask comments and, unless keep_code, code; retain offsets in linear time."""
    if _BT not in text and "~~~" not in text and "<!--" not in text:
        return text
    chars = list(text)
    lines = list(_MARKDOWN_LINE.finditer(text))
    tokens = list(_TOKENS.finditer(text))
    fences = []
    for line in lines:
        match = _FENCE_OPEN.fullmatch(line.group().rstrip("\r\n"))
        fences.append(match if match and (match[1][0] != _BT or _BT not in match[2]) else None)

    # A heading owns inline spans on its line, isolated from both neighbours.
    closing: list[int | None] = [None] * len(tokens)
    next_of_size: dict[int, int] = {}
    token_index = len(tokens) - 1
    for line_index in range(len(lines) - 1, -1, -1):
        line = lines[line_index]
        body = line.group().rstrip("\r\n")
        heading = _ATX_HEADING.match(body) is not None
        boundary = fences[line_index] or not body.strip(" \t") or _THEMATIC_BREAK.fullmatch(body) is not None
        if boundary or heading:
            next_of_size.clear()
        while token_index >= 0 and tokens[token_index].start() >= line.start():
            token = tokens[token_index]
            if not boundary and token[0][0] == _BT:
                width = len(token[0])
                closing[token_index] = next_of_size.get(width)
                next_of_size[width] = token_index
            token_index -= 1
        if heading:
            next_of_size.clear()

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    opening: tuple[str, int] | None = None
    cursor = 0
    token_index = 0
    for line_index, line in enumerate(lines):
        if line.end() <= cursor:
            continue
        body = line.group().rstrip("\r\n")
        if opening:
            if not keep_code:
                blank(line.start(), line.end())
            match = _FENCE_CLOSE.fullmatch(body)
            if match and match[1][0] == opening[0] and len(match[1]) >= opening[1]:
                opening = None
            cursor = line.end()
            continue
        fence = fences[line_index]
        if cursor <= line.start() and fence:
            opening = (fence[1][0], len(fence[1]))
            if not keep_code:
                blank(line.start(), line.end())
            cursor = line.end()
            continue
        while token_index < len(tokens) and tokens[token_index].start() < line.end():
            index = token_index
            token_index += 1
            token = tokens[index]
            start = token.start()
            if start < cursor:
                continue
            if token[0][0] == _BT:
                end_index = closing[index]
                cursor = token.end() if end_index is None else tokens[end_index].end()
                if end_index is not None and not keep_code:
                    blank(start, cursor)
                continue
            escapes = 0
            while start - escapes - 1 >= 0 and text[start - escapes - 1] == "\\":
                escapes += 1
            if escapes % 2:
                cursor = token.end()
                continue
            if text.startswith("<!-->", start):
                cursor = start + 5
            elif text.startswith("<!--->", start):
                cursor = start + 6
            else:
                closer = text.find("-->", token.end())
                cursor = len(text) if closer < 0 else closer + 3
            blank(start, cursor)
    return "".join(chars)
