"""Vault layout helpers for `whykit init`."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

# Marketing / GTM-shaped workstreams from the full template. Minimal layout drops them.
GTM_WORKSTREAMS = (
    "01-strategy",
    "02-discoverability",
    "03-website",
    "04-automation",
    "05-operations",
    "07-research",
)

GTM_TEMPLATES = (
    "automation-spec-template.md",
    "page-brief-template.md",
)

MINIMAL_HOME = """---
title: Home
aliases:
  - Map of content
type: map-of-content
status: approved
owner: TODO
created: {today}
last_updated: {today}
source_of_truth: false
sensitivity: public
tags:
  - map-of-content
---

# Home

Map of content for this WhyKit vault. Keep it current — every session starts here.

## Read first

- [[README|What this vault is]]
- [[AGENTS|Working rules for agents and people]]
- [[OBSIDIAN|Vault conventions]]
- [[INTEROP|Working across repositories]]

## Foundations

- [[00-context/company|Organization context]]
- [[00-context/goals|Goals]]
- [[00-context/terminology|Terminology]]
- [[00-context/evidence-register|Evidence register]]
- [[00-context/review-log|Review log]]

## Working notes

- [[notes/README|Freeform notes]]

## Decisions

- [[06-decisions/decision-log|Decision log]]

## Open threads

| Waiting on | What exactly | What it unblocks |
|---|---|---|
|  |  |  |
"""

MINIMAL_README = """---
title: Company knowledge vault
aliases: []
type: guide
status: approved
owner: TODO
created: {today}
last_updated: {today}
source_of_truth: false
sensitivity: public
tags: []
---

# Company knowledge vault

An [Obsidian](https://obsidian.md)-compatible Markdown vault checked by
[WhyKit](https://github.com/CometWeb-io/whykit): evidence, decisions, and review
dates in Git — not a second database.

## Layout

| Path | Role |
|---|---|
| `Home.md` | Map of content |
| `AGENTS.md` | Contract for people and agents |
| `00-context/` | Shared context and the evidence register |
| `06-decisions/` | Decision records and the decision log |
| `notes/` | Freeform working notes |
| `templates/` | Starters for new records |
| `whykit.toml` | Repository-local lint/CI policy |

Created with `whykit init` (the vendor-neutral default layout).
"""

MINIMAL_NOTES_README = """---
title: Notes
aliases: []
type: guide
status: draft
owner: TODO
created: {today}
last_updated: {today}
source_of_truth: false
sensitivity: internal
tags: []
---

# Notes

Freeform working notes. Link anything durable from `Home.md`. Promote decisions
into `06-decisions/` and sources into the evidence register — do not leave
material claims only in this folder.
"""

MINIMAL_AGENTS_PURPOSE = """## Repository purpose

This repository is a durable evidence and decision ledger. It stores context,
accepted decisions, supporting evidence and review dates. It is not a task
tracker, not a CRM, and not a place for credentials.
"""


def apply_minimal_layout(target: Path, *, today: dt.date | None = None) -> None:
    """Trim a full template copy into a vendor-neutral minimal vault."""
    today = today or dt.date.today()
    stamp = today.isoformat()

    for name in GTM_WORKSTREAMS:
        path = target / name
        if path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()

    templates = target / "templates"
    for name in GTM_TEMPLATES:
        path = templates / name
        if path.is_file():
            path.unlink()

    notes = target / "notes"
    notes.mkdir(exist_ok=True)
    (notes / "README.md").write_text(MINIMAL_NOTES_README.format(today=stamp), encoding="utf-8")
    (target / "Home.md").write_text(MINIMAL_HOME.format(today=stamp), encoding="utf-8")
    (target / "README.md").write_text(MINIMAL_README.format(today=stamp), encoding="utf-8")

    agents = target / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(encoding="utf-8")
        text = re.sub(
            r"## Repository purpose\n\n.*?(?=\n## )",
            MINIMAL_AGENTS_PURPOSE + "\n",
            text,
            count=1,
            flags=re.S,
        )
        text = text.replace(
            "This repository is the operating knowledge base for go-to-market work. It stores\n"
            "durable context, strategic documents, research, specifications and accepted\n"
            "decisions. It is not a task tracker, not a CRM, and not a place for credentials.\n",
            "",
        )
        text = re.sub(
            r"\d+\. Create an ingestion record in `07-research/sources/` from\n"
            r"   `templates/source-ingestion-template.md`\.\n",
            "3. Create an ingestion record (for example under `notes/` or `reports/`) from\n"
            "   `templates/source-ingestion-template.md`.\n",
            text,
            count=1,
        )
        agents.write_text(text, encoding="utf-8")

    leftovers = removed_workstream_mentions(target)
    if leftovers:
        raise RuntimeError(
            "minimal layout still points at a removed workstream: " + "; ".join(leftovers)
        )


def removed_workstream_mentions(root: Path) -> list[str]:
    """Paths of generated files that still name a workstream minimal layout deletes."""
    needles = tuple(f"{name}/" for name in GTM_WORKSTREAMS)
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".toml", ".txt", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        found = [needle for needle in needles if needle in text]
        if found:
            hits.append(f"{path.relative_to(root)} ({', '.join(found)})")
    return hits
