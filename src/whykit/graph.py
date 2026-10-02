"""Export the vault knowledge graph for agents, CI and visual tooling.

The graph is intentionally typed.  Wikilinks are only one relationship:
documents can also cite evidence and decisions can supersede one another.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .messages import print_no_vault
from .lint import (
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    WIKILINK_RE,
    _mask_code,
    evidence_register,
    find_vault_root,
    is_vault_root,
    rel,
)
from .vault_index import VaultIndex


def build_graph(
    root: Path,
    *,
    canonical_only: bool = False,
    vault: VaultIndex | None = None,
) -> dict:
    root = root.resolve()
    vault_index = vault or VaultIndex.load(root)
    notes = vault_index.notes
    selected = [
        note for note in notes
        if not canonical_only or (note.front.get("source_of_truth") is True and note.front.get("status") == "approved")
    ]
    nodes: list[dict] = []
    edges: list[dict] = []
    unresolved: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    path_by_decision: dict[str, str] = {}
    duplicate_decisions: set[str] = set()

    def node_id(path: Path) -> str:
        return vault_index.relative(path).removesuffix(".md")

    for note in notes:
        value = str(note.front.get("decision_id") or "").strip()
        if DECISION_ID_RE.fullmatch(value):
            if value in path_by_decision:
                duplicate_decisions.add(value)
            path_by_decision[value] = node_id(note.path)
    selected_ids = {node_id(note.path) for note in selected}

    def add_edge(source: str, destination: str, edge_type: str) -> None:
        key = (source, destination, edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append({"from": source, "to": destination, "type": edge_type})

    for note in selected:
        source = node_id(note.path)
        nodes.append({
            "id": source,
            "kind": "document",
            "title": str(note.front.get("title") or note.path.stem),
            "type": str(note.front.get("type") or ""),
            "status": str(note.front.get("status") or ""),
            "owner": str(note.front.get("owner") or ""),
            "sensitivity": str(note.front.get("sensitivity") or ""),
            "decision_id": str(note.front.get("decision_id") or "") or None,
            "source_of_truth": note.front.get("source_of_truth") is True,
        })
        for match in WIKILINK_RE.finditer(_mask_code(note.text)):
            target = match.group(1).strip()
            if not target or target.startswith(("http://", "https://")):
                continue
            resolved, ambiguous = vault_index.resolve_link(target)
            if resolved is None or ambiguous:
                unresolved.append({
                    "from": source,
                    "target": target,
                    "type": "wikilink",
                    "reason": "ambiguous" if ambiguous else "missing",
                })
                continue
            destination = node_id(resolved)
            if canonical_only and destination not in selected_ids:
                continue
            add_edge(source, destination, "wikilink")

        if source != "00-context/evidence-register":
            for evidence_id in sorted(set(EVIDENCE_ID_RE.findall(note.text))):
                add_edge(source, f"evidence:{evidence_id}", "evidence")

        supersedes = str(note.front.get("supersedes") or "").strip()
        if DECISION_ID_RE.fullmatch(supersedes):
            destination = path_by_decision.get(supersedes)
            if supersedes in duplicate_decisions:
                unresolved.append({"from": source, "target": supersedes, "type": "supersedes", "reason": "ambiguous"})
            elif destination is None:
                unresolved.append({"from": source, "target": supersedes, "type": "supersedes", "reason": "missing"})
            elif not canonical_only or destination in selected_ids:
                # A canonical-only export must not point at a node it left out.
                add_edge(source, destination, "supersedes")

    active, retired, _ = evidence_register(root)
    referenced_evidence = sorted({edge[1].split(":", 1)[1] for edge in seen_edges if edge[2] == "evidence"})
    for evidence_id in referenced_evidence:
        row = active.get(evidence_id) or retired.get(evidence_id)
        nodes.append({
            "id": f"evidence:{evidence_id}",
            "kind": "evidence",
            "evidence_id": evidence_id,
            "title": (row or {}).get("source") or evidence_id,
            "status": "active" if evidence_id in active else "retired" if evidence_id in retired else "missing",
            "type": (row or {}).get("type") or "",
            "owner": "",
            "sensitivity": "",
            "source_of_truth": False,
        })

    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["from"], e["to"], e["type"]))
    unresolved.sort(key=lambda e: (e["from"], e["target"], e.get("type", "")))
    edges_by_type: dict[str, int] = {}
    for edge in edges:
        edges_by_type[edge["type"]] = edges_by_type.get(edge["type"], 0) + 1
    return {
        "contract_version": 1,
        "nodes": nodes,
        "edges": edges,
        "unresolved": unresolved,
        "stats": {
            "nodes": len(nodes),
            "documents": sum(1 for node in nodes if node["kind"] == "document"),
            "evidence": sum(1 for node in nodes if node["kind"] == "evidence"),
            "edges": len(edges),
            "edges_by_type": dict(sorted(edges_by_type.items())),
            "unresolved": len(unresolved),
        },
    }


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def as_obsidian(graph: dict) -> dict:
    """Export a Graph-Analysis-friendly JSON view of the vault.

    Obsidian's own ``.obsidian/graph.json`` stores *view settings*, not nodes.
    This payload is a portable nodes/edges document that plugins and scripts can
    ingest; write it beside the vault (for example ``.whykit/graph.json``).
    """
    return {
        "format": "whykit.obsidian-graph/v1",
        "contract_version": 1,
        "directed": True,
        "nodes": [
            {
                "id": node["id"],
                "label": node.get("title") or node["id"],
                "kind": node.get("kind") or "document",
                "path": None if node.get("kind") == "evidence" else f"{node['id']}.md",
            }
            for node in graph["nodes"]
        ],
        "edges": [
            {
                "from": edge["from"],
                "to": edge["to"],
                "label": edge.get("type") or "wikilink",
            }
            for edge in graph["edges"]
        ],
        "stats": graph.get("stats") or {},
    }


def as_dot(graph: dict) -> str:
    lines = ["digraph whykit {", "  rankdir=LR;"]
    for node in graph["nodes"]:
        shape = "box" if node.get("kind") == "document" else "ellipse"
        lines.append(f'  "{_dot_escape(node["id"])}" [shape={shape}, label="{_dot_escape(node["title"])}"];')
    for edge in graph["edges"]:
        label = edge.get("type", "wikilink")
        lines.append(
            f'  "{_dot_escape(edge["from"])}" -> "{_dot_escape(edge["to"])}" '
            f'[label="{_dot_escape(label)}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def _mermaid_label(value: str) -> str:
    # Mermaid has no backslash escapes inside quoted labels; it uses HTML-style
    # entity codes.  Newlines would end the statement, so they become spaces.
    text = " ".join(str(value).split())
    for raw, code in (("#", "#35;"), ('"', "#quot;"), ("<", "#lt;"), (">", "#gt;")):
        text = text.replace(raw, code)
    return text


def as_mermaid(graph: dict) -> str:
    """Render the graph as a Mermaid flowchart (GitHub and Obsidian render it inline).

    Node IDs are generated (``n0``, ``n1``…) because vault paths contain
    characters Mermaid treats as syntax; the path is kept in the label.
    """
    ids = {node["id"]: f"n{index}" for index, node in enumerate(graph["nodes"])}
    lines = ["flowchart LR"]
    for node in graph["nodes"]:
        label = _mermaid_label(node.get("title") or node["id"])
        if node.get("kind") == "evidence":
            lines.append(f'  {ids[node["id"]]}(["{label}"])')
        else:
            lines.append(f'  {ids[node["id"]]}["{label}<br/><small>{_mermaid_label(node["id"])}</small>"]')
    arrows = {"wikilink": "-->", "evidence": "-.->", "supersedes": "==>"}
    for edge in graph["edges"]:
        source, target = ids.get(edge["from"]), ids.get(edge["to"])
        if source is None or target is None:
            continue
        edge_type = edge.get("type") or "wikilink"
        lines.append(f"  {source} {arrows.get(edge_type, '-->')}|{edge_type}| {target}")
    classes = {
        "evidence": [ids[n["id"]] for n in graph["nodes"] if n.get("kind") == "evidence" and n.get("status") == "active"],
        "unusable": [ids[n["id"]] for n in graph["nodes"] if n.get("kind") == "evidence" and n.get("status") != "active"],
        "decision": [ids[n["id"]] for n in graph["nodes"] if n.get("kind") == "document" and n.get("decision_id")],
    }
    styles = {
        "evidence": "stroke-dasharray: 4 2",
        "unusable": "stroke-dasharray: 4 2,stroke:#b42318,color:#b42318",
        "decision": "stroke-width:2px",
    }
    for name, members in classes.items():
        if members:
            lines.append(f"  classDef {name} {styles[name]}")
            lines.append(f"  class {','.join(members)} {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit graph", description="Export typed WhyKit relations as JSON, Graphviz DOT or Mermaid.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--format", choices=("json", "dot", "mermaid", "obsidian"), default="json")
    parser.add_argument("--canonical-only", action="store_true", help="include only approved source-of-truth documents (plus evidence they cite)")
    parser.add_argument("--output", help="write to this path (vault-relative or absolute); default: stdout")
    args = parser.parse_args(argv)
    requested_root = Path(args.root).expanduser().absolute() if args.root else find_vault_root()
    root = requested_root.resolve() if requested_root is not None else None
    if root is None or not is_vault_root(root):
        print_no_vault(args.root)
        return 2
    graph = build_graph(root, canonical_only=args.canonical_only)
    if args.format == "dot":
        rendered = as_dot(graph)
        text_mode = True
    elif args.format == "mermaid":
        rendered = as_mermaid(graph)
        text_mode = True
    elif args.format == "obsidian":
        rendered = json.dumps(as_obsidian(graph), ensure_ascii=False, indent=2)
        text_mode = True
    else:
        rendered = json.dumps(graph, ensure_ascii=False, indent=2)
        text_mode = True
    if args.output:
        from .io import atomic_write_text, safe_vault_target
        target = Path(args.output).expanduser()
        try:
            if target.is_absolute():
                try:
                    relative = target.relative_to(root)
                except ValueError:
                    relative = target.relative_to(requested_root)
            else:
                relative = target
            target = safe_vault_target(root, relative)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"--output must be a safe path inside the vault: {exc}", file=sys.stderr)
            return 2
        atomic_write_text(target, rendered + ("\n" if text_mode else ""))
        print(f"wrote {rel(root, target)}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
