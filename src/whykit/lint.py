#!/usr/bin/env python3
"""WhyKit vault linter.

The linter checks *shape*, not truth. It is intentionally deterministic and has
no runtime dependency beyond Python 3.11+. WhyKit parses the documented YAML
subset itself so installing an unrelated optional package cannot change lint
behavior.

Examples:
    whykit lint                     # finds the vault root by walking up
    whykit lint --strict
    whykit lint examples/northline  # a vault directory is used as the root
    whykit lint --json --quiet
"""
from __future__ import annotations

from .tables import split_table_row as _split_table_row, review_table_header

import argparse
import contextlib
import contextvars
import datetime as dt
import functools
import json
import os
import re
import stat
import unicodedata
from urllib.parse import unquote
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePath
from collections.abc import Callable, Iterator
from typing import Iterable

from .config import CONFIG_FILE, ConfigError, load_config
from .rule_policy import EMPTY_POLICY, Override, check_custom_rules, policy_from_config, secret_scan_skipped
from .console import emit_machine, one_line
from .placeholders import (
    PROSE_SECTIONS as PLACEHOLDER_PROSE_SECTIONS,
    SCAFFOLD_EVIDENCE_TODO,
    normalize as normalize_placeholder,
    paragraphs as placeholder_paragraphs,
    split_sections as split_placeholder_sections,
    template_section_prompts,
)

VAULT_MARKERS = ("Home.md", "00-context")


class VaultPathError(ValueError):
    """Raised when a requested lint path escapes the selected vault root."""


# ``Path.resolve()`` walks every path component with ``lstat``.  One command
# resolves the same few thousand paths hundreds of thousands of times (link
# targets, containment checks, relative paths), which dominated run time on
# large vaults.  Inside a ``path_cache()`` scope each distinct path is resolved
# once.  The scope is request-sized on purpose: a long-lived process such as the
# MCP server must not see a stale answer after the vault changes on disk.
class _RequestCache:
    __slots__ = ("realpaths", "relative", "within", "registers")

    def __init__(self) -> None:
        # Keyed by the path string, not the Path: Windows paths compare
        # case-insensitively, and a cache must never merge two spellings.
        self.realpaths: dict[str, Path] = {}
        self.relative: dict[tuple[str, str], str] = {}
        self.within: dict[tuple[str, str], bool] = {}
        # Parsed evidence registers keyed by (path, mtime_ns, size), so a write
        # inside the scope is still seen.
        self.registers: dict[tuple[Path, int, int], tuple] = {}


_REQUEST: contextvars.ContextVar[_RequestCache | None] = contextvars.ContextVar(
    "whykit_request_cache", default=None,
)


@contextlib.contextmanager
def path_cache():
    """Memoise ``Path.resolve()`` (and register parsing) for one read-only request.

    Re-entrant: a nested scope reuses the outer cache.  Usable as a decorator.
    """
    if _REQUEST.get() is not None:
        yield
        return
    token = _REQUEST.set(_RequestCache())
    try:
        yield
    finally:
        _REQUEST.reset(token)


_POSIX = os.name == "posix"


def _real(path: Path) -> Path:
    cache = _REQUEST.get()
    if cache is None:
        return path.resolve()
    key = os.fspath(path)
    hit = cache.realpaths.get(key)
    if hit is None:
        hit = _real_uncached(path)
        if type(hit) is type(path) and os.fspath(hit) == key:
            # Already real (the common case under a resolved vault root): keep
            # the caller's object instead of an equal copy of every path.
            hit = path
        cache.realpaths[key] = hit
    return hit


# Characters the Win32 namespace rejects in a file name before any file system
# is asked. NUL is impossible on every platform.
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*') | frozenset(chr(code) for code in range(32))
# Device names: on Windows ``notes/CON.md`` or ``nul`` opens a device, not a file.
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"{kind}{digit}" for kind in ("com", "lpt") for digit in "123456789\u00b9\u00b2\u00b3"}
)


def _impossible_name(name: str, windows: bool = not _POSIX) -> bool:
    """Whether no file or directory can be called *name* on this platform.

    A pure string test, so a link target such as ``a<b`` or one holding a NUL
    is answered without a system call (which on Windows can raise, or be slow).
    """
    if "\x00" in name:
        return True
    if not windows:
        return False
    if not _WINDOWS_FORBIDDEN.isdisjoint(name):
        return True
    return name.split(".", 1)[0].rstrip(" ").casefold() in _WINDOWS_DEVICES


def _first_impossible_part(path: PurePath, windows: bool = not _POSIX) -> int | None:
    """Index into ``path.parts`` of the first component that cannot exist."""
    parts = path.parts
    start = 1 if (path.drive or path.root) else 0  # the anchor (``C:\``) is not a name
    for index in range(start, len(parts)):
        if _impossible_name(parts[index], windows):
            return index
    return None


def _foreign_anchor(root: PurePath, path: PurePath) -> bool:
    """True when *path* is absolute on another drive, share or root than *root*.

    ``\\\\host\\share\\x`` in a note is a UNC path on Windows: resolving
    it asks the network for ``host``, which takes seconds and can offer the
    user's network credentials to whoever answers. Such a path can never be inside
    the vault, so containment is decided from the strings alone.
    """
    if not (path.anchor and root.anchor):
        return False
    return path.anchor.casefold() != root.anchor.casefold()


def _real_uncached(path: Path) -> Path:
    # On POSIX, realpath(dir/name) is realpath(dir)/name whenever `name` is a
    # plain component that is not itself a symlink.  Reusing the cached parent
    # turns one lstat per path component into one lstat per path.  Anything
    # else (Windows, relative paths, `.`/`..`, symlinks) takes the full route.
    if _POSIX and path.is_absolute():
        name = path.name
        parent = path.parent
        if name not in ("", ".", "..") and parent != path and not os.path.islink(path):
            return _real(parent) / name
    if not _POSIX and path.is_absolute():
        # Windows normalises `..` lexically, so after normpath nothing at or
        # below a component that cannot exist touches the file system: resolve
        # the part above it and keep the rest as written.
        normal = type(path)(os.path.normpath(path))
        index = _first_impossible_part(normal)
        if index is not None:
            parts = normal.parts
            return _real(type(path)(*parts[:index])).joinpath(*parts[index:])
    return path.resolve()


def _relative_text(path: Path, root: Path) -> str | None:
    """``path.relative_to(root).as_posix()``, or ``None`` when it raises.

    For two real (absolute, normalised) POSIX paths this is a prefix test on
    the strings.  ``relative_to`` gets the same answer by comparing every
    ancestor of *path* with *root*, and caches a list of parts on both paths
    while doing it, which at vault scale is both slow and tens of megabytes.
    """
    if not _POSIX:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return None
    inner, outer = os.fspath(path), os.fspath(root)
    if inner == outer:
        return "."
    if not (inner.startswith("/") and outer.startswith("/")):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return None
    prefix = outer if outer.endswith("/") else outer + "/"
    return inner[len(prefix):] if inner.startswith(prefix) else None


def _within(root: Path, path: Path) -> bool:
    cache = _REQUEST.get()
    key = (os.fspath(root), os.fspath(path))
    if cache is not None:
        hit = cache.within.get(key)
        if hit is not None:
            return hit
    if _foreign_anchor(root, path):
        inside = False
    else:
        try:
            inside = _relative_text(_real(path), _real(root)) is not None
        except (OSError, ValueError):
            # A name the operating system rejects (an embedded NUL) is a link
            # that does not resolve, never a crash.
            inside = False
    if cache is not None:
        cache.within[key] = inside
    return inside


def path_exists(path: Path) -> bool:
    """``path.exists()`` that answers False instead of raising.

    A link target comes from the note, so it can name a path the operating
    system rejects outright (a component longer than the filesystem allows,
    an embedded NUL). That is a link that does not resolve, not a crash.
    """
    if _first_impossible_part(path) is not None:
        return False
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def path_is_file(path: Path) -> bool:
    """``path.is_file()`` with the same tolerance as :func:`path_exists`."""
    if _first_impossible_part(path) is not None:
        return False
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


MARKDOWN_SUFFIX = ".md"


def is_markdown_name(name: str) -> bool:
    """Whether a file name is Markdown: ``.md`` in any letter case.

    One rule for every command. ``notes/old.MD`` is the same kind of file as
    ``notes/old.md`` on every filesystem, so lint, adopt, the index and the
    snapshot must not see it on one platform and miss it on another.
    """
    return name.lower().endswith(MARKDOWN_SUFFIX)


def strip_markdown_suffix(value: str) -> str:
    """``value`` without a trailing ``.md`` in any letter case (a node ID from a path)."""
    return value[: -len(MARKDOWN_SUFFIX)] if is_markdown_name(value) else value


def iter_markdown(root: Path) -> Iterator[Path]:
    """Every path under *root* whose name is Markdown, in any letter case.

    ``Path.rglob("*.md")`` matches case-sensitively on POSIX, so it would skip
    ``old.MD`` on Linux and macOS alike. Callers still check ``is_file`` and
    confinement, exactly as they did for the glob.
    """
    for path in root.rglob("*"):
        if is_markdown_name(path.name):
            yield path


def is_vault_root(path: Path) -> bool:
    """A directory is a vault root when it carries the map of content and the context dir."""
    return (path / "Home.md").is_file() and (path / "00-context").is_dir()


def find_vault_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for a vault root, so the CLI works from any subdirectory."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if is_vault_root(candidate):
            return candidate
    return None

REQUIRED_KEYS = (
    "title",
    "type",
    "status",
    "owner",
    "created",
    "last_updated",
    "source_of_truth",
    "sensitivity",
)
OPTIONAL_KEYS = (
    "aliases",
    "source_ids",
    "tags",
    "reviewers",
    "review_by",
    "decision_id",
    "supersedes",
    "superseded_by",
    "delivery_status",
    "sent_at",
    "provenance",
    "workstream",
)
ALLOWED_STATUS = {"template", "draft", "in_review", "approved", "superseded", "archived"}
# Records whose reasoning is closed: kept as written, never brought up to date.
HISTORICAL_STATUSES = frozenset({"superseded", "archived"})
ALLOWED_TYPE = {
    "strategy",
    "research",
    "framework",
    "specification",
    "decision",
    "map-of-content",
    "guide",
    "reference",
}
ALLOWED_SENSITIVITY = {"public", "internal", "confidential", "restricted"}
ALLOWED_DELIVERY_STATUS = {"draft", "ready_to_send", "sent"}
NO_FRONT_MATTER_REQUIRED = {"AGENTS.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"}
PLACEHOLDER_DATE = "YYYY-MM-DD"
PLACEHOLDER_OWNER = "TODO"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATED_FILENAME_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:-[A-Za-z]{2,4})?$")
# Inside a Markdown table Obsidian writes the alias pipe as `\|`; the optional
# backslash keeps it out of the target so `[[a/b\|alias]]` resolves as `a/b`.
# No part of a wikilink may hold `[` or a line break (Obsidian forbids both in
# link targets). Besides matching what Obsidian reads, that keeps every scan
# short: an unclosed `[[` stops at the next `[` or line end instead of
# reading to the end of the file once per opener, which made a note of
# repeated `[` characters quadratic.
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+?)\\?(?:#[^\[\]|\n]+)?(?:\|[^\[\]\n]*)?\]\]")
# Link text may not hold `[` and a destination may not hold `[` or a line
# break, for the same reason: each scan then ends at the next opener.
MARKDOWN_LINK_RE = re.compile(r"(!?)\[[^\[\]]*\]\(([^)\[\n]+)\)")
# ASCII digits only: `\d` also matches fullwidth and other Unicode digits, so
# `E-００１` would be a second ID that looks exactly like `E-001`.
EVIDENCE_ID_RE = re.compile(r"\bE-[0-9]{3,}\b")
DECISION_ID_RE = re.compile(r"\bD-[0-9]{3,}\b")
DECISION_FILE_RE = re.compile(r"^d-([0-9]{3,})-")
TEXT_SECRET_EXTENSIONS = {
    ".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".xml", ".html", ".log", ".env", ".toml", ".ini", ".conf",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".sh",
}
SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("credential assignment", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password)[\"']?\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9_\-/+=.]{20,}"
    )),
)
_CREDENTIAL_WORDS = ("key", "token", "secret", "password")


def _may_hold_credential(text: str) -> bool:
    # Every credential-assignment match contains one of these words, compared
    # case-insensitively.  For ASCII text, `re.IGNORECASE` and `str.lower()`
    # agree letter for letter.  Outside ASCII they do not (the regex also
    # matches the Kelvin sign as `k` and the long s as `s`), so such text
    # always gets the full scan.
    if not text.isascii():
        return True
    lowered = text.lower()
    return any(word in lowered for word in _CREDENTIAL_WORDS)


# A necessary condition for each same-index SECRET_PATTERNS entry: if it is
# false for a text, the pattern cannot match anywhere in it.  The patterns start
# with ``\b``, so the regex engine tries them at every offset; the prefilters
# start with a literal that it finds with a fast substring search.  Skipping a
# pattern only when its prefilter fails leaves the findings unchanged.
# ``tests/test_performance.py`` checks each prefilter against its pattern.
SECRET_PREFILTERS: tuple[Callable[[str], object], ...] = (
    re.compile(r"-----BEGIN ").search,
    re.compile(r"sk-").search,
    re.compile(r"AKIA").search,
    re.compile(r"gh[pousr]_").search,
    re.compile(r"xox[baprs]-").search,
    _may_hold_credential,
)
CONTENT_SKIP_DIRS = {
    ".git", ".obsidian", ".import-staging", "node_modules", "__pycache__",
    "apps", "examples", "tests", ".github", "schemas",
}
ATTACHMENT_SKIP_DIRS = {".git", ".obsidian", ".import-staging", "node_modules", "__pycache__", ".whykit"}
SECRET_SKIP_DIRS = {
    ".git", ".obsidian", ".import-staging", "node_modules", "__pycache__", "tests",
}


@dataclass
class Finding:
    path: str
    line: int | None
    level: str
    code: str
    message: str


@dataclass
class Note:
    path: Path
    text: str
    front: dict = field(default_factory=dict)
    has_front: bool = False
    front_error: str | None = None
    body_offset: int = 0  # line count, not a character index

    @functools.cached_property
    def content_sha256(self) -> str:
        """Bind pagination to this parsed content, not only its place in the rank."""
        import hashlib
        return hashlib.sha256(self.text.encode("utf-8", "surrogateescape")).hexdigest()

    @functools.cached_property
    def masked(self) -> str:
        """The text with code spans and fences blanked; offsets match ``text``."""
        return _mask_code(self.text)

    @functools.cached_property
    def cited_evidence(self) -> tuple[str, ...]:
        """Sorted E-NNN IDs this note cites, in front matter or prose.

        An ID inside inline code or a fenced code block is an example of the
        syntax, not a citation, so graph, impact, context, query and trace all
        ignore it; lint's fact-callout check reads the same masked text.
        """
        return tuple(sorted(set(EVIDENCE_ID_RE.findall(self.masked))))

    # The views below are pure functions of ``text``.  Each is computed at most
    # once per note, and the persistent parse cache (``whykit.parse_cache``)
    # stores them so an unchanged note is not read again on the next run.
    # NOTE_FACTS lists them; a new one must be added there too.

    @functools.cached_property
    def wikilink_hits(self) -> tuple[tuple[str, int, bool], ...]:
        """``(target, line, is_embed)`` for every wikilink outside code.

        *target* is stripped but otherwise unfiltered: callers decide whether
        an empty or ``http`` target counts.
        """
        text = self.text
        lines = _LineNumbers(text)
        hits = []
        for match in WIKILINK_RE.finditer(self.masked):
            start = match.start()
            # A vault links the same few targets from many notes; share them.
            hits.append((_shared_target(match.group(1).strip()), lines.at(start), start > 0 and text[start - 1] == "!"))
        return tuple(hits)

    @functools.cached_property
    def markdown_link_hits(self) -> tuple[tuple[str, str, int], ...]:
        """``(kind, target, line)`` for each local Markdown link or image outside code."""
        lines = _LineNumbers(self.text)
        hits = []
        for match in MARKDOWN_LINK_RE.finditer(self.masked):
            kind = "image" if match.group(1) else "link"
            raw = match.group(2).strip()
            # Markdown permits <path with spaces>; optional titles are intentionally
            # ignored here rather than pretending to implement a full CommonMark parser.
            if raw.startswith("<") and ">" in raw:
                target = raw[1:raw.index(">")].strip()
            else:
                target = raw.split(None, 1)[0].strip() if raw else ""
            if not target or target.startswith("#") or URL_SCHEME_RE.match(target):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target:
                continue
            hits.append((kind, target, lines.at(match.start())))
        return tuple(hits)

    @functools.cached_property
    def fact_callouts(self) -> tuple[tuple[int, tuple[str, ...]], ...]:
        """``(line, cited IDs in first-seen order)`` for each ``[!fact]`` callout."""
        lines = self.masked.splitlines()
        callouts = []
        i = 0
        while i < len(lines):
            if FACT_CALLOUT_RE.match(lines[i]):
                start = i
                block = [lines[i]]
                i += 1
                while i < len(lines) and lines[i].startswith(">"):
                    block.append(lines[i])
                    i += 1
                callouts.append((start + 1, tuple(dict.fromkeys(EVIDENCE_ID_RE.findall("\n".join(block))))))
                continue
            i += 1
        return tuple(callouts)

    @functools.cached_property
    def section_decision_id(self) -> str | None:
        """The ID under a ``## Decision ID`` heading, if the note has one."""
        m = DECISION_ID_SECTION_RE.search(self.text)
        return m.group(1) if m else None

    @functools.cached_property
    def secret_hits(self) -> tuple[tuple[int, str], ...]:
        """``(line, label)`` for each credential-shaped match, in report order."""
        text = self.text
        hits = []
        for (label, pattern), prefilter in zip(SECRET_PATTERNS, SECRET_PREFILTERS, strict=True):
            if not prefilter(text):
                continue
            numbers = _LineNumbers(text)
            for match in pattern.finditer(text):
                hits.append((numbers.at(match.start()), label))
        return tuple(hits)

    @functools.cached_property
    def decision_placeholders(self) -> tuple[tuple[int, str], ...]:
        """``(line, section)`` for each section still holding template placeholders."""
        return tuple(_decision_placeholders(self))

    @property
    def body(self) -> str:
        """The note text after the front matter block (the whole text when there is none)."""
        if not self.has_front:
            return self.text
        # Same as "\n".join(text.split("\n")[body_offset:]) without building
        # a list of every line: skip past the first body_offset newlines.
        text = self.text
        at = 0
        for _ in range(self.body_offset):
            at = text.find("\n", at) + 1
            if at == 0:
                return ""
        return text[at:]


def _strip_yaml_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for idx, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (idx == 0 or value[idx - 1].isspace()):
            return value[:idx].rstrip()
    return value.strip()


def _split_inline_list(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    for char in inner:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            buf.append(char)
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            buf.append(char)
            continue
        if char == "," and quote is None:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    if quote is not None:
        raise ValueError("unterminated quote in inline list")
    if buf or inner.strip():
        parts.append("".join(buf).strip())
    return parts


# Front matter repeats the same keys and many of the same short values
# (status, type, owner, tags) in every note.  Sharing one string object per
# spelling saves tens of megabytes at 20,000 notes.  The pool is bounded so a
# long-lived process reading many vaults cannot grow it without limit.
_SHARED: dict[str, str] = {}
_SHARED_MAX_ENTRIES = 8192
_SHARED_MAX_CHARS = 64


def _shared(value: str) -> str:
    if len(value) > _SHARED_MAX_CHARS:
        return value
    hit = _SHARED.get(value)
    if hit is not None:
        return hit
    if len(_SHARED) < _SHARED_MAX_ENTRIES:
        _SHARED[value] = value
    return value


# Link targets get a pool of their own: a large vault has more distinct
# targets than the front-matter pool holds, and filling that pool with them
# would stop front-matter values from being shared.
_SHARED_TARGETS: dict[str, str] = {}
_SHARED_TARGETS_MAX_ENTRIES = 65536


def _shared_target(value: str) -> str:
    if len(value) > _SHARED_MAX_CHARS:
        return value
    hit = _SHARED_TARGETS.get(value)
    if hit is not None:
        return hit
    if len(_SHARED_TARGETS) < _SHARED_TARGETS_MAX_ENTRIES:
        _SHARED_TARGETS[value] = value
    return value


def _yaml_scalar(value: str) -> object:
    result = _yaml_scalar_raw(value)
    return _shared(result) if type(result) is str else result


def _yaml_scalar_raw(value: str) -> object:
    import json as _json

    value = _strip_yaml_comment(value).strip()
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        items = _split_inline_list(inner) if inner else []
        # One level only, like block lists. Recursing let a line of brackets
        # cost quadratic time and then a RecursionError.
        for item in items:
            if item.startswith("[") and item.endswith("]"):
                raise ValueError("nested lists are not supported in front matter")
        return [_yaml_scalar(item) for item in items]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value in ("null", "~"):
        return None
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return _json.loads(value)
        except _json.JSONDecodeError as exc:
            raise ValueError(f"invalid double-quoted scalar: {value!r}") from exc
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?[1-9]?[+-]?$")
FRONT_CLOSE_RE = re.compile(r"^---$", re.MULTILINE)


def _front_value(value: str, key: str) -> object:
    if BLOCK_SCALAR_RE.match(_strip_yaml_comment(value)):
        raise ValueError(
            f"{key!r} uses a block scalar ({value.strip()}); WhyKit reads single-line values only"
        )
    return _yaml_scalar(value)


def _is_sequence_item(content: str) -> bool:
    return content.startswith("- ")


def _parse_front_matter(raw: str) -> dict:
    """Parse the deliberately small YAML subset supported by WhyKit.

    Supported: top-level scalars/lists, block lists, and one nested mapping
    level (used by `provenance`), including block lists inside that mapping.
    Block-list items may sit at any consistent indentation, including the
    column-0 form (`tags:` followed by `- a`) that many editors write.
    Rich YAML features such as anchors, block scalars and arbitrary nesting are
    rejected instead of behaving differently depending on installed packages.
    """
    out: dict = {}
    current_top: str | None = None
    child_indent: int | None = None
    current_nested: str | None = None
    nested_indent: int | None = None
    nested_item_indent: int | None = None

    for raw_line in raw.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ValueError("tabs are not supported for YAML indentation")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()

        if indent == 0 and not _is_sequence_item(content):
            if ":" not in content:
                raise ValueError(f"cannot read line: {raw_line!r}")
            key, _, value = content.partition(":")
            key = _shared(key.strip())
            if not key:
                raise ValueError("front matter key cannot be empty")
            if key in out:
                raise ValueError(f"duplicate front matter key: {key!r}")
            value = value.strip()
            current_top = key
            child_indent = None
            current_nested = None
            nested_indent = None
            out[key] = None if value == "" else _front_value(value, key)
            continue

        if current_top is None:
            raise ValueError(f"cannot read line: {raw_line!r}")
        parent = out.get(current_top)

        # A list item under the open nested key: deeper than the key, or at the
        # same column (YAML's compact form) once the key holds a list.
        if (
            current_nested is not None
            and nested_indent is not None
            and _is_sequence_item(content)
            and (indent > nested_indent or (indent == nested_indent and isinstance(parent, dict) and isinstance(parent.get(current_nested), list)))
        ):
            if not isinstance(parent, dict):
                raise ValueError(f"cannot nest list under {current_top!r}")
            nested = parent.get(current_nested)
            if not isinstance(nested, list):
                raise ValueError(f"{current_top}.{current_nested} is not a list")
            if nested_item_indent is None:
                nested_item_indent = indent
            elif indent != nested_item_indent:
                raise ValueError(f"inconsistent list indentation under {current_top}.{current_nested}")
            nested.append(_yaml_scalar(content[2:].strip()))
            continue

        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            raise ValueError(f"unsupported YAML indentation/structure: {raw_line!r}")

        if _is_sequence_item(content):
            if parent is None:
                parent = []
                out[current_top] = parent
            if not isinstance(parent, list):
                raise ValueError(f"{current_top!r} mixes mapping/scalar and list values")
            current_nested = None
            nested_indent = None
            parent.append(_yaml_scalar(content[2:].strip()))
            continue
        if indent == 0 or ":" not in content:
            raise ValueError(f"cannot read nested line: {raw_line!r}")
        if parent == []:
            raise ValueError(f"{current_top!r} mixes list and mapping values")
        if parent is None:
            parent = {}
            out[current_top] = parent
        if not isinstance(parent, dict):
            raise ValueError(f"cannot nest under scalar key {current_top!r}")
        nested_key, _, value = content.partition(":")
        nested_key = _shared(nested_key.strip())
        if not nested_key:
            raise ValueError("front matter nested key cannot be empty")
        if nested_key in parent:
            raise ValueError(f"duplicate front matter key: {current_top}.{nested_key!r}")
        value = value.strip()
        current_nested = nested_key
        nested_indent = indent
        nested_item_indent = None
        parent[nested_key] = [] if value == "" else _front_value(value, f"{current_top}.{nested_key}")
    return out


def read_vault_text(path: Path) -> tuple[str, str | None]:
    """Read a vault file for checking: (text, problem).

    A file that is not valid UTF-8 is read with U+FFFD for the bad bytes and
    the problem names the first one, so one damaged or hostile file becomes a
    finding instead of stopping every command that reads the vault.
    """
    data = path.read_bytes()
    problem: str | None = None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        text = data.decode("utf-8", "replace")
        problem = f"file is not valid UTF-8 (byte {exc.start}); WhyKit read it with the bad bytes replaced"
    # The same newline handling as Path.read_text().
    return text.replace("\r\n", "\n").replace("\r", "\n"), problem


def read_vault_lines(path: Path) -> list[str]:
    """The lines of a vault file, with any bytes that are not UTF-8 replaced."""
    return read_vault_text(path)[0].splitlines()


def _normalise(text: str) -> str:
    """Decoded file text as every parser sees it.

    Path.read_text() uses universal newlines; callers with decoded bytes
    (notably adopt's hash-preserving scan) must see the same parser input.
    Editors on Windows commonly save UTF-8 with a byte-order mark; it is not
    content, and leaving it in place hides the front matter fence.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("\ufeff")


def load_note(path: Path, *, text: str | None = None) -> Note:
    encoding_problem = None
    if text is None:
        text, encoding_problem = read_vault_text(path)
        text = text.removeprefix("\ufeff")
    else:
        text = _normalise(text)
    note = Note(path=path, text=text)
    if not (text.startswith("---\n") or text == "---"):
        note.front_error = encoding_problem
        return note
    # The closing fence may be the very next line (empty front matter) or the
    # last line of a file with no trailing newline.
    close = FRONT_CLOSE_RE.search(text, 4)
    if close is None:
        note.front_error = encoding_problem or "front matter opened with --- but never closed"
        return note
    note.has_front = True
    note.body_offset = text[: close.end()].count("\n") + 1
    try:
        note.front = _parse_front_matter(text[4:close.start()])
    except Exception as exc:  # noqa: BLE001
        note.front_error = f"front matter is not valid YAML: {exc}"
    if encoding_problem is not None:
        note.front_error = encoding_problem
    return note


def rel(root: Path, path: Path) -> str:
    # Resolve both sides so macOS /var vs /private/var (and similar aliasing)
    # does not break relative paths or silently fall back to absolutes.
    cache = _REQUEST.get()
    key = (os.fspath(root), os.fspath(path))
    if cache is not None:
        hit = cache.relative.get(key)
        if hit is not None:
            return hit
    resolved = _real(path)
    out = _relative_text(resolved, _real(root))
    if out is None:
        out = resolved.as_posix()
    if cache is not None:
        cache.relative[key] = out
    return out


def add(findings: list[Finding], root: Path, path: Path, line: int | None, level: str, code: str, message: str) -> None:
    findings.append(Finding(rel(root, path), line, level, code, message))


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _parse_date(value: object) -> dt.date | None:
    text = str(value)
    if not DATE_RE.match(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def check_front_matter(root: Path, note: Note, findings: list[Finding], today: dt.date) -> None:
    r = Path(rel(root, note.path))
    exempt = r.name in NO_FRONT_MATTER_REQUIRED or r.name == "README.md"
    if note.front_error:
        add(findings, root, note.path, 1, "error", "frontmatter.invalid", note.front_error)
        return
    if not note.has_front:
        if not exempt:
            add(findings, root, note.path, 1, "error", "frontmatter.missing", "no YAML front matter — see OBSIDIAN.md")
        return

    for key in REQUIRED_KEYS:
        if key not in note.front:
            add(findings, root, note.path, 1, "error", "frontmatter.required", f"front matter is missing `{key}`")
        elif note.front[key] is None or (isinstance(note.front[key], (str, list)) and not note.front[key]):
            # `owner:` with nothing after it parses, so the key is "present"; every
            # value check below would then skip it silently.
            add(findings, root, note.path, 1, "warning", "frontmatter.empty", f"`{key}` is present but empty")

    source_of_truth = note.front.get("source_of_truth")
    if source_of_truth is not None and not isinstance(source_of_truth, bool):
        add(
            findings, root, note.path, 1, "warning", "source_of_truth.invalid",
            f"source_of_truth `{source_of_truth}` is not a boolean; use true or false "
            "(the canonical-document checks do not apply to this note)",
        )

    status = note.front.get("status")
    if status is not None and (not isinstance(status, str) or status not in ALLOWED_STATUS):
        add(findings, root, note.path, 1, "error", "status.invalid", f"status `{status}` is not allowed")
    doc_type = note.front.get("type")
    if doc_type is not None and (not isinstance(doc_type, str) or doc_type not in ALLOWED_TYPE):
        add(findings, root, note.path, 1, "error", "type.invalid", f"type `{doc_type}` is not allowed")
    sensitivity = note.front.get("sensitivity")
    if sensitivity is not None and (not isinstance(sensitivity, str) or sensitivity not in ALLOWED_SENSITIVITY):
        add(findings, root, note.path, 1, "error", "sensitivity.invalid", f"sensitivity `{sensitivity}` is not allowed")

    if note.front.get("source_of_truth") is True and status != "approved":
        add(
            findings, root, note.path, 1, "error", "canonical.unapproved",
            "source_of_truth=true requires status=approved; drafts cannot be canonical",
        )

    is_template = status == "template"
    parsed_dates: dict[str, dt.date] = {}
    for key in ("created", "last_updated"):
        value = note.front.get(key)
        if value is None:
            continue
        text = str(value)
        if text == PLACEHOLDER_DATE:
            if not is_template:
                add(findings, root, note.path, 1, "error", "date.placeholder", f"`{key}` still holds {PLACEHOLDER_DATE}")
            continue
        parsed = _parse_date(text)
        if parsed is None:
            add(findings, root, note.path, 1, "error", "date.invalid", f"`{key}` is not a real ISO date: {text}")
        else:
            parsed_dates[key] = parsed
    if parsed_dates.get("last_updated") and parsed_dates.get("created") and parsed_dates["last_updated"] < parsed_dates["created"]:
        add(findings, root, note.path, 1, "error", "date.order", "last_updated is before created")

    review_by = note.front.get("review_by")
    if review_by and str(review_by) != PLACEHOLDER_DATE:
        parsed_review = _parse_date(review_by)
        if parsed_review is None:
            add(findings, root, note.path, 1, "error", "review_by.invalid", f"review_by is not a real ISO date: {review_by}")
        elif parsed_review < today and status == "approved":
            add(findings, root, note.path, 1, "warning", "review_by.overdue", f"approved document review was due {parsed_review.isoformat()}")

    owner = str(note.front.get("owner", ""))
    if note.front.get("source_of_truth") is True and owner in ("", PLACEHOLDER_OWNER):
        add(findings, root, note.path, 1, "warning", "canonical.owner", "canonical document has no real owner")

    delivery_status = note.front.get("delivery_status")
    if delivery_status and (not isinstance(delivery_status, str) or delivery_status not in ALLOWED_DELIVERY_STATUS):
        add(findings, root, note.path, 1, "error", "delivery_status.invalid", f"delivery_status `{delivery_status}` is not allowed")
    if delivery_status == "sent" and not note.front.get("sent_at"):
        add(findings, root, note.path, 1, "error", "delivery_status.sent_at", "delivery_status=sent requires sent_at")

    # Point-in-time reports must encode their date in the filename.
    if "reports" in r.parts and r.name != "README.md" and not DATED_FILENAME_RE.search(note.path.stem):
        add(findings, root, note.path, 1, "error", "report.undated", "report filename must end in YYYY-MM-DD")

    m = DATED_FILENAME_RE.search(note.path.stem)
    if m and parsed_dates.get("created"):
        try:
            filename_date = dt.date.fromisoformat(m.group(1))
        except ValueError:
            filename_date = None
        if filename_date and filename_date != parsed_dates["created"]:
            add(findings, root, note.path, 1, "warning", "date.filename_mismatch", f"filename says {filename_date} but created says {parsed_dates['created']}")


class _LineNumbers:
    """1-based line of a character offset, counted incrementally.

    Same answer as ``text[:offset].count("\\n") + 1``, without rescanning the
    text from the start for every match of a left-to-right ``finditer``.
    """

    __slots__ = ("text", "offset", "line")

    def __init__(self, text: str) -> None:
        self.text = text
        self.offset = 0
        self.line = 1

    def at(self, offset: int) -> int:
        if offset < self.offset:
            self.offset, self.line = 0, 1
        self.line += self.text.count("\n", self.offset, offset)
        self.offset = offset
        return self.line


URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FACT_CALLOUT_RE = re.compile(r"^> \[!fact\]", re.I)
DECISION_ID_SECTION_RE = re.compile(r"^## Decision ID\s*$\n+\s*(D-[0-9]{3,})\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _mask_code(text: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return INLINE_CODE_RE.sub(blank, FENCE_RE.sub(blank, text))


def _link_key(value: str) -> str:
    # Filenames written on macOS are often NFD while typed link text is NFC; on
    # a Linux CI checkout the two spellings are different byte strings.
    return unicodedata.normalize("NFC", value).casefold()


def _relative_key(root: Path, path: Path) -> str | None:
    return _relative_key_of(os.fspath(root), os.fspath(path))


@functools.lru_cache(maxsize=65536)
def _relative_key_of(root: str, path: str) -> str | None:
    # A pure string function (no filesystem access), so a process-wide cache
    # cannot go stale; link resolution asks for the same few paths repeatedly.
    if _POSIX and path.startswith("/") and root.startswith("/"):
        # Both are spellings of already-normalised absolute paths (``os.fspath``
        # of a ``Path``), so ``relative_to`` reduces to a prefix test on the
        # strings, without building and comparing a list of parts.
        prefix = root if root.endswith("/") else root + "/"
        if path == root:
            return _link_key(".")
        return _link_key(path[len(prefix):]) if path.startswith(prefix) else None
    try:
        return _link_key(PurePath(path).relative_to(root).as_posix())
    except ValueError:
        return None


def _build_index(notes: list[Note]) -> dict[str, set[Path]]:
    index: dict[str, set[Path]] = {}
    for note in notes:
        for key in {note.path.stem, *[a for a in _as_list(note.front.get("aliases")) if a]}:
            index.setdefault(_link_key(key), set()).add(note.path)
    return index


Resolver = Callable[[str], "tuple[Path | None, bool]"]


@functools.lru_cache(maxsize=65536)
def _leaves_vault(normalized: str) -> bool:
    """True when a link target has a `..` component (pure string function)."""
    return ".." in Path(normalized).parts


def _resolve(root: Path, target: str, index: dict[str, set[Path]]) -> tuple[Path | None, bool]:
    normalized = target.strip().replace("\\", "/").lstrip("/")
    if _leaves_vault(normalized):
        return None, False
    relative_target = normalized if is_markdown_name(normalized) else normalized + ".md"
    stem = _link_key(strip_markdown_suffix(normalized.rstrip("/").split("/")[-1]))
    hits = {path for path in index.get(stem, set()) if _within(root, path)}
    # Match the typed path against real note paths case- and NFC-insensitively,
    # as Obsidian does. `exists()` alone answers differently per filesystem:
    # on macOS and Windows it accepts `[[NOTES/readme]]` but hands back the
    # typed spelling, which is not a node ID; on Linux it misses entirely.
    wanted = _link_key(relative_target)
    spelled = [path for path in hits if _relative_key(root, path) == wanted]
    if len(spelled) == 1:
        return spelled[0], False
    candidate = root / relative_target
    # Containment first: it rejects another drive or a UNC share from the
    # strings alone, before anything asks the file system about the path.
    if _within(root, candidate) and path_exists(candidate):
        return candidate, False
    if len(hits) == 1:
        return next(iter(hits)), False
    if len(hits) > 1:
        return None, True
    return None, False


class AttachmentIndex:
    """Non-Markdown files a wikilink may target (`![[diagram.png]]`, `[[brief.pdf]]`).

    Built lazily: most vaults never link an attachment, and walking the tree is
    the expensive part.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._by_name: dict[str, list[Path]] | None = None

    def _names(self) -> dict[str, list[Path]]:
        if self._by_name is None:
            self._by_name = {}
            # rglob yields root-prefixed paths, so slicing the parts is the
            # same as relative_to() without re-parsing every path.
            depth = len(self.root.parts)
            for path in self.root.rglob("*"):
                if is_markdown_name(path.name) or not path.is_file() or not _within(self.root, path):
                    continue
                if any(part in ATTACHMENT_SKIP_DIRS for part in path.parts[depth:-1]):
                    continue
                self._by_name.setdefault(_link_key(path.name), []).append(path)
        return self._by_name

    def resolves(self, target: str, note_dir: Path) -> bool:
        normalized = target.strip().replace("\\", "/")
        suffix = Path(normalized).suffix.lower()
        if not suffix or suffix == ".md":
            return False
        for candidate in (self.root / normalized.lstrip("/"), note_dir / normalized):
            if _within(self.root, candidate) and path_is_file(candidate):
                return True
        # Obsidian resolves attachments by file name anywhere in the vault and
        # picks the nearest copy, so several matches are not an ambiguity here.
        return bool(self._names().get(_link_key(normalized.rstrip("/").split("/")[-1])))


def check_wikilinks(
    root: Path,
    note: Note,
    index: dict[str, set[Path]],
    findings: list[Finding],
    attachments: AttachmentIndex | None = None,
    resolve: Resolver | None = None,
) -> None:
    for target, line, is_embed in note.wikilink_hits:
        if not target or target.startswith(("http://", "https://")):
            continue
        if _leaves_vault(target.replace("\\", "/")):
            add(findings, root, note.path, line, "warning", "wikilink.outside", f"wikilink tries to leave the vault: [[{target}]]")
            continue
        resolved, ambiguous = resolve(target) if resolve else _resolve(root, target, index)
        if resolved is None and not ambiguous:
            if attachments is None:
                attachments = AttachmentIndex(root)
            if attachments.resolves(target, note.path.parent):
                continue
        if ambiguous:
            add(findings, root, note.path, line, "error", "wikilink.ambiguous", f"wikilink is ambiguous: [[{target}]] — use a path")
        elif resolved is None:
            if is_embed:
                add(
                    findings, root, note.path, line, "error", "embed.missing",
                    f"embed does not resolve: ![[{target}]]",
                )
            else:
                add(findings, root, note.path, line, "error", "wikilink.missing", f"wikilink does not resolve: [[{target}]]")


def check_markdown_links(
    root: Path,
    note: Note,
    findings: list[Finding],
    exists: Callable[[Path], bool] | None = None,
) -> None:
    """Flag local Markdown links that leave the vault or point nowhere.

    *exists* overrides the on-disk check, so a confined view of the vault can
    treat notes it does not contain exactly like files that do not exist.
    """
    for kind, target, line in note.markdown_link_hits:
        candidate = (root / target.lstrip("/")) if target.startswith("/") else (note.path.parent / target)
        if not _within(root, candidate):
            add(findings, root, note.path, line, "warning", "markdown_link.outside", f"Markdown {kind} leaves the vault: ({target})")
        elif not (exists(candidate) if exists else path_exists(candidate)):
            add(findings, root, note.path, line, "warning", "markdown_link.missing", f"local Markdown {kind} does not exist: ({target})")


_EVIDENCE_VIEW: contextvars.ContextVar[tuple[Path, tuple | Callable[[], tuple]] | None] = contextvars.ContextVar("whykit_evidence_view", default=None)


@contextlib.contextmanager
def evidence_view(root: Path, rows: tuple | Callable[[], tuple]):
    """Use one captured, filtered register throughout a read-only request."""
    token = _EVIDENCE_VIEW.set((_real(root), rows))
    try:
        yield
    finally:
        _EVIDENCE_VIEW.reset(token)


def evidence_register(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], list[tuple[str, int]]]:
    view = _EVIDENCE_VIEW.get()
    if view is not None and view[0] == _real(root):
        rows = view[1]() if callable(view[1]) else view[1]
        active, retired, occurrences = rows
        return ({key: dict(row) for key, row in active.items()}, {key: dict(row) for key, row in retired.items()}, list(occurrences))
    path = root / "00-context" / "evidence-register.md"
    if not path.exists():
        return {}, {}, []
    cache = _REQUEST.get()
    if cache is None:
        return _parse_evidence_register(path)
    try:
        stat = path.stat()
    except OSError:
        return _parse_evidence_register(path)
    key = (_real(path), stat.st_mtime_ns, stat.st_size)
    parsed = cache.registers.get(key)
    if parsed is None:
        parsed = cache.registers[key] = _parse_evidence_register(path)
    active, retired, occurrences = parsed
    # Callers own what they get back; never hand out the cached containers.
    return (
        {key: dict(row) for key, row in active.items()},
        {key: dict(row) for key, row in retired.items()},
        list(occurrences),
    )


def _parse_evidence_register(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], list[tuple[str, int]]]:
    return _parse_evidence_register_text(read_vault_text(path)[0])


def _parse_evidence_register_text(text: str) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], list[tuple[str, int]]]:
    """Parse register Markdown; also used for an unsaved editor buffer."""
    active: dict[str, dict[str, str]] = {}
    retired: dict[str, dict[str, str]] = {}
    occurrences: list[tuple[str, int]] = []
    mode = "active"
    sensitivity_column: int | None = None
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
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
        if line.strip().lower().startswith("## retired sources"):
            mode = "retired"
            sensitivity_column = None
            continue
        cells = _split_table_row(line) if line.lstrip().startswith("|") else []
        if cells and cells[0].casefold() == "id":
            from .tables import EVIDENCE_COLUMNS

            header = tuple(cell.casefold() for cell in cells)
            expected = EVIDENCE_COLUMNS[mode]
            sensitivity_column = len(expected) if header == (*expected, "sensitivity") else None if header == expected else -1
            continue
        if not cells or not EVIDENCE_ID_RE.fullmatch(cells[0]):
            continue
        eid = cells[0]
        if mode == "active" and len(cells) >= 7:
            # A placeholder ID does not become evidence until source + location exist.
            if not cells[1] or not cells[5]:
                continue
            occurrences.append((eid, lineno))
            active[eid] = {
                "source": cells[1], "type": cells[2], "date": cells[3],
                "accessed": cells[4], "location": cells[5], "claims": cells[6],
                "line": str(lineno),
            }
        elif mode == "retired" and len(cells) >= 5:
            if not cells[1]:
                continue
            occurrences.append((eid, lineno))
            retired[eid] = {
                "source": cells[1], "retired_on": cells[2], "why": cells[3],
                "replaced_by": cells[4], "line": str(lineno),
            }
        else:
            continue
        row = active[eid] if mode == "active" else retired[eid]
        expected_length = 7 if mode == "active" else 5
        if sensitivity_column is not None:
            row["sensitivity"] = cells[sensitivity_column] if sensitivity_column >= 0 and len(cells) == expected_length + 1 else ""
        elif len(cells) != expected_length:
            row["sensitivity"] = ""
    return active, retired, occurrences


def evidence_rows(root: Path) -> dict[str, dict[str, str]]:
    return evidence_register(root)[0]


def check_evidence_register(root: Path, findings: list[Finding], today: dt.date, config: dict | None = None) -> None:
    from .sensitivity import SENSITIVITY_LEVEL

    path = root / "00-context" / "evidence-register.md"
    if not path.exists():
        return
    active, retired, occurrences = evidence_register(root)
    try:
        if config is None:
            config, _ = load_config(root)
        access_age_policy = config["evidence_access_age_days"]
    except ConfigError:
        # check_config reports the malformed policy once, without extra findings.
        access_age_policy = {}
    seen: dict[str, int] = {}
    for eid, line in occurrences:
        if eid in seen:
            add(findings, root, path, line, "error", "evidence.duplicate", f"{eid} is already registered on line {seen[eid]}; IDs are never reused")
        else:
            seen[eid] = line
    for eid, row in active.items():
        if "sensitivity" in row and row["sensitivity"] not in SENSITIVITY_LEVEL:
            add(findings, root, path, int(row["line"]), "warning", "evidence.sensitivity", f"{eid} has an empty or invalid Sensitivity cell; filtered readers withhold it")
        for key in ("date", "accessed"):
            value = row.get(key, "")
            if value and _parse_date(value) is None:
                add(findings, root, path, int(row["line"]), "error", "evidence.date", f"{eid} has invalid {key} date `{value}`")
        max_age = access_age_policy.get(row["type"])
        if max_age is not None:
            accessed = row["accessed"]
            if not accessed:
                add(findings, root, path, int(row["line"]), "warning", "evidence.access_missing", f"{eid} has no Accessed date for its {row['type']} access-age policy")
            elif (access_date := _parse_date(accessed)) is not None:
                if access_date > today:
                    add(findings, root, path, int(row["line"]), "warning", "evidence.access_future", f"{eid} has Accessed date {accessed} after the lint date {today.isoformat()}")
                elif (today - access_date).days > max_age:
                    add(findings, root, path, int(row["line"]), "warning", "evidence.access_stale", f"{eid} was last accessed {accessed}, more than {max_age} days ago")
    known = set(active) | set(retired)
    for eid, row in retired.items():
        if "sensitivity" in row and row["sensitivity"] not in SENSITIVITY_LEVEL:
            add(findings, root, path, int(row["line"]), "warning", "evidence.sensitivity", f"{eid} has an empty or invalid Sensitivity cell; filtered readers withhold it")
        value = row.get("retired_on", "")
        if value and _parse_date(value) is None:
            add(findings, root, path, int(row["line"]), "error", "evidence.retired_date", f"{eid} has invalid retired-on date `{value}`")
        replaced = row.get("replaced_by", "").strip()
        # `whykit evidence retire` writes an em dash when nothing replaced the source.
        if replaced in {"\u2014", "-"}:
            replaced = ""
        if replaced:
            if not EVIDENCE_ID_RE.fullmatch(replaced):
                add(findings, root, path, int(row["line"]), "error", "evidence.replaced_by_format", f"{eid} has invalid replacement ID `{replaced}`")
            elif replaced not in known:
                add(findings, root, path, int(row["line"]), "error", "evidence.replaced_by_missing", f"{eid} says it was replaced by {replaced}, but that ID is not registered")


def check_evidence_ids(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    active, retired, _ = evidence_register(root)
    register = root / "00-context" / "evidence-register.md"
    for note in notes:
        if note.path == register:
            continue
        # A superseded or archived record is history: it may not be edited, and
        # citing what was believed at the time is exactly what it should do.
        # Warning on it would leave a strict gate red after the normal
        # supersede-then-retire workflow, with no allowed edit that clears it.
        historical = str(note.front.get("status") or "").strip() in HISTORICAL_STATUSES
        for sid in _as_list(note.front.get("source_ids")):
            if not EVIDENCE_ID_RE.fullmatch(sid):
                add(findings, root, note.path, 1, "error", "evidence.id_format", f"source_ids contains invalid evidence ID `{sid}`")
            elif sid in active:
                continue
            elif sid in retired:
                if historical:
                    continue
                add(findings, root, note.path, 1, "warning", "evidence.retired", f"source_ids cites retired evidence {sid}; preserve historical records but review current claims")
            else:
                add(findings, root, note.path, 1, "error", "evidence.missing", f"source_ids cites {sid}, but the register has no populated active or retired row for it")

def _decision_own_id(note: Note) -> str | None:
    explicit = str(note.front.get("decision_id", "")).strip()
    if DECISION_ID_RE.fullmatch(explicit):
        return explicit
    for alias in _as_list(note.front.get("aliases")):
        if DECISION_ID_RE.fullmatch(alias):
            return alias
    title = str(note.front.get("title", ""))
    m = DECISION_ID_RE.search(title)
    if m:
        return m.group(0)
    return note.section_decision_id


def check_decision_ids(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    seen: dict[str, Path] = {}
    for note in notes:
        r = Path(rel(root, note.path))
        if "06-decisions" not in r.parts or r.name in ("decision-log.md", "README.md"):
            continue
        if not DECISION_FILE_RE.match(r.name):
            continue
        did = _decision_own_id(note)
        if did is None:
            add(findings, root, note.path, 1, "error", "decision.id_missing", "decision record has no own D-NNN identifier; add decision_id to front matter")
            continue
        expected = "D-" + DECISION_FILE_RE.match(r.name).group(1)  # type: ignore[union-attr]
        if did != expected:
            add(findings, root, note.path, 1, "error", "decision.id_filename", f"decision_id {did} does not match filename ({expected})")
        if did in seen and seen[did] != note.path:
            add(findings, root, note.path, 1, "error", "decision.duplicate", f"{did} is also used by {rel(root, seen[did])} — IDs are never reused")
        else:
            seen[did] = note.path


def decision_log_rows(root: Path) -> list[dict[str, str | int]]:
    path = root / "06-decisions" / "decision-log.md"
    if not path.exists():
        return []
    rows: list[dict[str, str | int]] = []
    for lineno, line in enumerate(read_vault_lines(path), 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if len(cells) < 6 or not DECISION_ID_RE.fullmatch(cells[0]):
            continue
        # Empty starter rows are placeholders, not decisions.
        if not cells[1] or not cells[5]:
            continue
        target_match = WIKILINK_RE.search(cells[5])
        rows.append({
            "id": cells[0], "title": cells[1], "date": cells[2], "owner": cells[3],
            "status": cells[4], "record": target_match.group(1).strip() if target_match else "",
            "line": lineno,
        })
    return rows


def check_decision_log(root: Path, notes: list[Note], findings: list[Finding], resolve: Resolver | None = None) -> None:
    path = root / "06-decisions" / "decision-log.md"
    if not path.exists():
        return
    record_notes: dict[str, Note] = {}
    for note in notes:
        r = Path(rel(root, note.path))
        if "06-decisions" not in r.parts or r.name in ("decision-log.md", "README.md") or not DECISION_FILE_RE.match(r.name):
            continue
        did = _decision_own_id(note)
        if did:
            record_notes[did] = note

    rows = decision_log_rows(root)
    log_seen: dict[str, int] = {}
    row_ids: set[str] = set()
    status_map = {"draft": "proposed", "in_review": "proposed", "approved": "accepted", "superseded": "superseded", "archived": "archived"}
    index = _build_index(notes) if resolve is None else {}
    # First note wins for a resolved path, exactly like the linear scan it replaces.
    notes_by_real: dict[Path, Note] = {}
    for note in notes:
        notes_by_real.setdefault(_real(note.path), note)
    for row in rows:
        did = str(row["id"])
        line = int(row["line"])
        if did in log_seen:
            add(findings, root, path, line, "error", "decision_log.duplicate", f"{did} appears more than once in the decision log")
        log_seen[did] = line
        row_ids.add(did)
        target = str(row["record"])
        if not target:
            add(findings, root, path, line, "error", "decision_log.record", f"{did} has no wikilinked decision record")
            continue
        resolved, ambiguous = _resolve(root, target, index) if resolve is None else resolve(target)
        if ambiguous:
            add(findings, root, path, line, "error", "decision_log.ambiguous", f"{did} record link [[{target}]] is ambiguous")
            continue
        if resolved is None:
            add(findings, root, path, line, "error", "decision_log.missing_record", f"{did} points to missing decision record [[{target}]]")
            continue
        target_note = notes_by_real.get(_real(resolved))
        target_id = _decision_own_id(target_note) if target_note else None
        if target_id != did:
            add(findings, root, path, line, "error", "decision_log.id_mismatch", f"log row {did} points to a record whose decision_id is {target_id or 'missing'}")
            continue
        expected = status_map.get(str(target_note.front.get("status", ""))) if target_note else None
        if expected and str(row["status"]) != expected:
            add(findings, root, path, line, "error", "decision_log.status_mismatch", f"{did} log status `{row['status']}` does not match record status `{target_note.front.get('status')}` (expected `{expected}`)")
        created = str(target_note.front.get("created", "")) if target_note else ""
        if created and str(row["date"]) and created != str(row["date"]):
            add(findings, root, path, line, "warning", "decision_log.date_mismatch", f"{did} log date {row['date']} differs from record created date {created}")

    for did, note in record_notes.items():
        if did not in row_ids:
            add(findings, root, note.path, 1, "error", "decision_log.unindexed", f"{did} has a record but no populated row in decision-log.md")

    for did, note in record_notes.items():
        supersedes = str(note.front.get("supersedes", "")).strip()
        if supersedes:
            if not DECISION_ID_RE.fullmatch(supersedes):
                add(findings, root, note.path, 1, "error", "decision.supersedes_format", f"supersedes `{supersedes}` is not a D-NNN ID")
            elif supersedes not in record_notes:
                add(findings, root, note.path, 1, "error", "decision.supersedes_missing", f"{did} supersedes {supersedes}, but that decision record does not exist")
            elif note.front.get("status") == "approved" and record_notes[supersedes].front.get("status") != "superseded":
                add(findings, root, note.path, 1, "warning", "decision.supersedes_status", f"{did} is approved and supersedes {supersedes}, but the older record is not marked superseded")

    replacements: dict[str, list[str]] = {}
    historical_replacements: dict[str, list[str]] = {}
    supersedes_edges: dict[str, str] = {}
    for replacement_id, replacement_note in record_notes.items():
        predecessor = str(replacement_note.front.get("supersedes", "")).strip()
        if DECISION_ID_RE.fullmatch(predecessor) and predecessor in record_notes:
            supersedes_edges[replacement_id] = predecessor
        if DECISION_ID_RE.fullmatch(predecessor) and replacement_note.front.get("status") == "approved":
            replacements.setdefault(predecessor, []).append(replacement_id)
        elif DECISION_ID_RE.fullmatch(predecessor) and replacement_note.front.get("status") == "superseded":
            # A successor that was itself later superseded still replaces its
            # predecessor; the chain's own tail is checked on that record.
            historical_replacements.setdefault(predecessor, []).append(replacement_id)

    for predecessor, approved_replacements in replacements.items():
        if len(approved_replacements) > 1:
            joined = ", ".join(sorted(approved_replacements))
            add(findings, root, record_notes[predecessor].path, 1, "error", "decision.supersession_fork", f"{predecessor} has multiple approved replacements: {joined}")

    reported_cycles: set[tuple[str, ...]] = set()
    for start in sorted(supersedes_edges):
        order: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in supersedes_edges:
            if current in positions:
                cycle = order[positions[current]:]
                key = tuple(sorted(cycle))
                if key not in reported_cycles:
                    reported_cycles.add(key)
                    rendered = " -> ".join([*cycle, cycle[0]])
                    add(findings, root, record_notes[current].path, 1, "error", "decision.supersession_cycle", f"decision supersession contains a cycle: {rendered}")
                break
            positions[current] = len(order)
            order.append(current)
            current = supersedes_edges[current]

    for did, note in record_notes.items():
        approved_replacements = sorted(replacements.get(did, []) + historical_replacements.get(did, []))
        declared = str(note.front.get("superseded_by", "")).strip()
        if note.front.get("status") == "superseded" and not approved_replacements:
            add(findings, root, note.path, 1, "warning", "decision.superseded_by_missing", f"{did} is superseded but no approved newer record declares `supersedes: {did}`")
        if declared:
            if not DECISION_ID_RE.fullmatch(declared):
                add(findings, root, note.path, 1, "warning", "decision.superseded_by_format", f"superseded_by `{declared}` is not a D-NNN ID")
            elif declared not in approved_replacements:
                expected = ", ".join(approved_replacements) if approved_replacements else "no approved replacement"
                add(findings, root, note.path, 1, "warning", "decision.superseded_by_mismatch", f"{did} declares superseded_by {declared}, but the reverse supersedes edge resolves to {expected}")


def check_fact_evidence(
    root: Path,
    note: Note,
    findings: list[Finding],
    known_evidence: set[str] | None = None,
) -> None:
    """Fact callouts must cite evidence, and the evidence they cite must exist.

    `known_evidence` is every active or retired register ID; None skips the
    existence check (callers without a register view).
    """
    for line, cited in note.fact_callouts:
        if not cited:
            add(findings, root, note.path, line, "warning", "fact.inline_evidence", "verified fact callout has no inline E-NNN citation")
        elif known_evidence is not None:
            for eid in cited:
                if eid not in known_evidence:
                    add(
                        findings, root, note.path, line, "warning", "fact.evidence_missing",
                        f"fact callout cites {eid}, but the evidence register has no populated row for it",
                    )


AGENTS_TODO_RE = re.compile(r"(?m)^\s*(?:[-*]\s*|\d+[.)]\s*|#{1,6}\s*)?TODO\b[ :\u2014-]")


def check_path_collisions(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    """Paths that only a case-sensitive, normalization-sensitive filesystem keeps apart.

    A Linux checkout can hold `notes/Plan.md` and `notes/plan.md` (or an NFC and
    an NFD spelling of `café.md`); Git on macOS and Windows checks out only one.
    """
    groups: dict[str, list[Path]] = {}
    for note in notes:
        key = _relative_key(root, note.path)
        if key is not None:
            groups.setdefault(key, []).append(note.path)
    for paths in groups.values():
        if len(paths) < 2:
            continue
        ordered = sorted(paths, key=lambda path: path.relative_to(root).as_posix())
        first = ordered[0].relative_to(root).as_posix()
        for path in ordered[1:]:
            add(findings, root, path, None, "warning", "path.case_collision",
                f"path differs from {first} only by case or Unicode normalization; macOS and Windows checkouts keep one of them")


def check_agents_configured(root: Path, findings: list[Finding]) -> None:
    """The agent contract ships with blanks on purpose. Unfilled, it is worse than absent.

    A vault that never answers "which language", "commit or PR", "what may an agent
    touch" hands those decisions back to the agent, which will invent an answer.
    """
    path = root / "AGENTS.md"
    if not path.exists():
        add(findings, root, root / "AGENTS.md", None, "warning", "agents.absent",
            "no AGENTS.md — agents working here have no written contract")
        return
    text = read_vault_text(path)[0]
    numbers = _LineNumbers(text)
    for match in AGENTS_TODO_RE.finditer(_mask_code(text)):
        line = numbers.at(match.start())
        add(findings, root, path, line, "warning", "agents.unconfigured",
            "AGENTS.md still has an unanswered TODO — adopters must fill these before trusting the contract")


def check_decision_review(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    """An accepted decision with no review date is how a vault quietly goes stale.

    The record stays valid-looking forever while the world moves; nobody is ever
    prompted to ask whether it still holds. Shape cannot verify truth, but it can
    insist that somebody named a date on which truth gets re-checked.
    """
    for note in notes:
        r = Path(rel(root, note.path))
        if "06-decisions" not in r.parts or not DECISION_FILE_RE.match(r.name):
            continue
        if note.front.get("status") != "approved":
            continue
        provenance = note.front.get("provenance")
        if isinstance(provenance, dict) and "human_reviewed" in provenance and provenance["human_reviewed"] is not True:
            add(findings, root, note.path, 1, "warning", "decision.unreviewed",
                "approved decision declares it was not human-reviewed — keep it in review until an authorized person checks the record")
        review_by = str(note.front.get("review_by", "")).strip()
        if not review_by or review_by == PLACEHOLDER_DATE:
            add(findings, root, note.path, 1, "warning", "decision.review_missing",
                "approved decision has no `review_by` date — nothing will ever prompt a re-check")


# Statuses whose decision records must not keep template prompts. Superseded
# and archived records are history and may not be edited, so they are skipped;
# templates are meant to hold prompts. Every level is a warning for now (new
# rules land as warnings); `--strict` and the `ci` profile still fail on it.
PLACEHOLDER_LEVELS = {"approved": "warning", "in_review": "warning", "draft": "warning"}
_BARE_LINES = frozenset({"", "-", "*", "+"})


def _decision_placeholders(note: Note) -> list[tuple[int, str]]:
    """``(line, section)`` for each section still holding template placeholders.

    Headings and placeholder prose are read from the code-masked text, so a
    record that quotes the template inside a code block is not flagged. Whether
    a section is empty is judged on the raw text, so a section holding only a
    code block still counts as written.
    """
    masked = note.masked.split("\n")
    raw = note.text.split("\n")
    prompts = template_section_prompts()
    found: list[tuple[int, str]] = []
    for heading, start, end in split_placeholder_sections(masked):
        body = masked[start:end]
        known = prompts.get(heading, frozenset())
        if heading in PLACEHOLDER_PROSE_SECTIONS:
            # A prompt left on its own line above real prose is still a leftover.
            hits = [start + index + 1 for index, text in placeholder_paragraphs(body) if text in known]
            hits += [start + index + 1 for index, line in enumerate(body) if normalize_placeholder(line) in known]
            if hits:
                found.append((min(hits), heading))
            elif all(line.strip() in _BARE_LINES for line in raw[start:end]):
                found.append((start, heading))
        elif heading == "Evidence":
            hits = [start + index + 1 for index, line in enumerate(body) if line.strip() == SCAFFOLD_EVIDENCE_TODO]
            if hits:
                found.append((hits[0], heading))
        elif heading == "Alternatives considered":
            rows = [line.strip() for line in body if line.strip().startswith("|")]
            filled = [row for row in rows[2:] if any(cell.strip() for cell in row.strip("|").split("|"))]
            prose = [text for _, text in placeholder_paragraphs(body) if text not in known]
            if rows and not filled and not prose:
                found.append((start, heading))
        elif heading == "Consequences":
            if all(line.strip() in _BARE_LINES or line.lstrip().startswith("#") for line in raw[start:end]):
                found.append((start, heading))
    return found


def check_decision_placeholders(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    """Template prose left in a decision record that is meant to be read.

    Shape alone cannot tell whether a rationale is good, but it can tell that
    the record still says "State the choice in one sentence." The prompts are
    the ones WhyKit itself writes (see ``whykit.placeholders``), so this rule
    follows any change to the scaffold or the shipped template.
    """
    for note in notes:
        r = Path(rel(root, note.path))
        if "06-decisions" not in r.parts or not DECISION_FILE_RE.match(r.name):
            continue
        status = str(note.front.get("status") or "").strip()
        level = PLACEHOLDER_LEVELS.get(status)
        if level is None:
            continue
        leftovers = note.decision_placeholders
        if not leftovers:
            continue
        sections = ", ".join(f"`## {heading}`" for _, heading in leftovers)
        add(
            findings, root, note.path, leftovers[0][0], level, "decision.placeholder",
            f"{status} decision still has template text or nothing in {sections} — write the real content",
        )


REVIEW_OUTCOMES = {"confirmed", "update-required", "supersede-required", "archived", "approved"}

def check_config(root: Path, findings: list[Finding]) -> None:
    path = root / CONFIG_FILE
    if not path.exists():
        return
    try:
        load_config(root)
    except Exception as exc:  # noqa: BLE001
        add(findings, root, path, 1, "error", "config.invalid", str(exc))


def check_review_log(root: Path, notes: list[Note], findings: list[Finding], resolve: Resolver | None = None) -> None:
    path = root / "00-context" / "review-log.md"
    if not path.exists():
        return
    real = _real(path)
    note = next((item for item in notes if _real(item.path) == real), None) or load_note(path)
    lines = note.text.splitlines()
    header_idx = review_table_header(lines)
    if header_idx is None or header_idx + 1 >= len(lines):
        add(findings, root, path, None, "error", "review_log.table", "review log table is missing or malformed")
        return
    if resolve is None:
        resolve = functools.partial(_resolve, root, index=_build_index(notes))
    i = header_idx + 2
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = _split_table_row(lines[i])
        line_no = i + 1
        if len(cells) < 7:
            add(findings, root, path, line_no, "error", "review_log.row", "review log row must have seven columns")
            i += 1
            continue
        date, target_cell, reviewer, outcome, _previous, next_review, _note = cells[:7]
        if _parse_date(date) is None:
            add(findings, root, path, line_no, "error", "review_log.date", f"invalid review date `{date}`")
        if not reviewer.strip() or reviewer.strip() == PLACEHOLDER_OWNER:
            add(findings, root, path, line_no, "error", "review_log.reviewer", "review event must name a real reviewer")
        if outcome not in REVIEW_OUTCOMES:
            add(findings, root, path, line_no, "error", "review_log.outcome", f"unsupported review outcome `{outcome}`")
        if next_review not in {"", "—", "-"} and _parse_date(next_review) is None:
            add(findings, root, path, line_no, "error", "review_log.next_review", f"invalid next-review date `{next_review}`")
        match = WIKILINK_RE.search(target_cell)
        if not match:
            add(findings, root, path, line_no, "error", "review_log.target", "review event target must be a wikilink")
        else:
            resolved, ambiguous = resolve(match.group(1).strip())
            if resolved is None or ambiguous:
                add(findings, root, path, line_no, "error", "review_log.target", "review event target is missing or ambiguous")
        i += 1

def check_orphans(root: Path, notes: list[Note], findings: list[Finding], resolve: Resolver | None = None) -> None:
    exempt_names = {"README.md", "AGENTS.md", "OBSIDIAN.md", "INTEROP.md", "Home.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"}
    linked: set[Path] = set()
    if resolve is None:
        resolve = functools.partial(_resolve, root, index=_build_index(notes))
    for note in notes:
        for target, _line, _embed in note.wikilink_hits:
            resolved, ambiguous = resolve(target)
            if resolved and not ambiguous:
                linked.add(_real(resolved))
    for note in notes:
        r = Path(rel(root, note.path))
        if r.name in exempt_names or "templates" in r.parts:
            continue
        if _real(note.path) not in linked:
            add(findings, root, note.path, None, "warning", "note.orphan", "nothing links to this note — add it to Home.md or a workstream map")


def check_hub_links(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    """Require Home.md to wikilink into each top-level working directory."""
    home = next((note for note in notes if rel(root, note.path) == "Home.md"), None)
    if home is None:
        return
    linked_prefixes: set[str] = set()
    for match in WIKILINK_RE.finditer(_mask_code(home.text)):
        target = match.group(1).strip().replace("\\", "/")
        if not target:
            continue
        linked_prefixes.add(target.split("/", 1)[0])
        linked_prefixes.add(target)
    skip = {
        "assets", "templates", "reports", "schemas", "tests", "apps", "examples",
        ".git", ".obsidian", ".import-staging", ".whykit", "node_modules",
    }
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in skip or child.name.startswith("."):
            continue
        # Foundations and decisions are linked by path in the default Home maps.
        marker = child.name
        hit = any(
            prefix == marker or prefix.startswith(marker + "/")
            for prefix in linked_prefixes
        )
        if not hit:
            add(
                findings,
                root,
                home.path,
                None,
                "warning",
                "hub.unlinked_workstream",
                f"Home.md does not link into `{marker}/` — add a map link or remove the empty folder",
            )


def _text_files_for_secret_scan(root: Path) -> Iterable[tuple[Path, int]]:
    """Yield ``(path, size)`` for every regular text file the secret scan covers.

    The name filters run before the ``stat``: they are pure string tests, and
    most files in a large vault pass them, but no file is stat-ed twice.
    """
    depth = len(root.parts)
    for p in root.rglob("*"):
        if any(part in SECRET_SKIP_DIRS for part in p.parts[depth:]):
            continue
        if not (p.name.startswith(".env") or p.suffix.lower() in TEXT_SECRET_EXTENSIONS):
            continue
        try:
            info = p.stat()  # follows symlinks, like Path.is_file()
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode) and _within(root, p):
            yield p, info.st_size


def check_secrets(
    root: Path,
    findings: list[Finding],
    loaded: dict[Path, str] | None = None,
    *,
    notes: dict[Path, Note] | None = None,
) -> None:
    """Scan every text file under *root* for credential-shaped strings.

    *loaded* maps a note's path to the text the vault index already read, so a
    Markdown note is not read from disk a second time.  The index stripped a
    leading byte-order mark; no pattern can match one or depends on it, so the
    findings (including line numbers) are the same as for the raw file.
    *notes* maps a path to its parsed note instead, whose ``secret_hits`` are
    the same matches computed from the same text.
    """
    max_bytes = 5_000_000
    for path, size in _text_files_for_secret_scan(root):
        try:
            if size > max_bytes:
                add(
                    findings,
                    root,
                    path,
                    None,
                    "error",
                    "secret.scan_skipped_large_file",
                    (
                        f"secret scan skipped {size} byte file; "
                        "large files must be scanned externally or excluded explicitly"
                    ),
                )
                continue
            note = notes.get(path) if notes else None
            if note is not None:
                for line, label in note.secret_hits:
                    add(findings, root, path, line, "error", "secret.detected", f"looks like a {label} — keep locations, never secret values")
                continue
            text = loaded.get(path) if loaded else None
            if text is None:
                text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            add(
                findings,
                root,
                path,
                None,
                "error",
                "secret.scan_non_utf8",
                "secret scan cannot verify this non-UTF-8 file",
            )
            continue
        except OSError as exc:
            add(
                findings,
                root,
                path,
                None,
                "error",
                "secret.scan_unreadable",
                f"secret scan could not read file: {exc}",
            )
            continue
        for (label, pattern), prefilter in zip(SECRET_PATTERNS, SECRET_PREFILTERS, strict=True):
            if not prefilter(text):
                continue
            numbers = _LineNumbers(text)
            for match in pattern.finditer(text):
                line = numbers.at(match.start())
                add(findings, root, path, line, "error", "secret.detected", f"looks like a {label} — keep locations, never secret values")


def collect_markdown(root: Path, paths: list[str]) -> list[Path]:
    root = root.resolve()
    if paths:
        out: list[Path] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = root / p
            p = p.resolve()
            if not _within(root, p):
                raise VaultPathError(f"path escapes vault root: {raw}")
            if p.is_dir():
                out.extend(sorted(
                    child for child in iter_markdown(p)
                    if child.is_file() and _within(root, child)
                ))
            elif is_markdown_name(p.name) and p.is_file():
                out.append(p)
            elif not p.exists():
                # A typo in a CI path list must not turn into "0 files — clean".
                raise VaultPathError(f"path not found: {raw}")
            else:
                raise VaultPathError(f"not a Markdown file or directory: {raw}")
        return out
    depth = len(root.parts)
    return sorted(
        (
            p for p in iter_markdown(root)
            if p.is_file()
            and _within(root, p)
            and not any(part in CONTENT_SKIP_DIRS for part in p.parts[depth:])
        ),
        key=_path_order,
    )


def _path_order(path: Path) -> list[str]:
    """Sort key giving the same order as comparing the paths themselves.

    ``PurePath.__lt__`` compares the case-normalised string split on the
    separator, and caches that list on each path for the life of the object.
    The paths of a vault index live as long as the index, so sorting them
    directly would keep a second copy of every path's parts.
    """
    return os.path.normcase(os.fspath(path)).split(os.sep)


@path_cache()
def lint(
    root: Path,
    paths: list[str] | None = None,
    *,
    orphans: bool = True,
    secrets: bool = True,
    hub_links: bool | None = None,
    today: dt.date | None = None,
    vault: object | None = None,
    overrides: list[Override] | None = None,
    config: dict | None = None,
) -> tuple[list[Path], list[Finding]]:
    """Lint *root* (or the scoped *paths* inside it).

    The vault's team policy from ``whykit.toml`` is part of the result: its
    custom rules run with the built-in ones and its overrides are applied last.
    Pass a list as *overrides* to receive the override entries in effect, each
    counting what it changed and holding the findings it suppressed.
    """
    root = root.resolve()
    from .vault_index import VaultIndex

    index_model = vault if isinstance(vault, VaultIndex) else VaultIndex.load(root)
    all_notes = index_model.notes
    all_files = [note.path for note in all_notes]
    if paths:
        files = collect_markdown(root, paths)
        selected = {_real(path) for path in files}
        notes = [note for note in all_notes if _real(note.path) in selected]
    else:
        files = all_files
        notes = all_notes
    index = index_model.link_index
    findings: list[Finding] = []
    today = today or dt.date.today()
    attachments = AttachmentIndex(root)
    active_evidence, retired_evidence, _ = evidence_register(root)
    known_evidence = set(active_evidence) | set(retired_evidence)
    link_exists: Callable[[Path], bool] | None = None
    if getattr(index_model, "confined", False):
        def _confined_exists(candidate: Path) -> bool:
            # A confined index hides some notes; a link to one must look the
            # same as a link to a file that does not exist.
            if candidate.suffix.lower() == ".md":
                return index_model.note_for(candidate) is not None
            return path_exists(candidate)

        link_exists = _confined_exists

    for note in notes:
        check_front_matter(root, note, findings, today)
        # The index memoises link resolution: a vault repeats the same targets
        # many times, and each uncached lookup costs several filesystem calls.
        check_wikilinks(root, note, index, findings, attachments, index_model.resolve_link)
        check_markdown_links(root, note, findings, link_exists)
        check_fact_evidence(root, note, findings, known_evidence)
    check_evidence_register(root, findings, today, config)
    check_evidence_ids(root, notes, findings)
    check_decision_ids(root, notes, findings)
    check_decision_log(root, all_notes, findings, index_model.resolve_link)
    check_decision_review(root, notes, findings)
    check_decision_placeholders(root, notes, findings)
    if config is None:
        try:
            config = load_config(root)[0]
        except Exception:  # noqa: BLE001 — reported as config.invalid by check_config
            config = None
    policy = policy_from_config(config) if config is not None else EMPTY_POLICY
    if policy.custom:
        check_custom_rules(policy, notes, findings, today, lambda path: rel(root, path))
    if not paths:
        check_config(root, findings)
        check_path_collisions(root, all_notes, findings)
        check_review_log(root, all_notes, findings, index_model.resolve_link)
        check_agents_configured(root, findings)
    if orphans and not paths:
        check_orphans(root, notes, findings, index_model.resolve_link)
    if hub_links is None and not paths:
        hub_links = bool(config.get("defaults", {}).get("require_hub_links")) if config is not None else False
    if hub_links and not paths:
        check_hub_links(root, all_notes, findings)
    if secrets and not paths:
        check_secrets(root, findings, notes={note.path: note for note in all_notes})
    findings = policy.apply_overrides(findings)
    if overrides is not None:
        overrides.extend(policy.overrides)
    return files, findings


def resolve_root(explicit: str | None, paths: list[str], cwd: Path | None = None) -> tuple[Path, list[str], str | None]:
    """Work out which vault is being linted, and say so when it was not obvious.

    Three cases, in order:
      1. --root wins, always.
      2. A single positional argument that is itself a vault root becomes the root.
         Without this, `whykit lint examples/northline` lints another vault's files
         against *this* directory's evidence register and reports every wikilink as
         broken — a first run that looks like the tool is broken.
      3. Otherwise walk up from the working directory.
    """
    cwd = (cwd or Path.cwd()).resolve()
    if explicit:
        return Path(explicit).expanduser().resolve(), paths, None

    if len(paths) == 1:
        candidate = (cwd / paths[0]).resolve()
        if candidate.is_dir() and is_vault_root(candidate):
            return candidate, [], f"using {paths[0]} as the vault root"

    found = find_vault_root(cwd)
    if found:
        note = None if found == cwd else f"vault root: {found}"
        return found, paths, note
    return cwd, paths, None


LINT_FORMATS = ("text", "json", "sarif", "github")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit lint", description="Check a WhyKit vault.")
    parser.add_argument("paths", nargs="*", help="Markdown files/directories to check")
    parser.add_argument("--root", help="vault root (default: nearest vault at or above the working directory)")
    parser.add_argument("--strict", action="store_true", help="warnings count as failures")
    parser.add_argument("--quiet", action="store_true", help="only print summary")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--no-orphans", action="store_true", help="skip orphan-note warnings")
    parser.add_argument("--no-secrets", action="store_true", help="skip secret scan")
    parser.add_argument("--today", help="evaluate review dates as of this ISO date instead of today")
    parser.add_argument("--format", choices=LINT_FORMATS, default=None, help="output format (default: text)")
    args = parser.parse_args(argv)
    from .contract import emit_error, vault_not_found

    if args.json and args.format not in (None, "json"):
        return emit_error(
            "usage",
            f"--json conflicts with --format {args.format}\nhint: pass one of them to `whykit lint`",
            json_mode=True,
        )
    fmt = "json" if args.json else (args.format or "text")
    # Only the JSON format has an error object; a SARIF or annotation consumer
    # gets the human error on stderr and an empty stdout.
    json_mode = fmt == "json"

    as_of = None
    if args.today:
        as_of = _parse_date(args.today)
        if as_of is None:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=json_mode)

    root, paths, note = resolve_root(args.root, list(args.paths))
    if not is_vault_root(root):
        return vault_not_found(args.root, json_mode=json_mode)

    try:
        applied: list[Override] = []
        files, findings = lint(
            root, paths, orphans=not args.no_orphans, secrets=not args.no_secrets, today=as_of, overrides=applied,
        )
        if args.no_secrets:
            applied.append(secret_scan_skipped("--no-secrets"))
    except VaultPathError as exc:
        return emit_error(
            "invalid_argument",
            str(exc),
            hint=f"hint: paths are relative to the vault root ({root}); lint checks .md files and directories",
            json_mode=json_mode,
        )
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    failed = bool(errors or (warnings and args.strict))

    security_overrides = [entry for entry in applied if entry.security]
    if fmt == "sarif":
        from .ci_formats import to_sarif
        emit_machine(json.dumps(
            to_sarif([asdict(f) for f in findings], root, files=len(files), overrides=applied),
            ensure_ascii=False, indent=2,
        ))
    elif fmt == "github":
        from .ci_formats import github_annotations, override_notices, workflow_command
        lines = github_annotations([asdict(f) for f in findings], root)
        lines.extend(override_notices(security_overrides))
        summary = f"{len(files)} files — {len(errors)} error(s), {len(warnings)} warning(s)"
        lines.append(f"whykit lint: {summary}")
        if failed and not errors:
            lines.append(workflow_command("error", f"whykit lint --strict: {len(warnings)} warning(s) fail this gate", title="WhyKit"))
        emit_machine("\n".join(lines))
    elif fmt == "json":
        payload: dict = {
            "contract_version": 1,
            "root": str(root),
            "files": len(files),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [asdict(f) for f in findings],
        }
        if applied:
            payload["overrides"] = [entry.report() for entry in applied]
        emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if note and not args.quiet:
            print(note)
        if not args.quiet:
            by_path: dict[str, list[Finding]] = {}
            for f in findings:
                by_path.setdefault(f.path, []).append(f)
            for path in sorted(by_path):
                print(f"\n{one_line(path)}")
                for f in sorted(by_path[path], key=lambda x: (x.line or 0, x.code)):
                    where = f"line {f.line}" if f.line else ""
                    print(f"  {f.level:<7}  {where:<10} [{f.code}] {one_line(f.message)}")
        print(f"\n{len(files)} files — {len(errors)} error(s), {len(warnings)} warning(s)" if findings else f"\n{len(files)} files — clean")
        # Printed even with --quiet: a security rule turned down by policy is
        # part of what this result means, not detail.
        for entry in security_overrides:
            print(f"policy: {entry.describe()}")

    return 1 if failed else 0
