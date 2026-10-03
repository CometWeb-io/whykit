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
import os
import re
import subprocess
import sys

from .console import emit_machine

from .contract import emit_error

RECORD_RE = re.compile(r"(?:^|/)06-decisions/d-\d{3,}-.+\.md$")
RECORD_SUFFIX = "06-decisions/"
REVIEW_LOG_SUFFIX = "00-context/review-log.md"
STATUS_VALUE_RE = re.compile(
    r'(?mi)^status:\s*(?:"(approved|superseded|archived)"|\'(approved|superseded|archived)\'|(approved|superseded|archived))\s*(?:#.*)?$'
)
STATUS_KEY_RE = re.compile(r"(?m)^status\s*:")


def _git_arg(value: str) -> str | bytes:
    # Under a C/ASCII locale the interpreter cannot encode a non-ASCII argument
    # for exec. Git treats paths as bytes, so hand them over as UTF-8 (argv
    # surrogates turn back into their original bytes). Windows needs str.
    if os.name == "nt" or value.isascii():
        return value
    return value.encode("utf-8", "surrogateescape")


def git(*args: str, root: str | None = None) -> str:
    prefix = ["-C", root] if root else []
    # Decode as UTF-8 regardless of the locale: record names and contents are
    # UTF-8, and a C/ASCII locale on a CI runner must not crash the gate.
    # surrogateescape keeps invalid bytes distinct, so two different blobs
    # never compare equal after decoding, and paths round-trip back to Git.
    return subprocess.run(
        [_git_arg(arg) for arg in ("git", *prefix, *args)], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="surrogateescape",
    ).stdout


def _git_prefix(root: str | None) -> str:
    """Return the path from the Git work tree to *root*, with a trailing slash.

    When the vault lives in a subdirectory (``examples/northline``), ``git diff
    --relative`` speaks in vault-relative paths but ``git show REV:path`` needs
    work-tree-relative ones. History checks must convert between the two or
    they silently skip every decision record. Without *root* the vault is the
    working directory, which may itself be a subdirectory.
    """
    try:
        prefix = git("rev-parse", "--show-prefix", root=root).strip().replace("\\", "/")
    except subprocess.CalledProcessError:
        return ""
    return prefix if not prefix or prefix.endswith("/") else f"{prefix}/"


def _git_object_path(vault_rel: str, prefix: str) -> str:
    return f"{prefix}{vault_rel}" if prefix else vault_rel


def _front_matter_lines(text: str) -> tuple[list[str], int] | None:
    """Split *text* into lines and return them with the closing-fence index.

    Mirrors the linter: a UTF-8 byte-order mark is not content, and the front
    matter is the block between a leading ``---`` line and the next one.
    """
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return None
    return lines, end


def _status(text: str) -> str | None:
    """Return the lifecycle status declared in the front matter, if unambiguous.

    Only the front matter counts. A ``status:`` line in the body (a YAML
    example in a code fence, say) must neither hide nor fake a status.
    """
    parsed = _front_matter_lines(text)
    if parsed is None:
        return None
    lines, end = parsed
    front = "".join(lines[1:end])
    matches = list(STATUS_VALUE_RE.finditer(front))
    if len(matches) != 1 or len(STATUS_KEY_RE.findall(front)) != 1:
        return None
    return next(group for group in matches[0].groups() if group).lower()


def _without_frontmatter_keys(text: str, keys: set[str]) -> str | None:
    """Remove selected *top-level* front-matter lines for semantic comparison."""
    parsed = _front_matter_lines(text)
    if parsed is None:
        return None
    lines, end = parsed
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
    else:
        # superseded/archived (the only other values _status returns).
        # Lifecycle transition only. `superseded_by` is an optional convenience;
        # the authoritative reverse edge remains the newer record's `supersedes`.
        allowed = {"status", "last_updated", "superseded_by"}

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


def _require_revisions(root: str | None, *refs: str) -> None:
    """Fail with an actionable message before ``git diff`` dumps its own usage.

    The error is a ``CalledProcessError`` so callers that already handle Git
    failures (``whykit check`` included) report it without new plumbing.
    """
    where = root or os.getcwd()
    try:
        git("rev-parse", "--is-inside-work-tree", root=root)
    except subprocess.CalledProcessError as exc:
        raise subprocess.CalledProcessError(
            exc.returncode, exc.cmd, output="",
            stderr=(
                f"not inside a Git work tree: {where}\n"
                "hint: history checks compare commits; run from the vault's repository or pass --root"
            ),
        ) from None
    for ref in refs:
        try:
            if ref.startswith("-"):
                # Never let a revision be read as a Git option.
                raise subprocess.CalledProcessError(128, ["git", "rev-parse", ref])
            git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", root=root)
        except subprocess.CalledProcessError as exc:
            raise subprocess.CalledProcessError(
                exc.returncode, exc.cmd, output="",
                stderr=(
                    f"unknown Git revision: {ref}\n"
                    "hint: pass a commit, branch or tag that exists locally; "
                    "in a shallow CI checkout, fetch it first (e.g. `git fetch origin main`)"
                ),
            ) from None


def _diff_entries(out: str) -> list[tuple[str, str, str]]:
    """Parse ``git diff --name-status -z`` output into ``(status, old, new)``.

    NUL-separated output is the only form in which Git never C-quotes a path:
    without ``-z`` a name like ``d-001-café.md`` arrives as
    ``"06-decisions/d-001-caf\\303\\251.md"`` and no longer looks like a record.
    """
    tokens = out.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries: list[tuple[str, str, str]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        width = 2 if status[:1] in {"R", "C"} else 1
        paths = tokens[index + 1 : index + 1 + width]
        if not status or len(paths) != width:
            # Refuse to guess: a half-parsed diff must not pass the gate.
            raise subprocess.CalledProcessError(
                128, ["git", "diff", "--name-status", "-z"], output=out,
                stderr="could not parse `git diff --name-status -z` output",
            )
        entries.append((status, paths[0], paths[-1]))
        index += 1 + width
    return entries


def _diff_failure(exc: subprocess.CalledProcessError, base: str, head: str, root: str | None) -> subprocess.CalledProcessError:
    """Add the actionable hint a shallow CI checkout needs to Git's own error."""
    detail = (exc.stderr or "").rstrip()
    try:
        shallow = git("rev-parse", "--is-shallow-repository", root=root).strip() == "true"
    except subprocess.CalledProcessError:
        shallow = False
    if shallow or "no merge base" in detail:
        detail += (
            f"\nhint: cannot find the merge base of {base} and {head}"
            + (" in this shallow clone" if shallow else "")
            + "; fetch full history (actions/checkout `fetch-depth: 0`, or `git fetch --unshallow`)"
        )
    return subprocess.CalledProcessError(exc.returncode, exc.cmd, output="", stderr=detail.lstrip("\n"))


# The pseudo-revision a staged check compares against ``base``: Git's index,
# addressed as ``:path`` by ``git show``.
INDEX = ""


def has_commits(root: str | None = None) -> bool:
    """False in a repository whose HEAD is unborn (before the first commit)."""
    try:
        git("rev-parse", "--verify", "--quiet", "HEAD^{commit}", root=root)
    except subprocess.CalledProcessError:
        return False
    return True


def changed_records(base: str, head: str, root: str | None = None, *, staged: bool = False) -> list[tuple[str, str]]:
    """Immutable records and review-log rows rewritten between two states.

    With ``staged`` the second state is the index (what the next commit will
    contain), not *head*: that is the pre-commit form of the check.
    """
    if staged:
        _require_revisions(root, base)
        head = INDEX
    else:
        _require_revisions(root, base, head)
    prefix = _git_prefix(root)
    # --relative makes paths (and pathspecs) vault-rooted when the vault is a
    # subdirectory of the work tree; -z keeps every path byte-exact.
    revisions = ["--cached", base] if staged else [f"{base}...{head}"]
    try:
        out = git(
            "diff",
            "--relative",
            "--name-status",
            "-z",
            "--no-ext-diff",
            *revisions,
            "--",
            RECORD_SUFFIX.rstrip("/"),
            REVIEW_LOG_SUFFIX,
            root=root,
        )
    except subprocess.CalledProcessError as exc:
        raise _diff_failure(exc, base, head or "the index", root) from None

    def show(rev: str, path: str) -> str:
        return git("show", f"{rev}:{_git_object_path(path, prefix)}", root=root)

    blocked: list[tuple[str, str]] = []
    for status, old_path, new_path in _diff_entries(out):
        kind = status[0]
        display_path = f"{old_path} -> {new_path}" if kind in {"R", "C"} else old_path
        if kind == "A":
            continue
        if old_path == REVIEW_LOG_SUFFIX:
            if kind == "M":
                try:
                    if allowed_review_log_append(show(base, old_path), show(head, new_path)):
                        continue
                except subprocess.CalledProcessError:
                    pass
            blocked.append((status, display_path))
            continue
        if not RECORD_RE.search(old_path):
            continue
        # A copy does not rewrite the old record. The regular linter is
        # responsible for rejecting the duplicated decision_id on the new file.
        if kind == "C":
            continue
        if not immutable_at(base, old_path, root, prefix=prefix):
            continue

        if kind == "M":
            try:
                if allowed_lifecycle_change(show(base, old_path), show(head, new_path)):
                    continue
            except subprocess.CalledProcessError:
                pass

        # Rename/delete/type-change or any semantic edit of an immutable record.
        blocked.append((status, display_path))
    return blocked


def _tracks_vault_paths(base: str, head: str, root: str | None, *, staged: bool = False) -> bool:
    """True when either state has decision records or a review log under *root*."""
    pathspec = ["--", RECORD_SUFFIX.rstrip("/"), REVIEW_LOG_SUFFIX]
    listings: list[list[str]] = [["ls-tree", "--name-only", base, *pathspec]]
    listings.append(["ls-files", "--cached", *pathspec] if staged else ["ls-tree", "--name-only", head, *pathspec])
    for listing in listings:
        try:
            if git(*listing, root=root).strip():
                return True
        except subprocess.CalledProcessError:
            return True  # Unknown: do not add a misleading warning.
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit history",
        description="Reject semantic rewrites of historical decisions and review history.",
    )
    parser.add_argument("--base", help="base commit/ref (default with --staged: HEAD)")
    parser.add_argument("--head", default=None, help="head commit/ref (default: HEAD)")
    parser.add_argument("--staged", action="store_true", help="compare the staged index with --base (pre-commit use)")
    parser.add_argument("--root", help="vault root (may be a subdirectory of the Git work tree)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if args.staged and args.head is not None:
        return emit_error(
            "usage",
            "--staged compares the index with --base and does not take --head\nhint: drop --head, or drop --staged to compare two commits",
            json_mode=args.json,
        )
    if not args.staged and args.base is None:
        return emit_error(
            "usage",
            "whykit history needs --base <ref>, or --staged to check what is about to be committed\nhint: in CI pass the pull request base, e.g. --base origin/main",
            json_mode=args.json,
        )
    base = args.base or "HEAD"
    head = "INDEX" if args.staged else (args.head or "HEAD")
    try:
        if args.staged and args.base is None:
            _require_revisions(args.root)  # a work tree, but no ref yet
            if not has_commits(args.root):
                # Before the first commit nothing is accepted yet, so nothing
                # can have been rewritten.
                return _report(args.json, base, head, [], staged=True)
        blocked = changed_records(base, head, args.root, staged=args.staged)
        if not blocked and not _tracks_vault_paths(base, head, args.root, staged=args.staged):
            print(
                f"warning: no decision records or review log under {args.root or os.getcwd()} "
                "in either revision; nothing was checked\n"
                "hint: pass --root <vault> when the vault is a subdirectory of the repository",
                file=sys.stderr,
            )
    except subprocess.CalledProcessError as exc:
        return emit_error("git_error", (exc.stderr or str(exc)).rstrip(), json_mode=args.json)
    except FileNotFoundError:
        return emit_error(
            "missing_dependency",
            "git is not installed or not on PATH\nhint: `whykit history` needs Git to compare revisions",
            json_mode=args.json,
        )
    return _report(args.json, base, head, blocked, staged=args.staged)


def _report(json_mode: bool, base: str, head: str, blocked: list[tuple[str, str]], *, staged: bool) -> int:
    if json_mode:
        import json
        emit_machine(json.dumps({
            "contract_version": 1,
            "base": base,
            "head": head,
            "staged": staged,
            "passed": not blocked,
            "blocked": [{"status": status, "path": path} for status, path in blocked],
        }, ensure_ascii=False, indent=2))
        return 1 if blocked else 0
    if not blocked:
        scope = "staged changes: " if staged else ""
        print(f"history: {scope}immutable reasoning unchanged; review log append-only")
        return 0
    print("Historical decision reasoning is append-only. Supersede; do not rewrite:", file=sys.stderr)
    for status, path in blocked:
        print(f"  {status}\t{path}", file=sys.stderr)
    return 1
