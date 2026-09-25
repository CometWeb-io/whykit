"""Human and machine-readable vault status."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import ConfigError, load_config
from .lint import evidence_register, find_vault_root, is_vault_root, lint, rel
from .vault_index import VaultIndex


def build_status(root: Path, *, today: dt.date | None = None, due_days: int = 30) -> dict:
    today = today or dt.date.today()
    root = root.resolve()
    vault = VaultIndex.load(root)
    notes = vault.notes
    _, findings = lint(root, today=today, vault=vault)
    active_evidence, retired_evidence, _ = evidence_register(root)

    canonical = [n for n in notes if n.front.get("source_of_truth") is True and n.front.get("status") == "approved"]
    decisions = [n for n in notes if n.front.get("type") == "decision" and n.front.get("decision_id")]
    review_queue: list[dict] = []
    horizon = today + dt.timedelta(days=max(0, due_days))
    for note in notes:
        if note.front.get("status") != "approved":
            continue
        raw = str(note.front.get("review_by", "")).strip()
        try:
            date = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        if date <= horizon:
            review_queue.append({
                "path": rel(root, note.path),
                "title": str(note.front.get("title") or note.path.stem),
                "owner": str(note.front.get("owner") or ""),
                "review_by": date.isoformat(),
                "days": (date - today).days,
                "state": "overdue" if date < today else "due",
            })
    review_queue.sort(key=lambda item: (item["review_by"], item["path"]))
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
        if note.front.get("type") == "decision":
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
            print(f"--today is not a real ISO date: {args.today}", file=sys.stderr)
            return 2
    else:
        today = dt.date.today()
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    try:
        config, _ = load_config(root)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    due_days = args.due_days if args.due_days is not None else int(config["defaults"]["status_due_days"])
    if due_days < 0:
        print("--due-days must be >= 0", file=sys.stderr)
        return 2
    report = build_status(root, today=today, due_days=due_days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
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


if __name__ == "__main__":
    raise SystemExit(main())
