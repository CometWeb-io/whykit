#!/usr/bin/env python3
"""Replay a repository's recent commits through ``whykit affected``.

A field check, not a benchmark of truth: for each of the last ``--commits``
non-merge commits, the files it changed are passed to ``affected`` (plain ADR
mode with ``--adr``, otherwise the vault at ``--root``), and the script reports
how often decisions applied, how many files no decision covered, which ADRs
had a scope at all and which patterns fired most. That is what you need to
spot noise (a pattern matching almost every commit) and gaps (decisions that
never match) before labelling cases by hand.

    python3 scripts/eval_affected.py /path/to/repo --adr docs/decisions --commits 300
    python3 scripts/eval_affected.py /path/to/repo --root decisions --json

Read-only: it runs ``git log`` and ``git show`` and never checks out, writes
or fetches. The ADRs and the working tree are read as they are now, so a
scope inferred from today's paths is replayed against older commits; read the
results as "what WhyKit would say about this change today".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whykit.affected import _git, build_affected, build_affected_adr, parse_unified_diff  # noqa: E402
import subprocess  # noqa: E402


def recent_commits(repo: Path, count: int) -> list[str]:
    lines = _git(repo, "log", "--no-merges", f"--max-count={count}", "--format=%H%x00")
    return [line.strip() for line in lines if line.strip()]


def changed(repo: Path, commit: str) -> list[str]:
    return sorted({p.strip() for p in _git(repo, "show", "--name-only", "--format=", "--no-renames", "-z", commit, "--") if p.strip()})


def commit_diff(repo: Path, commit: str) -> dict[str, list[str]]:
    out = subprocess.run(["git", "-C", str(repo), "show", "-U0", "--no-renames", "--no-color", "--format=", commit],
                         capture_output=True, check=False).stdout.decode("utf-8", "replace")
    return parse_unified_diff(out)[0]


def evaluate(repo: Path, *, adr: Path | None, root: Path | None, commits: int, today: dt.date,
             with_diff: bool = False, samples: int = 0) -> dict:
    hits: Counter[str] = Counter()
    patterns: Counter[str] = Counter()
    signals: Counter[str] = Counter()
    rows = []
    scope_sources: Counter[str] = Counter()
    term_hits: dict[str, list[str]] = {}
    first = None
    for commit in recent_commits(repo, commits):
        paths = changed(repo, commit)
        if not paths:
            continue
        source = {"kind": "files", "base": None}
        diff = commit_diff(repo, commit) if with_diff else None
        if adr is not None:
            report = build_affected_adr(adr, repo, paths, source, today=today, diff=diff)
        else:
            assert root is not None
            report = build_affected(root, repo, paths, source, today=today, diff=diff)
        for item in report["decisions"]:
            for hit in item.get("matched_terms", []):
                term_hits.setdefault(hit["term"], []).append(commit[:12])
        first = first or report
        for item in report["decisions"]:
            hits[item["decision_id"]] += 1
            patterns.update(item["matched_patterns"])
            signals.update(item["problems"])
        rows.append({
            "commit": commit[:12],
            "files": report["summary"]["changed_files"],
            "decisions": report["summary"]["applicable_decisions"],
            "review": report["summary"]["needing_review"],
            "uncovered": report["summary"]["uncovered_files"],
        })
    evaluated = [row for row in rows if row["files"]]
    total = len(evaluated) or 1
    # Scope provenance per decision, from one full pass over every changed path.
    if first is not None and adr is not None:
        all_paths = sorted({p for c in recent_commits(repo, commits) for p in changed(repo, c)})
        full = build_affected_adr(adr, repo, all_paths, {"kind": "files", "base": None}, today=today)
        for item in full["decisions"]:
            scope_sources[item.get("scope_source", "declared")] += 1
        live = full.get("adr", {}).get("live", 0)
        unscoped = len(full["unscoped_decisions"])
        missing = sum(1 for item in full["decisions"] if item["problems"])
    else:
        live = unscoped = missing = 0
    noisy = [{"pattern": p, "share": round(n / total, 3)} for p, n in patterns.most_common(5)]
    return {
        "repo": str(repo),
        "mode": "adr" if adr is not None else "vault",
        "commits": len(evaluated),
        "with_decisions": sum(1 for row in evaluated if row["decisions"]),
        "with_review_signal": sum(1 for row in evaluated if row["review"]),
        "mean_decisions_per_commit": round(sum(row["decisions"] for row in evaluated) / total, 3),
        "uncovered_file_share": round(
            sum(row["uncovered"] for row in evaluated) / max(1, sum(row["files"] for row in evaluated)), 3),
        "decisions_ever_matched": len(hits),
        "adr": {"live": live, "unscoped": unscoped, "matched_with_moved_paths": missing,
                "scope_sources": dict(scope_sources)} if adr is not None else None,
        "top_decisions": hits.most_common(5),
        "top_patterns": noisy,
        "signals": dict(signals),
        "term_hits": {term: {"commits": len(found), "sample": found[:samples]} for term, found in term_hits.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay recent commits through whykit affected.")
    parser.add_argument("repo", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--adr", type=Path, help="ADR folder, relative to the repository")
    group.add_argument("--root", type=Path, help="vault, relative to the repository")
    parser.add_argument("--commits", type=int, default=200)
    parser.add_argument("--today")
    parser.add_argument("--diff", action="store_true", help="also scan each commit's changed lines for watch_terms")
    parser.add_argument("--samples", type=int, default=5, help="commit IDs to list per matched term, for spot checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    result = evaluate(repo, adr=repo / args.adr if args.adr else None,
                      root=repo / args.root if args.root else None, commits=args.commits, today=today,
                      with_diff=args.diff, samples=args.samples)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{result['repo']} ({result['mode']}): {result['commits']} commits")
    print(f"  with an applicable decision: {result['with_decisions']}  with a review signal: {result['with_review_signal']}")
    print(f"  decisions per commit: {result['mean_decisions_per_commit']}  uncovered file share: {result['uncovered_file_share']}")
    if result["adr"]:
        print(f"  ADRs live: {result['adr']['live']}  unscoped: {result['adr']['unscoped']}  scope sources: {result['adr']['scope_sources']}")
    print(f"  top patterns (share of commits): {result['top_patterns']}")
    print(f"  signals: {result['signals']}")
    for term, found in result["term_hits"].items():
        print(f"  term {term!r}: {found['commits']} commit(s), e.g. {', '.join(found['sample'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
