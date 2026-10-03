"""Deterministic synthetic vaults for performance work.

``generate(root, notes=5000)`` writes a vault that looks like a real one at
scale: workstream folders, a populated evidence register, a decision log with
supersession chains, a review log, and notes that carry front matter, tables,
fact callouts, code fences and roughly six wikilinks each (by path, by stem,
by alias, with anchors and labels). A small, fixed share of links is broken,
ambiguous or points at an attachment, and a few notes are orphans, so every
expensive lint path does real work instead of short-circuiting on a clean
vault.

The output depends only on ``notes`` and ``seed``: the same arguments produce
byte-identical files on every machine, which is what makes golden comparisons
of command output meaningful. Fixture data uses reserved names only
(``example.com`` / ``.example``).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import random
import re
from pathlib import Path

WORKSTREAMS = (
    "01-strategy", "02-discoverability", "03-website", "04-automation",
    "05-operations", "07-research",
)
OWNERS = ("Product Lead", "Growth Lead", "Operations Lead", "Revenue Operations", "Research Lead")
STATUSES = ("approved", "approved", "approved", "draft", "in_review", "archived")
TYPES = ("strategy", "research", "framework", "specification", "guide", "reference")
SENSITIVITY = ("public", "internal", "internal", "confidential")
TAGS = ("pricing", "icp", "seo", "pipeline", "onboarding", "churn", "messaging", "ops", "hiring", "partners")
WORDS = (
    "maintenance downtime pipeline evidence decision review owner budget plant shift "
    "machine list churn renewal onboarding interview pricing segment margin forecast "
    "capacity backlog incident ticket workflow migration benchmark quarter baseline "
    "conversion signal anomaly cohort retention narrative sequence regional partner"
).split()

_FRONT = """---
title: "{title}"
aliases: [{aliases}]
type: {doc_type}
status: {status}
owner: "{owner}"
created: {created}
last_updated: {updated}
{extra}source_of_truth: {sot}
sensitivity: {sensitivity}
source_ids: [{sources}]
tags: [{tags}]
workstream: "{workstream}"
---
"""


def _date(rng: random.Random, year: int = 2026) -> str:
    return f"{year}-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"


def _sentence(rng: random.Random, words: int = 14) -> str:
    text = " ".join(rng.choice(WORDS) for _ in range(words))
    return text[0].upper() + text[1:] + "."


def _quote_list(items: list[str]) -> str:
    return ", ".join(f'"{item}"' for item in items)


def generate(root: Path, notes: int = 5000, *, seed: int = 20261003) -> Path:
    """Write a synthetic vault of about ``notes`` Markdown files under ``root``."""
    rng = random.Random(f"{seed}:{notes}")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("00-context", "06-decisions", "reports", "templates", "assets", *WORKSTREAMS):
        (root / name).mkdir(exist_ok=True)

    evidence_count = max(20, notes // 10)
    decision_count = max(10, notes // 20)
    report_count = max(2, notes // 100)
    body_count = max(10, notes - decision_count - report_count - 12)

    evidence_ids = [f"E-{i:03d}" for i in range(1, evidence_count + 1)]
    retired_ids = evidence_ids[-max(2, evidence_count // 25):]
    active_ids = evidence_ids[: len(evidence_ids) - len(retired_ids)]

    # Note identities first, so links can point forward as well as backward.
    entries: list[tuple[str, str, str]] = []  # (workstream, stem, alias)
    for i in range(body_count):
        ws = WORKSTREAMS[i % len(WORKSTREAMS)]
        stem = f"note-{i:05d}"
        # About one note in 400 reuses a stem in another folder: links by bare
        # stem to it are ambiguous, links by path are fine.
        if i and i % 400 == 0:
            stem = f"note-{i - 1:05d}"
        entries.append((ws, stem, f"N{i:05d}"))
    paths = [f"{ws}/{stem}" for ws, stem, _ in entries]

    (root / "assets" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    (root / "assets" / "brief.pdf").write_bytes(b"%PDF-1.4 synthetic")

    def link(i: int) -> str:
        roll = rng.random()
        target = rng.randrange(len(entries))
        ws, stem, alias = entries[target]
        if roll < 0.55:
            text = f"[[{ws}/{stem}|{stem.replace('-', ' ')}]]"
        elif roll < 0.75:
            text = f"[[{stem}]]"
        elif roll < 0.85:
            text = f"[[{alias}]]"
        elif roll < 0.93:
            text = f"[[{ws}/{stem}#Context]]"
        elif roll < 0.96:
            text = f"[[06-decisions/d-{rng.randint(1, decision_count):03d}-record|decision]]"
        elif roll < 0.975:
            text = "![[diagram.png]]"
        elif roll < 0.99:
            text = "[[00-context/evidence-register|register]]"
        else:
            text = f"[[missing-target-{i % 97}]]"
        return text

    # Body notes.
    for i, (ws, stem, alias) in enumerate(entries):
        status = STATUSES[i % len(STATUSES)]
        created = _date(rng, 2025)
        updated = _date(rng, 2026)
        sources = rng.sample(evidence_ids, rng.randint(0, 3))
        if i % 211 == 0:
            sources.append("E-99999")  # unregistered: evidence.missing
        extra = f"review_by: {_date(rng, 2026 if i % 3 else 2027)}\n" if status == "approved" and i % 2 else ""
        front = _FRONT.format(
            title=f"Synthetic note {i:05d}", aliases=_quote_list([alias]), doc_type=TYPES[i % len(TYPES)],
            status=status, owner=OWNERS[i % len(OWNERS)], created=created, updated=updated, extra=extra,
            sot="true" if status == "approved" and i % 7 == 0 else "false",
            sensitivity=SENSITIVITY[i % len(SENSITIVITY)], sources=_quote_list(sources),
            tags=_quote_list(rng.sample(TAGS, 2)), workstream=ws,
        )
        parts = [front, f"\n# Synthetic note {i:05d}\n\n## Context\n\n"]
        for _ in range(rng.randint(3, 6)):
            links = " ".join(link(i) for _ in range(rng.randint(0, 2)))
            parts.append(f"{_sentence(rng)} {links}\n\n")
        if i % 5 == 0:
            cite = rng.choice(active_ids) if i % 15 else "E-88888"
            parts.append(f"> [!fact] {_sentence(rng, 8)} [{cite}]\n> {_sentence(rng, 6)}\n\n")
        if i % 9 == 0:
            parts.append("> [!fact] An uncited claim about the baseline.\n\n")
        if i % 4 == 0:
            parts.append("| Item | Owner | Link |\n|---|---|---|\n")
            for _ in range(rng.randint(2, 5)):
                parts.append(f"| {rng.choice(WORDS)} | {rng.choice(OWNERS)} | {link(i).replace('|', chr(92) + '|')} |\n")
            parts.append("\n")
        if i % 6 == 0:
            parts.append("```text\n[[not-a-link-in-code]] and `[[inline]]`\n```\n\n")
        if i % 10 == 0:
            parts.append(f"See [the brief](../assets/brief.pdf) and [a gap](./missing-{i}.md).\n\n")
        parts.append("## Notes\n\n")
        for _ in range(rng.randint(2, 5)):
            parts.append(f"- {_sentence(rng, 10)} {link(i)}\n")
        (root / ws / f"{stem}.md").write_text("".join(parts), encoding="utf-8", newline="\n")

    # Workstream maps link a slice of their notes, so most notes have an inbound link
    # even when no other note happens to point at them.
    for ws in WORKSTREAMS:
        members = [p for p in paths if p.startswith(ws + "/")]
        lines = [f"- [[{p}]]" for p in members if not p.endswith("7")]  # leave some orphans
        (root / ws / "README.md").write_text(f"# {ws}\n\n" + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    # Evidence register.
    rows = ["| ID | Source | Type | Date | Accessed | Location | Claims it supports |", "|---|---|---|---|---|---|---|"]
    for k, eid in enumerate(active_ids):
        loc = f"[[{paths[k % len(paths)]}]]" if k % 3 == 0 else f"`assets/source-{k}.csv`"
        rows.append(f"| {eid} | Synthetic source {k} | {('analytics', 'interview', 'internal')[k % 3]} | {_date(rng)} | {_date(rng)} | {loc} | {_sentence(rng, 6)} |")
    retired = ["| ID | Source | Retired on | Why | Replaced by |", "|---|---|---|---|---|"]
    for k, eid in enumerate(retired_ids):
        retired.append(f"| {eid} | Retired source {k} | {_date(rng)} | superseded | {active_ids[k]} |")
    (root / "00-context" / "evidence-register.md").write_text(
        _FRONT.format(title="Evidence register", aliases="", doc_type="reference", status="approved", owner="Product Lead",
                      created="2025-01-02", updated="2026-09-01", extra="", sot="true", sensitivity="internal",
                      sources="", tags='"evidence"', workstream="00-context")
        + "\n# Evidence register\n\n" + "\n".join(rows) + "\n\n## Retired sources\n\n" + "\n".join(retired) + "\n",
        encoding="utf-8", newline="\n",
    )

    # Decisions: every fifth one supersedes its predecessor.
    log = ["| ID | Decision | Date | Owner | Status | Record |", "|---|---|---|---|---|---|"]
    superseded = {d - 1 for d in range(2, decision_count + 1) if d % 5 == 0}
    for d in range(1, decision_count + 1):
        did = f"D-{d:03d}"
        status = "superseded" if d in superseded else ("approved" if d % 7 else "draft")
        created = _date(rng, 2026)
        extra = f"decision_id: {did}\n"
        if d % 5 == 0:
            extra += f"supersedes: D-{d - 1:03d}\n"
        if d in superseded:
            extra += f"superseded_by: D-{d + 1:03d}\n"
        if status == "approved" and d % 11:
            extra += f"review_by: {_date(rng, 2027)}\n"
        sources = rng.sample(active_ids, rng.randint(0, 3))
        body = (
            _FRONT.format(title=f"{did} — Synthetic decision {d}", aliases=_quote_list([did]), doc_type="decision",
                          status=status, owner=OWNERS[d % len(OWNERS)], created=created, updated=created,
                          extra=extra, sot="false", sensitivity="internal", sources=_quote_list(sources),
                          tags='"decisions"', workstream="06-decisions")
            + f"\n# Decision record {d}\n\n## Decision ID\n\n{did}\n\n## Context\n\n{_sentence(rng)} {link(d)}\n\n"
            + f"## Decision\n\n{_sentence(rng)}\n\n## Rationale\n\n{_sentence(rng)} [{rng.choice(active_ids)}] {link(d)}\n"
        )
        (root / "06-decisions" / f"d-{d:03d}-record.md").write_text(body, encoding="utf-8", newline="\n")
        log_status = {"approved": "accepted", "draft": "proposed", "superseded": "superseded"}[status]
        log.append(f"| {did} | Synthetic decision {d} | {created} | {OWNERS[d % len(OWNERS)]} | {log_status} | [[06-decisions/d-{d:03d}-record]] |")
    (root / "06-decisions" / "decision-log.md").write_text(
        _FRONT.format(title="Decision log", aliases="", doc_type="reference", status="approved", owner="Product Lead",
                      created="2025-01-02", updated="2026-09-01", extra="", sot="true", sensitivity="internal",
                      sources="", tags='"decisions"', workstream="06-decisions")
        + "\n# Decision log\n\n" + "\n".join(log) + "\n",
        encoding="utf-8", newline="\n",
    )
    (root / "06-decisions" / "README.md").write_text("# Decisions\n\n- [[06-decisions/decision-log]]\n", encoding="utf-8", newline="\n")

    # Review log.
    review = ["| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |", "|---|---|---|---|---|---|---|"]
    for k in range(max(5, decision_count // 4)):
        d = rng.randint(1, decision_count)
        review.append(f"| {_date(rng)} | [[06-decisions/d-{d:03d}-record]] | {OWNERS[k % len(OWNERS)]} | confirmed | — | {_date(rng, 2027)} | Rechecked. |")
    (root / "00-context" / "review-log.md").write_text(
        _FRONT.format(title="Review log", aliases="", doc_type="reference", status="approved", owner="Product Lead",
                      created="2025-01-02", updated="2026-09-01", extra="", sot="false", sensitivity="internal",
                      sources="", tags='"reviews"', workstream="00-context")
        + "\n# Review log\n\n" + "\n".join(review) + "\n",
        encoding="utf-8", newline="\n",
    )

    # Reports: dated filenames, plus one undated to keep report.undated exercised.
    report_lines = []
    for k in range(report_count):
        created = f"2026-{(k % 9) + 1:02d}-{(k % 27) + 1:02d}"
        name = f"baseline-{k:04d}-{created}" if k else f"baseline-undated-{k}"
        report_lines.append(f"- [[reports/{name}]]")
        (root / "reports" / f"{name}.md").write_text(
            _FRONT.format(title=f"Baseline {k}", aliases="", doc_type="research", status="approved", owner="Growth Lead",
                          created=created, updated=created, extra="", sot="false", sensitivity="internal",
                          sources=_quote_list(rng.sample(active_ids, 2)), tags='"reports"', workstream="reports")
            + f"\n# Baseline {k}\n\n{_sentence(rng)} {link(k)}\n",
            encoding="utf-8", newline="\n",
        )
    (root / "reports" / "README.md").write_text("# Reports\n\n" + "\n".join(report_lines) + "\n", encoding="utf-8", newline="\n")

    for name, title in (("company", "Company context"), ("goals", "Goals"), ("terminology", "Terminology")):
        (root / "00-context" / f"{name}.md").write_text(
            _FRONT.format(title=title, aliases="", doc_type="reference", status="approved", owner="Product Lead",
                          created="2025-01-02", updated="2026-09-01", extra="", sot="true", sensitivity="internal",
                          sources=_quote_list(active_ids[:2]), tags='"context"', workstream="00-context")
            + f"\n# {title}\n\n{_sentence(rng)}\n",
            encoding="utf-8", newline="\n",
        )
    (root / "00-context" / "README.md").write_text(
        "# Context\n\n- [[00-context/company]]\n- [[00-context/goals]]\n- [[00-context/terminology]]\n"
        "- [[00-context/evidence-register]]\n- [[00-context/review-log]]\n",
        encoding="utf-8", newline="\n",
    )
    (root / "templates" / "README.md").write_text("# Templates\n", encoding="utf-8", newline="\n")
    (root / "assets" / "README.md").write_text("# Assets\n", encoding="utf-8", newline="\n")
    (root / "AGENTS.md").write_text("# Agent rules\n\nWrite in English. Open a pull request for every change.\n", encoding="utf-8", newline="\n")
    (root / "whykit.toml").write_text(
        'format_version = 1\n\n[defaults]\nowner = "Product Lead"\nsensitivity = "internal"\n'
        "decision_review_days = 90\nstatus_due_days = 30\n\n[evidence_access_age_days]\nanalytics = 120\n",
        encoding="utf-8", newline="\n",
    )
    home_links = "\n".join(f"- [[{name}/README]]" for name in ("00-context", "06-decisions", "reports", *WORKSTREAMS))
    (root / "Home.md").write_text(
        _FRONT.format(title="Home", aliases='"Map of content"', doc_type="map-of-content", status="approved",
                      owner="Product Lead", created="2025-01-02", updated="2026-09-01", extra="", sot="false",
                      sensitivity="internal", sources="", tags='"map-of-content"', workstream="root")
        + "\n# Home\n\n- [[AGENTS]]\n" + home_links + "\n",
        encoding="utf-8", newline="\n",
    )
    return root


AS_OF = "2026-09-17"
# Read commands whose output is pinned by digest: (name, argv after `whykit`, before --root).
DIGEST_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("lint", ["lint", "--json", "--today", AS_OF]),
    ("status", ["status", "--json", "--today", AS_OF]),
    ("snapshot", ["snapshot", "--today", AS_OF, "--compact"]),
    ("check", ["check", "--profile", "ci", "--json", "--today", AS_OF]),
    ("query", ["query", "pipeline", "--json"]),
    ("context", ["context", "D-010", "--json"]),
    ("pack", ["pack", "D-010", "01-strategy/note-00006", "--query", "pipeline", "--json"]),
    ("trace", ["trace", "--today", AS_OF, "--json"]),
    ("explorer-index", ["explorer-index", "--today", AS_OF]),
    ("graph", ["graph", "--json"]),
    ("impact", ["impact", "01-strategy/note-00006", "--json"]),
    ("backlinks", ["backlinks", "E-010", "--json"]),
)
_GENERATED_AT = re.compile(r'"generatedAt": "[^"]*"')


def output_digests(root: Path) -> dict[str, str]:
    """SHA-256 of each pinned command's exit code and stdout, run in-process.

    The vault's absolute path and the Explorer's wall-clock stamp are the only
    values that legitimately vary between runs; both are normalised away.
    """
    from whykit.cli import main as whykit_main

    root = Path(root).resolve()
    digests: dict[str, str] = {}
    for name, argv in DIGEST_COMMANDS:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = whykit_main([argv[0], "--root", str(root), *argv[1:]])
            except SystemExit as exc:  # argparse paths
                code = exc.code
        text = _GENERATED_AT.sub('"generatedAt": ""', out.getvalue()).replace(json.dumps(str(root))[1:-1], "<ROOT>").replace(str(root), "<ROOT>")
        digests[name] = hashlib.sha256(f"exit={code}\n{text}".encode()).hexdigest()
    return digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a deterministic synthetic WhyKit vault.")
    parser.add_argument("destination")
    parser.add_argument("--notes", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--digests", action="store_true", help="print output digests of the pinned commands as JSON")
    args = parser.parse_args(argv)
    destination = Path(args.destination)
    if destination.exists() and any(destination.iterdir()):
        parser.error(f"{destination} is not empty")
    generate(destination, args.notes, seed=args.seed)
    if args.digests:
        print(json.dumps(output_digests(destination), indent=4, sort_keys=True))
        return 0
    print(f"wrote {sum(1 for _ in destination.rglob('*.md'))} Markdown files to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
