"""Decision-to-evidence traceability.

`whykit impact` answers "what breaks if I change this?" for one record.  This
module answers the ledger-wide question in the other direction: for every
decision, which evidence does it rest on, and is that evidence still usable?

A decision can cite evidence directly (an ``E-NNN`` in its text or
``source_ids``) or inherit it from a non-decision document it links to, such as
a research note.  Both are reported; direct citations win when the same ID
appears twice.
Evidence is unusable when it is retired, missing from the register, or older
than the vault's ``[evidence_access_age_days]`` policy for its source type.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .contract import emit_error
from .graph import build_graph
from .lint import DECISION_ID_RE, EVIDENCE_ID_RE, _parse_date, evidence_register, find_vault_root, is_vault_root, path_cache, strip_markdown_suffix
from .vault_index import VaultIndex
from .console import emit_machine

GAP_KINDS = ("no_evidence", "missing_evidence", "retired_evidence", "stale_evidence")


def _evidence_state(
    evidence_id: str,
    active: dict[str, dict[str, str]],
    retired: dict[str, dict[str, str]],
    access_age_days: dict[str, int],
    default_max_age: int | None,
    today: dt.date,
) -> dict:
    if evidence_id in active:
        row = active[evidence_id]
        max_age = access_age_days.get(row.get("type", ""), default_max_age)
        accessed = _parse_date(row.get("accessed", ""))
        age = (today - accessed).days if accessed is not None else None
        # Same rule as the `evidence.access_stale` lint: older than the window,
        # never in the future, and an unparseable date is reported by lint.
        stale = max_age is not None and age is not None and age > max_age
        return {
            "state": "active",
            "source": row.get("source", ""),
            "type": row.get("type", ""),
            "accessed": row.get("accessed", "") or None,
            "age_days": age,
            "max_age_days": max_age,
            "stale": stale,
            "replaced_by": None,
        }
    if evidence_id in retired:
        row = retired[evidence_id]
        replacement = row.get("replaced_by", "").strip()
        return {
            "state": "retired",
            "source": row.get("source", ""),
            "type": "",
            "accessed": None,
            "age_days": None,
            "max_age_days": None,
            "stale": False,
            "replaced_by": replacement if EVIDENCE_ID_RE.fullmatch(replacement) else None,
        }
    return {
        "state": "missing",
        "source": "",
        "type": "",
        "accessed": None,
        "age_days": None,
        "max_age_days": None,
        "stale": False,
        "replaced_by": None,
    }


@path_cache()
def build_trace(
    root: Path,
    *,
    today: dt.date | None = None,
    decision: str | None = None,
    default_max_age: int | None = None,
    access_age_days: dict[str, int] | None = None,
    vault: VaultIndex | None = None,
) -> dict:
    """Return a traceability report for every decision (or one, by ``D-NNN``)."""
    today = today or dt.date.today()
    root = root.resolve()
    vault_index = vault or VaultIndex.load(root)
    if access_age_days is None:
        try:
            access_age_days = dict(load_config(root)[0]["evidence_access_age_days"])
        except ConfigError:
            access_age_days = {}
    active, retired, _ = evidence_register(root)
    graph = build_graph(root, vault=vault_index)

    cites: dict[str, set[str]] = {}
    links: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        if edge["type"] == "evidence":
            cites.setdefault(edge["from"], set()).add(edge["to"].split(":", 1)[1])
        elif edge["type"] == "wikilink":
            links.setdefault(edge["from"], set()).add(edge["to"])

    superseded_by: dict[str, list[str]] = {}
    decision_notes = []
    for note in vault_index.notes:
        own = str(note.front.get("decision_id") or "").strip()
        if not DECISION_ID_RE.fullmatch(own):
            continue
        decision_notes.append((own, note))
        replaced = str(note.front.get("supersedes") or "").strip()
        if DECISION_ID_RE.fullmatch(replaced):
            superseded_by.setdefault(replaced, []).append(own)

    decision_nodes = {strip_markdown_suffix(vault_index.relative(note.path)) for _, note in decision_notes}
    states: dict[str, dict] = {}

    def state_of(evidence_id: str) -> dict:
        if evidence_id not in states:
            states[evidence_id] = _evidence_state(evidence_id, active, retired, access_age_days, default_max_age, today)
        return states[evidence_id]

    records: list[dict] = []
    for own, note in sorted(decision_notes, key=lambda item: (item[0], vault_index.relative(item[1].path))):
        if decision and own != decision:
            continue
        node = strip_markdown_suffix(vault_index.relative(note.path))
        evidence: dict[str, dict] = {}
        for evidence_id in sorted(cites.get(node, ())):
            evidence[evidence_id] = {"id": evidence_id, "via": None, **state_of(evidence_id)}
        for linked in sorted(links.get(node, ())):
            # Another decision's evidence is that decision's case, not this
            # one's; inheriting it would let a successor hide behind the very
            # record it superseded.
            if linked == node or linked in decision_nodes:
                continue
            for evidence_id in sorted(cites.get(linked, ())):
                if evidence_id not in evidence:
                    evidence[evidence_id] = {"id": evidence_id, "via": linked, **state_of(evidence_id)}
        items = [evidence[key] for key in sorted(evidence)]
        gaps: list[str] = []
        if not items:
            gaps.append("no_evidence")
        if any(item["state"] == "missing" for item in items):
            gaps.append("missing_evidence")
        if any(item["state"] == "retired" for item in items):
            gaps.append("retired_evidence")
        if any(item["stale"] for item in items):
            gaps.append("stale_evidence")
        status = str(note.front.get("status") or "")
        successors = sorted(superseded_by.get(own, []))
        records.append({
            "decision_id": own,
            "path": vault_index.relative(note.path),
            "title": str(note.front.get("title") or note.path.stem),
            "status": status,
            "owner": str(note.front.get("owner") or ""),
            "sensitivity": str(note.front.get("sensitivity") or "internal"),
            "superseded_by": successors,
            # Only an approved decision nobody has superseded is still binding;
            # historical records may legitimately cite since-retired evidence.
            "live": status == "approved" and not successors,
            "evidence": items,
            "gaps": gaps,
        })

    live = [record for record in records if record["live"]]
    gap_counts = {kind: sum(1 for record in live if kind in record["gaps"]) for kind in GAP_KINDS}
    return {
        "contract_version": 1,
        "as_of": today.isoformat(),
        "decision": decision,
        "decisions": records,
        "summary": {
            "decisions": len(records),
            "live": len(live),
            "live_with_gaps": sum(1 for record in live if record["gaps"]),
            "gaps": gap_counts,
        },
    }


def _describe(item: dict) -> str:
    text = f"{item['id']} {item['state']}"
    if item["state"] == "retired" and item.get("replaced_by"):
        text += f" -> {item['replaced_by']}"
    if item["stale"]:
        text += f" (stale: accessed {item['accessed']}, {item['age_days']}d > {item['max_age_days']}d)"
    if item.get("via"):
        text += f"  via {item['via']}"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit trace",
        description="Trace every decision to the evidence it rests on and flag missing, retired or stale sources.",
    )
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--decision", help="trace only this D-NNN")
    parser.add_argument("--today", help="evaluate evidence age as of this ISO date")
    parser.add_argument(
        "--max-age-days", type=int,
        help="treat evidence as stale after N days when [evidence_access_age_days] has no entry for its type",
    )
    parser.add_argument("--gaps-only", action="store_true", help="list only live decisions with gaps")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any live decision has a gap")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.decision and not DECISION_ID_RE.fullmatch(args.decision):
        return emit_error("invalid_argument", "--decision must be a D-NNN identifier", json_mode=args.json)
    if args.max_age_days is not None and args.max_age_days < 0:
        return emit_error("invalid_argument", "--max-age-days must be >= 0", json_mode=args.json)
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return emit_error("vault_not_found", "no WhyKit vault found", json_mode=args.json)
    try:
        config, _ = load_config(root)
    except ConfigError as exc:
        return emit_error("invalid_config", str(exc), json_mode=args.json)
    report = build_trace(
        root,
        today=today,
        decision=args.decision,
        default_max_age=args.max_age_days,
        access_age_days=dict(config["evidence_access_age_days"]),
    )
    found = bool(report["decisions"])
    if args.gaps_only:
        report["decisions"] = [record for record in report["decisions"] if record["live"] and record["gaps"]]
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Decision traceability — {report['as_of']}")
        for record in report["decisions"]:
            state = "live" if record["live"] else (
                f"superseded by {', '.join(record['superseded_by'])}" if record["superseded_by"] else record["status"] or "no status"
            )
            gaps = f"  [{', '.join(gap.replace('_', ' ') for gap in record['gaps'])}]" if record["gaps"] else ""
            print(f"  {record['decision_id']}  {record['path']}  ({state}){gaps}")
            for item in record["evidence"]:
                print(f"      {_describe(item)}")
        summary = report["summary"]
        print(
            f"\n{summary['decisions']} decision(s), {summary['live']} live, "
            f"{summary['live_with_gaps']} live with gaps"
        )
    if args.decision and not found:
        print(f"{args.decision}: not found", file=sys.stderr)
        return 1
    if args.strict and report["summary"]["live_with_gaps"]:
        return 1
    return 0
