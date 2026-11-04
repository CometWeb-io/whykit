"""Bounded reverse dependencies, not causal impact or action authorization."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import re
from typing import Any

from .graph import document_node, wikilink_resolutions
from .io import consistent_read
from .knowledge_visibility import canonical_decision_id, visible_knowledge_index
from .lint import DECISION_ID_RE, EVIDENCE_ID_RE, path_cache
from .sensitivity import SENSITIVITY_LEVEL, classified_evidence, evidence_register_notes, is_outcome_note, note_sensitivity_level
from .vault_index import VaultIndex


class GraphConstructionLimit(ValueError):
    """An explicitly bounded graph cannot admit another node or edge."""


def _view_graph(
    root: Path, vault: VaultIndex | None, allowed_sensitivities: set[str] | None,
    *, construction_nodes: int | None = None, construction_edges: int | None = None,
) -> tuple[VaultIndex, dict[str, dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    """Build once from captured visible notes; never reopen a full register."""
    index = vault or VaultIndex.load(root)
    if index.root.resolve() != root.resolve():
        raise ValueError("vault index must belong to the requested root")
    levels = {level for name, level in SENSITIVITY_LEVEL.items()
              if allowed_sensitivities is None or name in allowed_sensitivities}
    inherited = index.derived.get("evidence_register")
    index = index.subset(lambda note: allowed_sensitivities is None or note_sensitivity_level(note) in levels)
    if inherited is None:
        active, retired, _, per_row = classified_evidence(index)
    else:
        active, retired, _ = inherited
        per_row = True
    active = {key: row for key, row in active.items() if SENSITIVITY_LEVEL.get(row["sensitivity"]) in levels}
    retired = {key: row for key, row in retired.items() if SENSITIVITY_LEVEL.get(row["sensitivity"]) in levels}
    if per_row and allowed_sensitivities is not None:
        registers = {note.path for note in evidence_register_notes(index)}
        if registers:
            index = index.subset(lambda note: note.path not in registers)
    index.derived["evidence_register"] = (active, retired, [])
    index = visible_knowledge_index(index, set(active) | set(retired))
    return _build_visible_graph(root, index, active, retired,
        construction_nodes=construction_nodes, construction_edges=construction_edges)


def _build_visible_graph(
    root: Path, index: VaultIndex, active: dict[str, dict], retired: dict[str, dict],
    *, construction_nodes: int | None = None, construction_edges: int | None = None,
) -> tuple[VaultIndex, dict[str, dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    """Build from a closed classified grant without reopening source content.

    Callers must supply only permitted rows and notes, with native dependency
    closure already applied. Existing path/link resolution semantics are retained.
    """
    nodes: dict[str, dict[str, Any]] = {}
    paths: dict[Path, str] = {}
    decisions: dict[str, list[str]] = defaultdict(list)
    canonical_decisions: dict[str, list[str]] = defaultdict(list)
    claims: dict[str, list[str]] = defaultdict(list)
    edges: set[tuple[str, str, str]] = set()
    signals: set[tuple[str, str]] = set()
    def admit_node() -> None:
        if construction_nodes is not None and len(nodes) >= construction_nodes:
            raise GraphConstructionLimit("scope_too_large")

    def add_edge(edge: tuple[str, str, str]) -> None:
        if edge not in edges:
            if construction_edges is not None and len(edges) >= construction_edges:
                raise GraphConstructionLimit("scope_too_large")
            edges.add(edge)

    for note in index.notes:
        admit_node()
        path = index.relative(note.path)
        node_id = f"document:{path}"
        paths[note.path] = node_id
        nodes[node_id] = {**document_node(note, node_id), "path": path}
        canonical = canonical_decision_id(note, root)
        if canonical is not None:
            canonical_decisions[canonical].append(node_id)
            nodes[node_id]["decision_id"] = canonical
        claim_id = note.front.get("claim_id")
        if isinstance(claim_id, str):
            claims[claim_id].append(node_id)
        decision = str(note.front.get("decision_id") or "").strip()
        if DECISION_ID_RE.fullmatch(decision):
            decisions[decision].append(node_id)
        if note.front_error:
            signals.add((node_id, "invalid_metadata"))
    for evidence_id, row in {**active, **retired}.items():
        level = SENSITIVITY_LEVEL.get(row["sensitivity"])
        if level is None:
            continue
        node_id = f"evidence:{evidence_id}"
        admit_node()
        nodes[node_id] = {"id": node_id, "kind": "evidence", "path": None, "evidence_id": evidence_id,
                          "title": row.get("source") or evidence_id,
                          "status": "active" if evidence_id in active else "retired",
                          "type": row.get("type", ""), "owner": "", "sensitivity": row["sensitivity"],
                          "decision_id": None, "source_of_truth": False}
        if evidence_id in retired:
            signals.add((node_id, "retired_evidence"))
    for note in index.notes:
        source = paths[note.path]
        if is_outcome_note(note):
            from .outcome_ledger import outcome_references
            decision_id, claim_ids = outcome_references(note.text)
            for matches, kind in [(canonical_decisions.get(decision_id, []), "outcome_decision"),
                                  *[(claims.get(claim_id, []), "measurement_claim") for claim_id in sorted(claim_ids)]]:
                if len(matches) == 1:
                    add_edge((source, matches[0], kind))
        for _target, resolved, ambiguous in wikilink_resolutions(note, index):
            if resolved is not None and resolved in paths and not ambiguous:
                add_edge((source, paths[resolved], "wikilink"))
            else:
                signals.add((source, "unavailable_dependency"))
        if index.relative(note.path).casefold() != "00-context/evidence-register.md":
            for evidence_id in note.cited_evidence:
                target = f"evidence:{evidence_id}"
                if target in nodes:
                    add_edge((source, target, "evidence"))
                else:
                    signals.add((source, "unavailable_evidence"))
        supersedes = str(note.front.get("supersedes") or "").strip()
        if supersedes:
            matches = decisions.get(supersedes, []) if DECISION_ID_RE.fullmatch(supersedes) else []
            if len(matches) == 1:
                add_edge((source, matches[0], "supersedes"))
            else:
                signals.add((source, "unavailable_decision"))
    return index, nodes, [{"from": a, "to": b, "type": kind} for a, b, kind in sorted(edges)], [
        {"node": node, "reason": reason} for node, reason in sorted(signals)]


def _target_node(index: VaultIndex, nodes: dict[str, dict[str, Any]], target: str) -> tuple[str | None, bool]:
    if not isinstance(target, str) or not target.strip() or len(target) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in target):
        raise ValueError("target must be a nonempty record ID or relative note name")
    target = target.strip()
    normalized = target.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("target must remain inside the vault")
    if EVIDENCE_ID_RE.fullmatch(target):
        key = f"evidence:{target}"
        return (key if key in nodes else None), False
    if DECISION_ID_RE.fullmatch(target):
        matches = [key for key, node in nodes.items() if node.get("decision_id") == target]
        return (matches[0] if len(matches) == 1 else None), len(matches) > 1
    if re.match(r"[ED]-", target):
        raise ValueError("target record ID is malformed")
    if target in nodes:
        return target, False
    resolved, ambiguous = index.resolve_link(target)
    key = f"document:{index.relative(resolved)}" if resolved is not None else ""
    return (key if key in nodes else None), ambiguous


def _cycle_components(visited: set[str], edges: list[dict[str, str]]) -> list[list[str]]:
    """Iterative strongly connected components; do not enumerate all cycle paths."""
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    loops: set[str] = set()
    for edge in edges:
        source, target = edge["from"], edge["to"]
        if source in visited and target in visited:
            outgoing[source].append(target)
            incoming[target].append(source)
            if source == target:
                loops.add(source)
    seen: set[str] = set()
    order: list[str] = []
    for start in sorted(visited):
        stack = [(start, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                order.append(node)
            elif node not in seen:
                seen.add(node)
                stack.append((node, True))
                stack.extend((other, False) for other in reversed(outgoing[node]) if other not in seen)
    seen.clear()
    components: list[list[str]] = []
    for start in reversed(order):
        if start in seen:
            continue
        component = []
        pending = [start]
        seen.add(start)
        while pending:
            node = pending.pop()
            component.append(node)
            for other in incoming[node]:
                if other not in seen:
                    seen.add(other)
                    pending.append(other)
        if len(component) > 1 or start in loops:
            components.append(sorted(component))
    return sorted(components)


@consistent_read
@path_cache()
def deep_impact(
    root: Path, target: str, *, max_depth: int = 8, max_nodes: int = 1000,
    allowed_sensitivities: set[str] | None = None, vault: VaultIndex | None = None,
) -> dict[str, Any]:
    """Traverse reverse typed dependencies with one shortest explanation per node.

    Limits include the target. Paths run from the target outward, while each
    edge retains its original dependent -> prerequisite direction. A supplied
    index is a trusted captured grant; labels only narrow it, never authorize it.
    """
    if type(max_depth) is not int or max_depth < 0 or type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_depth must be >= 0 and max_nodes must be >= 1 integers")
    index, nodes, edges, signals = _view_graph(root, vault, allowed_sensitivities)
    start, ambiguous = _target_node(index, nodes, target)
    reverse: dict[str, list[dict[str, str]]] = defaultdict(list)
    for edge in edges:
        reverse[edge["to"]].append(edge)
    distances = {start: 0} if start is not None else {}
    parents: dict[str, tuple[str, dict[str, str]]] = {}
    pending = deque([start] if start is not None else [])
    truncation = {"max_depth": False, "max_nodes": False}
    while pending:
        node = pending.popleft()
        for edge in reverse[node]:
            other = edge["from"]
            if other in distances:
                continue
            if distances[node] >= max_depth:
                truncation["max_depth"] = True
                continue
            if len(distances) >= max_nodes:
                truncation["max_nodes"] = True
                continue
            distances[other] = distances[node] + 1
            parents[other] = (node, edge)
            pending.append(other)
    results = []
    for node in sorted(parents, key=lambda key: (distances[key], key)):
        cursor = node
        route = []
        path_nodes = [cursor]
        while cursor != start:
            previous, edge = parents[cursor]
            route.append(edge)
            path_nodes.append(previous)
            cursor = previous
        results.append({"node": nodes[node], "distance": distances[node],
                        "path_nodes": list(reversed(path_nodes)), "path_edges": list(reversed(route))})
    visited = set(distances)
    induced = [edge for edge in edges if edge["from"] in visited and edge["to"] in visited]
    cycles = _cycle_components(visited, induced)
    signals = [item for item in signals if item["node"] in visited]
    signals.extend({"node": node, "reason": "dependency_cycle"} for component in cycles for node in component)
    return {"contract_version": 1, "target": target, "exists": start is not None, "ambiguous": ambiguous,
            "target_node": nodes.get(start) if start is not None else None,
            "results": results, "edges": induced, "cycle_components": cycles,
            "review_signals": sorted(signals, key=lambda item: (item["node"], item["reason"])),
            "limits": {"max_depth": max_depth, "max_nodes": max_nodes}, "truncation": truncation,
            "truncated": any(truncation.values()),
            "stats": {"view_nodes": len(nodes), "view_edges": len(edges), "visited_nodes": len(visited)}}
