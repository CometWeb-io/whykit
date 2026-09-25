"""Evidence lifecycle operations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .io import atomic_write_text, safe_vault_target, vault_mutation_lock
from .impact import analyze_impact
from .lint import EVIDENCE_ID_RE, _split_table_row, evidence_register, find_vault_root, is_vault_root
from .scaffold import _frontmatter_replace, _table_cell


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


def _find_table(lines: list[str], header: str) -> tuple[int, int]:
    header_idx = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if header_idx is None or header_idx + 1 >= len(lines):
        raise ValueError(f"expected table header: {header}")
    end = header_idx + 2
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return header_idx, end


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
        active, retired, _ = evidence_register(root)
        if evidence_id in retired:
            raise ValueError(f"{evidence_id} is already retired")
        row = active.get(evidence_id)
        if row is None:
            raise ValueError(f"unknown active evidence: {evidence_id}")
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
        lines = original.splitlines()
        active_header = "| ID | Source | Type | Date | Accessed | Location | Claims it supports |"
        retired_header = "| ID | Source | Retired on | Why | Replaced by |"
        _, active_end = _find_table(lines, active_header)
        active_start = next(i for i, line in enumerate(lines) if line.strip() == active_header) + 2
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
        _, retired_end = _find_table(lines, retired_header)
        retired_row = "| " + " | ".join(_table_cell(value) for value in (
            evidence_id,
            source,
            today.isoformat(),
            reason,
            replaced_by or "—",
        )) + " |"
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
        print("no WhyKit vault found", file=sys.stderr)
        return 2

    if args.evidence_command == "list":
        payload = list_evidence(root, state=args.state)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
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
            print(f"--today is not a real ISO date: {args.today}", file=sys.stderr)
            return 2
    try:
        payload = retire_evidence(root, args.id, reason=args.reason, replaced_by=args.replaced_by, today=today)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        replacement = f"; replacement {payload['replaced_by']}" if payload.get("replaced_by") else ""
        print(f"retired {payload['id']}; {payload['references']} current reference(s){replacement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
