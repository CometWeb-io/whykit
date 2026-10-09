"""Create new WhyKit records without hand-editing indexes.

The file format stays Markdown + Git. These helpers only remove the repetitive,
error-prone work of allocating IDs, naming files and keeping the two registries
in sync.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import datetime as dt
import json
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from .contract import describe_os_error, emit_error, vault_not_found
from .messages import parse_iso_date
from .io import apply_transaction, atomic_write_text, ensure_writable, safe_vault_target, vault_mutation_lock
from .config import ConfigError, load_config
from .lint import (
    DECISION_ID_RE, EVIDENCE_ID_RE, _split_table_row, decision_log_rows, evidence_register,
    find_vault_root, is_markdown_name, is_vault_root, load_note,
)
from .vault_index import VaultIndex
from .console import emit_machine, one_line
from .placeholders import (
    ALTERNATIVES_EMPTY_ROW, ALTERNATIVES_HEADER, SCAFFOLD_EVIDENCE_TODO, SCAFFOLD_SECTION_PROMPTS,
)

DECISION_STATUS_TO_LOG = {
    "draft": "proposed",
    "in_review": "proposed",
    "approved": "accepted",
    "superseded": "superseded",
    "archived": "archived",
}
VALID_SENSITIVITY = ("public", "internal", "confidential", "restricted")
VALID_NOTE_TYPES = ("strategy", "research", "framework", "specification", "guide", "reference")
ID_NUMBER_RE = re.compile(r"^[CDE]-([0-9]+)$")

if TYPE_CHECKING:
    from .promote import Promotion


def _yaml_string(value: str) -> str:
    """JSON strings are valid YAML scalars and avoid quote/comment surprises."""
    return json.dumps(value, ensure_ascii=False)


# Latin letters that NFKD does not split into a base letter plus a mark, so
# dropping non-ASCII would delete them outright ("Łódź" would become "odz").
_LATIN_FOLD = str.maketrans({
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss",
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH",
    "ð": "d", "Ð": "D", "ı": "i", "ħ": "h", "Ħ": "H",
})


def _slugify(value: str) -> str:
    # A C-locale argv title carries surrogates for its UTF-8 bytes; recover the
    # text first so the file name does not depend on the locale.
    value = value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    normalized = unicodedata.normalize("NFKD", value.translate(_LATIN_FOLD))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if slug:
        return slug
    # No Latin letters or digits at all (Chinese, Cyrillic, Greek…). File names
    # stay ASCII, so derive a short stable suffix from the title instead of
    # giving every such title the same name.
    canonical = unicodedata.normalize("NFC", value).strip()
    if not canonical:
        return "record"
    return "record-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _table_cell(value: str) -> str:
    return " ".join(value.replace("|", r"\|").split())


def _next_id(values: list[str], prefix: str) -> str:
    numbers = []
    for value in values:
        match = ID_NUMBER_RE.match(value)
        if match and value.startswith(prefix + "-"):
            numbers.append(int(match.group(1)))
    return f"{prefix}-{(max(numbers, default=0) + 1):03d}"


def _frontmatter_replace(text: str, key: str, value: str) -> str:
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text.removeprefix("\ufeff")
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    head = text[:end]
    pattern = re.compile(rf"(?m)^{re.escape(key)}:[ \t]*.*$")
    replacement = f"{key}: {value}"
    if pattern.search(head):
        head = pattern.sub(replacement, head, count=1)
    else:
        head += "\n" + replacement
    return bom + head + text[end:]


def _append_table_row_text(text: str, header_prefix: str, row: str, today: dt.date, *, record_id: str, path_label: str = "table") -> str:
    lines = text.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.strip().startswith(header_prefix)), None)
    if header_idx is None or header_idx + 1 >= len(lines):
        raise ValueError(f"expected Markdown table in {path_label}")
    insert_at = header_idx + 2
    placeholder_idx: int | None = None
    while insert_at < len(lines) and lines[insert_at].lstrip().startswith("|"):
        cells = [cell.strip() for cell in lines[insert_at].strip().strip("|").split("|")]
        if cells and cells[0] == record_id:
            populated = [cell for cell in cells[1:] if cell and cell not in {"proposed / accepted / superseded", "interview / report / analytics / vendor doc / internal"}]
            if not populated:
                placeholder_idx = insert_at
            else:
                raise ValueError(f"{record_id} already has a populated row in {path_label}")
        insert_at += 1
    if placeholder_idx is not None:
        lines[placeholder_idx] = row
    else:
        lines.insert(insert_at, row)
    updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return _frontmatter_replace(updated, "last_updated", today.isoformat())


def _update_decision_log_status_text(text: str, decision_id: str, status: str, today: dt.date) -> str:
    lines = text.splitlines()
    hits = 0
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if len(cells) < 6 or cells[0] != decision_id:
            continue
        hits += 1
        cells[4] = status
        rendered = []
        for i, cell in enumerate(cells):
            rendered.append(cell if i == 5 and cell.startswith("[[") else _table_cell(cell))
        lines[index] = "| " + " | ".join(rendered) + " |"
    if hits != 1:
        raise ValueError(f"expected one decision-log row for {decision_id}, found {hits}")
    updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return _frontmatter_replace(updated, "last_updated", today.isoformat())


def _existing_decision_ids(vault: Path) -> list[str]:
    values: list[str] = []
    decisions = vault / "06-decisions"
    if decisions.is_dir():
        for path in decisions.iterdir():
            if not is_markdown_name(path.name):
                continue
            match = re.match(r"d-([0-9]{3,})-", path.name)
            if match:
                values.append(f"D-{match.group(1)}")
    values.extend(str(row["id"]) for row in decision_log_rows(vault))
    return values


def _existing_evidence_ids(vault: Path) -> list[str]:
    active, retired, _ = evidence_register(vault)
    return [*active.keys(), *retired.keys()]


def _decision_record_path(vault: Path, decision_id: str) -> Path:
    number = decision_id.split("-", 1)[1]
    matches = sorted(
        path for path in (vault / "06-decisions").glob(f"d-{number}-*")
        if is_markdown_name(path.name)
    )
    exact = [path for path in matches if str(load_note(path).front.get("decision_id") or "").strip() == decision_id]
    if len(exact) != 1:
        raise ValueError(f"{decision_id} does not resolve to exactly one decision record")
    return exact[0]


def _update_decision_log_status(path: Path, decision_id: str, status: str, today: dt.date) -> None:
    text = path.read_text(encoding="utf-8")
    atomic_write_text(path, _update_decision_log_status_text(text, decision_id, status, today))

def create_claim(vault: Path, title: str, *, statement: str, scope: str, valid_from: str,
                 valid_to: str | None = None, owner: str | None = None, sensitivity: str | None = None,
                 supersedes: str | None = None, today: dt.date | None = None) -> tuple[str, Path]:
    from .claims import CLAIM_COLUMNS, CLAIM_ID_RE, capture_claims, claims_enabled
    today = today or dt.date.today()
    if any(not value.strip() for value in (title, statement, scope)):
        raise ValueError("claim title, statement and scope cannot be empty")
    start = parse_iso_date("--valid-from", valid_from)
    if valid_to and parse_iso_date("--valid-to", valid_to) < start:
        raise ValueError("valid_to must not precede valid_from")
    with vault_mutation_lock(vault):
        config, _ = load_config(vault)
        if not claims_enabled(config):
            raise ValueError("claim creation requires claims opt-in")
        owner = owner or str(config["defaults"]["owner"])
        sensitivity = sensitivity or str(config["defaults"]["sensitivity"])
        if sensitivity not in VALID_SENSITIVITY or not owner.strip():
            raise ValueError("claim owner and sensitivity must be valid")
        index = VaultIndex.load(vault)
        view = capture_claims(index, config)
        ids = list(view["records"])
        ids.extend(match.group().upper() for note in index.notes for match in re.finditer(r"C-[0-9]{3,}", note.text, re.I))
        ids.extend("C-" + match.group(1) for path in (vault / "00-context/claims").glob("*")
                   if (match := re.match(r"c-([0-9]{3,})-", path.name, re.I)))
        if supersedes and (not CLAIM_ID_RE.fullmatch(supersedes) or supersedes not in view["records"]
                           or view["records"][supersedes]["front"].get("status") != "approved"):
            raise ValueError("--supersedes must identify an approved claim")
        cid = _next_id(ids, "C")
        path = safe_vault_target(vault, f"00-context/claims/c-{cid[2:]}-{_slugify(title)}.md")
        if path.exists():
            raise FileExistsError(path)
        fields = {"title": title.strip(), "type": "claim", "claim_id": cid, "status": "draft", "owner": owner,
                  "created": today.isoformat(), "last_updated": today.isoformat(), "sensitivity": sensitivity,
                  "statement": statement.strip(), "scope": scope.strip(), "valid_from": valid_from}
        if valid_to:
            fields["valid_to"] = valid_to
        if supersedes:
            fields["supersedes"] = supersedes
        text = "---\n" + "".join(f"{key}: {_yaml_string(value)}\n" for key, value in fields.items()) + "---\n\n"
        text += f"# {title.strip()}\n\n## Evidence\n\n| " + " | ".join(CLAIM_COLUMNS) + " |\n| " + " | ".join("---" for _ in CLAIM_COLUMNS) + " |\n"
        apply_transaction(vault, {path: text})
        return cid, path


def create_decision(
    vault: Path,
    title: str,
    *,
    owner: str = "TODO",
    status: str = "draft",
    sensitivity: str = "internal",
    source_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    review_by: str | None = None,
    supersedes: str | None = None,
    today: dt.date | None = None,
    promotion: Promotion | None = None,
) -> tuple[str, Path]:
    today = today or dt.date.today()
    if promotion is not None and status not in {"draft", "in_review"}:
        raise ValueError("imported decisions must start as draft or in_review; the original status is retained in provenance")
    if status not in {"draft", "in_review"}:
        raise ValueError("new decisions must start as draft or in_review; complete the record, then use `whykit review approve`")
    title = title.strip() or (promotion.title if promotion else "")
    if not title:
        raise ValueError("decision title cannot be empty")
    with vault_mutation_lock(vault):
        if promotion is not None:
            from .promote import promoted_from

            earlier = promoted_from(vault, promotion.sha256)
            if earlier:
                raise FileExistsError(
                    f"{promotion.source_label} was already promoted as {earlier}\n"
                    "hint: edit that record, or supersede it with a new decision"
                )
        existing_decisions = _existing_decision_ids(vault)
        decision_id = _next_id(existing_decisions, "D")
        number = decision_id.split("-", 1)[1]
        path = safe_vault_target(
            vault,
            Path("06-decisions") / f"d-{number}-{_slugify(title)}.md",
        )
        if path.exists():
            raise FileExistsError(path)
        if review_by:
            parse_iso_date("--review-by", review_by)
        if supersedes:
            if not DECISION_ID_RE.fullmatch(supersedes):
                raise ValueError("--supersedes must be a D-NNN identifier")
            if supersedes not in set(existing_decisions):
                raise ValueError(f"--supersedes points to unknown decision {supersedes}")
            predecessor_path = _decision_record_path(vault, supersedes)
            predecessor_note = load_note(predecessor_path)
            if predecessor_note.front.get("status") != "approved":
                raise ValueError(f"{supersedes} must be approved before it can be superseded")
        source_ids = source_ids or []
        invalid = [value for value in source_ids if not EVIDENCE_ID_RE.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid evidence ID(s): {', '.join(invalid)}")
        known_evidence = set(_existing_evidence_ids(vault))
        missing_evidence = [value for value in source_ids if value not in known_evidence]
        if missing_evidence:
            raise ValueError(f"unknown evidence ID(s): {', '.join(missing_evidence)}")

        source_yaml = "[" + ", ".join(_yaml_string(value) for value in source_ids) + "]" if source_ids else "[]"
        optional = ""
        if claim_ids:
            from .claims import CLAIM_ID_RE, capture_claims, claims_enabled
            config, _ = load_config(vault)
            if not claims_enabled(config):
                raise ValueError("claim references require claims opt-in")
            known = capture_claims(VaultIndex.load(vault), config)["records"]
            if len(set(claim_ids)) != len(claim_ids) or any(not CLAIM_ID_RE.fullmatch(cid) or cid not in known for cid in claim_ids):
                raise ValueError("claim references must be unique existing C-NNN identifiers")
            optional += "claim_ids: [" + ", ".join(_yaml_string(cid) for cid in claim_ids) + "]\n"
        if review_by:
            optional += f"review_by: {review_by}\n"
        if supersedes:
            optional += f"supersedes: {supersedes}\n"
        prose = dict(SCAFFOLD_SECTION_PROMPTS)
        evidence_lines = [f"- {sid}" for sid in source_ids] or [SCAFFOLD_EVIDENCE_TODO]
        alternative_rows = [ALTERNATIVES_EMPTY_ROW]
        consequences = "### Positive\n\n-\n\n### Negative and trade-offs\n\n-"
        appendix = ""
        if promotion is not None:
            from .promote import render_original, render_provenance, render_sections

            optional += render_provenance(promotion.provenance(today.isoformat()))
            sections = render_sections(promotion, SCAFFOLD_SECTION_PROMPTS)
            prose = {name: sections[name] for name in SCAFFOLD_SECTION_PROMPTS}
            evidence_lines += [
                f"- TODO — candidate source from the original record: <{url}>"
                for url in promotion.evidence_candidates
            ]
            if promotion.alternatives:
                alternative_rows = [f"| {_table_cell(name)} |  |  | Not chosen; see the original record |" for name in promotion.alternatives]
            general = sections["Consequences"]
            positive = "\n".join(f"- {item}" for item in promotion.positive) or "-"
            negative = "\n".join(f"- {item}" for item in promotion.negative) or "-"
            consequences = (
                (general + "\n\n" if general else "")
                + f"### Positive\n\n{positive}\n\n### Negative and trade-offs\n\n{negative}"
            )
            appendix = "\n" + render_original(promotion)
        body = f'''---
title: {_yaml_string(title)}
aliases: []
type: decision
decision_id: {decision_id}
status: {status}
owner: {_yaml_string(owner)}
created: {today.isoformat()}
last_updated: {today.isoformat()}
source_of_truth: false
sensitivity: {sensitivity}
source_ids: {source_yaml}
tags: []
{optional}---

# Decision record: {title}

## Decision ID

{decision_id}

## Status

{DECISION_STATUS_TO_LOG[status].capitalize()}

## Context

{prose['Context']}

## Decision

{prose['Decision']}

## Rationale

{prose['Rationale']}

## Evidence

{chr(10).join(evidence_lines)}

## Alternatives considered

{ALTERNATIVES_HEADER}
|---|---|---|---|
{chr(10).join(alternative_rows)}

## Consequences

{consequences}

## Ownership and review

- Owner: {owner}
- Decided on: {today.isoformat() if status == 'approved' else 'TBD'}
- Review on: {review_by or 'TBD'}
- Supersedes: {supersedes or '—'}
- Superseded by: —
{appendix}'''
        log = safe_vault_target(vault, "06-decisions/decision-log.md", create_parents=False)
        if not log.exists():
            raise FileNotFoundError(errno.ENOENT, "the decision log is missing", str(log))
        log_before = log.read_text(encoding="utf-8")
        row = (
            f"| {decision_id} | {_table_cell(title)} | {today.isoformat()} | {_table_cell(owner)} | "
            f"{DECISION_STATUS_TO_LOG[status]} | [[06-decisions/{path.stem}]] |"
        )
        updates: dict[Path, str] = {
            path: body,
            log: _append_table_row_text(
                log_before, "| ID | Decision |", row, today, record_id=decision_id, path_label=str(log)
            ),
        }
        apply_transaction(vault, updates)
        return decision_id, path


def _with_evidence_sensitivity(text: str, mode: str, inherited: str) -> str:
    """Extend one legacy table without declassifying any existing row."""
    from .tables import evidence_table_bounds

    lines = text.splitlines()
    start, end, labeled = evidence_table_bounds(lines, mode)
    if labeled:
        return text
    for index in range(start, end):
        value = "Sensitivity" if index == start else "---" if index == start + 1 else inherited
        if index > start + 1:
            cells = _split_table_row(lines[index])
            if len(cells) != (7 if mode == "active" else 5):
                raise ValueError("cannot extend an evidence table with malformed rows")
            if not cells[1]:
                value = ""
        lines[index] = lines[index].rstrip() + f" {_table_cell(value)} |"
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _register_inherited_label(path: Path, text: str) -> str:
    from .sensitivity import SENSITIVITY_LEVEL, note_sensitivity_level

    level = note_sensitivity_level(load_note(path, text=text))
    return next((name for name, value in SENSITIVITY_LEVEL.items() if value == level), "unclassified")


def create_evidence(
    vault: Path,
    *,
    source: str,
    location: str,
    kind: str,
    claims: str,
    date: str | None = None,
    accessed: str | None = None,
    sensitivity: str | None = None,
    today: dt.date | None = None,
) -> str:
    today = today or dt.date.today()
    if not source.strip() or not location.strip() or not kind.strip() or not claims.strip():
        raise ValueError("evidence source, location, type and claims must be non-empty")
    source_date = date or today.isoformat()
    accessed_date = accessed or today.isoformat()
    parse_iso_date("--date", source_date)
    parse_iso_date("--accessed", accessed_date)
    if sensitivity is not None and sensitivity not in VALID_SENSITIVITY:
        raise ValueError("unsupported evidence sensitivity")
    with vault_mutation_lock(vault):
        evidence_id = _next_id(_existing_evidence_ids(vault), "E")
        register = safe_vault_target(vault, "00-context/evidence-register.md", create_parents=False)
        if not register.exists():
            raise FileNotFoundError(errno.ENOENT, "the evidence register is missing", str(register))
        from .tables import evidence_table_bounds

        original = register.read_text(encoding="utf-8")
        inherited = _register_inherited_label(register, original)
        updated = _with_evidence_sensitivity(original, "active", inherited) if sensitivity is not None else original
        lines = updated.splitlines()
        header, end, labeled = evidence_table_bounds(lines, "active")
        placeholder = next((index for index in range(header + 2, end)
                            if _split_table_row(lines[index])[0] == evidence_id), None)
        values: tuple[str, ...] = (evidence_id, source, kind, source_date, accessed_date, location, claims)
        if labeled:
            prior = _split_table_row(lines[placeholder]) if placeholder is not None else []
            values += (sensitivity or (prior[7] if len(prior) == 8 and prior[7] else inherited),)
        row = "| " + " | ".join(
            _table_cell(value)
            for value in values
        ) + " |"
        if placeholder is not None:
            lines[placeholder] = row
        else:
            lines.insert(end, row)
        updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        atomic_write_text(register, _frontmatter_replace(updated, "last_updated", today.isoformat()))
        return evidence_id


def _resolve_link_from(vault: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("--link-from must be a vault-relative Markdown path")
    path = (vault / candidate).resolve()
    try:
        path.relative_to(vault.resolve())
    except ValueError as exc:
        raise ValueError("--link-from must stay inside the vault") from exc
    if not is_markdown_name(path.name) or not path.is_file():
        raise ValueError(f"--link-from is not an existing Markdown file: {value}")
    return path


def _append_note_link(vault: Path, map_path: Path, note_path: Path, title: str, today: dt.date) -> str:
    text = map_path.read_text(encoding="utf-8")
    target = note_path.relative_to(vault).with_suffix("").as_posix()
    if re.search(rf"\[\[{re.escape(target)}(?:\||\]\])", text):
        return text
    bullet = f"- [[{target}|{title}]]"
    lines = text.splitlines()
    heading = "## Linked notes"
    try:
        heading_idx = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([heading, "", bullet])
    else:
        insert_at = len(lines)
        for i in range(heading_idx + 1, len(lines)):
            if lines[i].startswith("## "):
                insert_at = i
                break
        while insert_at > heading_idx + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, bullet)
    updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    updated = _frontmatter_replace(updated, "last_updated", today.isoformat())
    return updated


def _note_exists_message(vault: Path, path: Path) -> str:
    relative = path.relative_to(vault.resolve()).as_posix() if path.is_absolute() else path.as_posix()
    return (
        f"{relative} already exists\n"
        "hint: open and extend that note, or give the new one a more specific title"
    )


def create_note(
    vault: Path,
    title: str,
    *,
    workstream: str,
    owner: str = "TODO",
    doc_type: str = "guide",
    sensitivity: str = "internal",
    link_from: str | None = None,
    today: dt.date | None = None,
) -> Path:
    today = today or dt.date.today()
    title = title.strip()
    if not title:
        raise ValueError("note title cannot be empty")
    if doc_type not in VALID_NOTE_TYPES:
        raise ValueError(f"unsupported type: {doc_type}")
    workstream_path = Path(workstream)
    if workstream_path.is_absolute() or ".." in workstream_path.parts:
        raise ValueError("workstream must stay inside the vault")
    root = vault.resolve(strict=True)
    current = root
    for part in workstream_path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlinked workstream refused: {workstream}")
        if not current.is_dir():
            raise ValueError(f"workstream does not exist: {workstream}")
        try:
            current.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise ValueError("workstream must stay inside the vault") from exc
    path = safe_vault_target(vault, workstream_path / f"{_slugify(title)}.md", create_parents=False)
    if path.exists():
        raise FileExistsError(_note_exists_message(vault, path))
    with vault_mutation_lock(vault):
        if path.exists():
            raise FileExistsError(_note_exists_message(vault, path))
        map_path = _resolve_link_from(vault, link_from) if link_from else None
        if map_path:
            # Fail before the note exists rather than half-way through.
            ensure_writable(map_path)
        body = f'''---
title: {_yaml_string(title)}
aliases: []
type: {doc_type}
status: draft
owner: {_yaml_string(owner)}
created: {today.isoformat()}
last_updated: {today.isoformat()}
source_of_truth: false
sensitivity: {sensitivity}
source_ids: []
tags: []
workstream: {_yaml_string(workstream)}
---

# {title}

## Purpose

What question or operating need does this document answer?

## Current state

Separate verified facts from hypotheses, recommendations and open questions.

## Evidence

- TODO — add E-NNN references for material factual claims.

## Open questions

-
'''
        updates = {path: body}
        if map_path:
            updates[map_path] = _append_note_link(vault, map_path, path, title, today)
        apply_transaction(vault, updates)
        return path


def _promote(vault: Path, args: argparse.Namespace, *, owner: str, sensitivity: str, review_by: str | None) -> int:
    """`new decision --from FILE`: print the mapping, or with --write create the record."""
    from .promote import PromotionError, build_promotion, promoted_from

    if not args.from_path:
        return emit_error(
            "usage", "--write only applies with --from FILE",
            hint='hint: whykit new decision "Title" creates a record directly; --write confirms a --from promotion',
            json_mode=args.json,
        )
    source = Path(args.from_path).expanduser()
    if not source.exists():
        return emit_error("invalid_target", f"no such file: {args.from_path}", json_mode=args.json)
    try:
        promotion = build_promotion(vault, source, title=args.title)
    except PromotionError as exc:
        return emit_error("invalid_target", str(exc), json_mode=args.json)
    except OSError as exc:
        return emit_error("io_error", describe_os_error(exc), json_mode=args.json)
    if args.status not in {"draft", "in_review"}:
        return emit_error(
            "operation_rejected", "imported decisions must start as draft or in_review; review the imported record before approval",
            json_mode=args.json,
        )
    earlier = promoted_from(vault, promotion.sha256)
    if earlier and not args.write:
        return emit_error(
            "target_exists", f"{one_line(promotion.source_label)} was already promoted as {earlier}",
            hint="hint: edit that record, or supersede it with a new decision",
            json_mode=args.json,
        )
    if args.write:
        try:
            decision_id, path = create_decision(
                vault, args.title or "", owner=owner, status=args.status, sensitivity=sensitivity,
                source_ids=args.source_ids, claim_ids=args.claim_ids, review_by=review_by, supersedes=args.supersedes,
                promotion=promotion,
            )
        except FileExistsError as exc:
            return emit_error("target_exists", str(exc), json_mode=args.json)
        except FileNotFoundError as exc:
            return emit_error(
                "vault_invalid", describe_os_error(exc),
                hint="hint: restore the file from Git, or copy it from a fresh `whykit init` vault",
                json_mode=args.json,
            )
        except ValueError as exc:
            return emit_error("operation_rejected", str(exc), json_mode=args.json)
        relative = path.relative_to(vault).as_posix()
    else:
        decision_id = _next_id(_existing_decision_ids(vault), "D")
        relative = f"06-decisions/d-{decision_id.split('-', 1)[1]}-{_slugify(promotion.title)}.md"
    title = (args.title or "").strip() or promotion.title
    if args.json:
        emit_machine(json.dumps({
            "contract_version": 1,
            "kind": "decision",
            "id": decision_id,
            "path": relative,
            "write": bool(args.write),
            "title": title,
            "status": args.status,
            "source": promotion.as_json(),
            "mapping": promotion.mapping,
            "unmapped": promotion.unmapped,
            "evidence_candidates": promotion.evidence_candidates,
            "warnings": promotion.warnings,
        }, ensure_ascii=False, indent=2))
        return 0
    verb = "created" if args.write else "would create"
    print(f"Promote {one_line(promotion.source_label)} ({promotion.shape})")
    print(f"  sha256        {promotion.sha256}")
    print(f"  {verb:<13} {decision_id}: {one_line(relative)} (status {args.status})")
    print(f"  title         {one_line(title)}")
    if promotion.mapping:
        print("  mapping")
        width = max(len(item["section"]) for item in promotion.mapping)
        for item in promotion.mapping:
            print(f"    {item['section']:<{width}}  <- {one_line(item['from'])}")
    for name in promotion.unmapped:
        print(f"  unmapped      {one_line(name)} (kept only under Original record)")
    if promotion.source_status:
        print(f"  source status {one_line(promotion.source_status)} (kept in provenance; the record is {args.status})")
    if promotion.evidence_candidates:
        print(f"  evidence      {len(promotion.evidence_candidates)} link(s) listed under Evidence as TODO candidates")
    for warning in promotion.warnings:
        print(f"  note: {one_line(warning)}")
    if not args.write:
        print("\nDry run. Nothing was written. Re-run with --write to create the record and its decision-log row.")
    else:
        print("The original text is kept under \"Original record\". Review the mapped sections, then approve it.")
    return 0


def _vault(value: str | None) -> Path | None:
    if value:
        path = Path(value).expanduser().resolve()
        return path if is_vault_root(path) else None
    return find_vault_root()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit new", description="Create a record and keep WhyKit indexes in sync.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    sub = parser.add_subparsers(dest="kind", required=True)

    decision = sub.add_parser("decision", help="create a decision record and decision-log row")
    decision.add_argument("title", nargs="?")
    decision.add_argument("--from", dest="from_path", metavar="FILE")
    decision.add_argument("--write", action="store_true")
    decision.add_argument("--owner")
    decision.add_argument("--status", choices=("draft", "in_review"), default="draft")
    decision.add_argument("--sensitivity", choices=VALID_SENSITIVITY)
    decision.add_argument("--source", action="append", default=[], dest="source_ids", help="supporting E-NNN (repeatable)")
    decision.add_argument("--claim", action="append", default=[], dest="claim_ids")
    decision.add_argument("--review-by")
    decision.add_argument("--supersedes")
    decision.add_argument("--json", action="store_true")

    claim = sub.add_parser("claim", help="create an unreviewed claim draft")
    claim.add_argument("title")
    for field in ("statement", "scope", "valid-from"):
        claim.add_argument("--" + field, required=True)
    for field in ("valid-to", "owner", "supersedes", "today"):
        claim.add_argument("--" + field)
    claim.add_argument("--sensitivity", choices=VALID_SENSITIVITY)
    claim.add_argument("--json", action="store_true")

    evidence = sub.add_parser("evidence", help="append a source to the evidence register")
    evidence.add_argument("--source", required=True, dest="source_name")
    evidence.add_argument("--location", required=True)
    evidence.add_argument("--type", required=True, dest="evidence_type")
    evidence.add_argument("--claims", required=True)
    evidence.add_argument("--date")
    evidence.add_argument("--accessed")
    evidence.add_argument("--sensitivity", choices=VALID_SENSITIVITY)
    evidence.add_argument("--json", action="store_true")

    note = sub.add_parser("note", help="create a draft note in an existing workstream")
    note.add_argument("title")
    note.add_argument("--workstream", required=True)
    note.add_argument("--owner")
    note.add_argument("--type", choices=VALID_NOTE_TYPES, default="guide", dest="doc_type")
    note.add_argument("--sensitivity", choices=VALID_SENSITIVITY)
    note.add_argument("--link-from", help="vault-relative Markdown map to link the new note from")
    note.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    vault = _vault(args.root)
    if vault is None:
        return vault_not_found(args.root, json_mode=args.json)
    try:
        config, _ = load_config(vault)
    except ConfigError as exc:
        return emit_error("invalid_config", str(exc), json_mode=args.json)
    default_owner = str(config["defaults"]["owner"])
    default_sensitivity = str(config["defaults"]["sensitivity"])
    owner = getattr(args, "owner", None) or default_owner
    sensitivity = getattr(args, "sensitivity", None) or default_sensitivity
    review_by = getattr(args, "review_by", None)
    if args.kind == "decision" and (args.from_path or args.write):
        return _promote(vault, args, owner=owner, sensitivity=sensitivity, review_by=review_by)
    if args.kind == "decision" and not (args.title or "").strip():
        return emit_error(
            "usage", "a decision needs a title",
            hint='hint: whykit new decision "Adopt usage-based pricing", or --from FILE to promote an existing ADR',
            json_mode=args.json,
        )
    try:
        if args.kind == "decision":
            decision_id, path = create_decision(
                vault, args.title, owner=owner, status=args.status, sensitivity=sensitivity,
                source_ids=args.source_ids, claim_ids=args.claim_ids, review_by=review_by, supersedes=args.supersedes,
            )
            payload = {"contract_version": 1, "kind": "decision", "id": decision_id, "path": path.relative_to(vault).as_posix()}
            if args.json:
                emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created {decision_id}: {path.relative_to(vault).as_posix()}")
        elif args.kind == "claim":
            cid, path = create_claim(vault, args.title, statement=args.statement, scope=args.scope,
                                    valid_from=args.valid_from, valid_to=args.valid_to, owner=owner,
                                    sensitivity=sensitivity, supersedes=args.supersedes,
                                    today=parse_iso_date("--today", args.today) if args.today else None)
            payload = {"contract_version": 1, "kind": "claim", "id": cid, "path": path.relative_to(vault).as_posix()}
            if args.json:
                emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created {cid}: {payload['path']}")
        elif args.kind == "evidence":
            evidence_id = create_evidence(
                vault, source=args.source_name, location=args.location, kind=args.evidence_type,
                claims=args.claims, date=args.date, accessed=args.accessed, sensitivity=args.sensitivity,
            )
            payload = {"contract_version": 1, "kind": "evidence", "id": evidence_id, "path": "00-context/evidence-register.md"}
            if args.json:
                emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created {evidence_id}: 00-context/evidence-register.md")
        else:
            path = create_note(
                vault, args.title, workstream=args.workstream, owner=owner,
                doc_type=args.doc_type, sensitivity=sensitivity, link_from=args.link_from,
            )
            payload = {
                "contract_version": 1, "kind": "note", "path": path.relative_to(vault).as_posix(),
                "linked_from": args.link_from, "strict_linked": bool(args.link_from),
            }
            if args.json:
                emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created note: {path.relative_to(vault).as_posix()}")
                if args.link_from:
                    print(f"linked from: {args.link_from}")
                else:
                    print("note is not linked from a map; strict profiles may report note.orphan")
    except FileExistsError as exc:
        return emit_error("target_exists", str(exc), json_mode=args.json)
    except FileNotFoundError as exc:
        return emit_error(
            "vault_invalid",
            describe_os_error(exc),
            hint="hint: restore the file from Git, or copy it from a fresh `whykit init` vault",
            json_mode=args.json,
        )
    except ValueError as exc:
        return emit_error("operation_rejected", str(exc), json_mode=args.json)
    return 0
