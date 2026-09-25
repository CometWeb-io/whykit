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

import argparse
import datetime as dt
import json
import re
import sys
from urllib.parse import unquote
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .config import CONFIG_FILE, ConfigError, load_config

VAULT_MARKERS = ("Home.md", "00-context")


class VaultPathError(ValueError):
    """Raised when a requested lint path escapes the selected vault root."""


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


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
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
EVIDENCE_ID_RE = re.compile(r"\bE-\d{3,}\b")
DECISION_ID_RE = re.compile(r"\bD-\d{3,}\b")
DECISION_FILE_RE = re.compile(r"^d-(\d{3,})-")
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
CONTENT_SKIP_DIRS = {
    ".git", ".obsidian", ".import-staging", "node_modules", "__pycache__",
    "apps", "examples", "tests", ".github", "schemas",
}
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
    body_offset: int = 0


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


def _yaml_scalar(value: str) -> object:
    import json as _json

    value = _strip_yaml_comment(value).strip()
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_yaml_scalar(item) for item in _split_inline_list(inner)] if inner else []
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


def _parse_front_matter(raw: str) -> dict:
    """Parse the deliberately small YAML subset supported by WhyKit.

    Supported: top-level scalars/lists, block lists, and one nested mapping
    level (used by `provenance`), including block lists inside that mapping.
    Rich YAML features such as anchors, folded scalars and arbitrary nesting are
    rejected instead of behaving differently depending on installed packages.
    """
    out: dict = {}
    current_top: str | None = None
    current_nested: str | None = None

    for raw_line in raw.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ValueError("tabs are not supported for YAML indentation")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()

        if indent == 0:
            if ":" not in content:
                raise ValueError(f"cannot read line: {raw_line!r}")
            key, _, value = content.partition(":")
            key = key.strip()
            if not key:
                raise ValueError("front matter key cannot be empty")
            if key in out:
                raise ValueError(f"duplicate front matter key: {key!r}")
            value = value.strip()
            current_top = key
            current_nested = None
            out[key] = None if value == "" else _yaml_scalar(value)
            continue

        if indent == 2 and current_top:
            parent = out.get(current_top)
            if content.startswith("- "):
                if parent is None:
                    parent = []
                    out[current_top] = parent
                if not isinstance(parent, list):
                    raise ValueError(f"{current_top!r} mixes mapping/scalar and list values")
                parent.append(_yaml_scalar(content[2:].strip()))
                continue
            if ":" not in content:
                raise ValueError(f"cannot read nested line: {raw_line!r}")
            if parent == []:
                raise ValueError(f"{current_top!r} mixes list and mapping values")
            if parent is None:
                parent = {}
                out[current_top] = parent
            if not isinstance(parent, dict):
                raise ValueError(f"cannot nest under scalar key {current_top!r}")
            nested_key, _, value = content.partition(":")
            nested_key = nested_key.strip()
            if not nested_key:
                raise ValueError("front matter nested key cannot be empty")
            if nested_key in parent:
                raise ValueError(f"duplicate front matter key: {current_top}.{nested_key!r}")
            value = value.strip()
            current_nested = nested_key
            parent[nested_key] = [] if value == "" else _yaml_scalar(value)
            continue

        if indent == 4 and current_top and current_nested and content.startswith("- "):
            parent = out.get(current_top)
            if not isinstance(parent, dict):
                raise ValueError(f"cannot nest list under {current_top!r}")
            nested = parent.get(current_nested)
            if nested == "":
                nested = []
                parent[current_nested] = nested
            if not isinstance(nested, list):
                raise ValueError(f"{current_top}.{current_nested} is not a list")
            nested.append(_yaml_scalar(content[2:].strip()))
            continue

        raise ValueError(f"unsupported YAML indentation/structure: {raw_line!r}")
    return out


def load_note(path: Path, *, text: str | None = None) -> Note:
    if text is None:
        text = path.read_text(encoding="utf-8")
    else:
        # Path.read_text() uses universal newlines; callers with decoded bytes
        # (notably adopt's hash-preserving scan) must see the same parser input.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    note = Note(path=path, text=text)
    if not text.startswith("---\n"):
        return note
    end = text.find("\n---\n", 4)
    if end == -1:
        note.front_error = "front matter opened with --- but never closed"
        return note
    note.has_front = True
    note.body_offset = text[: end + 5].count("\n")
    try:
        note.front = _parse_front_matter(text[4:end])
    except Exception as exc:  # noqa: BLE001
        note.front_error = f"front matter is not valid YAML: {exc}"
    return note


def rel(root: Path, path: Path) -> str:
    # Resolve both sides so macOS /var vs /private/var (and similar aliasing)
    # does not break relative paths or silently fall back to absolutes.
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


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


FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _mask_code(text: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return INLINE_CODE_RE.sub(blank, FENCE_RE.sub(blank, text))


def _build_index(notes: list[Note]) -> dict[str, set[Path]]:
    index: dict[str, set[Path]] = {}
    for note in notes:
        for key in {note.path.stem, *[a for a in _as_list(note.front.get("aliases")) if a]}:
            index.setdefault(key.casefold(), set()).add(note.path)
    return index


def _resolve(root: Path, target: str, index: dict[str, set[Path]]) -> tuple[Path | None, bool]:
    normalized = target.strip().replace("\\", "/").lstrip("/")
    if ".." in Path(normalized).parts:
        return None, False
    candidate = root / (normalized if normalized.endswith(".md") else normalized + ".md")
    if candidate.exists() and _within(root, candidate):
        return candidate, False
    stem = normalized.rstrip("/").split("/")[-1].removesuffix(".md").casefold()
    hits = {path for path in index.get(stem, set()) if _within(root, path)}
    if len(hits) == 1:
        return next(iter(hits)), False
    if len(hits) > 1:
        return None, True
    return None, False


def check_wikilinks(root: Path, note: Note, index: dict[str, set[Path]], findings: list[Finding]) -> None:
    masked = _mask_code(note.text)
    for match in WIKILINK_RE.finditer(masked):
        target = match.group(1).strip()
        if not target or target.startswith(("http://", "https://")):
            continue
        line = note.text[: match.start()].count("\n") + 1
        is_embed = match.start() > 0 and note.text[match.start() - 1] == "!"
        normalized_target = target.replace("\\", "/")
        if ".." in Path(normalized_target).parts:
            add(findings, root, note.path, line, "warning", "wikilink.outside", f"wikilink tries to leave the vault: [[{target}]]")
            continue
        resolved, ambiguous = _resolve(root, target, index)
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


def check_markdown_links(root: Path, note: Note, findings: list[Finding]) -> None:
    masked = _mask_code(note.text)
    for match in MARKDOWN_LINK_RE.finditer(masked):
        raw = match.group(1).strip()
        # Markdown permits <path with spaces>; optional titles are intentionally
        # ignored here rather than pretending to implement a full CommonMark parser.
        if raw.startswith("<") and ">" in raw:
            target = raw[1:raw.index(">")].strip()
        else:
            target = raw.split(None, 1)[0].strip() if raw else ""
        if not target or target.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target:
            continue
        candidate = (root / target.lstrip("/")) if target.startswith("/") else (note.path.parent / target)
        line = note.text[: match.start()].count("\n") + 1
        if not _within(root, candidate):
            add(findings, root, note.path, line, "warning", "markdown_link.outside", f"Markdown link leaves the vault: ({target})")
        elif not candidate.exists():
            add(findings, root, note.path, line, "warning", "markdown_link.missing", f"local Markdown link does not exist: ({target})")


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    buf: list[str] = []
    wiki_depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "\\" and nxt == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "[" and nxt == "[":
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


def evidence_register(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], list[tuple[str, int]]]:
    path = root / "00-context" / "evidence-register.md"
    if not path.exists():
        return {}, {}, []
    active: dict[str, dict[str, str]] = {}
    retired: dict[str, dict[str, str]] = {}
    occurrences: list[tuple[str, int]] = []
    mode = "active"
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().lower().startswith("## retired sources"):
            mode = "retired"
            continue
        cells = _split_table_row(line) if line.lstrip().startswith("|") else []
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
    return active, retired, occurrences


def evidence_rows(root: Path) -> dict[str, dict[str, str]]:
    return evidence_register(root)[0]


def check_evidence_register(root: Path, findings: list[Finding], today: dt.date) -> None:
    path = root / "00-context" / "evidence-register.md"
    if not path.exists():
        return
    active, retired, occurrences = evidence_register(root)
    try:
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
        value = row.get("retired_on", "")
        if value and _parse_date(value) is None:
            add(findings, root, path, int(row["line"]), "error", "evidence.retired_date", f"{eid} has invalid retired-on date `{value}`")
        replaced = row.get("replaced_by", "").strip()
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
        for sid in _as_list(note.front.get("source_ids")):
            if not EVIDENCE_ID_RE.fullmatch(sid):
                add(findings, root, note.path, 1, "error", "evidence.id_format", f"source_ids contains invalid evidence ID `{sid}`")
            elif sid in active:
                continue
            elif sid in retired:
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
    m = re.search(r"^## Decision ID\s*$\n+\s*(D-\d{3,})\s*$", note.text, re.MULTILINE)
    return m.group(1) if m else None


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
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def check_decision_log(root: Path, notes: list[Note], findings: list[Finding]) -> None:
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
    index = _build_index(notes)
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
        resolved, ambiguous = _resolve(root, target, index)
        if ambiguous:
            add(findings, root, path, line, "error", "decision_log.ambiguous", f"{did} record link [[{target}]] is ambiguous")
            continue
        if resolved is None:
            add(findings, root, path, line, "error", "decision_log.missing_record", f"{did} points to missing decision record [[{target}]]")
            continue
        target_note = next((n for n in notes if n.path.resolve() == resolved.resolve()), None)
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
    supersedes_edges: dict[str, str] = {}
    for replacement_id, replacement_note in record_notes.items():
        predecessor = str(replacement_note.front.get("supersedes", "")).strip()
        if DECISION_ID_RE.fullmatch(predecessor) and predecessor in record_notes:
            supersedes_edges[replacement_id] = predecessor
        if DECISION_ID_RE.fullmatch(predecessor) and replacement_note.front.get("status") == "approved":
            replacements.setdefault(predecessor, []).append(replacement_id)

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
        approved_replacements = sorted(replacements.get(did, []))
        declared = str(note.front.get("superseded_by", "")).strip()
        if note.front.get("status") == "superseded" and not approved_replacements:
            add(findings, root, note.path, 1, "warning", "decision.superseded_by_missing", f"{did} is superseded but no approved newer record declares `supersedes: {did}`")
        if declared:
            if not DECISION_ID_RE.fullmatch(declared):
                add(findings, root, note.path, 1, "warning", "decision.superseded_by_format", f"superseded_by `{declared}` is not a D-NNN ID")
            elif declared not in approved_replacements:
                expected = ", ".join(approved_replacements) if approved_replacements else "no approved replacement"
                add(findings, root, note.path, 1, "warning", "decision.superseded_by_mismatch", f"{did} declares superseded_by {declared}, but the reverse supersedes edge resolves to {expected}")


def check_fact_evidence(root: Path, note: Note, findings: list[Finding]) -> None:
    lines = _mask_code(note.text).splitlines()
    i = 0
    while i < len(lines):
        if re.match(r"^> \[!fact\]", lines[i], re.I):
            start = i
            block = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i])
                i += 1
            if not EVIDENCE_ID_RE.search("\n".join(block)):
                add(findings, root, note.path, start + 1, "warning", "fact.inline_evidence", "verified fact callout has no inline E-NNN citation")
            continue
        i += 1


AGENTS_TODO_RE = re.compile(r"(?m)^\s*(?:[-*]\s*|\d+[.)]\s*|#{1,6}\s*)?TODO\b[ :\u2014-]")


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
    text = path.read_text(encoding="utf-8")
    for match in AGENTS_TODO_RE.finditer(_mask_code(text)):
        line = text[: match.start()].count("\n") + 1
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
        review_by = str(note.front.get("review_by", "")).strip()
        if not review_by or review_by == PLACEHOLDER_DATE:
            add(findings, root, note.path, 1, "warning", "decision.review_missing",
                "approved decision has no `review_by` date — nothing will ever prompt a re-check")



REVIEW_OUTCOMES = {"confirmed", "update-required", "supersede-required", "archived"}

def check_config(root: Path, findings: list[Finding]) -> None:
    path = root / CONFIG_FILE
    if not path.exists():
        return
    try:
        load_config(root)
    except Exception as exc:  # noqa: BLE001
        add(findings, root, path, 1, "error", "config.invalid", str(exc))


def check_review_log(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    path = root / "00-context" / "review-log.md"
    if not path.exists():
        return
    note = next((item for item in notes if item.path.resolve() == path.resolve()), load_note(path))
    lines = note.text.splitlines()
    header = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
    header_idx = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if header_idx is None or header_idx + 1 >= len(lines):
        add(findings, root, path, None, "error", "review_log.table", "review log table is missing or malformed")
        return
    index = _build_index(notes)
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
            resolved, ambiguous = _resolve(root, match.group(1).strip(), index)
            if resolved is None or ambiguous:
                add(findings, root, path, line_no, "error", "review_log.target", "review event target is missing or ambiguous")
        i += 1

def check_orphans(root: Path, notes: list[Note], findings: list[Finding]) -> None:
    exempt_names = {"README.md", "AGENTS.md", "OBSIDIAN.md", "INTEROP.md", "Home.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"}
    linked: set[Path] = set()
    index = _build_index(notes)
    for note in notes:
        for match in WIKILINK_RE.finditer(_mask_code(note.text)):
            resolved, ambiguous = _resolve(root, match.group(1).strip(), index)
            if resolved and not ambiguous:
                linked.add(resolved.resolve())
    for note in notes:
        r = Path(rel(root, note.path))
        if r.name in exempt_names or "templates" in r.parts:
            continue
        if note.path.resolve() not in linked:
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


def _text_files_for_secret_scan(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file() or not _within(root, p):
            continue
        if any(part in SECRET_SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.name.startswith(".env") or p.suffix.lower() in TEXT_SECRET_EXTENSIONS:
            yield p


def check_secrets(root: Path, findings: list[Finding]) -> None:
    max_bytes = 5_000_000
    for path in _text_files_for_secret_scan(root):
        try:
            size = path.stat().st_size
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
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
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
                    child for child in p.rglob("*.md")
                    if child.is_file() and _within(root, child)
                ))
            elif p.suffix.lower() == ".md" and p.exists():
                out.append(p)
        return out
    return sorted(
        p for p in root.rglob("*.md")
        if p.is_file()
        and _within(root, p)
        and not any(part in CONTENT_SKIP_DIRS for part in p.relative_to(root).parts)
    )


def lint(
    root: Path,
    paths: list[str] | None = None,
    *,
    orphans: bool = True,
    secrets: bool = True,
    hub_links: bool | None = None,
    today: dt.date | None = None,
    vault: object | None = None,
) -> tuple[list[Path], list[Finding]]:
    root = root.resolve()
    from .vault_index import VaultIndex

    index_model = vault if isinstance(vault, VaultIndex) else VaultIndex.load(root)
    all_notes = index_model.notes
    all_files = [note.path for note in all_notes]
    if paths:
        files = collect_markdown(root, paths)
        selected = {path.resolve() for path in files}
        notes = [note for note in all_notes if note.path.resolve() in selected]
    else:
        files = all_files
        notes = all_notes
    index = index_model.link_index
    findings: list[Finding] = []
    today = today or dt.date.today()

    for note in notes:
        check_front_matter(root, note, findings, today)
        check_wikilinks(root, note, index, findings)
        check_markdown_links(root, note, findings)
        check_fact_evidence(root, note, findings)
    check_evidence_register(root, findings, today)
    check_evidence_ids(root, notes, findings)
    check_decision_ids(root, notes, findings)
    check_decision_log(root, all_notes, findings)
    check_decision_review(root, notes, findings)
    if not paths:
        check_config(root, findings)
        check_review_log(root, all_notes, findings)
        check_agents_configured(root, findings)
    if orphans and not paths:
        check_orphans(root, notes, findings)
    if hub_links is None and not paths:
        try:
            config, _ = load_config(root)
            hub_links = bool(config.get("defaults", {}).get("require_hub_links"))
        except Exception:  # noqa: BLE001 — config errors already emitted by check_config
            hub_links = False
    if hub_links and not paths:
        check_hub_links(root, all_notes, findings)
    if secrets and not paths:
        check_secrets(root, findings)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit lint", description="Check an WhyKit vault.")
    parser.add_argument("paths", nargs="*", help="Markdown files/directories to check")
    parser.add_argument("--root", help="vault root (default: nearest vault at or above the working directory)")
    parser.add_argument("--strict", action="store_true", help="warnings count as failures")
    parser.add_argument("--quiet", action="store_true", help="only print summary")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--no-orphans", action="store_true", help="skip orphan-note warnings")
    parser.add_argument("--no-secrets", action="store_true", help="skip secret scan")
    parser.add_argument("--today", help="evaluate review dates as of this ISO date instead of today")
    args = parser.parse_args(argv)

    as_of = None
    if args.today:
        as_of = _parse_date(args.today)
        if as_of is None:
            print(f"--today is not a real ISO date: {args.today}", file=sys.stderr)
            return 2

    root, paths, note = resolve_root(args.root, list(args.paths))
    if not is_vault_root(root):
        print(f"not an WhyKit vault: {root}", file=sys.stderr)
        print("expected Home.md and 00-context/ — run `whykit init <dir>` to create one", file=sys.stderr)
        return 2

    try:
        files, findings = lint(root, paths, orphans=not args.no_orphans, secrets=not args.no_secrets, today=as_of)
    except VaultPathError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]

    if args.json:
        print(json.dumps({
            "contract_version": 1,
            "root": str(root),
            "files": len(files),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [asdict(f) for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        if note and not args.quiet:
            print(note)
        if not args.quiet:
            by_path: dict[str, list[Finding]] = {}
            for f in findings:
                by_path.setdefault(f.path, []).append(f)
            for path in sorted(by_path):
                print(f"\n{path}")
                for f in sorted(by_path[path], key=lambda x: (x.line or 0, x.code)):
                    where = f":{f.line}" if f.line else ""
                    print(f"  {f.level:<7}{where:<6} [{f.code}] {f.message}")
        print(f"\n{len(files)} files — {len(errors)} error(s), {len(warnings)} warning(s)" if findings else f"\n{len(files)} files — clean")

    if errors or (warnings and args.strict):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
