#!/usr/bin/env python3
"""Bring an existing pile of Markdown into a WhyKit vault.

Nobody adopting this tool starts from nothing. They start from a docs directory,
a folder of ADRs, a Notion export, or four years of meeting notes. `init` serves
the empty case; this serves the real one.

The design rule comes straight from the vault's own AGENTS.md: *raw platform
exports are never canonical notes.* So `adopt` never writes into a workstream.
It stages files under `.import-staging/`, records what it saw — including a
SHA-256 for every source — and leaves the judgement calls to a human. What it
automates is the inventory, which is the boring half and the half people skip.

    whykit adopt ../old-docs
    whykit adopt ../old-docs --profile obsidian-loose --json
    whykit adopt ../old-docs --write
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .contract import emit_error
from .io import atomic_write_bytes, atomic_write_text, safe_vault_dir, safe_vault_target, vault_mutation_lock
from .lint import (
    EVIDENCE_ID_RE, DECISION_ID_RE, WIKILINK_RE, _split_table_row,
    Note, check_front_matter, find_vault_root, is_vault_root, load_note,
)
from .console import emit_machine

SKIP_DIRS = {
    ".git", ".obsidian", ".import-staging", "node_modules", "__pycache__",
    ".venv", "venv", "dist", "build", ".next", ".cache",
}

# Michael Nygard's layout, MADR, adr-tools and log4brains all land in this shape.
ADR_FILENAME_RE = re.compile(r"^(?:adr[-_]?)?(\d{3,4})[-_]")
ADR_HEADING_RE = re.compile(r"(?mi)^#{1,3}\s*(status|context|decision|consequences)\b")
FRONT_MATTER_RE = re.compile(r"\A---\r?\n")

ASSESSMENTS = ("useful", "duplicate", "empty", "heading-only", "unsupported")
ADOPT_PROFILES = ("generic", "adr-only", "obsidian-loose")


@dataclass
class Candidate:
    path: Path
    relative: str
    sha256: str
    bytes: int
    words: int
    assessment: str
    has_front_matter: bool
    looks_like_decision: bool
    duplicate_of: str | None = None
    whykit_ready: bool = False
    decision_id: str | None = None
    format_warnings: list[str] = field(default_factory=list)

    def destination_for(self, profile: str) -> str:
        if self.assessment != "useful":
            return "—"
        if self.looks_like_decision or profile == "adr-only":
            return "06-decisions/ (assign D-NNN)"
        if profile == "obsidian-loose":
            return "notes/ (normalize front matter)"
        return "notes/ or a workstream (human choice)"


def _looks_like_decision(name: str, text: str) -> bool:
    if ADR_FILENAME_RE.match(name):
        return True
    headings = {m.group(1).lower() for m in ADR_HEADING_RE.finditer(text)}
    return {"context", "decision"}.issubset(headings)


def _whykit_ready(path: Path, source: Path, text: str, note: Note | None = None) -> bool:
    """Check front matter with the same parser and rules as `whykit lint`.

    This does not certify links, evidence or the containing vault.
    """
    note = note or load_note(path, text=text)
    if not note.has_front:
        return False
    findings = []
    try:
        check_front_matter(source, note, findings, dt.date.today())
    except (TypeError, ValueError):
        # A malformed imported value must not crash a dry-run or score as ready.
        return False
    return not any(f.level == "error" for f in findings)


def _format_warnings(relative: str, text: str) -> list[str]:
    """Point out known ledger-table layouts that `adopt` cannot normalize."""
    name = Path(relative).name
    warnings = []
    if name not in {"evidence-register.md", "decision-log.md"}:
        return warnings
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if name == "evidence-register.md" and cells and EVIDENCE_ID_RE.fullmatch(cells[0]):
            if len(cells) < 7:
                warnings.append(f"{relative}: evidence row has {len(cells)} columns; WhyKit needs 7 (including Accessed and Location)")
                break
        elif name == "decision-log.md" and cells and DECISION_ID_RE.fullmatch(cells[0]):
            if len(cells) < 6 or not WIKILINK_RE.search(cells[5]):
                warnings.append(f"{relative}: decision row has no record wikilink in column 6; manual table mapping is required")
                break
    return warnings


def _has_front_matter(text: str) -> bool:
    # A UTF-8 byte-order mark is not content; the linter skips it too.
    return bool(FRONT_MATTER_RE.match(text.removeprefix("\ufeff")))


def _assess(text: str, *, profile: str, name: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    body = re.sub(r"(?m)^#{1,6}\s+.*$", "", stripped).strip()
    # Short is not the same as empty: a terse ADR or a note somebody gave front
    # matter is structured work, so only unstructured stubs are left behind.
    structured = _has_front_matter(text) or _looks_like_decision(name, text)
    if len(body.split()) < 15 and not structured:
        return "heading-only"
    if profile == "adr-only" and not _looks_like_decision(name, text):
        return "unsupported"
    return "useful"


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def scan(source: Path, *, profile: str = "generic") -> list[Candidate]:
    """Inventory Markdown under ``source`` without following file symlinks outside it."""
    source = source.resolve()
    seen: dict[str, str] = {}
    out: list[Candidate] = []
    for path in sorted(source.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(source).parts):
            continue
        if not _within(source, path):
            continue
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "\0" in text:
            # Valid UTF-8 but not text: a binary renamed to .md.
            continue
        digest = hashlib.sha256(data).hexdigest()
        relative = path.relative_to(source).as_posix()
        assessment = _assess(text, profile=profile, name=path.name)
        duplicate_of = None
        if digest in seen:
            assessment, duplicate_of = "duplicate", seen[digest]
        else:
            seen[digest] = relative
        note = load_note(path, text=text)
        declared = str(note.front.get("decision_id") or "").strip() if note.has_front else ""
        out.append(Candidate(
            path=path,
            relative=relative,
            sha256=digest,
            bytes=len(data),
            words=len(text.split()),
            assessment=assessment,
            has_front_matter=_has_front_matter(text),
            looks_like_decision=_looks_like_decision(path.name, text),
            duplicate_of=duplicate_of,
            whykit_ready=_whykit_ready(path, source, text, note),
            decision_id=declared if DECISION_ID_RE.fullmatch(declared) else None,
            format_warnings=_format_warnings(relative, text),
        ))
    return out


def _vault_decision_ids(vault: Path) -> dict[str, str]:
    """Map each decision ID already declared in the vault to its record path."""
    owners: dict[str, str] = {}
    folder = vault / "06-decisions"
    if folder.is_symlink() or not folder.is_dir():
        return owners
    for path in sorted(folder.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            note = load_note(path)
        except (OSError, UnicodeDecodeError):
            continue
        declared = str(note.front.get("decision_id") or "").strip()
        if DECISION_ID_RE.fullmatch(declared):
            owners.setdefault(declared, path.relative_to(vault).as_posix())
    return owners


def id_conflicts(candidates: list[Candidate], vault: Path | None = None) -> list[str]:
    """Report decision IDs claimed twice: within the import, or by the vault already.

    Two ADR folders that both start at D-001 are the normal case, not the edge
    case. Staging both is fine; finding out at lint time after normalizing is not.
    """
    claims: dict[str, list[str]] = {}
    for candidate in candidates:
        if candidate.assessment == "useful" and candidate.decision_id:
            claims.setdefault(candidate.decision_id, []).append(candidate.relative)
    existing = _vault_decision_ids(vault) if vault is not None else {}
    conflicts = []
    for decision_id, paths in sorted(claims.items()):
        listed = ", ".join(f"`{path}`" for path in paths)
        if len(paths) > 1:
            conflicts.append(f"{decision_id} is declared by {len(paths)} source files: {listed}")
        if decision_id in existing:
            conflicts.append(f"{decision_id} ({listed}) is already used in the vault by `{existing[decision_id]}`")
    return conflicts


def score_adoption(candidates: list[Candidate], vault: Path | None = None) -> dict:
    total = len(candidates)
    useful = [c for c in candidates if c.assessment == "useful"]
    ready = [c for c in useful if c.whykit_ready]
    decisions = [c for c in useful if c.looks_like_decision]
    needs_front = [c for c in useful if not c.has_front_matter]
    minutes = (
        len(useful) * 3
        + len(needs_front) * 5
        + len(decisions) * 8
        + sum(1 for c in candidates if c.assessment in {"heading-only", "unsupported"}) * 1
    )
    return {
        "files": total,
        "useful": len(useful),
        "whykit_ready": len(ready),
        "need_front_matter": len(needs_front),
        "looks_like_decision": len(decisions),
        "duplicates": sum(1 for c in candidates if c.assessment == "duplicate"),
        "empty_or_stub": sum(1 for c in candidates if c.assessment in {"empty", "heading-only"}),
        "unsupported": sum(1 for c in candidates if c.assessment == "unsupported"),
        "estimated_minutes_to_first_green_lint": minutes,
        "readiness_pct": round(100.0 * len(ready) / total, 1) if total else 0.0,
        "format_warnings": [warning for c in candidates for warning in c.format_warnings],
        "id_conflicts": id_conflicts(candidates, vault),
    }


def _migration_md(source: Path, candidates: list[Candidate], score: dict, profile: str) -> str:
    lines = [
        f"# Migration from `{source}`",
        "",
        f"Profile: `{profile}`. Generated by `whykit adopt`.",
        "",
        "## Score",
        "",
        f"- Files scanned: **{score['files']}**",
        f"- Useful: **{score['useful']}** (front matter passes WhyKit checks: **{score['whykit_ready']}**)",
        f"- Estimated human minutes to first green lint: **{score['estimated_minutes_to_first_green_lint']}**",
        f"- Readiness: **{score['readiness_pct']}%**",
        "- Scope: UTF-8 `*.md` only. Other assets need separate review; this is not a full vault lint.",
        "",
        "## Format warnings",
        "",
        *(f"- {warning}" for warning in score["format_warnings"]),
        *(["- None detected."] if not score["format_warnings"] else []),
        "",
        "## ID conflicts",
        "",
        *(f"- {conflict}" for conflict in score["id_conflicts"]),
        *(["- None detected."] if not score["id_conflicts"] else []),
        "",
        "## Bring into the vault",
        "",
    ]
    for candidate in candidates:
        if candidate.assessment != "useful":
            continue
        lines.append(f"- `{candidate.relative}` → {candidate.destination_for(profile)}")
    lines.extend(["", "## Leave outside / discard", ""])
    for candidate in candidates:
        if candidate.assessment == "useful":
            continue
        reason = candidate.assessment
        if candidate.duplicate_of:
            reason += f" (same bytes as `{candidate.duplicate_of}`)"
        lines.append(f"- `{candidate.relative}` — {reason}")
    lines.extend([
        "",
        "## Suggested next steps",
        "",
        "1. Review staged files under `.import-staging/`.",
        "2. Normalize front matter for notes that are not WhyKit-ready.",
        "3. Allocate `D-NNN` / `E-NNN` where claims must stay durable.",
        "4. Link durable notes from `Home.md`, then `whykit lint --strict`.",
        "",
    ])
    return "\n".join(lines)


def _ingestion_record(
    batch: str,
    today: dt.date,
    source: Path,
    candidates: list[Candidate],
    owner: str,
    profile: str,
) -> str:
    rows = "\n".join(
        f"| `{c.relative}` | `{c.sha256}` | {c.assessment} |  | {c.destination_for(profile)} |"
        for c in candidates
    ) or "|  |  |  |  |  |"
    useful = sum(1 for c in candidates if c.assessment == "useful")
    decisions = sum(1 for c in candidates if c.assessment == "useful" and c.looks_like_decision)
    return f"""---
title: "Source ingestion batch {batch}"
aliases: []
type: research
status: draft
owner: "{owner}"
created: {today.isoformat()}
last_updated: {today.isoformat()}
source_of_truth: false
sensitivity: internal
source_ids: []
tags: ["ingestion", "adoption"]
---

# Source ingestion batch {batch}

## Purpose

Record how one batch of existing material was assessed on import. Generated by
`whykit adopt --profile {profile}` from `{source}` on {today.isoformat()}.
Nothing here is canonical until a person normalizes it and changes its status.

## Results

| Uploaded file | SHA-256 | Assessment | Evidence ID | Destination in repository |
|---|---|---|---|---|
{rows}

Scanned {len(candidates)} file(s): {useful} worth keeping, of which {decisions} look
like existing decision records.

## Normalization performed

- Not yet assessed.

## Review required

- Assign evidence IDs to sources that support a claim anywhere in the vault.
- Decide a sensitivity label per file before anything leaves `.import-staging/`.
"""


def _ingestion_dir(vault: Path) -> Path:
    legacy_parent = vault / "07-research"
    if legacy_parent.is_dir() or (vault / "07-research" / "sources").is_dir():
        return safe_vault_dir(vault, "07-research/sources")
    return safe_vault_dir(vault, "notes")


def adopt(
    source: Path,
    vault: Path,
    *,
    write: bool,
    owner: str,
    profile: str = "generic",
    today: dt.date | None = None,
) -> tuple[list[Candidate], Path | None, Path | None, dict]:
    today = today or dt.date.today()
    candidates = scan(source, profile=profile)
    score = score_adoption(candidates, vault)
    if not write:
        return candidates, None, None, score

    with vault_mutation_lock(vault):
        staging_root = safe_vault_dir(vault, ".import-staging")
        batch = today.isoformat()
        suffix = 1
        while (staging_root / batch).exists() or (staging_root / batch).is_symlink():
            suffix += 1
            batch = f"{today.isoformat()}-{suffix}"
        # Stage into a hidden directory and rename it into place only once it
        # is complete. A failure removes everything this run created, and a
        # crash leaves a `.partial-*` directory that no later run mistakes for
        # a finished batch.
        partial_rel = Path(".import-staging") / f".partial-{secrets.token_hex(6)}"
        partial = safe_vault_dir(vault, partial_rel)
        final = staging_root / batch
        created = partial
        try:
            for candidate in candidates:
                if candidate.assessment != "useful":
                    continue
                target = safe_vault_target(vault, partial_rel / candidate.relative)
                data = candidate.path.read_bytes()
                current_digest = hashlib.sha256(data).hexdigest()
                if current_digest != candidate.sha256:
                    raise RuntimeError(
                        f"source changed during adoption: {candidate.relative} "
                        f"({candidate.sha256[:12]} -> {current_digest[:12]})"
                    )
                atomic_write_bytes(target, data)

            migration_name = "MIGRATION.md"
            suffix = 1
            while (partial / migration_name).exists() or (partial / migration_name).is_symlink():
                suffix += 1
                migration_name = f"MIGRATION-{suffix}.md"
            atomic_write_text(
                safe_vault_target(vault, partial_rel / migration_name),
                _migration_md(source, candidates, score, profile),
            )
            os.rename(partial, final)
            created = final

            sources_dir = _ingestion_dir(vault)
            record_name = f"ingestion-{batch}.md"
            record = sources_dir / record_name
            suffix = 1
            while record.exists() or record.is_symlink():
                suffix += 1
                record_name = f"ingestion-{batch}-{suffix}.md"
                record = sources_dir / record_name
            record = safe_vault_target(vault, record.relative_to(vault.resolve()))
            atomic_write_text(record, _ingestion_record(batch, today, source, candidates, owner, profile))
        except BaseException:
            shutil.rmtree(created, ignore_errors=True)
            raise
        return candidates, record, final / migration_name, score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit adopt",
        description="Inventory existing Markdown and stage it for a WhyKit vault.",
    )
    parser.add_argument("source", help="directory of existing Markdown to adopt")
    parser.add_argument("--into", help="target vault (default: nearest vault at or above the working directory)")
    parser.add_argument("--write", action="store_true", help="stage files and write an ingestion record")
    parser.add_argument("--owner", default="TODO", help="owner recorded on the ingestion record")
    parser.add_argument("--profile", choices=ADOPT_PROFILES, default="generic", help="adoption mapping profile")
    parser.add_argument("--json", action="store_true", help="machine-readable inventory + score (implies dry-run shape)")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        return emit_error("invalid_target", f"not a directory: {source}", json_mode=args.json)

    vault = Path(args.into).expanduser().resolve() if args.into else find_vault_root()
    if vault is None or not is_vault_root(vault):
        return emit_error(
            "vault_not_found",
            "no WhyKit vault found — run `whykit init <dir>` first, or pass --into",
            json_mode=args.json,
        )
    if source == vault or vault in source.parents or source in vault.parents:
        return emit_error("invalid_target", "refusing to adopt overlapping source/vault directories", json_mode=args.json)

    try:
        candidates, record, migration, score = adopt(
            source, vault, write=args.write, owner=args.owner, profile=args.profile,
        )
    except (OSError, RuntimeError) as exc:
        return emit_error("io_error", f"adoption failed: {exc}", json_mode=args.json)

    visible_files = [
        path for path in source.rglob("*")
        if path.is_file() and _within(source, path)
        and not any(part in SKIP_DIRS for part in path.relative_to(source).parts)
    ]
    omitted = sorted(path.relative_to(source).as_posix() for path in visible_files if path.suffix != ".md")
    scanned = {candidate.relative for candidate in candidates}
    unreadable_markdown = sorted(
        path.relative_to(source).as_posix()
        for path in visible_files if path.suffix == ".md"
        and path.relative_to(source).as_posix() not in scanned
    )
    scope_note = (
        "Only UTF-8 *.md files were scanned; "
        f"{len(omitted)} other file(s) excluded"
        + (f" (e.g. {', '.join(omitted[:5])})" if omitted else "")
        + f"; {len(unreadable_markdown)} Markdown file(s) unreadable, binary or non-UTF-8"
        + (f" (e.g. {', '.join(unreadable_markdown[:5])})" if unreadable_markdown else "")
        + ". Front matter readiness is not full vault lint."
    )

    if args.json:
        payload = {
            "contract_version": 1,
            "profile": args.profile,
            "source": str(source),
            "vault": str(vault),
            "write": bool(args.write),
            "score": score,
            "scope_note": scope_note,
            "candidates": [
                {
                    "path": c.relative,
                    "assessment": c.assessment,
                    "destination": c.destination_for(args.profile),
                    "has_front_matter": c.has_front_matter,
                    "whykit_ready": c.whykit_ready,
                    "looks_like_decision": c.looks_like_decision,
                    "duplicate_of": c.duplicate_of,
                    "bytes": c.bytes,
                    "words": c.words,
                    "sha256": c.sha256,
                }
                for c in candidates
            ],
            "ingestion_record": str(record.relative_to(vault)) if record else None,
            "migration": str(migration.relative_to(vault)) if migration else None,
        }
        emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not candidates:
        print(f"no Markdown found under {source}")
        print(scope_note)
        return 0

    counts = {a: sum(1 for c in candidates if c.assessment == a) for a in ASSESSMENTS}
    decisions = [c for c in candidates if c.assessment == "useful" and c.looks_like_decision]
    width = max(len(c.relative) for c in candidates)
    for candidate in candidates:
        marker = "decision?" if candidate.looks_like_decision and candidate.assessment == "useful" else ""
        note = f" (same bytes as {candidate.duplicate_of})" if candidate.duplicate_of else ""
        print(f"  {candidate.relative:<{width}}  {candidate.assessment:<12}{marker}{note}")

    print(f"\n{len(candidates)} file(s): " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    print(
        f"Score: {score['readiness_pct']}% front-matter-ready only; "
        f"~{score['estimated_minutes_to_first_green_lint']} heuristic minutes to first green lint"
    )
    print(scope_note)
    for warning in score["format_warnings"]:
        print(f"Format warning: {warning}")
    for conflict in score["id_conflicts"]:
        print(f"ID conflict: {conflict}")
    if decisions:
        print(f"{len(decisions)} look like existing decision records — they need D-NNN IDs and log rows.")

    if not args.write:
        print("\nDry run. Nothing was written. Re-run with --write to stage these files")
        print(f"under {vault}/.import-staging/ and create an ingestion record + MIGRATION.md.")
        return 0

    print(f"\nStaged under {vault / '.import-staging'}{os.sep}")
    print(f"Ingestion record: {record.relative_to(vault).as_posix() if record else '—'}")
    print(f"Migration guide: {migration.relative_to(vault).as_posix() if migration else '—'}")
    print("Staging is gitignored on purpose. Normalize into a workstream before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
