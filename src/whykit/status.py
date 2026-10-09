"""Human and machine-readable vault status."""
from __future__ import annotations

from .io import consistent_read, vault_read_lock

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from .claim_readers import claim_reader
from .contract import ERROR_EXIT_CODES, emit_error, error_payload, vault_not_found
from .config import ConfigError, load_config
from .lint import DECISION_ID_RE, evidence_register, find_vault_root, is_vault_root, lint, path_cache
from .vault_index import VaultIndex
from .console import emit_machine


def is_decision_record(front: dict) -> bool:
    """True for a decision record with a real D-NNN.

    The decision log and the decision template share `type: decision` but are
    not decisions; counting them made `status` disagree with `trace` and the log.
    """
    return (
        front.get("type") == "decision"
        and front.get("status") != "template"
        and DECISION_ID_RE.fullmatch(str(front.get("decision_id") or "").strip()) is not None
    )


def build_review_queue(vault: VaultIndex, *, today: dt.date, due_days: int = 30) -> list[dict]:
    """Approved records whose `review_by` falls on or before today + due_days.

    Independent of lint findings, so `review list` does not pay for a full lint.
    """
    review_queue: list[dict] = []
    horizon = today + dt.timedelta(days=max(0, due_days))
    for note in vault.notes:
        if note.front.get("status") != "approved":
            continue
        raw = str(note.front.get("review_by", "")).strip()
        try:
            date = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        if date <= horizon:
            review_queue.append({
                "path": vault.relative(note.path),
                "title": str(note.front.get("title") or note.path.stem),
                "owner": str(note.front.get("owner") or ""),
                "review_by": date.isoformat(),
                "days": (date - today).days,
                "state": "overdue" if date < today else "due",
            })
    from .claim_readers import claim_view, assessments
    from .claim_review import decision_claim_review_current
    view = claim_view(vault)
    if view is not None:
        states = assessments(vault, today=today)
        queue_by_path = {item["path"]:item for item in review_queue}
        for note in vault.notes:
            cid = note.front.get("claim_id")
            ids = note.front.get("claim_ids", [])
            needs = cid in states and states[cid]["verification_status"] == "unknown" or bool(ids) and not decision_claim_review_current({**view, "root": vault.root}, note, today=today)
            path = vault.relative(note.path)
            if needs and note.front.get("status") == "approved":
                queue_by_path[path] = {"path":path,"title":str(note.front.get("title") or note.path.stem),"owner":str(note.front.get("owner") or ""),
                                       "review_by":str(note.front.get("review_by") or today.isoformat()),"days":0,"state":"requires_review"}
            if path in queue_by_path:
                queue_by_path[path].update(record_kind="claim" if cid else "decision" if note.front.get("decision_id") else "document",
                                          claim_id=cid,decision_id=note.front.get("decision_id"))
        review_queue = list(queue_by_path.values())
    review_queue.sort(key=lambda item: (item["review_by"], item["path"]))
    return review_queue


@consistent_read
@path_cache()
@claim_reader("status")
def build_status(
    root: Path,
    *,
    today: dt.date | None = None,
    due_days: int = 30,
    vault: VaultIndex | None = None,
) -> dict:
    today = today or dt.date.today()
    root = root.resolve()
    vault = vault or VaultIndex.load(root)
    notes = vault.notes
    _, findings = lint(root, today=today, vault=vault)
    active_evidence, retired_evidence, _ = evidence_register(root)

    canonical = [n for n in notes if n.front.get("source_of_truth") is True and n.front.get("status") == "approved"]
    decisions = [n for n in notes if is_decision_record(n.front)]
    review_queue = build_review_queue(vault, today=today, due_days=due_days)
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    decision_states: dict[str, int] = {}
    document_states: dict[str, int] = {}
    sensitivity_counts: dict[str, int] = {}
    ownership_gaps = 0
    for note in notes:
        status_value = str(note.front.get("status") or "")
        if status_value:
            document_states[status_value] = document_states.get(status_value, 0) + 1
        sensitivity = str(note.front.get("sensitivity") or "")
        if sensitivity:
            sensitivity_counts[sensitivity] = sensitivity_counts.get(sensitivity, 0) + 1
        if is_decision_record(note.front):
            decision_states[status_value or "unknown"] = decision_states.get(status_value or "unknown", 0) + 1
        if status_value == "approved" and str(note.front.get("owner") or "").strip() in {"", "TODO"}:
            ownership_gaps += 1
    by_code: dict[str, int] = {}
    for finding in findings:
        by_code[finding.code] = by_code.get(finding.code, 0) + 1

    return {
        "contract_version": 1,
        "root": str(root),
        "as_of": today.isoformat(),
        "documents": len(notes),
        "canonical": len(canonical),
        "decisions": len(decisions),
        "evidence_active": len(active_evidence),
        "evidence_retired": len(retired_evidence),
        "errors": len(errors),
        "warnings": len(warnings),
        "finding_codes": dict(sorted(by_code.items())),
        "review_queue": review_queue,
        "review_overdue": sum(1 for item in review_queue if item["state"] == "overdue"),
        "decision_states": dict(sorted(decision_states.items())),
        "document_states": dict(sorted(document_states.items())),
        "sensitivity": dict(sorted(sensitivity_counts.items())),
        "approved_without_owner": ownership_gaps,
        "findings": [asdict(f) for f in findings],
    }


def build_workspace_status(
    roots: list[Path], *, today: dt.date | None = None,
    due_days: int | None = None, strict: bool = False,
) -> dict:
    """Read independent vaults; one failure never hides the remaining results."""
    if not roots or (due_days is not None and due_days < 0):
        raise ValueError("supply vault roots and a non-negative review window")
    today = today or dt.date.today()
    entries = []
    seen: set[Path] = set()
    vault_roots: set[Path] = set()
    identities: set[tuple[int, int]] = set()
    for requested in roots:
        entry: dict = {"root": str(requested)}
        try:
            root = requested.expanduser().resolve()
            if root in seen:
                continue
            seen.add(root)
            entry["root"] = str(root)
            if is_vault_root(root):
                stat = root.stat()
                identity = (stat.st_dev, stat.st_ino)
                if identity in identities:
                    continue
                identities.add(identity)
                vault_roots.add(root)
            else:
                entry["error"] = error_payload("vault_not_found", "no WhyKit vault at this root")["error"]
        except (OSError, ValueError, RuntimeError):
            entry["error"] = error_payload("io_error", "vault root could not be resolved")["error"]
        entries.append(entry)
    for entry in entries:
        try:
            root = Path(entry["root"])
            if "error" in entry:
                pass
            elif any(other != root and (
                any(parent.samefile(other) for parent in root.parents)
                or any(parent.samefile(root) for parent in other.parents)
            ) for other in vault_roots):
                entry["error"] = error_payload("invalid_target", "overlapping vault roots must be checked separately")["error"]
            else:
                with vault_read_lock(root):
                    config, _ = load_config(root)
                    window = due_days if due_days is not None else int(config["defaults"]["status_due_days"])
                    status = build_status(root, today=today, due_days=window)
                    entry.update(status=status, due_days=window,
                                 exit_code=int(bool(status["errors"] or (strict and status["warnings"]))))
        except ConfigError as exc:
            entry["error"] = error_payload("invalid_config", str(exc))["error"]
        except (OSError, ValueError):
            entry["error"] = error_payload("io_error", "vault could not be read")["error"]
        except Exception:  # noqa: BLE001 - isolate a failing vault, but never report it healthy
            entry["error"] = error_payload("internal_error", "vault status could not be computed")["error"]
        if "error" in entry:
            entry["exit_code"] = ERROR_EXIT_CODES[entry["error"]["code"]]
    codes = {entry["exit_code"] for entry in entries}
    return {"contract_version": 1, "as_of": today.isoformat(), "vaults": entries,
            "exit_code": next((code for code in (70, 2, 1) if code in codes), 0)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit status", description="Summarize vault health and upcoming review work.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--today", help="evaluate as of this ISO date")
    parser.add_argument("--due-days", type=int, help="include reviews due within N days (default: policy setting)")
    parser.add_argument("--strict", action="store_true", help="exit 1 when warnings exist as well as errors")
    args = parser.parse_args(argv)

    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    else:
        today = dt.date.today()
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)
    try:
        config, _ = load_config(root)
    except ConfigError as exc:
        return emit_error("invalid_config", str(exc), json_mode=args.json)
    due_days = args.due_days if args.due_days is not None else int(config["defaults"]["status_due_days"])
    if due_days < 0:
        return emit_error("invalid_argument", "--due-days must be >= 0", json_mode=args.json)
    report = build_status(root, today=today, due_days=due_days)
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"WhyKit status — {report['as_of']}")
        print(f"  documents       {report['documents']}")
        print(f"  canonical       {report['canonical']}")
        print(f"  decisions       {report['decisions']}")
        print(f"  evidence        {report['evidence_active']} active / {report['evidence_retired']} retired")
        print(f"  lint            {report['errors']} error(s), {report['warnings']} warning(s)")
        queue = report["review_queue"]
        print(f"  review queue    {len(queue)} due within {due_days} day(s); {report['review_overdue']} overdue")
        for item in queue[:20]:
            marker = "OVERDUE" if item["state"] == "overdue" else "DUE"
            print(f"    {marker:<7} {item['review_by']}  {item['path']}  ({item['owner'] or 'no owner'})")
        if len(queue) > 20:
            print(f"    … {len(queue) - 20} more")
    if report["errors"] or (args.strict and report["warnings"]):
        return 1
    return 0
