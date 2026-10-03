"""Inbound link discovery for documents, decisions and evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import vault_not_found
from .graph import build_graph
from .lint import (
    path_is_file,
    path_cache,
    strip_markdown_suffix,
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    find_vault_root,
    is_vault_root,
)
from .vault_index import VaultIndex
from .console import emit_machine


def _normalize_target(root: Path, target: str, vault: VaultIndex) -> tuple[str, str]:
    """Return (kind, graph_id) for a CLI target.

    Documents resolve exactly as wikilinks do, so a bare stem or an alias finds
    the note wherever it lives instead of being looked up as a root-level path.
    """
    raw = target.strip()
    if EVIDENCE_ID_RE.fullmatch(raw):
        return "evidence", f"evidence:{raw}"
    if DECISION_ID_RE.fullmatch(raw):
        for note in vault.notes:
            if str(note.front.get("decision_id") or "").strip() == raw:
                return "decision", strip_markdown_suffix(vault.relative(note.path))
        return "decision", raw
    try:
        path = Path(raw).expanduser()
    except RuntimeError:  # `~name` for a user that does not exist
        path = Path(raw)
    if path.is_absolute():
        if path.suffix.lower() == ".md" and path_is_file(path) and vault.note_for(path) is not None:
            return "document", strip_markdown_suffix(vault.relative(path))
        return "document", strip_markdown_suffix(raw)
    resolved, ambiguous = vault.resolve_link(raw)
    if resolved is not None and not ambiguous:
        return "document", strip_markdown_suffix(vault.relative(resolved))
    return "document", strip_markdown_suffix(raw)


@path_cache()
def build_backlinks(root: Path, target: str, *, vault: VaultIndex | None = None) -> dict:
    root = root.resolve()
    vault = vault or VaultIndex.load(root)
    kind, node_id = _normalize_target(root, target, vault)
    graph = build_graph(root, vault=vault)
    known = {node["id"] for node in graph["nodes"]}
    exists = node_id in known
    inbound = [
        {
            "from": edge["from"],
            "type": edge["type"],
            "to": edge["to"],
        }
        for edge in graph["edges"]
        if edge["to"] == node_id
    ]
    inbound.sort(key=lambda item: (item["type"], item["from"]))
    return {
        "contract_version": 1,
        "target": target,
        "id": node_id,
        "kind": kind,
        "exists": exists,
        "backlinks": inbound,
        "count": len(inbound),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit backlinks", description="List inbound links to a note, decision or evidence ID.")
    parser.add_argument("target", help="path, stem, D-NNN or E-NNN")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)
    report = build_backlinks(root, args.target)
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if not report["exists"]:
            print(f"target not found in graph: {report['id']}", file=sys.stderr)
            return 1
        print(f"backlinks for {report['id']} ({report['count']})")
        for item in report["backlinks"]:
            print(f"  {item['type']:<10} {item['from']}")
    return 0 if report["exists"] else 1
