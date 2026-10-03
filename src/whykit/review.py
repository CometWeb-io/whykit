"""Operational review queue and append-only review events."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from .contract import TargetNotFound, describe_os_error, emit_error, vault_not_found
from .io import apply_transaction, safe_vault_target, vault_mutation_lock
from .config import ConfigError, load_config
from .lint import DECISION_ID_RE, _build_index, _parse_date, _resolve, collect_markdown, find_vault_root, is_vault_root, load_note, rel, path_cache, strip_markdown_suffix
from .scaffold import _frontmatter_replace, _table_cell
from .status import build_review_queue
from .vault_index import VaultIndex
from .console import emit_machine

OUTCOMES = ("confirmed", "update-required", "supersede-required", "archived")


def _review_log_template(today: dt.date, owner: str = "TODO") -> str:
    # Build the YAML scalar outside the f-string: Python 3.11 rejects backslashes
    # inside f-string expression parts.
    owner_yaml = json.dumps(owner, ensure_ascii=False)
    return f'''---
title: "Review log"
aliases: []
type: reference
status: approved
owner: {owner_yaml}
created: {today.isoformat()}
last_updated: {today.isoformat()}
source_of_truth: false
sensitivity: internal
source_ids: []
tags: ["review", "governance"]
---

# Review log

Append-only operational record of document and decision reviews. A review event
records that somebody re-checked a record; it does not prove the underlying
claim is true.

| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |
|---|---|---|---|---|---|---|
'''


def _front_date(text: str, key: str) -> dt.date | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    match = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(\S+)[ \t]*$", text[:end])
    return _parse_date(match.group(1)) if match else None


def _touch_last_updated(text: str, today: dt.date) -> str:
    # A back-dated review (--today in the past) must not move last_updated
    # backwards: the file is being modified now, and a last_updated earlier than
    # `created` is a lint error.
    floor = max(filter(None, (_front_date(text, "created"), _front_date(text, "last_updated"))), default=today)
    return _frontmatter_replace(text, "last_updated", max(today, floor).isoformat())


def _decision_record(root: Path, decision_id: str):
    matches = []
    for path in collect_markdown(root, []):
        note = load_note(path)
        if str(note.front.get("decision_id") or "").strip() == decision_id:
            matches.append(note)
    if len(matches) != 1:
        return None, len(matches) > 1
    return matches[0], False


def _resolve_note(root: Path, target: str):
    notes = [load_note(path) for path in collect_markdown(root, [])]
    if DECISION_ID_RE.fullmatch(target):
        return _decision_record(root, target)
    index = _build_index(notes)
    path, ambiguous = _resolve(root, target, index)
    if path is None:
        return None, ambiguous
    by_path = {note.path.resolve(): note for note in notes}
    return by_path.get(path.resolve()), ambiguous


@path_cache()
def review_queue(root: Path, *, today: dt.date | None = None, due_days: int = 30, owner: str | None = None, overdue_only: bool = False) -> list[dict]:
    today = today or dt.date.today()
    queue = build_review_queue(VaultIndex.load(root), today=today, due_days=due_days)
    if owner:
        needle = owner.casefold()
        queue = [item for item in queue if needle in str(item.get("owner") or "").casefold()]
    if overdue_only:
        queue = [item for item in queue if item.get("state") == "overdue"]
    return queue


def record_review(
    root: Path,
    target: str,
    *,
    reviewer: str,
    outcome: str,
    note_text: str = "",
    next_review: str | None = None,
    today: dt.date | None = None,
) -> dict:
    today = today or dt.date.today()
    reviewer = reviewer.strip()
    if not reviewer or reviewer == "TODO":
        raise ValueError("--reviewer must name a real reviewer")
    if outcome not in OUTCOMES:
        raise ValueError(f"unsupported review outcome: {outcome}")

    with vault_mutation_lock(root):
        note, ambiguous = _resolve_note(root, target)
        if note is None and not ambiguous:
            raise TargetNotFound(f"review target is missing: {target}")
        if note is None:
            raise ValueError(f"review target is ambiguous: {target}")
        if rel(root, note.path) == "00-context/review-log.md":
            raise ValueError("the review log cannot review itself")

        created = _parse_date(note.front.get("created") or "")
        if created is not None and today < created:
            raise ValueError(
                f"review date {today.isoformat()} is before {rel(root, note.path)} was created ({created.isoformat()})"
            )

        previous_review = str(note.front.get("review_by") or "").strip() or "—"
        resolved_next = next_review
        if resolved_next:
            try:
                parsed_next = dt.date.fromisoformat(resolved_next)
            except ValueError as exc:
                raise ValueError("--next-review must be a real YYYY-MM-DD date") from exc
            if parsed_next <= today:
                raise ValueError("--next-review must be after the review date")
        elif outcome == "confirmed":
            config, _ = load_config(root)
            days = int(config["defaults"]["decision_review_days"])
            resolved_next = (today + dt.timedelta(days=days)).isoformat()

        if outcome == "confirmed" and note.front.get("status") != "approved":
            raise ValueError("only approved records can be confirmed; review/update the lifecycle state first")

        log = safe_vault_target(root, "00-context/review-log.md")
        log_before = log.read_text(encoding="utf-8") if log.exists() else _review_log_template(today)
        target_rel = note.path.resolve().relative_to(root.resolve(strict=True))
        note_path = safe_vault_target(root, target_rel, create_parents=False)
        target_before = note_path.read_text(encoding="utf-8")

        lines = log_before.splitlines()
        header = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
        header_idx = next((idx for idx, line in enumerate(lines) if line.strip() == header), None)
        if header_idx is None or header_idx + 1 >= len(lines):
            raise ValueError("review log table is missing or malformed")
        insert_at = header_idx + 2
        while insert_at < len(lines) and lines[insert_at].lstrip().startswith("|"):
            insert_at += 1
        target_link = f"[[{strip_markdown_suffix(rel(root, note.path))}]]"
        row = "| " + " | ".join(_table_cell(value) for value in (
            today.isoformat(), target_link, reviewer, outcome, previous_review, resolved_next or "—", note_text or "—"
        )) + " |"
        lines.insert(insert_at, row)
        updated_log = "\n".join(lines) + ("\n" if log_before.endswith("\n") else "")
        updated_log = _touch_last_updated(updated_log, today)

        updates: dict = {log: updated_log}
        if outcome == "confirmed" and resolved_next:
            updated_target = _frontmatter_replace(target_before, "review_by", resolved_next)
            updated_target = _touch_last_updated(updated_target, today)
            updates[note_path] = updated_target
        apply_transaction(root, updates)

        return {
            "contract_version": 1,
            "date": today.isoformat(),
            "target": rel(root, note.path),
            "decision_id": str(note.front.get("decision_id") or "") or None,
            "reviewer": reviewer,
            "outcome": outcome,
            "previous_review": previous_review if previous_review != "—" else None,
            "next_review": resolved_next,
            "note": note_text or None,
            "log": "00-context/review-log.md",
        }


def _queue_summary(count: int, today: dt.date, due_days: int, owner: str | None, overdue_only: bool) -> str:
    """The closing line of `review list`, naming the window it searched.

    "0 review(s)" alone reads as "nothing to review ever", when it only means
    nothing falls inside this window.
    """
    if overdue_only:
        window = f"overdue as of {today.isoformat()}"
    else:
        horizon = today + dt.timedelta(days=due_days)
        window = f"overdue or due by {horizon.isoformat()} ({due_days} day(s) from {today.isoformat()})"
    scope = f", owner matching {owner!r}" if owner else ""
    return f"{count} review(s) {window}{scope}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit review", description="List review work or record an auditable review event.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    sub = parser.add_subparsers(dest="review_command", required=True)

    listing = sub.add_parser("list", help="show upcoming/overdue review work")
    listing.add_argument("--today")
    listing.add_argument("--due-days", type=int)
    listing.add_argument("--owner")
    listing.add_argument("--overdue-only", action="store_true")
    listing.add_argument("--json", action="store_true")

    record = sub.add_parser("record", help="append a review event and optionally schedule the next review")
    record.add_argument("target", help="D-NNN, path, stem or alias")
    record.add_argument("--reviewer", required=True)
    record.add_argument("--outcome", choices=OUTCOMES, default="confirmed")
    record.add_argument("--next-review")
    record.add_argument("--note", default="", dest="note_text")
    record.add_argument("--today")
    record.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)

    if args.review_command == "list":
        today = None
        if args.today:
            try:
                today = dt.date.fromisoformat(args.today)
            except ValueError:
                return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
        try:
            config, _ = load_config(root)
        except ConfigError as exc:
            return emit_error("invalid_config", str(exc), json_mode=args.json)
        due_days = args.due_days if args.due_days is not None else int(config["defaults"]["status_due_days"])
        if due_days < 0:
            return emit_error("invalid_argument", "--due-days must be >= 0", json_mode=args.json)
        queue = review_queue(root, today=today, due_days=due_days, owner=args.owner, overdue_only=args.overdue_only)
        payload = {"contract_version": 1, "due_days": due_days, "count": len(queue), "reviews": queue}
        if args.json:
            emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in queue:
                marker = "OVERDUE" if item["state"] == "overdue" else "DUE"
                print(f"{marker:<7} {item['review_by']}  {item['path']}  ({item['owner'] or 'no owner'})")
            print(f"\n{_queue_summary(len(queue), today or dt.date.today(), due_days, args.owner, args.overdue_only)}")
        return 0

    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    try:
        result = record_review(
            root, args.target, reviewer=args.reviewer, outcome=args.outcome,
            note_text=args.note_text, next_review=args.next_review, today=today,
        )
    except TargetNotFound as exc:
        return emit_error("not_found", str(exc), json_mode=args.json)
    except ValueError as exc:
        return emit_error("operation_rejected", str(exc), json_mode=args.json)
    except OSError as exc:
        return emit_error("io_error", describe_os_error(exc), json_mode=args.json)
    if args.json:
        emit_machine(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"review recorded: {result['target']} -> {result['outcome']}")
        if result.get("next_review"):
            print(f"next review: {result['next_review']}")
    return 0
