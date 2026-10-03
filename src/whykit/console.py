"""Console output that survives terminals which cannot encode Unicode.

Human messages use a few typographic glyphs (em dash, ellipsis, arrow) and
echo vault content, which can be in any script. On an ASCII console
(``LC_ALL=C``, ``PYTHONIOENCODING=ascii``) or a legacy Windows code page,
Python's default ``strict`` stdout raises ``UnicodeEncodeError`` mid-report.

Two rules keep output safe without changing what UTF-8 consumers receive:

* Human text keeps the stream's encoding. Characters it cannot represent are
  replaced: known glyphs by an ASCII spelling, anything else by a
  backslash escape, so a path stays unambiguous rather than becoming ``?``.
* Machine output (JSON, DOT, Mermaid) is always written as UTF-8, whatever
  the console encoding. On a UTF-8 stream the bytes are unchanged; elsewhere
  this is the only encoding a JSON consumer is required to accept (RFC 8259).
"""

from __future__ import annotations

import codecs
import sys
from typing import Any, TextIO

ERROR_HANDLER = "whykit-ascii-fallback"

# Glyphs WhyKit itself prints, plus common typography that vault titles carry.
ASCII_FALLBACKS: dict[str, str] = {
    "—": "-",  # em dash
    "–": "-",  # en dash
    "−": "-",  # minus sign
    "…": "...",
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    " ": " ",
    "•": "*",
    "·": "-",
    "✓": "OK",
    "✗": "x",
    "×": "x",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "﻿": "",
}

_DEFAULT_ERROR_MODES = frozenset({"strict", "backslashreplace", "surrogateescape"})
_UTF_CODECS = frozenset({"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"})


def _fallback(exc: UnicodeError) -> tuple[str | bytes, int]:
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    chunk = exc.object[exc.start:exc.end]
    if all(0xDC80 <= ord(char) <= 0xDCFF for char in chunk):
        # A POSIX name that was not valid in the locale (C locale argv or file
        # name). Usually the bytes are UTF-8: spell them like any other text so
        # an ASCII console stays ASCII; bytes that are not UTF-8 become \xNN.
        raw = bytes(ord(char) - 0xDC00 for char in chunk)
        try:
            chunk = raw.decode("utf-8")
        except UnicodeDecodeError:
            return "".join(f"\\x{byte:02x}" for byte in raw), exc.end
    pieces = []
    for char in chunk:
        if char.isascii():
            pieces.append(char)
            continue
        replacement = ASCII_FALLBACKS.get(char)
        if replacement is None:
            code = ord(char)
            if code <= 0xFF:
                replacement = f"\\x{code:02x}"
            elif code <= 0xFFFF:
                replacement = f"\\u{code:04x}"
            else:
                replacement = f"\\U{code:08x}"
        pieces.append(replacement)
    return "".join(pieces), exc.end


try:
    codecs.lookup_error(ERROR_HANDLER)
except LookupError:
    codecs.register_error(ERROR_HANDLER, _fallback)


def _codec_name(stream: Any) -> str | None:
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return None
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return None


def is_utf(stream: Any) -> bool:
    """True when ``stream`` encodes every code point (or its encoding is unknown)."""
    name = _codec_name(stream)
    return name is None or name in _UTF_CODECS


def as_printed(text: str, stream: Any = None) -> str:
    """*text* as ``stream`` (default stdout) will show it.

    On a console that cannot encode every character the fallback handler
    spells some of them as escapes, which changes how many columns the text
    takes; layout code measures this form so tables stay aligned.
    """
    stream = sys.stdout if stream is None else stream
    if is_utf(stream):
        return text
    encoding = _codec_name(stream)
    assert encoding is not None
    try:
        return text.encode(encoding, getattr(stream, "errors", None) or "strict").decode(encoding, "replace")
    except (UnicodeError, LookupError):
        return text


def harden_stream(stream: Any) -> None:
    """Replace unencodable characters on a non-UTF stream instead of raising.

    An explicit user choice such as ``PYTHONIOENCODING=ascii:replace`` is
    respected: only the interpreter defaults (``strict`` on stdout,
    ``backslashreplace`` on stderr, ``surrogateescape`` under the C/POSIX
    locale) are swapped for the fallback handler, which keeps the
    surrogateescape round-trip for undecodable file names.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None or is_utf(stream):
        return
    if getattr(stream, "errors", None) not in _DEFAULT_ERROR_MODES:
        return
    try:
        reconfigure(errors=ERROR_HANDLER)
    except (ValueError, OSError):  # pragma: no cover - closed or detached stream
        return


def harden_stdio() -> None:
    harden_stream(sys.stdout)
    harden_stream(sys.stderr)


def emit_machine(text: str, *, file: TextIO | None = None) -> None:
    """Print machine-readable output (JSON, DOT, Mermaid) as UTF-8.

    ``print`` is used whenever the stream is already UTF-8 or is an in-memory
    buffer, so those bytes are identical to plain ``print``. Otherwise the
    stream is switched to UTF-8 for this one write and switched back, which
    keeps the platform's newline translation.
    """
    stream = sys.stdout if file is None else file
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None or is_utf(stream):
        print(text, file=stream)
        return
    encoding, errors = stream.encoding, stream.errors
    stream.flush()
    # surrogateescape turns C-locale argv/file-name surrogates back into the
    # original (UTF-8) bytes, so the output matches a UTF-8 console byte for byte.
    reconfigure(encoding="utf-8", errors="surrogateescape")
    try:
        print(text, file=stream)
        stream.flush()
    finally:
        reconfigure(encoding=encoding, errors=errors)

