"""Explain the reverse-dependency/blast radius of a WhyKit record."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import build_graph
from .lint import (
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    _resolve,
    evidence_register,
    find_vault_root,
    is_vault_root,
    rel,
)
from .vault_index import VaultIndex


def _note_summary(root: Path, note) -> dict:
    return {
        "path": rel(root, note.path),
        "title": str(note.front.get("title") or note.path.stem),
        "type": str(note.front.get("type") or ""),
        "status": str(note.front.get("status") or ""),
        "owner": str(note.front.get("owner") or ""),
        "sensitivity": str(note.front.get("sensitivity") or "internal"),
        "source_of_truth": note.front.get("source_of_truth") is True,
    }


def _evidence_ids(note) -> list[str]:
    return sorted(set(EVIDENCE_ID_RE.findall(note.text)))


def _decision_id(note) -> str | None:
    value = str(note.front.get("decision_id") or "").strip()
    return value if DECISION_ID_RE.fullmatch(value) else None


def analyze_impact(root: Path, target: str, *, vault: VaultIndex | None = None) -> dict:
    vault_index = vault or VaultIndex.load(root)
    notes = vault_index.notes
    active, retired, _ = evidence_register(root)
    graph = build_graph(root, vault=vault_index)

    if EVIDENCE_ID_RE.fullmatch(target):
        row = active.get(target) or retired.get(target)
        references = [
            _note_summary(root, note)
            for note in notes
            if target in set(EVIDENCE_ID_RE.findall(note.text))
            and rel(root, note.path) != "00-context/evidence-register.md"
        ]
        references.sort(key=lambda item: item["path"])
        replacement = row.get("replaced_by", "") if row and target in retired else ""
        return {
            "contract_version": 1,
            "target": target,
            "kind": "evidence",
            "exists": row is not None,
            "state": "active" if target in active else "retired" if target in retired else "missing",
            "record": row,
            "replacement": replacement or None,
            "references": references,
            "reference_count": len(references),
        }

    if DECISION_ID_RE.fullmatch(target):
        matches = [note for note in notes if _decision_id(note) == target]
        note = matches[0] if len(matches) == 1 else None
        if note is None:
            return {
                "contract_version": 1,
                "target": target,
                "kind": "decision",
                "exists": False,
                "ambiguous": len(matches) > 1,
                "references": [],
                "reference_count": 0,
            }
        node_id = rel(root, note.path).removesuffix(".md")
        incoming_ids = sorted({edge["from"] for edge in graph["edges"] if edge["to"] == node_id and edge.get("type", "wikilink") == "wikilink"})
        outgoing_ids = sorted({edge["to"] for edge in graph["edges"] if edge["from"] == node_id and edge.get("type", "wikilink") == "wikilink"})
        by_node = {item["id"]: item for item in graph["nodes"]}
        incoming = [by_node[value] for value in incoming_ids if value in by_node]
        outgoing = [by_node[value] for value in outgoing_ids if value in by_node]
        superseded_by = sorted(
            [_note_summary(root, item) | {"decision_id": _decision_id(item)} for item in notes if str(item.front.get("supersedes") or "").strip() == target],
            key=lambda item: item["path"],
        )
        evidence = _evidence_ids(note)
        return {
            "contract_version": 1,
            "target": target,
            "kind": "decision",
            "exists": True,
            "record": _note_summary(root, note),
            "supersedes": str(note.front.get("supersedes") or "").strip() or None,
            "superseded_by": superseded_by,
            "evidence": evidence,
            "incoming": incoming,
            "outgoing": outgoing,
            "reference_count": len(incoming),
        }

    resolved, ambiguous = _resolve(root, target, vault_index.link_index)
    if resolved is None:
        return {
            "contract_version": 1,
            "target": target,
            "kind": "document",
            "exists": False,
            "ambiguous": ambiguous,
            "incoming": [],
            "outgoing": [],
            "reference_count": 0,
        }
    note = vault_index.note_for(resolved)
    node_id = rel(root, resolved).removesuffix(".md")
    by_node = {item["id"]: item for item in graph["nodes"]}
    incoming_ids = sorted({edge["from"] for edge in graph["edges"] if edge["to"] == node_id and edge.get("type", "wikilink") == "wikilink"})
    outgoing_ids = sorted({edge["to"] for edge in graph["edges"] if edge["from"] == node_id and edge.get("type", "wikilink") == "wikilink"})
    return {
        "contract_version": 1,
        "target": target,
        "kind": "document",
        "exists": True,
        "record": _note_summary(root, note) if note else {"path": rel(root, resolved)},
        "evidence": _evidence_ids(note) if note else [],
        "incoming": [by_node[value] for value in incoming_ids if value in by_node],
        "outgoing": [by_node[value] for value in outgoing_ids if value in by_node],
        "reference_count": len(incoming_ids),
    }


def _human(report: dict) -> None:
    target = report["target"]
    if not report.get("exists"):
        suffix = " (ambiguous)" if report.get("ambiguous") else ""
        print(f"{target}: not found{suffix}")
        return
    print(f"Impact for {target} [{report['kind']}]")
    if report["kind"] == "evidence":
        print(f"  state          {report['state']}")
        print(f"  references     {report['reference_count']}")
        if report.get("replacement"):
            print(f"  replacement    {report['replacement']}")
        for item in report["references"][:30]:
            print(f"    {item['path']}  ({item['status'] or 'no status'}, {item['owner'] or 'no owner'})")
        return
    record = report.get("record") or {}
    print(f"  document       {record.get('path', '—')}")
    print(f"  backlinks      {report.get('reference_count', 0)}")
    evidence = report.get("evidence") or []
    if evidence:
        print(f"  evidence       {', '.join(evidence)}")
    if report.get("supersedes"):
        print(f"  supersedes     {report['supersedes']}")
    if report.get("superseded_by"):
        print("  superseded by")
        for item in report["superseded_by"]:
            print(f"    {item.get('decision_id') or '—'}  {item['path']}")
    if report.get("incoming"):
        print("  referenced by")
        for item in report["incoming"][:30]:
            print(f"    {item['id']}  ({item.get('status') or 'no status'})")
    if report.get("outgoing"):
        print("  links to")
        for item in report["outgoing"][:30]:
            print(f"    {item['id']}  ({item.get('status') or 'no status'})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit impact",
        description="Show what depends on an evidence ID, decision ID or vault document before you change it.",
    )
    parser.add_argument("target", help="E-NNN, D-NNN, path, stem or alias")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    report = analyze_impact(root, args.target)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _human(report)
    return 0 if report.get("exists") else 1


if __name__ == "__main__":
    raise SystemExit(main())
