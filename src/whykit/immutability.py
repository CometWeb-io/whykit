#!/usr/bin/env python3
"""Reject semantic rewrites of historical decision records on a pull request.

Decision *reasoning* becomes immutable once approved. A small lifecycle envelope
remains mutable so the system can be operated without falsifying history:

- an approved decision may move to ``superseded`` or ``archived`` while only
  lifecycle metadata changes;
- an approved decision may move its next ``review_by`` date after a re-review;
- superseded/archived records are fully immutable.

Everything else must be represented by a new D-NNN record.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RECORD_RE = re.compile(r"(?:^|/)06-decisions/d-\d{3,}-.+\.md$")
RECORD_SUFFIX = "06-decisions/"
REVIEW_LOG_SUFFIX = "00-context/review-log.md"
STATUS_VALUE_RE = re.compile(
    r'(?mi)^status:\s*(?:"(approved|superseded|archived)"|\'(approved|superseded|archived)\'|(approved|superseded|archived))\s*(?:#.*)?$'
)
STATUS_KEY_RE = re.compile(r"(?m)^status\s*:")


def git(*args: str, root: str | None = None) -> str:
    prefix = ["-C", root] if root else []
    return subprocess.run(["git", *prefix, *args], check=True, capture_output=True, text=True).stdout


def _git_prefix(root: str | None) -> str:
    """Return the path from the Git work tree to *root*, with a trailing slash.

    When the vault lives in a subdirectory (``examples/northline``), ``git diff``
    and ``git show`` speak in work-tree-relative paths. History checks must use
    the same coordinate system or they silently skip every decision record.
    """
    if not root:
        return ""
    try:
        prefix = git("rev-parse", "--show-prefix", root=root).strip().replace("\\", "/")
    except subprocess.CalledProcessError:
        return ""
    if not prefix:
        # Fallback for unusual layouts: compute relative to the work-tree root.
        try:
            work_tree = git("rev-parse", "--show-toplevel", root=root).strip()
            rel = Path(root).resolve().relative_to(Path(work_tree).resolve()).as_posix()
            prefix = "" if rel in {"", "."} else f"{rel}/"
        except (OSError, ValueError, subprocess.CalledProcessError):
            return ""
    return prefix if prefix.endswith("/") or prefix == "" else f"{prefix}/"


def _vault_rel(path: str, prefix: str) -> str:
    normalized = path.replace("\\", "/")
    if prefix and normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized


def _git_object_path(vault_rel: str, prefix: str) -> str:
    return f"{prefix}{vault_rel}" if prefix else vault_rel


def _status(text: str) -> str | None:
    matches = list(STATUS_VALUE_RE.finditer(text))
    if len(matches) != 1 or len(STATUS_KEY_RE.findall(text)) != 1:
        return None
    match = matches[0]
    return next((group for group in match.groups() if group), None)


def _without_frontmatter_keys(text: str, keys: set[str]) -> str | None:
    """Remove selected *top-level* front-matter lines for semantic comparison."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return None
    key_re = re.compile(r"^(" + "|".join(re.escape(key) for key in sorted(keys)) + r")\s*:")
    kept = [lines[0]]
    for line in lines[1:end]:
        if key_re.match(line):
            continue
        kept.append(line)
    kept.extend(lines[end:])
    return "".join(kept)


def allowed_lifecycle_change(base_text: str, head_text: str) -> bool:
    """Return True only for explicitly allowed metadata-only lifecycle changes."""
    base_status = _status(base_text)
    head_status = _status(head_text)
    if base_status != "approved" or head_status is None:
        return False

    if head_status == "approved":
        # Re-reviewing an unchanged decision may move only the operational review
        # date (and last_updated). Its rationale/evidence/ownership remain frozen.
        allowed = {"review_by", "last_updated"}
    elif head_status in {"superseded", "archived"}:
        # Lifecycle transition only. `superseded_by` is an optional convenience;
        # the authoritative reverse edge remains the newer record's `supersedes`.
        allowed = {"status", "last_updated", "superseded_by"}
    else:
        return False

    base_normalized = _without_frontmatter_keys(base_text, allowed)
    head_normalized = _without_frontmatter_keys(head_text, allowed)
    return base_normalized is not None and base_normalized == head_normalized


def _review_log_parts(text: str) -> tuple[list[str], list[str], list[str]] | None:
    normalized = _without_frontmatter_keys(text, {"last_updated"})
    if normalized is None:
        return None
    lines = normalized.splitlines(keepends=True)
    header = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
    header_idx = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if header_idx is None or header_idx + 1 >= len(lines):
        return None
    row_start = header_idx + 2
    row_end = row_start
    while row_end < len(lines) and lines[row_end].lstrip().startswith("|"):
        row_end += 1
    return lines[:row_start], lines[row_start:row_end], lines[row_end:]


def allowed_review_log_append(base_text: str, head_text: str) -> bool:
    """Allow only new table rows plus a last_updated refresh."""
    base = _review_log_parts(base_text)
    head = _review_log_parts(head_text)
    if base is None or head is None:
        return False
    base_before, base_rows, base_after = base
    head_before, head_rows, head_after = head
    return (
        base_before == head_before
        and base_after == head_after
        and len(head_rows) >= len(base_rows)
        and head_rows[: len(base_rows)] == base_rows
    )


def immutable_at(base: str, path: str, root: str | None = None, *, prefix: str | None = None) -> bool:
    git_prefix = _git_prefix(root) if prefix is None else prefix
    try:
        content = git("show", f"{base}:{_git_object_path(path, git_prefix)}", root=root)
    except subprocess.CalledProcessError:
        return False
    return _status(content) in {"approved", "superseded", "archived"}


def changed_records(base: str, head: str, root: str | None = None) -> list[tuple[str, str]]:
    prefix = _git_prefix(root)
    # Prefer --relative so paths are vault-rooted when `root` is a subdirectory.
    # Fall back to stripping the prefix manually if an older Git rejects the flag
    # combination (should not happen on CI runners).
    try:
        out = git(
            "diff",
            "--relative",
            "--name-status",
            f"{base}...{head}",
            "--",
            RECORD_SUFFIX.rstrip("/"),
            REVIEW_LOG_SUFFIX,
            root=root,
        )
        relative_paths = True
    except subprocess.CalledProcessError:
        out = git(
            "diff",
            "--name-status",
            f"{base}...{head}",
            "--",
            _git_object_path(RECORD_SUFFIX.rstrip("/"), prefix),
            _git_object_path(REVIEW_LOG_SUFFIX, prefix),
            root=root,
        )
        relative_paths = False

    blocked: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        kind = status[0]
        if kind in {"R", "C"} and len(parts) >= 3:
            old_raw, new_raw = parts[1], parts[2]
            display_path = f"{old_raw} -> {new_raw}"
        else:
            old_raw = parts[1] if len(parts) > 1 else ""
            new_raw = old_raw
            display_path = old_raw

        old_path = old_raw if relative_paths else _vault_rel(old_raw, prefix)
        new_path = new_raw if relative_paths else _vault_rel(new_raw, prefix)

        if kind == "A":
            continue
        if old_path == REVIEW_LOG_SUFFIX or old_path.endswith("/" + REVIEW_LOG_SUFFIX):
            old_path = REVIEW_LOG_SUFFIX
            new_path = REVIEW_LOG_SUFFIX if new_path.endswith(REVIEW_LOG_SUFFIX.split("/")[-1]) else new_path
            if kind == "M":
                try:
                    base_text = git("show", f"{base}:{_git_object_path(old_path, prefix)}", root=root)
                    head_text = git("show", f"{head}:{_git_object_path(new_path, prefix)}", root=root)
                except subprocess.CalledProcessError:
                    blocked.append((status, display_path))
                    continue
                if allowed_review_log_append(base_text, head_text):
                    continue
            blocked.append((status, display_path))
            continue
        if not RECORD_RE.search(old_path.replace("\\", "/")):
            continue
        # Normalize to vault-relative decision path for git show.
        if not old_path.startswith("06-decisions/"):
            idx = old_path.find("06-decisions/")
            if idx >= 0:
                old_path = old_path[idx:]
        if not new_path.startswith("06-decisions/"):
            idx = new_path.find("06-decisions/")
            if idx >= 0:
                new_path = new_path[idx:]
        # A copy does not rewrite the old record. The regular linter is
        # responsible for rejecting the duplicated decision_id on the new file.
        if kind == "C":
            continue
        if not immutable_at(base, old_path, root, prefix=prefix):
            continue

        if kind == "M":
            try:
                base_text = git("show", f"{base}:{_git_object_path(old_path, prefix)}", root=root)
                head_text = git("show", f"{head}:{_git_object_path(new_path, prefix)}", root=root)
            except subprocess.CalledProcessError:
                blocked.append((status, display_path))
                continue
            if allowed_lifecycle_change(base_text, head_text):
                continue

        # Rename/delete/type-change or any semantic edit of an immutable record.
        blocked.append((status, display_path))
    return blocked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit history",
        description="Reject semantic rewrites of historical decisions and review history.",
    )
    parser.add_argument("--base", required=True, help="base commit/ref")
    parser.add_argument("--head", default="HEAD", help="head commit/ref")
    parser.add_argument("--root", help="vault root (may be a subdirectory of the Git work tree)")
    args = parser.parse_args(argv)
    try:
        blocked = changed_records(args.base, args.head, args.root)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return 2
    if not blocked:
        print("history: immutable reasoning unchanged; review log append-only")
        return 0
    print("Historical decision reasoning is append-only. Supersede; do not rewrite:", file=sys.stderr)
    for status, path in blocked:
        print(f"  {status}\t{path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
