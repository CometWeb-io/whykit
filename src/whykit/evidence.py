"""Evidence lifecycle operations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .contract import TargetNotFound, describe_os_error, emit_error, vault_not_found
from .io import atomic_write_text, safe_vault_target, vault_mutation_lock
from .impact import analyze_impact
from .lint import HISTORICAL_STATUSES, EVIDENCE_ID_RE, _split_table_row, evidence_register, find_vault_root, is_vault_root
from .scaffold import _frontmatter_replace, _table_cell, _with_evidence_sensitivity, _register_inherited_label
from .tables import evidence_table_bounds
from .console import emit_machine


def list_evidence(root: Path, *, state: str = "all") -> dict:
    active, retired, _ = evidence_register(root)
    items: list[dict] = []
    if state in {"all", "active"}:
        for evidence_id, row in active.items():
            items.append({"id": evidence_id, "state": "active", **row})
    if state in {"all", "retired"}:
        for evidence_id, row in retired.items():
            items.append({"id": evidence_id, "state": "retired", **row})
    items.sort(key=lambda item: item["id"])
    return {"contract_version": 1, "state": state, "count": len(items), "evidence": items}


def retire_evidence(
    root: Path,
    evidence_id: str,
    *,
    reason: str,
    replaced_by: str | None = None,
    today: dt.date | None = None,
) -> dict:
    today = today or dt.date.today()
    if not EVIDENCE_ID_RE.fullmatch(evidence_id):
        raise ValueError("evidence ID must be E-NNN")
    reason = reason.strip()
    if not reason:
        raise ValueError("--why must explain why the evidence is being retired")

    with vault_mutation_lock(root):
        active, retired, occurrences = evidence_register(root)
        if evidence_id in retired:
            raise ValueError(f"{evidence_id} is already retired")
        row = active.get(evidence_id)
        if row is None:
            raise TargetNotFound(f"unknown active evidence: {evidence_id}")
        if sum(key == evidence_id for key, _ in occurrences) != 1:
            raise ValueError("evidence ID must identify exactly one populated row")
        if replaced_by:
            if not EVIDENCE_ID_RE.fullmatch(replaced_by):
                raise ValueError("--replaced-by must be an E-NNN identifier")
            if replaced_by == evidence_id:
                raise ValueError("evidence cannot replace itself")
            if replaced_by not in active:
                raise ValueError(f"replacement evidence must exist and be active: {replaced_by}")

        impact = analyze_impact(root, evidence_id)

        path = safe_vault_target(root, "00-context/evidence-register.md", create_parents=False)
        original = path.read_text(encoding="utf-8")
        inherited = _register_inherited_label(path, original)
        if "sensitivity" in row:
            original = _with_evidence_sensitivity(original, "retired", inherited)
        lines = original.splitlines()
        active_header, active_end, _ = evidence_table_bounds(lines, "active")
        active_start = active_header + 2
        row_idx = None
        source = str(row.get("source") or "")
        for idx in range(active_start, active_end):
            cells = _split_table_row(lines[idx])
            if cells and cells[0] == evidence_id:
                row_idx = idx
                if len(cells) > 1:
                    source = cells[1]
                break
        if row_idx is None:
            raise ValueError(f"could not locate active row for {evidence_id}")

        del lines[row_idx]
        # Recompute retired bounds after deletion because line indexes shifted.
        _, retired_end, labeled = evidence_table_bounds(lines, "retired")
        values: tuple[str, ...] = (
            evidence_id,
            source,
            today.isoformat(),
            reason,
            replaced_by or "—",
        )
        if labeled:
            values += (row.get("sensitivity", inherited),)
        retired_row = "| " + " | ".join(_table_cell(value) for value in values) + " |"
        lines.insert(retired_end, retired_row)
        updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        updated = _frontmatter_replace(updated, "last_updated", today.isoformat())
        atomic_write_text(path, updated)

        return {
            "contract_version": 1,
            "id": evidence_id,
            "state": "retired",
            "retired_on": today.isoformat(),
            "why": reason,
            "replaced_by": replaced_by,
            "references": impact.get("reference_count", 0),
            # Superseded and archived records cite what was believed at the
            # time; only the remainder can still need a new citation.
            "current_references": sum(
                1 for item in impact.get("references", [])
                if str(item.get("status") or "") not in HISTORICAL_STATUSES
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit evidence", description="Inspect and manage evidence lifecycle.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    sub = parser.add_subparsers(dest="evidence_command", required=True)

    listing = sub.add_parser("list", help="list active/retired evidence")
    listing.add_argument("--state", choices=("all", "active", "retired"), default="all")
    listing.add_argument("--json", action="store_true")

    retire = sub.add_parser("retire", help="move active evidence to the retired ledger")
    retire.add_argument("id")
    retire.add_argument("--why", required=True, dest="reason")
    retire.add_argument("--replaced-by")
    retire.add_argument("--today")
    retire.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)

    if args.evidence_command == "list":
        payload = list_evidence(root, state=args.state)
        if args.json:
            emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload["evidence"]:
                replacement = f" -> {item.get('replaced_by')}" if item.get("replaced_by") else ""
                print(f"{item['id']}  {item['state']:<7}  {item.get('source') or '—'}{replacement}")
            print(f"\n{payload['count']} evidence item(s)")
        return 0

    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    try:
        payload = retire_evidence(root, args.id, reason=args.reason, replaced_by=args.replaced_by, today=today)
    except TargetNotFound as exc:
        return emit_error("not_found", str(exc), json_mode=args.json)
    except ValueError as exc:
        return emit_error("operation_rejected", str(exc), json_mode=args.json)
    except OSError as exc:
        return emit_error("io_error", describe_os_error(exc), json_mode=args.json)
    if args.json:
        emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        replacement = f"; replacement {payload['replaced_by']}" if payload.get("replaced_by") else ""
        historical = payload["references"] - payload["current_references"]
        print(
            f"retired {payload['id']}{replacement}; "
            f"{payload['current_references']} current and {historical} historical reference(s)"
        )
        if payload["current_references"]:
            print(f"next: `whykit impact {payload['id']}` lists what still cites it")
    return 0
