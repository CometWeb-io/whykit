"""Which decisions does a code change touch, and do any of them need review?

``whykit affected`` answers the question a reviewer, or an agent about to edit
files, actually has: *before I change these files, which accepted decisions
cover them, and has anything they rest on changed since they were approved?*

It works in three deterministic steps:

1. **Find the change.** Files named with ``--files`` (an agent's plan), or the
   files Git reports as changed against ``--base`` (a pull request), in the
   code repository ``--repo``.
2. **Map files to decisions.** A decision declares the code it covers with an
   optional ``applies_to`` list of repository-relative globs in its front
   matter. Only live decisions (approved, not superseded) are matched.
3. **Check the premises.** Every matched decision goes through the same
   evaluation as ``whykit agent preflight``: retired, missing or stale
   evidence, a captured source whose bytes changed since the claim was pinned,
   an overdue review, a review that asked for an update or a successor, and an
   approval that cannot be verified.

The report never says a change is safe. It says which decisions apply, which
of them have review signals, which changed files no decision covers and which
live decisions declare no scope, so a reader can see what was *not* checked.
Whether the change is consistent with a decision is left to a person.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import os
import re
import subprocess
from pathlib import Path

from .agent_preflight import assess_decisions
from .lint import DECISION_ID_RE
from .vault_index import VaultIndex

MAX_FILES = 5000
MAX_TEXT_FILES = 5
# A label WhyKit cannot read ranks above every ceiling, as in the MCP server.
SENSITIVITY_ORDER = ("public", "internal", "confidential", "restricted")
MAX_PATTERNS = 64

# Plain-language reasons for the review codes a reader is most likely to meet.
# Any other code is shown as-is; `whykit agent preflight` documents them all.
REASONS: dict[str, str] = {
    "retired_evidence": "a cited source was retired",
    "missing_evidence": "a cited source is missing from the register",
    "stale_evidence": "a cited source is older than its allowed age",
    "no_evidence": "the decision cites no source",
    "unverifiable_evidence_access": "a cited source has no usable access date",
    "review_overdue": "its review date has passed",
    "review_update_required": "its last review asked for an update",
    "review_supersede_required": "its last review asked for a successor",
    "claim_source_changed": "a captured source changed since its claim was pinned",
    "claim_snapshot_changed": "a pinned claim changed since approval",
    "claim_snapshot_unpinned": "its claims are not pinned to a version",
    "claim_not_current": "a claim is outside its validity interval",
    "claim_contradicted": "a linked source contradicts a claim",
    "decision_expired": "its declared validity has ended",
    "unverified_local_approval": "no matching approval event in the review log",
    "invalid_decision_record": "the record is incomplete or malformed",
    "missing_referenced_path": "it names code paths that no longer exist",
    "cited_source_changed": "a source it cites is a file this change modifies",
}


CAVEATS: dict[str, str] = {
    "terms_not_checked": "watch_terms were not checked: they need a diff (use --base or Git changes, not --files)",
    "diff_truncated": "the diff was too large to scan completely for watch_terms",
    "adr_mode": "read from plain ADR files: scopes and moved paths are checked, but there is no evidence "
                "register or review log; adopt the ADRs into a vault for those checks",
    "inferred_scope": "some scopes were inferred from paths the ADRs mention; add applies_to to make them explicit",
    "evidence_age_unbounded": "some cited source types have no maximum age, so staleness was not checked; "
                              "set [evidence_access_age_days] in whykit.toml",
}

# Codes that describe the vault's configuration rather than a change in a
# decision's premises. They are reported once as caveats, not as review
# signals, so a fresh vault does not flag every decision.
CAVEAT_CODES = frozenset({"evidence_age_unbounded"})


class AffectedError(Exception):
    """A request that cannot be carried out; ``code`` is a contract error code."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


# ---------------------------------------------------------------- patterns

def pattern_problem(pattern: object) -> str | None:
    """Why ``pattern`` is not a usable repository-relative glob, or None."""
    if not isinstance(pattern, str) or not pattern.strip():
        return "must be a non-empty string"
    if pattern != pattern.strip():
        return "must not start or end with whitespace"
    if "\\" in pattern or "\x00" in pattern:
        return "must use forward slashes"
    if pattern.startswith("/") or (len(pattern) > 1 and pattern[1] == ":"):
        return "must be relative to the repository root"
    if any(part in {"..", "."} for part in pattern.rstrip("/").split("/")):
        return "must not contain . or .. segments"
    return None


def _segments_match(pattern: list[str], path: list[str]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_segments_match(rest, path[index:]) for index in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], head) and _segments_match(rest, path[1:])


def is_broad(pattern: str) -> bool:
    """A pattern covering a whole top-level folder (``src/**``) or everything."""
    parts = pattern.rstrip("/").split("/")
    return pattern in {"**", "*", "**/*"} or (len(parts) <= 2 and parts[-1] in {"**", "*"}) or pattern.endswith("/") and len(parts) == 1


def _broad_match(patterns: list[str], sources: list) -> bool:
    """A decision matched only through broad patterns applies to most changes."""
    return bool(patterns) and not sources and all(is_broad(pattern) for pattern in patterns)


def glob_match(pattern: str, path: str) -> bool:
    """Match a repository-relative POSIX path against an anchored glob.

    ``*`` and ``?`` stay inside one path segment, ``**`` spans any number of
    segments, and a trailing ``/`` means everything below that directory.
    Patterns are anchored at the repository root, like ``src/auth/**``; use
    ``**/*.sql`` to match anywhere.
    """
    if pattern.endswith("/"):
        pattern += "**"
    return _segments_match(pattern.split("/"), path.split("/"))


def declared_scope(front: dict) -> tuple[list[str], list[dict]]:
    """``applies_to`` patterns from front matter, and any that are invalid."""
    raw = front.get("applies_to")
    if raw is None:
        return [], []
    values = raw if isinstance(raw, list) else [raw]
    valid: list[str] = []
    invalid: list[dict] = []
    for value in values[:MAX_PATTERNS]:
        problem = pattern_problem(value)
        if problem:
            invalid.append({"pattern": str(value), "problem": problem})
        else:
            valid.append(str(value))
    if len(values) > MAX_PATTERNS:
        invalid.append({"pattern": "", "problem": f"more than {MAX_PATTERNS} patterns"})
    return valid, invalid


MAX_TERMS = 32
MAX_DIFF_BYTES = 8 * 1024 * 1024


def term_problem(term: object) -> str | None:
    if not isinstance(term, str) or not term.strip():
        return "must be a non-empty string"
    if term != term.strip() or len(term) < 2 or len(term) > 100 or "\n" in term or "\x00" in term:
        return "must be 2-100 characters on one line, without surrounding spaces"
    return None


def declared_terms(front: dict) -> tuple[list[str], list[dict]]:
    """``watch_terms`` from front matter: words whose appearance in a diff makes a decision apply."""
    raw = front.get("watch_terms")
    if raw is None:
        return [], []
    values = raw if isinstance(raw, list) else [raw]
    valid: list[str] = []
    invalid: list[dict] = []
    for value in values[:MAX_TERMS]:
        problem = term_problem(value)
        if problem:
            invalid.append({"pattern": "term:" + str(value), "problem": problem})
        elif value not in valid:
            valid.append(value)
    return valid, invalid


def _term_re(term: str) -> re.Pattern[str]:
    # Whole words only: `moment` must not match `momentum`.
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", re.IGNORECASE)


# Files that change with everything and say nothing about one decision.
NOISE_FILES = re.compile(
    r"(^|/)(CHANGELOG|CHANGES|HISTORY)(\.[a-z]+)?$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|uv\.lock|"
    r"poetry\.lock|Cargo\.lock|go\.sum|Gemfile\.lock|composer\.lock|\.changeset/.*)$",
    re.IGNORECASE,
)
# Field data (Backstage, 300 commits): term matches in prose, lock files and
# comments were all noise; the one real finding was in source code.
_PROSE = re.compile(r"\.(md|mdx|markdown|rst|txt|adoc)$", re.IGNORECASE)
_COMMENT_ONLY = re.compile(r"^\s*(//|#(?![!\[{])|/\*|\*|<!--|--|;)")


def term_scannable(path: str) -> bool:
    """Source files only: no prose, lock files or changelogs."""
    return not NOISE_FILES.search(path) and not _PROSE.search(path)


def term_hits(terms: list[str], diff: dict[str, list[str]], paths: list[str]) -> list[dict]:
    """Which declared terms appear in changed code lines of which files.

    Comment-only lines, prose files, lock files and changelogs are skipped:
    in field testing every match there was noise.
    """
    hits: list[dict] = []
    code = {
        path: [line for line in diff.get(path, ()) if not _COMMENT_ONLY.match(line)]
        for path in paths if term_scannable(path)
    }
    for term in terms:
        pattern = _term_re(term)
        files = sorted(path for path, lines in code.items() if any(pattern.search(line) for line in lines))
        if files:
            hits.append({"term": term, "files": files})
    return hits


# ------------------------------------------------------------------- input

def _git(repo: Path, *args: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
            capture_output=True, timeout=60, check=False,
        )
    except FileNotFoundError as exc:
        raise AffectedError("missing_dependency", "git is not installed",
                            hint="pass the files explicitly with --files") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AffectedError("git_error", message[-1] if message else "git failed",
                            hint="check --repo and --base, or pass the files with --files")
    return [item for item in result.stdout.decode("utf-8", "surrogateescape").split("\0") if item]


def _normalize(repo: Path, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(repo)
        except ValueError as exc:
            raise AffectedError("invalid_argument", f"file is outside the repository: {raw}") from exc
    posix = path.as_posix()
    while posix.startswith("./"):
        posix = posix[2:]
    if not posix or posix == "." or any(part == ".." for part in posix.split("/")):
        raise AffectedError("invalid_argument", f"not a repository-relative file path: {raw}")
    return posix


def changed_files(repo: Path, *, files: list[str] | None, base: str | None) -> tuple[list[str], dict]:
    """The repository-relative paths under review and where they came from."""
    if files:
        paths = [_normalize(repo, item) for item in files]
        source = {"kind": "files", "base": None}
    else:
        reference = base or "HEAD"
        if reference.startswith("-"):
            raise AffectedError("invalid_argument", f"invalid --base: {reference}")
        paths = _git(repo, "diff", "--name-only", "--relative", "--no-renames", "-z", reference, "--")
        # A plan in progress includes new files Git does not track yet.
        paths += _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
        source = {"kind": "git", "base": reference}
    unique = sorted(set(paths))
    if len(unique) > MAX_FILES:
        raise AffectedError("invalid_argument", f"more than {MAX_FILES} changed files")
    return unique, source


def parse_unified_diff(text: str) -> tuple[dict[str, list[str]], bool]:
    """Added and removed lines per file from a unified diff (``git diff`` or a planned patch)."""
    lines: dict[str, list[str]] = {}
    total = 0
    current: str | None = None
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+) b/(.+)$", raw)
            current = match.group(2) if match else None
            continue
        if raw.startswith("+++ "):
            name = raw[4:].split("\t", 1)[0]
            current = name[2:] if name.startswith("b/") else (None if name == "/dev/null" else name)
            continue
        if raw.startswith("--- "):
            name = raw[4:].split("\t", 1)[0]
            if name != "/dev/null":
                current = name[2:] if name.startswith("a/") else name
            continue
        if current and raw[:1] in "+-":
            total += len(raw)
            if total > MAX_DIFF_BYTES:
                return lines, True
            lines.setdefault(current, []).append(raw[1:])
    return lines, False


def diff_paths(lines: dict[str, list[str]], text: str) -> list[str]:
    """Every file a unified diff names, including ones with no changed lines."""
    names = set(lines)
    for match in re.finditer(r"(?m)^diff --git a/(.+) b/(.+)$", text):
        names.add(match.group(2))
    return sorted(name for name in names if not pattern_problem(name))


def changed_lines(repo: Path, base: str, paths: list[str]) -> tuple[dict[str, list[str]], bool]:
    """Added and removed lines per file since ``base``, plus whether it was cut short.

    Untracked files count as entirely added. Binary files have no lines.
    """
    total = 0
    if base.startswith("-"):
        raise AffectedError("invalid_argument", f"invalid --base: {base}")
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-C", str(repo), "diff", "-U0", "--no-renames", "--no-color",
             "--no-ext-diff", "--relative", base, "--"],
            capture_output=True, timeout=120, check=False,
        )
    except FileNotFoundError as exc:
        raise AffectedError("missing_dependency", "git is not installed") from exc
    if result.returncode != 0:
        raise AffectedError("git_error", "git diff failed", hint="check --repo and --base")
    lines, truncated = parse_unified_diff(result.stdout.decode("utf-8", "replace"))
    total = sum(len(line) for content in lines.values() for line in content)
    tracked = set(lines)
    for path in paths:
        if path in tracked or truncated:
            continue
        target = repo / path
        try:
            if target.is_file() and not target.is_symlink() and target.stat().st_size <= 1024 * 1024:
                text = target.read_bytes()
                if b"\0" not in text:
                    content = text.decode("utf-8", "replace").splitlines()
                    total += sum(len(line) for line in content)
                    if total > MAX_DIFF_BYTES:
                        truncated = True
                        break
                    if _git(repo, "ls-files", "-z", "--", path) == []:
                        lines[path] = content
        except OSError:
            continue
    return lines, truncated


# ------------------------------------------------------------- sources

def repository_sources(root: Path, vault_prefix: str | None) -> dict[str, list[str]]:
    """Active evidence whose location is a file or folder in the repository.

    Many decisions rest on sources kept next to the code: a benchmark, a spec,
    a config. A location without a URL scheme is read as a path relative to
    the repository root, or to the vault when the vault sits inside it.
    """
    from .lint import evidence_register

    active, _, _ = evidence_register(root)
    sources: dict[str, list[str]] = {}
    for evidence_id, row in active.items():
        location = str(row.get("location") or "").strip().strip("<>`")
        if not location or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", location):
            continue
        location = location.removeprefix("./")
        if pattern_problem(location) or any(char in location for char in "*?[ "):
            continue
        candidates = [location]
        if vault_prefix and vault_prefix != ".":
            candidates.append(f"{vault_prefix}/{location}")
        sources[evidence_id] = candidates
    return sources


def _touches(candidate: str, path: str) -> bool:
    base = candidate.rstrip("/")
    return path == base or path.startswith(base + "/")


# ------------------------------------------------------------------ report

def _sensitivity_rank(value: object) -> int:
    label = str(value or "internal")
    return SENSITIVITY_ORDER.index(label) if label in SENSITIVITY_ORDER else len(SENSITIVITY_ORDER)


def build_affected(
    root: Path,
    repo: Path | None,
    paths: list[str],
    source: dict,
    *,
    today: dt.date,
    max_sensitivity: str | None = None,
    hide_above_ceiling: bool = False,
    match_sources: bool = True,
    diff: dict[str, list[str]] | None = None,
    diff_truncated: bool = False,
) -> dict:
    """Map ``paths`` to live decisions by scope, then assess their premises.

    Above ``max_sensitivity`` a decision is counted as withheld, or, with
    ``hide_above_ceiling`` (the MCP server's rule), treated as nonexistent.
    """
    root = root.resolve()
    # A vault kept inside the code repository changes in the same commits; its
    # own records are the ledger, not code the decisions govern.
    try:
        vault_prefix = root.relative_to(repo).as_posix() if repo is not None else None
    except ValueError:
        vault_prefix = None
    all_paths = list(paths)
    if vault_prefix and vault_prefix != ".":
        paths = [path for path in paths if path != vault_prefix and not path.startswith(vault_prefix + "/")]
    vault = VaultIndex.load(root)
    ceiling = SENSITIVITY_ORDER.index(max_sensitivity) if max_sensitivity else None

    live: list[dict] = []
    for note in vault.notes:
        did = str(note.front.get("decision_id") or "").strip()
        if not DECISION_ID_RE.fullmatch(did) or note.front.get("type") != "decision":
            continue
        if str(note.front.get("status") or "") != "approved":
            continue
        patterns, invalid = declared_scope(note.front)
        terms, invalid_terms = declared_terms(note.front)
        invalid = invalid + invalid_terms
        live.append({
            "decision_id": did,
            "title": str(note.front.get("title") or note.path.stem),
            "path": vault.relative(note.path),
            "owner": str(note.front.get("owner") or ""),
            "rank": _sensitivity_rank(note.front.get("sensitivity")),
            "patterns": patterns,
            "invalid": invalid,
            "cited": set(note.cited_evidence),
            "terms": terms,
        })
    superseded = {
        str(note.front.get("supersedes") or "").strip()
        for note in vault.notes
        if str(note.front.get("status") or "") in {"approved", "superseded", "archived"}
    }
    live = [record for record in live if record["decision_id"] not in superseded]
    if hide_above_ceiling and ceiling is not None:
        live = [record for record in live if record["rank"] <= ceiling]

    # Sources kept in the repository that this change modifies.
    changed_sources: dict[str, list[str]] = {}
    if match_sources:
        for evidence_id, candidates in repository_sources(root, vault_prefix).items():
            touched = sorted({path for path in all_paths for candidate in candidates if _touches(candidate, path)})
            if touched:
                changed_sources[evidence_id] = touched

    covered: set[str] = set()
    matched: list[dict] = []
    for record in sorted(live, key=lambda item: item["decision_id"]):
        hits = [path for path in paths if any(glob_match(pattern, path) for pattern in record["patterns"])]
        sources = [
            {"evidence_id": evidence_id, "files": changed_sources[evidence_id]}
            for evidence_id in sorted(record["cited"] & set(changed_sources))
        ]
        term_matches: list[dict] = (
            term_hits(record["terms"], diff, paths) if diff is not None and record["terms"] else []
        )
        if not hits and not sources and not term_matches:
            continue
        source_files = sorted({path for item in sources for path in item["files"]})
        term_files = sorted({path for item in term_matches for path in item["files"]})
        covered.update(hits)
        covered.update(source_files)
        covered.update(term_files)
        record["matched_files"] = sorted(set(hits) | set(source_files) | set(term_files))
        record["matched_terms"] = term_matches
        record["matched_patterns"] = [p for p in record["patterns"] if any(glob_match(p, path) for path in hits)]
        record["matched_sources"] = sources
        matched.append(record)

    findings = {
        item["decision_id"]: item["problems"]
        for item in assess_decisions(root, [record["decision_id"] for record in matched], today=today)
    } if matched else {}

    decisions: list[dict] = []
    caveats: set[str] = set()
    withheld = 0
    for record in matched:
        if ceiling is not None and record["rank"] > ceiling:
            withheld += 1
            continue
        all_problems = findings.get(record["decision_id"], [])
        caveats.update(code for code in all_problems if code in CAVEAT_CODES)
        problems = [code for code in all_problems if code not in CAVEAT_CODES]
        reasons = [REASONS.get(code, code.replace("_", " ")) for code in problems]
        if record["matched_sources"]:
            problems = sorted([*problems, "cited_source_changed"])
            detail = ", ".join(f"{item['evidence_id']} ({', '.join(item['files'][:3])})" for item in record["matched_sources"])
            reasons = [f"{REASONS['cited_source_changed']}: {detail}", *reasons]
        decisions.append({
            "decision_id": record["decision_id"],
            "title": record["title"],
            "path": record["path"],
            "owner": record["owner"],
            "matched_files": record["matched_files"],
            "matched_patterns": record["matched_patterns"],
            "matched_sources": record["matched_sources"],
            "matched_terms": record["matched_terms"],
            "problems": problems,
            "review_signal": bool(problems),
            "reasons": reasons,
            "broad": _broad_match(record["matched_patterns"], record["matched_sources"] or record["matched_terms"]),
        })

    visible = [record for record in live if ceiling is None or record["rank"] <= ceiling]
    if diff is None and any(record["terms"] for record in visible):
        caveats.add("terms_not_checked")
    if diff_truncated:
        caveats.add("diff_truncated")
    unscoped = sorted(record["decision_id"] for record in visible if not record["patterns"] and not record["terms"])
    invalid_scopes = [
        {"decision_id": record["decision_id"], **item}
        for record in sorted(visible, key=lambda r: r["decision_id"]) for item in record["invalid"]
    ]
    uncovered = [path for path in paths if path not in covered]
    needing_review = sum(1 for item in decisions if item["review_signal"])
    return {
        "contract_version": 1,
        "as_of": today.isoformat(),
        "root": str(root),
        "repo": str(repo) if repo is not None else "",
        "source": source,
        "changed_files": paths,
        "decisions": decisions,
        "uncovered_files": uncovered,
        "unscoped_decisions": unscoped,
        "invalid_scopes": invalid_scopes,
        "withheld_decisions": withheld,
        "caveats": sorted(caveats),
        "summary": {
            "changed_files": len(paths),
            "applicable_decisions": len(decisions) + withheld,
            "needing_review": needing_review,
            "uncovered_files": len(uncovered),
            "unscoped_decisions": len(unscoped),
        },
        "review_required": bool(needing_review),
        "semantics_checked": False,
        "mode": "vault",
    }


# ----------------------------------------------------------------- render

def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _summary_line(report: dict) -> str:
    summary = report["summary"]
    broad = sum(1 for item in report["decisions"] if item.get("broad"))
    parts = [
        _plural(summary["changed_files"], "changed file"),
        f"{_plural(summary['applicable_decisions'], 'decision')} appl{'ies' if summary['applicable_decisions'] == 1 else 'y'}"
        + (f" ({broad} broad)" if broad else ""),
        f"{summary['needing_review']} need{'s' if summary['needing_review'] == 1 else ''} review",
    ]
    if summary["uncovered_files"]:
        parts.append(f"{_plural(summary['uncovered_files'], 'file')} not covered by any decision")
    return " · ".join(parts)


def render_text(report: dict) -> str:
    lines = [f"WhyKit affected — {_summary_line(report)}"]
    if not report["changed_files"]:
        lines.append("  no changed files")
    for item in sorted(report["decisions"], key=lambda d: (bool(d.get("broad")) and not d["review_signal"], d["decision_id"])):
        mark = "review" if item["review_signal"] else ("broad" if item.get("broad") else "applies")
        lines.append(f"  {item['decision_id']}  {mark:<7}  {item['title']}")
        shown = item["matched_files"][:MAX_TEXT_FILES]
        more = len(item["matched_files"]) - len(shown)
        # Field data: one broad decision matched 1,000+ files in a 200-commit range.
        lines.append(f"      files: {', '.join(shown)}" + (f" (+{more} more)" if more else ""))
        for hit in item.get("matched_terms", []):
            lines.append(f"      mentions {hit['term']!r} in {', '.join(hit['files'][:3])}")
        for reason in item["reasons"]:
            lines.append(f"      - {reason}")
    if report["withheld_decisions"]:
        lines.append(f"  {report['withheld_decisions']} more above the sensitivity ceiling")
    if report["uncovered_files"]:
        shown = report["uncovered_files"][:10]
        more = len(report["uncovered_files"]) - len(shown)
        lines.append("  not covered by any decision: " + ", ".join(shown) + (f" (+{more} more)" if more else ""))
    if report["unscoped_decisions"]:
        lines.append(f"  {len(report['unscoped_decisions'])} live decision(s) declare no applies_to scope: "
                     + ", ".join(report["unscoped_decisions"][:10]))
    for item in report["invalid_scopes"]:
        lines.append(f"  {item['decision_id']} applies_to {item['pattern']!r}: {item['problem']}")
    for code in report["caveats"]:
        lines.append(f"  caveat: {CAVEATS.get(code, code)}")
    lines.append("  This matches declared scopes and checks premises; it does not judge whether the change is right.")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    lines = ["### WhyKit: decisions this change touches", "", _summary_line(report), ""]
    if report["decisions"]:
        lines += ["| Decision | Files | Signal |", "|---|---|---|"]
        for item in sorted(report["decisions"], key=lambda d: (bool(d.get("broad")) and not d["review_signal"], d["decision_id"])):
            files = ", ".join(f"`{_cell(path)}`" for path in item["matched_files"][:5])
            if len(item["matched_files"]) > 5:
                files += f" +{len(item['matched_files']) - 5}"
            mentions = "; ".join(f"changes mention `{_cell(hit['term'])}`" for hit in item.get("matched_terms", []))
            if item["review_signal"]:
                signal = "⚠️ review: " + "; ".join(item["reasons"])
            elif mentions:
                signal = mentions + " — read before merging"
            elif item.get("broad"):
                signal = "applies broadly (a whole top-level folder)"
            else:
                signal = "applies — read before merging"
            lines.append(f"| **{item['decision_id']}** {_cell(item['title'])} | {files} | {_cell(signal)} |")
        lines.append("")
    if report["withheld_decisions"]:
        lines += [f"{report['withheld_decisions']} more decision(s) apply but are above the sensitivity ceiling of this report.", ""]
    if report["uncovered_files"]:
        lines.append(f"<details><summary>{_plural(len(report['uncovered_files']), 'file')} not covered by any decision</summary>")
        lines.append("")
        lines += [f"- `{_cell(path)}`" for path in report["uncovered_files"][:50]]
        lines += ["", "</details>", ""]
    if report["unscoped_decisions"]:
        lines += [f"{len(report['unscoped_decisions'])} live decision(s) declare no `applies_to` scope, so they cannot be matched to files.", ""]
    for code in report["caveats"]:
        lines += [f"Caveat: {CAVEATS.get(code, code)}", ""]
    lines.append("> WhyKit matches declared decision scopes and checks the sources and reviews behind them. "
                 "It does not judge whether this change is consistent with a decision; a reviewer does.")
    return "\n".join(lines)


def default_today(raw: str | None) -> dt.date:
    if raw is None:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise AffectedError("invalid_argument", f"invalid --today: {raw}") from exc


def resolve_repo(raw: str | None) -> Path:
    path = Path(os.path.abspath(Path(raw).expanduser())) if raw else Path.cwd()
    if not path.is_dir():
        raise AffectedError("invalid_target", f"repository directory does not exist: {path}")
    return path.resolve()


# ------------------------------------------------------------- plain ADRs

ADR_DIR_CANDIDATES = ("doc/adr", "docs/adr", "docs/decisions", "docs/architecture/decisions", "adr", "decisions")
MAX_ADRS = 2000
_SKIP_NAMES = {"readme.md", "index.md", "template.md", "adr-template.md", "_template.md"}
_LIVE = ("accepted", "approved", "active", "decided", "przyjęt", "zaakceptow")
_NOT_LIVE = ("proposed", "draft", "rejected", "deprecated", "superseded", "obsolete", "withdrawn", "replaced",
             "odrzuc", "zastąpion", "wycofan", "propon")
# A repository path as people write it in prose: two or more segments, no URL.
_PATH_RE = re.compile(r"(?<![\w/:.@-])((?:[A-Za-z0-9_.-]+/)+(?:[A-Za-z0-9_.*-]+/?)?)")


def find_adr_dir(repo: Path) -> Path | None:
    """The first conventional ADR folder in ``repo``, if any."""
    for candidate in ADR_DIR_CANDIDATES:
        path = repo / candidate
        if path.is_dir() and not path.is_symlink() and any(path.glob("*.md")):
            return path
    return None


def _adr_live(status: str | None) -> bool | None:
    """True for a binding ADR, False for one that is not, None when unknown."""
    if not status:
        return None
    folded = status.strip().casefold()
    if folded.startswith(_NOT_LIVE):
        return False
    if folded.startswith(_LIVE):
        return True
    return None


def referenced_paths(text: str) -> list[str]:
    """Repository-looking paths a document mentions, outside URLs."""
    text = re.sub(r"[a-z][a-z0-9+.-]*://\S+", " ", text)
    found: list[str] = []
    for match in _PATH_RE.findall(text):
        candidate = match.strip(".")
        if candidate.count("/") == 0 or candidate.startswith(("./", "../")) or pattern_problem(candidate):
            continue
        if candidate not in found:
            found.append(candidate)
    return found[:MAX_PATTERNS]


def _anchored(repo: Path, path: str) -> bool:
    """Whether a mentioned path starts at an entry that exists at the repository root.

    Field data: ADRs mention ``dd/mm/yyyy``, ``CI/CD``, ``and/or``, domains and
    paths relative to some package (``components/index.ts``). Only a path whose
    first segment is a real top-level entry is read as a repository path.
    """
    first = path.split("/", 1)[0]
    return bool(first) and (repo / first).exists()


def _inferred_scope(repo: Path, mentioned: list[str]) -> tuple[list[str], list[str]]:
    """Patterns for mentioned paths that exist, and anchored ones that do not."""
    patterns: list[str] = []
    missing: list[str] = []
    for path in mentioned:
        if not _anchored(repo, path):
            continue
        if any(char in path for char in "*?["):
            patterns.append(path)
            continue
        target = repo / path.rstrip("/")
        try:
            inside = target.resolve().is_relative_to(repo.resolve())
        except OSError:
            inside = False
        if not inside:
            continue
        if target.is_dir():
            patterns.append(path.rstrip("/") + "/**")
        elif target.is_file():
            patterns.append(path)
        elif "." in path.rsplit("/", 1)[-1] or path.endswith("/"):
            # Anchored at a real top-level folder, but gone: the code moved.
            missing.append(path)
    return patterns, missing


def build_affected_adr(adr_dir: Path, repo: Path, paths: list[str], source: dict, *, today: dt.date,
                       max_sensitivity: str | None = None, diff: dict[str, list[str]] | None = None,
                       diff_truncated: bool = False) -> dict:
    """``affected`` for a folder of plain ADRs, without a vault or migration."""
    from .lint import is_markdown_name, load_note
    from .promote import PromotionError, build_promotion

    # Both sides resolved: on macOS a temp or home path may sit behind a symlink
    # (/var -> /private/var), and a half-resolved pair never relates.
    repo = repo.resolve()
    adr_dir = adr_dir.resolve()
    try:
        prefix = adr_dir.relative_to(repo).as_posix()
    except ValueError:
        prefix = None
    if prefix and prefix != ".":
        paths = [path for path in paths if not path.startswith(prefix + "/")]
    files = sorted(
        path for path in adr_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and is_markdown_name(path.name)
        and path.name.casefold() not in _SKIP_NAMES and "template" not in path.stem.casefold()
    )
    if len(files) > MAX_ADRS:
        raise AffectedError("invalid_argument", f"more than {MAX_ADRS} Markdown files under {adr_dir}")

    records: list[dict] = []
    unreadable = 0
    status_unknown = 0
    for path in files:
        try:
            promotion = build_promotion(adr_dir, path)
        except (PromotionError, OSError):
            unreadable += 1
            continue
        note = load_note(path, text=promotion.original)
        front = note.front if note.has_front else {}
        status = promotion.source_status or (str(front.get("status")) if front.get("status") else None)
        live = _adr_live(status)
        # Field data: some projects mark the state in the title, e.g. "ADR013: [superseded] ...".
        marker = re.search(r"\[(superseded|deprecated|rejected|obsolete)\]", promotion.title, re.IGNORECASE)
        if marker:
            live, status = False, status or marker.group(1).lower()
        if live is False:
            continue
        if live is None:
            status_unknown += 1
        declared, invalid = declared_scope(front)
        terms, invalid_terms = declared_terms(front)
        invalid = invalid + invalid_terms
        mentioned = referenced_paths(promotion.original)
        inferred, missing = ([], []) if declared else _inferred_scope(repo, mentioned)
        records.append({
            "decision_id": "adr:" + path.relative_to(adr_dir).as_posix(),
            "title": promotion.title,
            "path": path.relative_to(adr_dir).as_posix(),
            "owner": str(front.get("owner") or front.get("deciders") or ""),
            "patterns": declared or inferred,
            "scope_source": "declared" if declared else ("inferred" if inferred else "none"),
            "invalid": invalid,
            "missing": missing,
            "status": status,
            "terms": terms,
            # No label means internal, as in a vault.
            "rank": _sensitivity_rank(front.get("sensitivity")),
        })

    ceiling = SENSITIVITY_ORDER.index(max_sensitivity) if max_sensitivity else None
    covered: set[str] = set()
    decisions: list[dict] = []
    withheld = 0
    inferred_used = False
    for record in records:
        hits = [path for path in paths if any(glob_match(pattern, path) for pattern in record["patterns"])]
        term_matches: list[dict] = term_hits(record["terms"], diff, paths) if diff is not None and record["terms"] else []
        if not hits and not term_matches:
            continue
        term_files = sorted({path for item in term_matches for path in item["files"]})
        covered.update(hits)
        covered.update(term_files)
        if ceiling is not None and record["rank"] > ceiling:
            withheld += 1
            continue
        inferred_used = inferred_used or record["scope_source"] == "inferred"
        problems = ["missing_referenced_path"] if record["missing"] else []
        decisions.append({
            "decision_id": record["decision_id"],
            "title": record["title"],
            "path": record["path"],
            "owner": record["owner"],
            "matched_files": sorted(set(hits) | set(term_files)),
            "matched_patterns": [p for p in record["patterns"] if any(glob_match(p, path) for path in hits)],
            "matched_terms": term_matches,
            "problems": problems,
            "review_signal": bool(problems),
            "reasons": [f"{REASONS[code]}: {', '.join(record['missing'][:5])}" for code in problems],
            "scope_source": record["scope_source"],
            "broad": _broad_match([p for p in record["patterns"] if any(glob_match(p, path) for path in hits)], term_matches),
            "status": record["status"],
            "missing_paths": record["missing"],
        })

    visible = [record for record in records if ceiling is None or record["rank"] <= ceiling]
    unscoped = sorted(record["decision_id"] for record in visible if not record["patterns"] and not record["terms"])
    invalid_scopes = [{"decision_id": record["decision_id"], **item} for record in visible for item in record["invalid"]]
    uncovered = [path for path in paths if path not in covered]
    caveats = ["adr_mode"] + (["inferred_scope"] if inferred_used else [])
    if diff is None and any(record["terms"] for record in visible):
        caveats.append("terms_not_checked")
    if diff_truncated:
        caveats.append("diff_truncated")
    needing_review = sum(1 for item in decisions if item["review_signal"])
    return {
        "contract_version": 1,
        "as_of": today.isoformat(),
        "root": str(adr_dir),
        "repo": str(repo),
        "source": source,
        "changed_files": paths,
        "decisions": decisions,
        "uncovered_files": uncovered,
        "unscoped_decisions": unscoped,
        "invalid_scopes": invalid_scopes,
        "withheld_decisions": withheld,
        "caveats": caveats,
        "summary": {
            "changed_files": len(paths),
            "applicable_decisions": len(decisions) + withheld,
            "needing_review": needing_review,
            "uncovered_files": len(uncovered),
            "unscoped_decisions": len(unscoped),
        },
        "review_required": bool(needing_review),
        "semantics_checked": False,
        "mode": "adr",
        "adr": {"files": len(files), "live": len(records), "status_unknown": status_unknown, "unreadable": unreadable},
    }
