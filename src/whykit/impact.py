"""Explain the reverse-dependency/blast radius of a WhyKit record."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .contract import vault_not_found
from .graph import build_graph, document_node, wikilink_resolutions
from .lint import (
    path_cache,
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    evidence_register,
    find_vault_root,
    is_vault_root,
    rel,
)
from .vault_index import VaultIndex
from .console import emit_machine


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
    return list(note.cited_evidence)


def _decision_id(note) -> str | None:
    value = str(note.front.get("decision_id") or "").strip()
    return value if DECISION_ID_RE.fullmatch(value) else None


class _WikilinkView:
    """Wikilink adjacency between documents, with graph nodes built on demand.

    Equivalent to the ``wikilink`` edges and the node map of :func:`build_graph`,
    without materialising every node and edge dict of the whole vault (at
    20,000 notes that graph is over a hundred megabytes, while one record needs
    only its neighbours).
    """

    __slots__ = ("notes_by_id", "incoming", "outgoing", "nodes")

    def __init__(self, root: Path, vault_index: VaultIndex) -> None:
        ids: dict[Path, str] = {}

        def node_id(path: Path) -> str:
            value = ids.get(path)
            if value is None:
                value = ids[path] = vault_index.relative(path).removesuffix(".md")
            return value

        self.notes_by_id: dict[str, object] = {}
        self.incoming: dict[str, set[str]] = {}
        self.outgoing: dict[str, set[str]] = {}
        for note in vault_index.notes:
            source = node_id(note.path)
            self.notes_by_id[source] = note
            for _, resolved, ambiguous in wikilink_resolutions(note, vault_index):
                if resolved is None or ambiguous:
                    continue
                destination = node_id(resolved)
                self.incoming.setdefault(destination, set()).add(source)
                self.outgoing.setdefault(source, set()).add(destination)
        # The full graph also holds `evidence:E-NNN` nodes, which win over a
        # document whose path happens to spell the same id.  Only then is the
        # full node map needed to answer exactly as the graph does.
        self.nodes: dict[str, dict] | None = None
        if any(value.startswith("evidence:") for value in self.notes_by_id):
            graph = build_graph(root, vault=vault_index)
            self.nodes = {item["id"]: item for item in graph["nodes"]}

    def node(self, value: str) -> dict | None:
        if self.nodes is not None:
            return self.nodes.get(value)
        note = self.notes_by_id.get(value)
        return None if note is None else document_node(note, value)

    def neighbours(self, ids: list[str]) -> list[dict]:
        out = []
        for value in ids:
            node = self.node(value)
            if node is not None:
                out.append(node)
        return out


def _wikilink_view(root: Path, vault_index: VaultIndex) -> _WikilinkView:
    """The wikilink view of *vault_index*, built once per vault parse.

    The cache key carries the evidence register's stat signature, because the
    rare full-graph fallback reads the register: a register rewritten under a
    reused index is re-read.
    """
    register = root / "00-context" / "evidence-register.md"
    try:
        stat = register.stat()
        stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    key = ("impact.wikilinks", os.fspath(root), stamp)
    cached = vault_index.derived.get(key)
    if cached is None:
        cached = vault_index.derived[key] = _WikilinkView(root, vault_index)
    return cached


@path_cache()
def analyze_impact(root: Path, target: str, *, vault: VaultIndex | None = None) -> dict:
    vault_index = vault or VaultIndex.load(root)
    notes = vault_index.notes
    active, retired, _ = evidence_register(root)

    if EVIDENCE_ID_RE.fullmatch(target):
        row = active.get(target) or retired.get(target)
        references = [
            _note_summary(root, note)
            for note in notes
            if target in note.cited_evidence
            and vault_index.relative(note.path) != "00-context/evidence-register.md"
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
        node_id = vault_index.relative(note.path).removesuffix(".md")
        view = _wikilink_view(root, vault_index)
        incoming = view.neighbours(sorted(view.incoming.get(node_id, ())))
        outgoing = view.neighbours(sorted(view.outgoing.get(node_id, ())))
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

    resolved, ambiguous = vault_index.resolve_link(target)
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
    node_id = vault_index.relative(resolved).removesuffix(".md")
    view = _wikilink_view(root, vault_index)
    incoming_ids = sorted(view.incoming.get(node_id, ()))
    outgoing_ids = sorted(view.outgoing.get(node_id, ()))
    return {
        "contract_version": 1,
        "target": target,
        "kind": "document",
        "exists": True,
        "record": _note_summary(root, note) if note else {"path": rel(root, resolved)},
        "evidence": _evidence_ids(note) if note else [],
        "incoming": view.neighbours(incoming_ids),
        "outgoing": view.neighbours(outgoing_ids),
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
        return vault_not_found(args.root, json_mode=args.json)
    report = analyze_impact(root, args.target)
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _human(report)
    return 0 if report.get("exists") else 1
