"""Suggest ``applies_to`` scopes for decisions from Git history.

Writing scopes by hand is the chore that keeps ``whykit affected`` from being
useful on day one. The repository already knows a lot: the commits that
changed a decision record (when the vault lives in the code repository) and
the commits whose message names the decision (``D-007``), which is what
``whykit connect`` asks agents to write. The files those commits changed,
outside the vault, are the code the decision has travelled with.

The suggestion is evidence, not a verdict: every pattern lists the commits and
files behind it, a person confirms it, and ``--write`` only fills in decisions
that have no scope yet. Writing ``applies_to`` is allowed on an approved
decision; it is routing metadata outside the reasoning and the approval hash.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .affected import NOISE_FILES as NOISE, AffectedError, _git, declared_scope, glob_match
from .lint import DECISION_ID_RE

MAX_COMMITS = 200
MAX_PATTERNS = 8
# Field data: initial imports, formatting sweeps and mass renames touch hundreds
# of files and say nothing about one decision. Skip commits wider than this.
DEFAULT_MAX_FILES = 25
def _log_records(repo: Path, *args: str) -> list[tuple[str, str, list[str]]]:
    """``(commit, message, files)`` from one ``git log`` pass, newest first."""
    raw = _git(repo, "log", "--no-renames", "--format=%x1e%H%x1f%B%x1f", "--name-only", *args)
    # _git splits on NUL; this format uses none, so rejoin defensively.
    text = "\0".join(raw)
    records: list[tuple[str, str, list[str]]] = []
    for chunk in text.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        commit, message, names = (chunk.split("\x1f", 2) + ["", ""])[:3]
        records.append((commit.strip(), message, [line.strip() for line in names.splitlines() if line.strip()]))
    return records


def _id_re(decision_id: str) -> re.Pattern[str]:
    # A message that names the decision: D-007, not D-0071 or XD-007.
    return re.compile(r"(?<![A-Za-z0-9-])" + re.escape(decision_id) + r"(?![0-9])", re.IGNORECASE)


def history_index(repo: Path, records: dict[str, str | None]) -> dict[str, list[tuple[str, list[str]]]]:
    """Commits per decision, newest first, from two ``git log`` passes for all decisions.

    ``records`` maps each decision ID to its record path in the repository, or
    ``None`` when the vault is elsewhere. Field data (Backstage, 77k commits):
    one pass per decision took 22 s for 15 decisions.
    """
    found: dict[str, dict[str, list[str]]] = {did: {} for did in records}
    if records:
        alternation = "|".join(re.escape(did) for did in records)
        for commit, message, files in _log_records(repo, "-E", "-i", f"--grep=({alternation})"):
            for did in records:
                if len(found[did]) < MAX_COMMITS and _id_re(did).search(message):
                    found[did].setdefault(commit, files)
    by_path: dict[str, str] = {path: did for did, path in records.items() if path}
    if by_path:
        for commit, _message, files in _log_records(repo, "--full-diff", "--", *by_path):
            for name in files:
                owner = by_path.get(name)
                if owner and len(found[owner]) < MAX_COMMITS:
                    found[owner].setdefault(commit, files)
    return {did: list(commits.items()) for did, commits in found.items()}


_BACKTICK = re.compile(r"`([@A-Za-z0-9_./-]{2,60})`")
_FILE_LIKE = re.compile(r"(?<![\w.-])([A-Za-z][\w-]*\.(?:js|ts|py|rb|go|rs|java|kt))(?![\w.])")
_PACKAGE = re.compile(r"(?<![\w@/.-])((?:@[a-z0-9][\w.-]*/)?[a-z0-9]+(?:-[a-z0-9]+)+)(?![\w/.-])")


def rejected_alternatives(text: str) -> list[str]:
    """First-column names from the record's "Alternatives considered" table."""
    names: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Alternatives considered"
            continue
        if inside and line.startswith("|") and not re.match(r"^\|\s*-", line):
            cell = line.strip("|").split("|", 1)[0].strip()
            if cell and cell.casefold() != "alternative":
                names.append(cell)
    return names


def term_candidates(names: list[str]) -> list[dict]:
    """Code-looking names in rejected alternatives: `backticked`, Fuse.js, date-fns.

    Field data (four ADR repositories): most rejected options are prose
    ("Use badge", "Monolith") or licence names that would match package
    manifests. Only tokens that look like code are proposed, for a person to
    confirm; they are never written automatically.
    """
    found: list[dict] = []
    for name in names:
        # Field data: URL fragments, dates and document file names were noise.
        text = re.sub(r"<?[a-z][a-z0-9+.-]*://\S+>?", " ", name)
        tokens = _BACKTICK.findall(text) + _FILE_LIKE.findall(text) + _PACKAGE.findall(text)
        for token in tokens:
            token = token.strip(".")
            if (len(token) < 3 or token.isdigit() or re.fullmatch(r"\d{4}-\d{2}-\d{2}", token)
                    or re.search(r"\.(md|mdx|txt|rst|adoc)$", token, re.I)
                    or any(item["term"] == token for item in found)):
                continue
            found.append({"term": token, "from": name[:120]})
    return found[:10]


def patterns_for(files: dict[str, set[str]]) -> list[dict]:
    """Group changed files into a few directory patterns, with their support.

    ``files`` maps each path to the commits that changed it. A directory with
    two or more files becomes ``dir/**``; a lone file stays exact. Patterns are
    ranked by how many distinct commits support them.
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for path in files:
        parts = path.split("/")
        # Group by the first two directory levels: src/payments, not src.
        key = "/".join(parts[: min(2, len(parts) - 1)]) if len(parts) > 1 else path
        groups[key].add(path)
    suggestions: list[dict] = []
    for key, members in groups.items():
        commits = set().union(*(files[path] for path in members))
        if len(members) == 1 and key in files:
            pattern = key
        elif len(members) == 1:
            pattern = next(iter(members))
        else:
            pattern = f"{key}/**"
        suggestions.append({"pattern": pattern, "commits": len(commits), "files": sorted(members)[:10],
                            "file_count": len(members)})
    suggestions.sort(key=lambda item: (-item["commits"], -item["file_count"], item["pattern"]))
    return suggestions[:MAX_PATTERNS]


def suggest_scopes(root: Path, repo: Path, decision_ids: list[str] | None, *, include_scoped: bool = False,
                   max_files: int = DEFAULT_MAX_FILES) -> dict:
    """Scope suggestions for live decisions; read-only."""
    from .vault_index import VaultIndex

    root = root.resolve()
    try:
        prefix = root.relative_to(repo).as_posix()
    except ValueError:
        prefix = None
    vault = VaultIndex.load(root)
    superseded = {str(note.front.get("supersedes") or "").strip() for note in vault.notes}
    live: dict[str, dict] = {}
    for note in vault.notes:
        did = str(note.front.get("decision_id") or "").strip()
        if (not DECISION_ID_RE.fullmatch(did) or note.front.get("type") != "decision"
                or str(note.front.get("status") or "") not in {"approved", "draft", "in_review"}
                or did in superseded):
            continue
        live[did] = {"note": note, "scope": declared_scope(note.front)[0]}
    if decision_ids:
        unknown = [did for did in decision_ids if did not in live]
        if unknown:
            raise AffectedError("not_found", f"no live decision {', '.join(unknown)}")
        selected = list(dict.fromkeys(decision_ids))
    else:
        selected = sorted(did for did, item in live.items() if include_scoped or not item["scope"])

    record_paths: dict[str, str | None] = {}
    for did in selected:
        note = live[did]["note"]
        record_paths[did] = None
        if prefix is not None:
            relative = vault.relative(note.path)
            record_paths[did] = (Path(prefix) / relative).as_posix() if prefix != "." else relative
    history = history_index(repo, record_paths)

    results: list[dict] = []
    for did in selected:
        note = live[did]["note"]
        commits = history[did]
        files: dict[str, set[str]] = defaultdict(set)
        skipped = 0
        for commit, changed in commits:
            code = [path for path in changed
                    if not (prefix and prefix != "." and (path == prefix or path.startswith(prefix + "/")))
                    and not NOISE.search(path)]
            if len(code) > max_files:
                skipped += 1
                continue
            for path in code:
                files[path].add(commit)
        existing = live[did]["scope"]
        from .affected import declared_terms
        known_terms = {term.casefold() for term in declared_terms(note.front)[0]}
        terms = [item for item in term_candidates(rejected_alternatives(note.text))
                 if item["term"].casefold() not in known_terms]
        suggestions = patterns_for(files)
        for item in suggestions:
            item["already_covered"] = any(glob_match(pattern, path) for pattern in existing for path in item["files"])
        results.append({
            "decision_id": did,
            "title": str(note.front.get("title") or note.path.stem),
            "path": vault.relative(note.path),
            "status": str(note.front.get("status") or ""),
            "current_scope": existing,
            "commits": len(commits),
            "skipped_wide_commits": skipped,
            "suggestions": suggestions,
            "term_suggestions": terms,
        })
    return {
        "contract_version": 1,
        "root": str(root),
        "repo": str(repo),
        "decisions": results,
        "summary": {
            "decisions": len(results),
            "with_suggestions": sum(1 for item in results if item["suggestions"]),
            "without_history": sum(1 for item in results if not item["commits"]),
        },
        "written": [],
    }


def write_scopes(root: Path, report: dict, *, min_commits: int = 1) -> list[str]:
    """Add the suggested patterns to decisions that have no scope yet."""
    from .io import atomic_write_text, vault_mutation_lock
    from .lint import load_note

    written: list[str] = []
    with vault_mutation_lock(root):
        for item in report["decisions"]:
            patterns = [s["pattern"] for s in item["suggestions"] if s["commits"] >= min_commits]
            if item["current_scope"] or not patterns:
                continue
            path = root / item["path"]
            note = load_note(path)
            if note.front_error or "applies_to" in note.front:
                continue
            text = path.read_text(encoding="utf-8")
            match = re.match(r"\A(﻿?---\r?\n.*?\r?\n)(---[ \t]*\r?\n)", text, re.S)
            if not match:
                continue
            newline = "\r\n" if "\r\n" in match.group(1) else "\n"
            line = "applies_to: [" + ", ".join(f'"{p}"' for p in patterns) + "]" + newline
            atomic_write_text(path, match.group(1) + line + text[match.end(1):])
            written.append(item["decision_id"])
    return written


def render_text(report: dict) -> str:
    lines = [f"WhyKit scope suggestions — {report['summary']['decisions']} decision(s), "
             f"{report['summary']['with_suggestions']} with suggestions"]
    for item in report["decisions"]:
        lines.append(f"  {item['decision_id']}  {item['title']}")
        if item["current_scope"]:
            lines.append(f"      current: {', '.join(item['current_scope'])}")
        if item.get("skipped_wide_commits"):
            lines.append(f"      ignored {item['skipped_wide_commits']} wide commit(s) (sweeps, imports, mass renames)")
        if not item["suggestions"]:
            lines.append("      no history: no commit changed this record or named "
                         f"{item['decision_id']} in its message")
        for term in item.get("term_suggestions", []):
            lines.append(f"      watch term? {term['term']!r} (rejected: {term['from'][:60]})")
        for suggestion in item["suggestions"]:
            covered = " (already covered)" if suggestion["already_covered"] else ""
            lines.append(f"      {suggestion['pattern']:<32} {suggestion['commits']} commit(s), "
                         f"{suggestion['file_count']} file(s){covered}")
    if report["written"]:
        lines.append(f"  wrote applies_to to {', '.join(report['written'])}")
    elif any(item["suggestions"] and not item["current_scope"] for item in report["decisions"]):
        lines.append("  Review the patterns, then run again with --write to add them to decisions without a scope.")
    return "\n".join(lines)


# ------------------------------------------------------------- scope check

def tracked_files(repo: Path) -> list[str]:
    return [path for path in _git(repo, "ls-files", "-z") if path]


def check_scopes(root: Path, repo: Path, *, term_scan_limit: int = 20_000) -> dict:
    """Find scopes that have rotted: patterns matching nothing, broad patterns, unseen terms.

    A scope written once decays silently: code moves, a library is renamed, a
    term is misspelt. This reads the repository's tracked files (and, for
    terms, the contents of up to ``term_scan_limit`` source files) and reports
    what no longer points anywhere. Read-only.
    """
    from .affected import declared_terms, is_broad, term_scannable, _term_re
    from .vault_index import VaultIndex

    root = root.resolve()
    try:
        prefix = root.relative_to(repo).as_posix()
    except ValueError:
        prefix = None
    files = [
        path for path in tracked_files(repo)
        if not (prefix and prefix != "." and (path == prefix or path.startswith(prefix + "/")))
    ]
    vault = VaultIndex.load(root)
    superseded = {str(note.front.get("supersedes") or "").strip() for note in vault.notes}
    decisions = []
    all_terms: set[str] = set()
    for note in vault.notes:
        did = str(note.front.get("decision_id") or "").strip()
        if (not DECISION_ID_RE.fullmatch(did) or note.front.get("type") != "decision"
                or str(note.front.get("status") or "") != "approved" or did in superseded):
            continue
        patterns, invalid = declared_scope(note.front)
        terms, invalid_terms = declared_terms(note.front)
        all_terms.update(terms)
        decisions.append((did, note, patterns, terms, invalid + invalid_terms))

    # One pass over source files for every declared term.
    seen_terms: set[str] = set()
    scanned = 0
    if all_terms:
        compiled = {term: _term_re(term) for term in all_terms}
        for path in files:
            if scanned >= term_scan_limit or len(seen_terms) == len(all_terms):
                break
            if not term_scannable(path):
                continue
            target = repo / path
            try:
                if target.is_symlink() or not target.is_file() or target.stat().st_size > 1024 * 1024:
                    continue
                data = target.read_bytes()
            except OSError:
                continue
            if b"\0" in data:
                continue
            scanned += 1
            text = data.decode("utf-8", "replace")
            for term, regex in compiled.items():
                if term not in seen_terms and regex.search(text):
                    seen_terms.add(term)

    results: list[dict] = []
    for did, note, patterns, terms, invalid in sorted(decisions, key=lambda item: item[0]):
        counts = {pattern: sum(1 for path in files if glob_match(pattern, path)) for pattern in patterns}
        findings: list[dict[str, str]] = []
        for pattern, count in counts.items():
            if count == 0:
                findings.append({"kind": "dead_pattern", "value": pattern,
                                 "detail": "matches no tracked file; the code may have moved"})
            elif is_broad(pattern):
                findings.append({"kind": "broad_pattern", "value": pattern,
                                 "detail": f"covers a whole top-level folder ({count} files); most changes will match"})
        for term in terms:
            if term not in seen_terms:
                findings.append({"kind": "unseen_term", "value": term,
                                 "detail": "appears in no scanned source file; fine for a rejected option, "
                                           "but check the spelling"})
        for item in invalid:
            findings.append({"kind": "invalid", "value": item["pattern"], "detail": item["problem"]})
        if not patterns and not terms:
            findings.append({"kind": "unscoped", "value": "", "detail": "no applies_to or watch_terms; "
                                                                     "`whykit affected` can never match it"})
        results.append({
            "decision_id": did,
            "title": str(note.front.get("title") or note.path.stem),
            "path": vault.relative(note.path),
            "patterns": [{"pattern": pattern, "files": count} for pattern, count in counts.items()],
            "terms": terms,
            "findings": findings,
        })
    kinds = [finding["kind"] for item in results for finding in item["findings"]]
    return {
        "contract_version": 1,
        "root": str(root),
        "repo": str(repo),
        "tracked_files": len(files),
        "scanned_for_terms": scanned,
        "decisions": results,
        "summary": {kind: kinds.count(kind) for kind in ("dead_pattern", "broad_pattern", "unseen_term", "invalid", "unscoped")},
        "problems": sum(kinds.count(kind) for kind in ("dead_pattern", "invalid")),
    }


def render_check(report: dict) -> str:
    summary = report["summary"]
    lines = [
        f"WhyKit scope check — {len(report['decisions'])} live decision(s), {report['tracked_files']} tracked files: "
        f"{summary['dead_pattern']} dead pattern(s), {summary['broad_pattern']} broad, "
        f"{summary['unseen_term']} unseen term(s), {summary['unscoped']} unscoped"
    ]
    for item in report["decisions"]:
        if not item["findings"]:
            continue
        lines.append(f"  {item['decision_id']}  {item['title']}")
        for finding in item["findings"]:
            value = f" {finding['value']}" if finding["value"] else ""
            lines.append(f"      {finding['kind']}{value}: {finding['detail']}")
    if not any(item["findings"] for item in report["decisions"]):
        lines.append("  every scope points at tracked files and every term was seen")
    return "\n".join(lines)
