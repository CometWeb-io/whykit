"""Turn an adopted architecture decision record into a WhyKit decision record.

`whykit adopt --write` stages old ADRs under `.import-staging/` and stops: a
person decides what becomes canonical. Once they have decided, retyping an ADR
into `whykit new decision` loses its wording and any trace of where it came
from. `whykit new decision --from FILE` does the mechanical half instead:

* recognise the common shapes — MADR, Michael Nygard's layout (also written by
  adr-tools and log4brains), Y-statements and ADRs with Polish headings;
* copy each recognised section into the matching WhyKit section, and keep the
  complete original text, byte for byte, under "Original record";
* record provenance in front matter: the source path, its SHA-256, the import
  batch and the status and date the source claimed;
* list links found in the source under Evidence as TODO candidates, without
  inventing evidence IDs;
* allocate the next D-NNN and write the record and its decision-log row in one
  transaction.

The record always starts as whatever ``--status`` says (``draft`` by default):
a source that calls itself "Accepted" was accepted somewhere else, by a
process WhyKit cannot see, so approval stays a human step.

Without ``--write`` nothing is written; the mapping is printed so a person can
check it first.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .adopt import ADR_FILENAME_RE
from .lint import DECISION_ID_RE, EVIDENCE_ID_RE, is_markdown_name, load_note, _within

PRODUCER = "whykit.promote/1"
STAGING_DIR = ".import-staging"
MAX_EVIDENCE_CANDIDATES = 20

# WhyKit section a source heading maps to. Keys are folded with `_fold`
# (lower case, diacritics and punctuation removed), so "Rozważane opcje",
# "rozwazane opcje:" and "## 2. Rozważane opcje" are one heading.
TARGETS: dict[str, tuple[str, ...]] = {
    "status": ("status", "stan", "decision status", "status decyzji"),
    "context": (
        "context", "context and problem statement", "problem statement", "problem", "background",
        "issue", "motivation", "kontekst", "kontekst i problem", "problem i kontekst", "tlo",
        "opis problemu", "kontekst i opis problemu",
    ),
    "drivers": (
        "decision drivers", "drivers", "forces", "requirements", "czynniki decyzyjne", "czynniki",
        "wymagania", "kryteria", "kryteria decyzji",
    ),
    "options": (
        "considered options", "options", "options considered", "alternatives", "alternatives considered",
        "rozwazane opcje", "rozwazane alternatywy", "alternatywy", "opcje", "warianty", "rozwazane warianty",
    ),
    "decision": (
        "decision", "decision outcome", "outcome", "decyzja", "wynik decyzji", "rozstrzygniecie",
        "podjeta decyzja",
    ),
    "rationale": ("rationale", "justification", "reasoning", "why", "uzasadnienie", "powody", "dlaczego"),
    "consequences": ("consequences", "implications", "konsekwencje", "skutki", "nastepstwa"),
    "positive": (
        "positive consequences", "positive", "pros", "benefits", "pozytywne konsekwencje", "korzysci",
        "zalety", "plusy",
    ),
    "negative": (
        "negative consequences", "negative", "cons", "drawbacks", "trade offs", "tradeoffs", "risks",
        "negatywne konsekwencje", "wady", "ryzyka", "koszty", "minusy",
    ),
    "links": (
        "links", "references", "more information", "related", "see also", "linki", "odnosniki",
        "zrodla", "powiazane", "wiecej informacji", "dodatkowe informacje",
    ),
}
POLISH_HEADINGS = frozenset({
    "stan", "status decyzji", "kontekst", "kontekst i problem", "problem i kontekst", "tlo", "opis problemu",
    "kontekst i opis problemu", "czynniki decyzyjne", "czynniki", "wymagania", "kryteria", "kryteria decyzji",
    "rozwazane opcje", "rozwazane alternatywy", "alternatywy", "opcje", "warianty", "rozwazane warianty",
    "decyzja", "wynik decyzji", "rozstrzygniecie", "podjeta decyzja", "uzasadnienie", "powody", "dlaczego",
    "konsekwencje", "skutki", "nastepstwa", "pozytywne konsekwencje", "korzysci", "zalety", "plusy",
    "negatywne konsekwencje", "wady", "ryzyka", "koszty", "minusy", "linki", "odnosniki", "zrodla",
    "powiazane", "wiecej informacji", "dodatkowe informacje",
})
MADR_HEADINGS = frozenset({
    "context and problem statement", "decision drivers", "considered options", "decision outcome",
    "positive consequences", "negative consequences", "pros and cons of the options", "more information",
})
SUBSECTION_TARGETS = frozenset({"positive", "negative", "consequences", "rationale"})
SECTION_LABELS = {
    "context": "Context", "drivers": "Context (decision drivers)", "decision": "Decision",
    "rationale": "Rationale", "options": "Alternatives considered", "consequences": "Consequences",
    "positive": "Consequences / Positive", "negative": "Consequences / Negative and trade-offs",
    "links": "Evidence (candidates)", "status": "provenance.source_status",
}

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
META_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?\**(status|date|data|stan|deciders|decyzja podjęta przez|decydenci)\**\s*:\s*\**\s*(.+?)\s*$",
    re.I,
)
# ADR numbering in a title: "7. Use queues", "0007 - Use queues", "ADR-0007: Use
# queues". A bare number followed by a word ("2024 roadmap") is part of the title.
TITLE_NUMBER_RE = re.compile(
    r"^(?:adr[\s_-]*\d{1,6}\s*[.:)\]—–-]?\s*|\d{1,6}\s*[.:)\]]\s*|\d{1,6}\s+[—–-]\s+)",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s<>()\[\]`'\"|]+[^\s<>()\[\]`'\".,;:!?|]")
WIKILINK_TEXT_RE = re.compile(r"(?<!`)\[\[[^\]\n]+\]\](?!`)")
RECORD_ID_TEXT_RE = re.compile(r"(?<![`\w-])(?:D|E)-[0-9]{3,}\b(?!`)")
GOOD_BAD_RE = re.compile(
    r"^(good|bad|neutral|dobrze|źle|zle|neutralnie)\b\s*[,:]?\s*(?:(?:because|since|ponieważ|bo|gdyż)\b\s*)?", re.I,
)
BECAUSE_RE = re.compile(r"^(?P<head>.+?),?\s+(?P<word>because|since|ponieważ|bo|gdyż)\s+(?P<why>.+)$", re.S | re.I)
CHOSEN_RE = re.compile(r"(?:chosen option|wybrana opcja|wybrany wariant)\s*:?\s*[\"“„']?(?P<name>[^\"”',\n]+)", re.I)

# "In the context of <use case>, facing <concern>, we decided for <option>
# [and neglected <others>], to achieve <quality>[, accepting <downside>]
# [, because <reason>]." Zimmermann's Y-statement, plus the Polish form.
Y_STATEMENT_RES = (
    re.compile(
        r"in the context of\s+(?P<context>.+?),\s*facing\s+(?P<facing>.+?),\s*we decided\s+(?:for|on|to)\s+"
        r"(?P<decision>.+?)(?:,?\s+and neglected\s+(?P<neglected>.+?))?,\s*(?:in order\s+)?to achieve\s+"
        r"(?P<achieve>.+?)(?:,?\s*accepting(?:\s+that)?\s+(?P<accepting>.+?))?"
        r"(?:,?\s*because\s+(?P<because>.+?))?\s*\.?\s*$",
        re.I | re.S,
    ),
    re.compile(
        r"w kontek[sś]cie\s+(?P<context>.+?),\s*(?:w obliczu|wobec|mierz[aą]c si[eę] z)\s+(?P<facing>.+?),\s*"
        r"zdecydowali?[sś]my\s+(?:si[eę]\s+na|o)\s+(?P<decision>.+?)"
        r"(?:,?\s+(?:i|oraz)\s+odrzucili?[sś]my\s+(?P<neglected>.+?))?,\s*aby\s+(?:osi[aą]gn[aą][cć]\s+)?"
        r"(?P<achieve>.+?)(?:,?\s*akceptuj[aą]c(?:,?\s+[zż]e)?\s+(?P<accepting>.+?))?"
        r"(?:,?\s*poniewa[zż]\s+(?P<because>.+?))?\s*\.?\s*$",
        re.I | re.S,
    ),
)

_FOLD = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe"})


def _fold(heading: str) -> str:
    text = unicodedata.normalize("NFKD", heading.translate(_FOLD))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[ivx]+\.)\s+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_ALIASES = {alias: target for target, aliases in TARGETS.items() for alias in aliases}


class PromotionError(ValueError):
    """The file cannot be promoted as asked."""


@dataclass
class Section:
    heading: str
    level: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


@dataclass
class Promotion:
    """What a promotion would write, computed without touching the vault."""

    source_label: str
    sha256: str
    original: str
    shape: str
    title: str
    sections: dict[str, str]
    alternatives: list[str]
    positive: list[str]
    negative: list[str]
    mapping: list[dict[str, str]]
    unmapped: list[str]
    evidence_candidates: list[str]
    warnings: list[str]
    source_status: str | None = None
    source_date: str | None = None
    source_id: str | None = None
    batch: str | None = None
    batch_path: str | None = None
    in_vault: bool = False

    def provenance(self, today: str) -> dict[str, object]:
        """The front-matter ``provenance`` block (see schemas/provenance.schema.json)."""
        block: dict[str, object] = {"producer": PRODUCER}
        if self.batch:
            block["producer_run"] = self.batch
        if self.in_vault:
            block["payload_ref"] = self.source_label
        block["snapshot_hash"] = f"sha256:{self.sha256}"
        block["recorded_at"] = today
        block["human_reviewed"] = False
        block["source_format"] = self.shape
        block["source_path"] = self.batch_path or self.source_label
        block["source_sha256"] = self.sha256
        if self.source_status:
            block["source_status"] = self.source_status
        if self.source_date:
            block["source_date"] = self.source_date
        if self.source_id:
            block["source_id"] = self.source_id
        return block

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.source_label,
            "sha256": self.sha256,
            "format": self.shape,
            "title": self.title,
            "status": self.source_status,
            "date": self.source_date,
            "original_id": self.source_id,
            "batch": self.batch,
        }


def source_label(vault: Path, path: Path) -> tuple[str, str | None, str | None]:
    """How the record names its source, without publishing a local home directory.

    Inside the vault: the vault-relative path, plus the import batch and the
    path inside the adopted folder when the file is staged. Outside it: the
    file name only, the same rule the ingestion record follows.
    """
    root = vault.resolve()
    resolved = path.resolve()
    if _within(root, resolved):
        relative = resolved.relative_to(root).as_posix()
        parts = relative.split("/")
        if parts[0] == STAGING_DIR and len(parts) >= 3 and not parts[1].startswith("."):
            return relative, parts[1], "/".join(parts[2:])
        return relative, None, None
    return path.name, None, None


def _split(text: str) -> tuple[list[str], list[Section]]:
    """Preamble lines and sections; headings inside code fences are text."""
    preamble: list[str] = []
    sections: list[Section] = []
    fence: str | None = None
    for line in text.splitlines():
        opener = FENCE_OPEN_RE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
        elif opener:
            fence = opener.group(1)
        heading = HEADING_RE.match(line) if fence is None and not opener else None
        if heading:
            sections.append(Section(heading.group(2).strip(), len(heading.group(1))))
        elif sections:
            sections[-1].lines.append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _first_paragraph_line(text: str) -> str | None:
    for line in text.splitlines():
        value = line.strip().strip("*_").strip()
        value = re.sub(r"^[-*+]\s+", "", value)
        if value:
            return value
    return None


def _list_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if match and not line.startswith(("    ", "\t")):
            items.append(match.group(1).strip())
    return items


def _plain(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    return " ".join(value.replace("|", "/").strip(" *_`\"“”„'").split())


def _neutralise(text: str, warnings: list[str]) -> str:
    """Keep copied prose from claiming links and IDs in *this* vault.

    An ADR from another repository may cite `E-012` or link `[[pricing]]`;
    copied verbatim, lint would check those against this vault and fail. They
    stay readable as code spans, and the untouched original keeps them as written.
    """
    ids = sorted(set(RECORD_ID_TEXT_RE.findall(text)))
    links = WIKILINK_TEXT_RE.findall(text)
    if ids:
        warnings.append(
            f"the source cites {', '.join(ids)}; kept as text, because IDs from another ledger do not "
            "resolve here — cite this vault's records instead"
        )
        text = RECORD_ID_TEXT_RE.sub(lambda m: f"`{m.group(0)}`", text)
    if links:
        warnings.append(f"{len(links)} wikilink(s) kept as text; relink them to notes in this vault")
        text = WIKILINK_TEXT_RE.sub(lambda m: f"`{m.group(0)}`", text)
    return text


def _demote(text: str) -> str:
    """Nest copied headings below the WhyKit section they now live in."""
    out = []
    fence: str | None = None
    for line in text.splitlines():
        opener = FENCE_OPEN_RE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
            out.append(line)
            continue
        if opener:
            fence = opener.group(1)
            out.append(line)
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) < 4:
            line = "#### " + heading.group(2)
        out.append(line)
    return "\n".join(out)


def _title_from(value: str) -> str:
    value = value.strip().strip("#").strip()
    value = re.sub(r"^D-[0-9]{3,}\s*[—–:-]\s*", "", value)
    value = TITLE_NUMBER_RE.sub("", value, count=1).strip()
    return value.strip(" -—–:")


def _humanize_stem(stem: str) -> str:
    stem = ADR_FILENAME_RE.sub("", stem)
    words = re.sub(r"[-_]+", " ", stem).strip()
    return words[:1].upper() + words[1:]


def _y_statement(body: str) -> dict[str, str] | None:
    flattened = " ".join(
        line.strip().lstrip(">").strip() for line in body.splitlines() if not HEADING_RE.match(line)
    ).strip()
    flattened = re.sub(r"[*_]{1,2}", "", flattened)
    for pattern in Y_STATEMENT_RES:
        start = re.search(r"(?i)\b(?:in the context of|w kontek[sś]cie)\b", flattened)
        if not start:
            continue
        sentence = flattened[start.start():]
        end = re.search(r"\.\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])", sentence)
        if end:
            sentence = sentence[: end.start() + 1]
        match = pattern.match(sentence)
        if match:
            return {key: (value or "").strip() for key, value in match.groupdict().items()}
    return None


def _split_names(value: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\band\b|\bor\b|\bi\b|\boraz\b|\blub\b)\s*", value)
    return [_plain(part) for part in parts if _plain(part)]


def _capital(value: str) -> str:
    return value[:1].upper() + value[1:]


def _sentence(value: str) -> str:
    value = value.strip().rstrip(".")
    return (value[:1].upper() + value[1:] + ".") if value else ""


def build_promotion(vault: Path, path: Path, *, title: str | None = None) -> Promotion:
    """Read *path* and work out the decision record it would become."""
    if not is_markdown_name(path.name):
        raise PromotionError(f"not a Markdown file: {path.name}")
    if path.is_symlink() or not path.is_file():
        raise PromotionError(f"not a regular Markdown file: {path}")
    data = path.read_bytes()
    try:
        original = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromotionError(f"{path.name} is not UTF-8 text") from exc
    if "\0" in original:
        raise PromotionError(f"{path.name} is binary, not Markdown")
    label, batch, batch_path = source_label(vault, path)
    note = load_note(path, text=original)
    front = note.front if note.has_front else {}
    body = original.removeprefix("﻿")
    if note.has_front:
        closing = re.search(r"(?m)^---[ \t]*\r?$", body[3:])
        body = body[3 + closing.end():] if closing else ""
    body = body.replace("\r\n", "\n")
    preamble, sections = _split(body)
    warnings: list[str] = []

    headings = list(sections)
    title_section: Section | None = None
    if headings and headings[0].level == 1 and _fold(headings[0].heading) not in _ALIASES:
        title_section = headings.pop(0)
        preamble.extend(title_section.lines)
    level = min((section.level for section in headings), default=2)

    # Fold each section, and each recognised subsection, into a WhyKit target.
    collected: dict[str, list[str]] = {}
    sources: dict[str, list[str]] = {}
    unmapped: list[str] = []
    parent: str | None = None
    for section in headings:
        target = _ALIASES.get(_fold(section.heading))
        if section.level == level:
            parent = target
            if target is None:
                unmapped.append(section.heading)
                continue
        elif not (section.level == level + 1 and target in SUBSECTION_TARGETS):
            # Any other subsection stays inside its parent, heading and all.
            if parent is not None:
                collected[parent].append("#" * section.level + " " + section.heading)
                collected[parent].extend(section.lines)
            continue
        assert target is not None
        collected.setdefault(target, []).extend(section.lines)
        sources.setdefault(target, []).append(section.heading)

    meta: dict[str, str] = {}
    for line in preamble:
        match = META_LINE_RE.match(line)
        if match:
            meta.setdefault(_fold(match.group(1)), match.group(2).strip())

    texts = {key: "\n".join(lines).strip("\n") for key, lines in collected.items()}
    texts = {key: value for key, value in texts.items() if value.strip()}
    folded = {_fold(section.heading) for section in headings}

    y = None if {"context", "decision"} & set(texts) else _y_statement(body)
    if y:
        shape = "y-statement"
    elif folded & MADR_HEADINGS:
        shape = "madr"
    elif folded & POLISH_HEADINGS and {"context", "decision"} & set(texts):
        shape = "polish-adr"
    elif {"context", "decision"} & set(texts):
        shape = "nygard"
    else:
        shape = "unstructured"

    out: dict[str, str] = {}
    alternatives: list[str] = []
    positive: list[str] = []
    negative: list[str] = []
    mapping: list[dict[str, str]] = []

    if y:
        out["context"] = _sentence(f"{y['context']}, facing {y['facing']}") if y["context"] else ""
        out["decision"] = _sentence(y["decision"])
        rationale = _sentence(f"to achieve {y['achieve']}") if y["achieve"] else ""
        if y.get("because"):
            rationale = (rationale + " " + _sentence(f"because {y['because']}")).strip()
        out["rationale"] = rationale
        alternatives = _split_names(y.get("neglected") or "")
        if y.get("accepting"):
            negative = [_sentence(y["accepting"])]
        for target in ("context", "decision", "rationale"):
            if out.get(target):
                mapping.append({"section": SECTION_LABELS[target], "from": "Y-statement"})
        if alternatives:
            mapping.append({"section": SECTION_LABELS["options"], "from": "Y-statement (neglected)"})
        if negative:
            mapping.append({"section": SECTION_LABELS["negative"], "from": "Y-statement (accepting)"})
    elif shape != "unstructured":
        for target in ("context", "drivers", "decision", "rationale", "consequences"):
            if target in texts:
                out[target] = texts[target]
        if "decision" in out and "rationale" not in out:
            first = out["decision"].strip().split("\n\n", 1)[0]
            because = BECAUSE_RE.match(" ".join(first.split()))
            if because:
                out["rationale"] = _sentence(f"{because.group('word')} {because.group('why')}")
                sources.setdefault("rationale", []).append(f"{sources['decision'][0]} (the “{because.group('word')}” clause)")
        options = [name for name in (_plain(item) for item in _list_items(texts.get("options", ""))) if name]
        chosen = CHOSEN_RE.search(out.get("decision", ""))
        chosen_name = _fold(chosen.group("name")) if chosen else ""
        if not chosen_name:
            # No "Chosen option:" line: the option the decision text names, if only one.
            decision_text = f" {_fold(out.get('decision', ''))} "
            named = [_fold(name) for name in options if f" {_fold(name)} " in decision_text]
            chosen_name = named[0] if len(named) == 1 else ""
        alternatives = [name for name in options if _fold(name) != chosen_name]
        if "options" in texts and not alternatives and texts["options"].strip():
            warnings.append("the options section has no list items; it is kept only in the original record")
        for key, bucket in (("positive", positive), ("negative", negative)):
            bucket.extend(_plain(item) for item in _list_items(texts.get(key, "")))
        general: list[str] = []
        for item in _list_items(texts.get("consequences", "")):
            tone = GOOD_BAD_RE.match(item)
            if tone and tone.group(1).casefold() in {"good", "dobrze"}:
                positive.append(_capital(_plain(GOOD_BAD_RE.sub("", item))))
            elif tone and tone.group(1).casefold() in {"bad", "źle", "zle"}:
                negative.append(_capital(_plain(GOOD_BAD_RE.sub("", item))))
            else:
                general.append(item)
        if "consequences" in out and (positive or negative) and not general:
            del out["consequences"]
        for target in ("context", "drivers", "decision", "rationale", "options", "consequences", "positive", "negative", "links"):
            if target in sources and (target in out or target in texts):
                mapping.append({"section": SECTION_LABELS[target], "from": ", ".join(sources[target])})
        if "status" in sources:
            mapping.append({"section": SECTION_LABELS["status"], "from": ", ".join(sources["status"])})

    status = (
        _first_paragraph_line(texts.get("status", ""))
        or meta.get("status") or meta.get("stan")
        or (str(front.get("status")) if front.get("status") else None)
    )
    date = meta.get("date") or meta.get("data") or (str(front.get("date")) if front.get("date") else None)
    declared = str(front.get("decision_id") or "").strip()
    number = ADR_FILENAME_RE.match(path.name)
    numbering = TITLE_NUMBER_RE.match(title_section.heading) if title_section else None
    heading_number = re.search(r"(\d+)", numbering.group(0)) if numbering else None
    source_id = (
        declared if DECISION_ID_RE.fullmatch(declared)
        else number.group(1) if number
        else heading_number.group(1) if heading_number
        else None
    )

    chosen_title = (title or "").strip()
    if not chosen_title:
        for candidate in (
            str(front.get("title") or ""),
            title_section.heading if title_section else "",
            _humanize_stem(path.stem),
        ):
            chosen_title = _title_from(candidate)
            if chosen_title:
                break
    if not chosen_title:
        raise PromotionError("could not derive a title; pass one: whykit new decision \"Title\" --from FILE")

    candidates: list[str] = []
    for url in URL_RE.findall(body):
        if url not in candidates:
            candidates.append(url)
    if len(candidates) > MAX_EVIDENCE_CANDIDATES:
        warnings.append(f"{len(candidates)} links found; the first {MAX_EVIDENCE_CANDIDATES} are listed as evidence candidates")
        candidates = candidates[:MAX_EVIDENCE_CANDIDATES]
    if EVIDENCE_ID_RE.search(" ".join(candidates)):
        candidates = [url for url in candidates if not EVIDENCE_ID_RE.search(url)]

    cleaned = {key: _neutralise(_demote(value), warnings) for key, value in out.items() if value.strip()}
    alternatives = [_neutralise(item, warnings) for item in alternatives]
    positive = [_neutralise(item, warnings) for item in positive if item]
    negative = [_neutralise(item, warnings) for item in negative if item]
    if shape == "unstructured":
        warnings.append(
            "no ADR shape recognised (MADR, Nygard, Y-statement or Polish headings); "
            "every WhyKit section keeps its prompt and the text is kept only under Original record"
        )
    for target in ("context", "decision", "rationale"):
        if shape != "unstructured" and not cleaned.get(target):
            warnings.append(f"no {target} section in the source; the {target.capitalize()} prompt is left for a person to answer")

    return Promotion(
        source_label=label,
        sha256=hashlib.sha256(data).hexdigest(),
        original=original,
        shape=shape,
        title=chosen_title,
        sections=cleaned,
        alternatives=alternatives,
        positive=positive,
        negative=negative,
        mapping=mapping,
        unmapped=unmapped,
        evidence_candidates=candidates,
        warnings=list(dict.fromkeys(warnings)),
        source_status=_plain(status) if status else None,
        source_date=_plain(date) if date else None,
        source_id=source_id,
        batch=batch,
        batch_path=batch_path,
        in_vault=_within(vault.resolve(), path.resolve()),
    )


def _fence_for(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=0)
    return "`" * max(4, longest + 1)


def render_sections(promotion: Promotion, prompts: dict[str, str]) -> dict[str, str]:
    """Prose for each WhyKit section, falling back to the scaffold prompt."""
    context = promotion.sections.get("context", "")
    drivers = promotion.sections.get("drivers", "")
    if drivers:
        context = (context + "\n\n" if context else "") + "Decision drivers:\n\n" + drivers
    consequences = promotion.sections.get("consequences", "")
    return {
        "Context": context or prompts["Context"],
        "Decision": promotion.sections.get("decision") or prompts["Decision"],
        "Rationale": promotion.sections.get("rationale") or prompts["Rationale"],
        "Consequences": consequences,
    }


def render_original(promotion: Promotion) -> str:
    fence = _fence_for(promotion.original)
    original = promotion.original.removeprefix("﻿").replace("\r\n", "\n").rstrip("\n")
    return (
        "## Original record\n\n"
        f"Promoted from `{promotion.source_label}` (SHA-256 `{promotion.sha256}`, format "
        f"{promotion.shape}). The text below is the source as it was; edit the sections above, not this copy.\n\n"
        f"{fence}markdown\n{original}\n{fence}\n"
    )


def render_provenance(block: dict[str, object]) -> str:
    import json

    lines = ["provenance:"]
    for key, value in block.items():
        rendered = ("true" if value else "false") if isinstance(value, bool) else json.dumps(str(value), ensure_ascii=False)
        lines.append(f"  {key}: {rendered}")
    return "\n".join(lines) + "\n"


def promoted_from(vault: Path, sha256: str) -> str | None:
    """The decision record already promoted from these exact bytes, if any."""
    folder = vault / "06-decisions"
    if folder.is_symlink() or not folder.is_dir():
        return None
    for path in sorted(folder.iterdir()):
        if not is_markdown_name(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            note = load_note(path)
        except (OSError, UnicodeDecodeError):
            continue
        block = note.front.get("provenance") if note.has_front else None
        if isinstance(block, dict) and str(block.get("source_sha256") or "") == sha256:
            declared = str(note.front.get("decision_id") or "").strip()
            return f"{declared or '?'} ({path.relative_to(vault).as_posix()})"
    return None
