"""Create new WhyKit records without hand-editing indexes.

The file format stays Markdown + Git. These helpers only remove the repetitive,
error-prone work of allocating IDs, naming files and keeping the two registries
in sync.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

from .messages import parse_iso_date, print_no_vault
from .io import apply_transaction, atomic_write_text, safe_vault_target, vault_mutation_lock
from .config import ConfigError, load_config
from .lint import (
    DECISION_ID_RE, EVIDENCE_ID_RE, _split_table_row, decision_log_rows, evidence_register,
    find_vault_root, is_vault_root, load_note,
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
ID_NUMBER_RE = re.compile(r"^[DE]-(\d+)$")


def _yaml_string(value: str) -> str:
    """JSON strings are valid YAML scalars and avoid quote/comment surprises."""
    return json.dumps(value, ensure_ascii=False)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "record"


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
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    head = text[:end]
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
    replacement = f"{key}: {value}"
    if pattern.search(head):
        head = pattern.sub(replacement, head, count=1)
    else:
        head += "\n" + replacement
    return head + text[end:]


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


def _append_table_row(path: Path, header_prefix: str, row: str, today: dt.date, *, record_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    atomic_write_text(
        path,
        _append_table_row_text(text, header_prefix, row, today, record_id=record_id, path_label=str(path)),
    )


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
        for path in decisions.glob("d-*.md"):
            match = re.match(r"d-(\d{3,})-", path.name)
            if match:
                values.append(f"D-{match.group(1)}")
    values.extend(str(row["id"]) for row in decision_log_rows(vault))
    return values


def _existing_evidence_ids(vault: Path) -> list[str]:
    active, retired, _ = evidence_register(vault)
    return [*active.keys(), *retired.keys()]


def _decision_record_path(vault: Path, decision_id: str) -> Path:
    number = decision_id.split("-", 1)[1]
    matches = sorted((vault / "06-decisions").glob(f"d-{number}-*.md"))
    exact = [path for path in matches if str(load_note(path).front.get("decision_id") or "").strip() == decision_id]
    if len(exact) != 1:
        raise ValueError(f"{decision_id} does not resolve to exactly one decision record")
    return exact[0]


def _update_decision_log_status(path: Path, decision_id: str, status: str, today: dt.date) -> None:
    text = path.read_text(encoding="utf-8")
    atomic_write_text(path, _update_decision_log_status_text(text, decision_id, status, today))

def create_decision(
    vault: Path,
    title: str,
    *,
    owner: str = "TODO",
    status: str = "draft",
    sensitivity: str = "internal",
    source_ids: list[str] | None = None,
    review_by: str | None = None,
    supersedes: str | None = None,
    today: dt.date | None = None,
) -> tuple[str, Path]:
    today = today or dt.date.today()
    title = title.strip()
    if not title:
        raise ValueError("decision title cannot be empty")
    with vault_mutation_lock(vault):
        existing_decisions = _existing_decision_ids(vault)
        decision_id = _next_id(existing_decisions, "D")
        number = decision_id.split("-", 1)[1]
        path = safe_vault_target(
            vault,
            Path("06-decisions") / f"d-{number}-{_slugify(title)}.md",
        )
        if path.exists():
            raise FileExistsError(path)
        if status == "approved" and not review_by:
            raise ValueError("approved decisions require --review-by")
        if status == "approved" and owner.strip() in {"", "TODO"}:
            raise ValueError("approved decisions require a real --owner")
        if review_by:
            parse_iso_date("--review-by", review_by)
        predecessor_path: Path | None = None
        predecessor_text: str | None = None
        if supersedes:
            if not DECISION_ID_RE.fullmatch(supersedes):
                raise ValueError("--supersedes must be a D-NNN identifier")
            if supersedes not in set(existing_decisions):
                raise ValueError(f"--supersedes points to unknown decision {supersedes}")
            predecessor_path = _decision_record_path(vault, supersedes)
            predecessor_note = load_note(predecessor_path)
            if predecessor_note.front.get("status") != "approved":
                raise ValueError(f"{supersedes} must be approved before it can be superseded")
            predecessor_text = predecessor_path.read_text(encoding="utf-8")
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
        if review_by:
            optional += f"review_by: {review_by}\n"
        if supersedes:
            optional += f"supersedes: {supersedes}\n"
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

What is true now, and what forces a choice?

## Decision

State the choice in one sentence.

## Rationale

Why this option, given the evidence and constraints?

## Evidence

{chr(10).join(f'- {sid}' for sid in source_ids) if source_ids else '- TODO — add E-NNN references or explain why none apply.'}

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
|  |  |  |  |

## Consequences

### Positive

-

### Negative and trade-offs

-

## Ownership and review

- Owner: {owner}
- Decided on: {today.isoformat() if status == 'approved' else 'TBD'}
- Review on: {review_by or 'TBD'}
- Supersedes: {supersedes or '—'}
- Superseded by: —
'''
        log = safe_vault_target(vault, "06-decisions/decision-log.md", create_parents=False)
        if not log.exists():
            raise FileNotFoundError(log)
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
        if status == "approved" and supersedes and predecessor_path and predecessor_text is not None:
            predecessor_path = safe_vault_target(
                vault,
                # The record was globbed under `vault` as given, so it is relative
                # to that spelling even when the vault sits behind a symlink.
                predecessor_path.relative_to(vault),
                create_parents=False,
            )
            updated_predecessor = _frontmatter_replace(predecessor_text, "status", "superseded")
            updated_predecessor = _frontmatter_replace(updated_predecessor, "last_updated", today.isoformat())
            updated_predecessor = _frontmatter_replace(updated_predecessor, "superseded_by", decision_id)
            updates[predecessor_path] = updated_predecessor
            updates[log] = _update_decision_log_status_text(updates[log], supersedes, "superseded", today)
        apply_transaction(vault, updates)
        return decision_id, path


def create_evidence(
    vault: Path,
    *,
    source: str,
    location: str,
    kind: str,
    claims: str,
    date: str | None = None,
    accessed: str | None = None,
    today: dt.date | None = None,
) -> str:
    today = today or dt.date.today()
    if not source.strip() or not location.strip() or not kind.strip() or not claims.strip():
        raise ValueError("evidence source, location, type and claims must be non-empty")
    source_date = date or today.isoformat()
    accessed_date = accessed or today.isoformat()
    parse_iso_date("--date", source_date)
    parse_iso_date("--accessed", accessed_date)
    with vault_mutation_lock(vault):
        evidence_id = _next_id(_existing_evidence_ids(vault), "E")
        register = safe_vault_target(vault, "00-context/evidence-register.md", create_parents=False)
        if not register.exists():
            raise FileNotFoundError(register)
        row = "| " + " | ".join(
            _table_cell(value)
            for value in (evidence_id, source, kind, source_date, accessed_date, location, claims)
        ) + " |"
        _append_table_row(register, "| ID | Source | Type |", row, today, record_id=evidence_id)
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
    if path.suffix.lower() != ".md" or not path.is_file():
        raise ValueError(f"--link-from is not an existing Markdown file: {value}")
    return path


def _append_note_link(vault: Path, map_path: Path, note_path: Path, title: str, today: dt.date) -> None:
    text = map_path.read_text(encoding="utf-8")
    target = note_path.relative_to(vault).with_suffix("").as_posix()
    if re.search(rf"\[\[{re.escape(target)}(?:\||\]\])", text):
        return
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
    atomic_write_text(map_path, updated)


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
        raise FileExistsError(path)
    with vault_mutation_lock(vault):
        if path.exists():
            raise FileExistsError(path)
        map_path = _resolve_link_from(vault, link_from) if link_from else None
        map_before = map_path.read_text(encoding="utf-8") if map_path else None
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
        try:
            atomic_write_text(path, body)
            if map_path:
                _append_note_link(vault, map_path, path, title, today)
        except Exception:
            path.unlink(missing_ok=True)
            if map_path and map_before is not None:
                atomic_write_text(map_path, map_before)
            raise
        return path


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
    decision.add_argument("title")
    decision.add_argument("--owner")
    decision.add_argument("--status", choices=tuple(DECISION_STATUS_TO_LOG), default="draft")
    decision.add_argument("--sensitivity", choices=VALID_SENSITIVITY)
    decision.add_argument("--source", action="append", default=[], dest="source_ids", help="supporting E-NNN (repeatable)")
    decision.add_argument("--review-by")
    decision.add_argument("--supersedes")
    decision.add_argument("--json", action="store_true")

    evidence = sub.add_parser("evidence", help="append a source to the evidence register")
    evidence.add_argument("--source", required=True, dest="source_name")
    evidence.add_argument("--location", required=True)
    evidence.add_argument("--type", required=True, dest="evidence_type")
    evidence.add_argument("--claims", required=True)
    evidence.add_argument("--date")
    evidence.add_argument("--accessed")
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
        print_no_vault(args.root)
        return 2
    try:
        config, _ = load_config(vault)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    default_owner = str(config["defaults"]["owner"])
    default_sensitivity = str(config["defaults"]["sensitivity"])
    owner = getattr(args, "owner", None) or default_owner
    sensitivity = getattr(args, "sensitivity", None) or default_sensitivity
    review_by = getattr(args, "review_by", None)
    if args.kind == "decision" and args.status == "approved" and not review_by:
        review_by = (dt.date.today() + dt.timedelta(days=int(config["defaults"]["decision_review_days"]))).isoformat()
    try:
        if args.kind == "decision":
            decision_id, path = create_decision(
                vault, args.title, owner=owner, status=args.status, sensitivity=sensitivity,
                source_ids=args.source_ids, review_by=review_by, supersedes=args.supersedes,
            )
            payload = {"contract_version": 1, "kind": "decision", "id": decision_id, "path": path.relative_to(vault).as_posix()}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created {decision_id}: {path.relative_to(vault)}")
        elif args.kind == "evidence":
            evidence_id = create_evidence(
                vault, source=args.source_name, location=args.location, kind=args.evidence_type,
                claims=args.claims, date=args.date, accessed=args.accessed,
            )
            payload = {"contract_version": 1, "kind": "evidence", "id": evidence_id, "path": "00-context/evidence-register.md"}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
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
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"created note: {path.relative_to(vault)}")
                if args.link_from:
                    print(f"linked from: {args.link_from}")
                else:
                    print("note is not linked from a map; strict profiles may report note.orphan")
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
