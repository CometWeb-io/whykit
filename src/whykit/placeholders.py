"""Placeholder prose that WhyKit itself writes into new decision records.

`whykit new decision` renders its skeleton from the constants below, and the
`decision.placeholder` lint rule reads the same constants, so the two cannot
drift apart. The hand-copied template in ``template/templates/`` is parsed at
lint time for the same reason: whatever prose ships there is what an adopter
forgets to replace.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

# Section heading -> the paragraph `whykit new decision` writes under it.
SCAFFOLD_SECTION_PROMPTS: dict[str, str] = {
    "Context": "What is true now, and what forces a choice?",
    "Decision": "State the choice in one sentence.",
    "Rationale": "Why this option, given the evidence and constraints?",
}
SCAFFOLD_EVIDENCE_TODO = "- TODO — add E-NNN references or explain why none apply."
ALTERNATIVES_HEADER = "| Alternative | Upside | Risk | Why rejected |"
ALTERNATIVES_EMPTY_ROW = "|  |  |  |  |"

# Sections whose prose is the decision itself: a placeholder or nothing at all
# here means the record does not say what was decided or why.
PROSE_SECTIONS = ("Context", "Decision", "Rationale")

TEMPLATE_PATH = Path(__file__).resolve().parent / "template" / "templates" / "decision-record-template.md"


def normalize(text: str) -> str:
    """Collapse whitespace so a re-wrapped placeholder still matches."""
    return " ".join(text.split())


def split_sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """``(heading, first_body_line, end_line)`` for every ``## `` section.

    Line indexes are 0-based and half-open; ``### `` subsections stay inside
    their parent section.
    """
    heads = [(index, line[3:].strip()) for index, line in enumerate(lines) if line.startswith("## ")]
    sections = []
    for position, (index, heading) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        sections.append((heading, index + 1, end))
    return sections


def paragraphs(lines: list[str]) -> list[tuple[int, str]]:
    """Plain prose paragraphs as ``(first_line_index, normalized_text)``.

    Headings, list items, table rows and quotes end a paragraph and are not
    part of one.
    """
    found: list[tuple[int, str]] = []
    start: int | None = None
    chunk: list[str] = []
    for index, line in enumerate([*lines, ""]):
        stripped = line.strip()
        prose = bool(stripped) and not re.match(r"^(#|[-*+] |\d+[.)] |\||>)", stripped) and stripped not in {"-", "*", "+"}
        if prose:
            if start is None:
                start = index
            chunk.append(stripped)
        elif chunk:
            found.append((start or 0, normalize(" ".join(chunk))))
            start, chunk = None, []
    return found


@functools.lru_cache(maxsize=1)
def template_section_prompts() -> dict[str, frozenset[str]]:
    """Placeholder paragraphs per section, from the scaffold and the shipped template."""
    prompts: dict[str, set[str]] = {name: {normalize(text)} for name, text in SCAFFOLD_SECTION_PROMPTS.items()}
    try:
        lines = TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for heading, start, end in split_sections(lines):
        for _, text in paragraphs(lines[start:end]):
            prompts.setdefault(heading, set()).add(text)
    return {name: frozenset(values) for name, values in prompts.items()}
