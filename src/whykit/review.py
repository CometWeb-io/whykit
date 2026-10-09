"""Operational review queue and append-only review events."""
from __future__ import annotations

from .io import consistent_read, vault_read_lock

from .tables import review_table_header

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
from pathlib import Path

from .contract import TargetNotFound, describe_os_error, emit_error, vault_not_found
from .io import apply_transaction, safe_vault_target, vault_mutation_lock
from .config import CONFIG_FILE, ConfigError, load_config, parse_config
from .lint import DECISION_ID_RE, _build_index, _parse_date, _parse_evidence_register_text, _resolve, check_front_matter, check_decision_review, check_decision_placeholders, collect_markdown, find_vault_root, is_vault_root, lint, load_note, rel, path_cache, strip_markdown_suffix
from .scaffold import _frontmatter_replace, _table_cell, _update_decision_log_status_text
from .immutability import RECORD_RE, _front_matter_lines, _review_log_parts, approval_record_hash
from .placeholders import PROSE_SECTIONS, split_sections
from .status import build_review_queue
from .vault_index import VaultIndex
from .console import emit_machine, one_line

OUTCOMES = ("confirmed", "update-required", "supersede-required", "archived")


def _append_review_row(log_before: str, values: tuple[str, ...], today: dt.date) -> str:
    lines = log_before.splitlines()
    header_idx = review_table_header(lines)
    if header_idx is None:
        raise ValueError("review log table is missing or malformed")
    insert_at = header_idx + 2
    while insert_at < len(lines) and lines[insert_at].lstrip().startswith("|"):
        insert_at += 1
    lines.insert(insert_at, "| " + " | ".join(_table_cell(value) for value in values) + " |")
    updated = "\n".join(lines) + ("\n" if log_before.endswith("\n") else "")
    return _touch_last_updated(updated, today)


def _reviewed_provenance(text: str) -> str:
    """Change only the review flag, preserving producer extensions verbatim."""
    parsed = _front_matter_lines(text)
    if parsed is None:
        raise ValueError("approval requires readable front matter")
    lines, end = parsed
    start = next((i for i in range(1, end) if re.match(r"^provenance[ \t]*:", lines[i])), None)
    if start is None:
        return text
    stop = next((i for i in range(start + 1, end) if re.match(r"^[^\s#][^:]*:", lines[i])), end)
    indent = next((match.group() for line in lines[start + 1:stop] if line.strip() and (match := re.match(r"^[ \t]+", line)) is not None), "  ")
    flag = next((i for i in range(start + 1, stop) if re.match(rf"^{re.escape(indent)}human_reviewed[ \t]*:", lines[i])), None)
    if flag is None:
        lines.insert(stop, indent + "human_reviewed: true\n")
    else:
        lines[flag] = indent + "human_reviewed: true\n"
    return ("\ufeff" if text.startswith("\ufeff") else "") + "".join(lines)


@consistent_read
@path_cache()
def _approval_plan(root: Path, target: str, reviewer: str, today: dt.date, next_review: str | None) -> tuple[dict, dict[Path, str], dict[Path, bytes | None]]:
    root = root.resolve()
    reviewer = reviewer.strip()
    if not reviewer or reviewer.casefold() == "todo" or any(ord(c) < 32 for c in reviewer) or one_line(reviewer) != reviewer:
        raise ValueError("--reviewer must name a reviewer on one line")
    index = VaultIndex.load(root)
    if DECISION_ID_RE.fullmatch(target):
        matches = [note for note in index.notes if note.front.get("decision_id") == target]
        note = matches[0] if len(matches) == 1 else None
    else:
        path, ambiguous = index.resolve_link(target)
        note = index.note_for(path) if path is not None and not ambiguous else None
    if note is None:
        raise TargetNotFound(f"decision is missing or ambiguous: {target}")
    target_rel = rel(root, note.path)
    if not RECORD_RE.fullmatch(target_rel) or not DECISION_ID_RE.fullmatch(str(note.front.get("decision_id") or "")):
        raise ValueError("only a decision record can be approved")
    if note.front_error or note.front.get("status") not in {"draft", "in_review"}:
        raise ValueError("only readable draft/in_review decisions can be approved; never reset an accepted record")
    owner = note.front.get("owner")
    if not isinstance(owner, str) or not owner.strip() or owner.strip().casefold() == "todo":
        raise ValueError("approval requires a real owner")
    created = _parse_date(note.front.get("created") or "")
    if created is None or today < created:
        raise ValueError("approval date must be on or after the record's creation")
    required_sections = {*PROSE_SECTIONS, "Evidence", "Alternatives considered", "Consequences"}
    headings = [heading for heading, _, _ in split_sections(note.masked.splitlines())]
    missing = required_sections - set(headings)
    if missing or any(headings.count(heading) != 1 for heading in required_sections):
        raise ValueError("approval requires exactly one of each decision section: " + ", ".join(sorted(required_sections)))
    raw_lines = note.text.splitlines()
    for heading, start, stop in split_sections(note.masked.splitlines()):
        if heading in required_sections and not any(
            line.strip() not in {"", "-", "*", "+", "—", "---"}
            and not line.lstrip().startswith(("#", "```", "~~~"))
            for line in raw_lines[start:stop]
        ):
            raise ValueError(f"approval requires content in the {heading} section")
    if note.decision_placeholders:
        raise ValueError("replace every decision placeholder before approval")
    inputs: dict[Path, bytes | None] = {}

    def capture(relative: str) -> tuple[Path, str | None]:
        path = safe_vault_target(root, relative, create_parents=False)
        data = path.read_bytes() if path.exists() else None
        inputs[path] = data
        try:
            return path, data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n") if data is not None else None
        except UnicodeDecodeError:
            raise ValueError(f"approval input is not UTF-8: {relative}") from None

    note_path, target_before = capture(target_rel)
    assert target_before is not None
    if load_note(note_path, text=target_before).text != note.text:
        raise ValueError("record changed during preview; retry")
    _, config_text = capture(CONFIG_FILE)
    config = parse_config(config_text or "")
    _, register_text = capture("00-context/evidence-register.md")
    decision_log, decision_log_before = capture("06-decisions/decision-log.md")
    if decision_log_before is None:
        raise ValueError("decision log is missing")
    review_log, review_before = capture("00-context/review-log.md")
    review_before = review_before or _review_log_template(today, reviewer)
    parsed_log = _review_log_parts(review_before)
    if parsed_log is None:
        raise ValueError("review log table is missing or malformed")
    for row in parsed_log[1]:
        link = re.fullmatch(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", row[1])
        if row[3] == "approved" and link and strip_markdown_suffix(link.group(1).strip()) == strip_markdown_suffix(target_rel):
            raise ValueError("this record already has an approval event; never reset an accepted record")
    active, retired, _ = _parse_evidence_register_text(register_text or "")
    cited = set(note.cited_evidence)
    if (not cited and not note.front.get("claim_ids")) or not cited <= set(active) or cited & set(retired):
        raise ValueError("approval requires cited active evidence; missing or retired sources must be reviewed first")
    _, findings = lint(root, orphans=False, today=today, vault=index, config=config)
    blocked = [item for item in findings
               if not (item.path == target_rel and item.code == "canonical.unapproved")
               and (item.level == "error" or item.path == target_rel
                    or (item.code == "evidence.access_stale" and cited.intersection(re.findall(r"E-[0-9]{3,}", item.message))))]
    if blocked:
        raise ValueError("approval refused by vault checks: " + "; ".join(f"{item.path}: {item.code}" for item in blocked[:10]))
    resolved_next = next_review or (today + dt.timedelta(days=int(config["defaults"]["decision_review_days"]))).isoformat()
    next_date = _parse_date(resolved_next)
    if next_date is None or next_date <= today:
        raise ValueError("--next-review must be a real YYYY-MM-DD after the approval date")
    updated_target = _frontmatter_replace(target_before, "status", "approved")
    updated_target = _frontmatter_replace(updated_target, "review_by", resolved_next)
    updated_target = re.sub(r"(?m)(^## Status\n[ \t]*\n)(?:Proposed|Draft|In review)[ \t]*$", r"\1Accepted", updated_target, count=1)
    updated_target = updated_target.replace("- Decided on: TBD\n", f"- Decided on: {today.isoformat()}\n", 1)
    updated_target = _touch_last_updated(_reviewed_provenance(updated_target), today)
    candidate = load_note(note_path, text=updated_target)
    final_findings: list = []
    check_front_matter(root, candidate, final_findings, today)
    check_decision_review(root, [candidate], final_findings)
    check_decision_placeholders(root, [candidate], final_findings)
    if final_findings:
        raise ValueError("approval refused: " + "; ".join(item.code for item in final_findings))
    decision_id = str(note.front["decision_id"])
    updated_decision_log = _update_decision_log_status_text(decision_log_before, decision_id, "accepted", today)
    updates = {note_path: updated_target, decision_log: updated_decision_log}
    predecessor = note.front.get("supersedes")
    if predecessor:
        matches = [other for other in index.notes if other.front.get("decision_id") == predecessor]
        if len(matches) != 1 or matches[0].front.get("status") != "approved":
            raise ValueError("supersedes must name one currently approved decision")
        previous_path, previous_text = capture(rel(root, matches[0].path))
        assert previous_text is not None
        if load_note(previous_path, text=previous_text).text != matches[0].text:
            raise ValueError("predecessor changed during preview; retry")
        previous_text = _frontmatter_replace(previous_text, "status", "superseded")
        previous_text = _frontmatter_replace(previous_text, "superseded_by", decision_id)
        updates[previous_path] = _touch_last_updated(previous_text, today)
        updates[decision_log] = _update_decision_log_status_text(updates[decision_log], str(predecessor), "superseded", today)
    snapshot = hashlib.sha256(inputs[note_path] or b"").hexdigest()
    record_hash = approval_record_hash(updated_target)
    receipt = f"record-sha256:{record_hash}; snapshot-sha256:{snapshot}"
    updates[review_log] = _append_review_row(review_before, (
        today.isoformat(), f"[[{strip_markdown_suffix(target_rel)}]]", reviewer, "approved",
        str(note.front.get("review_by") or "—"), resolved_next, receipt,
    ), today)
    binding = {"updates": {rel(root, path): hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in updates.items()},
               "root": str(root), "target": target_rel, "reviewer": reviewer, "date": today.isoformat(),
               "next_review": resolved_next, "files": {rel(root, path): hashlib.sha256(data).hexdigest() if data is not None else None for path, data in inputs.items()}}
    expected = hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    for path, captured_bytes in inputs.items():
        if (path.read_bytes() if path.exists() else None) != captured_bytes:
            raise ValueError("approval inputs changed during preview; retry")
    changes = []
    for path, after in updates.items():
        before = (inputs[path] or b"").decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        relative = rel(root, path)
        diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=relative, tofile=relative))
        changes.append({"path": relative, "diff": diff})
    return {"contract_version": 1, "applied": False, "expected_sha256": expected,
            "snapshot_sha256": snapshot, "record_sha256": record_hash, "target": target_rel,
            "decision_id": decision_id, "reviewer": reviewer, "date": today.isoformat(),
            "next_review": resolved_next, "reviewed_record": target_before,
            "evidence": [{"id": eid, **active[eid]} for eid in sorted(cited)],
            "changes": changes}, updates, inputs


def _approve_legacy_decision(root: Path, target: str, *, reviewer: str, today: dt.date | None = None,
                     next_review: str | None = None, write: bool = False, expected_sha256: str | None = None) -> dict:
    """Preview a reviewable diff; apply only the same snapshot under the vault lock."""
    today = today or dt.date.today()
    if write and (expected_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        raise ValueError("--write requires --expect-hash from a reviewed preview")
    if not write:
        return _approval_plan(root, target, reviewer, today, next_review)[0]
    with vault_mutation_lock(root):
        plan, updates, inputs = _approval_plan(root, target, reviewer, today, next_review)
        if plan["expected_sha256"] != expected_sha256:
            raise ValueError("approval preview is stale or its parameters changed; review a fresh preview")
        for path, before in inputs.items():
            if (path.read_bytes() if path.exists() else None) != before:
                raise ValueError("approval inputs changed; review a fresh preview")
        apply_transaction(root, updates)
        return {**plan, "applied": True}


def approve_record(root: Path, target: str, *, reviewer: str, today: dt.date | None = None,
                   next_review: str | None = None, write: bool = False, expected_sha256: str | None = None) -> dict:
    scope = vault_mutation_lock(root) if write else vault_read_lock(root)
    with scope:
        from .claim_review import bound_review, resolve_record
        note = resolve_record(VaultIndex.load(root), target)
        if note is not None and (note.front.get("type") == "claim" or note.front.get("claim_ids")):
            return bound_review(root, target, reviewer=reviewer, today=today or dt.date.today(), next_review=next_review,
                                outcome="approved", write=write, expected_sha256=expected_sha256)
        result = _approve_legacy_decision(root, target, reviewer=reviewer, today=today, next_review=next_review,
                                          write=write, expected_sha256=expected_sha256)
        from .claim_readers import report_version
        result["contract_version"] = report_version(root)
        return result


def approve_decision(root: Path, target: str, *, reviewer: str, today: dt.date | None = None,
                     next_review: str | None = None, write: bool = False, expected_sha256: str | None = None) -> dict:
    return approve_record(root, target, reviewer=reviewer, today=today, next_review=next_review,
                          write=write, expected_sha256=expected_sha256)


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


@consistent_read
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
    write: bool | None = None,
    expected_sha256: str | None = None,
) -> dict:
    from .claim_review import bound_review, resolve_record
    target_note = resolve_record(VaultIndex.load(root), target)
    if target_note is not None and (target_note.front.get("type") == "claim" or target_note.front.get("claim_ids")):
        return bound_review(root, target, reviewer=reviewer, today=today or dt.date.today(), next_review=next_review,
                            outcome=outcome, note_text=note_text, write=bool(write), expected_sha256=expected_sha256)
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

        target_link = f"[[{strip_markdown_suffix(rel(root, note.path))}]]"
        updated_log = _append_review_row(log_before, (
            today.isoformat(), target_link, reviewer, outcome, previous_review, resolved_next or "—", note_text or "—"
        ), today)

        updates: dict = {log: updated_log}
        if outcome == "confirmed" and resolved_next:
            updated_target = _frontmatter_replace(target_before, "review_by", resolved_next)
            updated_target = _touch_last_updated(updated_target, today)
            updates[note_path] = updated_target
        apply_transaction(root, updates)

        return {
            "contract_version": 2 if load_config(root)[0].get("claims") == {"format_version": 1} else 1,
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
    record.add_argument("--write", action="store_true", default=None)
    record.add_argument("--expect-hash", dest="expected_sha256")
    record.add_argument("--json", action="store_true")

    approval = sub.add_parser("approve", help="preview an approval; apply only the reviewed snapshot")
    approval.add_argument("target", help="D-NNN, path, stem or alias")
    approval.add_argument("--reviewer", required=True)
    approval.add_argument("--next-review")
    approval.add_argument("--today")
    approval.add_argument("--write", action="store_true")
    approval.add_argument("--expect-hash", dest="expected_sha256")
    approval.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)

    if args.review_command == "approve":
        today = _parse_date(args.today) if args.today else dt.date.today()
        if today is None:
            return emit_error("invalid_argument", "--today must be a real YYYY-MM-DD", json_mode=args.json)
        try:
            result = approve_decision(root, args.target, reviewer=args.reviewer, today=today,
                                      next_review=args.next_review, write=args.write,
                                      expected_sha256=args.expected_sha256)
        except ConfigError as exc:
            return emit_error("invalid_config", str(exc), json_mode=args.json)
        except TargetNotFound as exc:
            return emit_error("not_found", str(exc), json_mode=args.json)
        except ValueError as exc:
            return emit_error("operation_rejected", str(exc), json_mode=args.json)
        except OSError as exc:
            return emit_error("io_error", describe_os_error(exc), json_mode=args.json)
        if args.json:
            emit_machine(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"{'approved' if result['applied'] else 'approval preview'}: {one_line(result['target'])}")
            print(f"reviewer: {one_line(result['reviewer'])}; next review: {result['next_review']}")
            print(f"snapshot: {result['snapshot_sha256']}")
            print("reviewed record:")
            for line in result["reviewed_record"].splitlines():
                print(one_line(line))
            print("cited evidence:")
            for evidence in result["evidence"]:
                print(one_line(f"{evidence['id']}: {evidence['source']} | {evidence['location']} | {evidence['claims']}"))
            for change in result["changes"]:
                for line in change["diff"].splitlines():
                    print(one_line(line))
            if not result["applied"]:
                print(f"review this snapshot, then pass --write --expect-hash {result['expected_sha256']} with the same parameters")
        return 0

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
        payload = {"contract_version": 2 if config.get("claims") == {"format_version": 1} else 1, "due_days": due_days, "count": len(queue), "reviews": queue}
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
            note_text=args.note_text, next_review=args.next_review, today=today, write=args.write, expected_sha256=args.expected_sha256,
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
