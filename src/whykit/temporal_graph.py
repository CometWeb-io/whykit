"""A pinned recorded snapshot plus explicit, independently asserted valid time."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
import tempfile
from typing import Any
import unicodedata

from . import diff as git_diff
from .dependency_graph import _target_node, _view_graph
from .immutability import _git_prefix, _require_revisions, git
from .lint import Note, is_vault_root
from .vault_index import VaultIndex


def _date(value: object) -> dt.date:
    if type(value) is dt.date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return dt.date.fromisoformat(value)
    raise ValueError("valid dates must be YYYY-MM-DD")


def _validity(note: Note | None, valid_on: dt.date) -> dict[str, Any]:
    result: dict[str, Any] = {"state": "unknown", "valid_from": None, "valid_until": None}
    if note is None:
        return result
    keys = {"valid_from", "valid_until"}
    if note.front_error or any(str(key).strip().casefold() in keys and key not in keys for key in note.front):
        return {**result, "state": "invalid"}
    try:
        start = _date(note.front["valid_from"]) if "valid_from" in note.front else None
        end = _date(note.front["valid_until"]) if "valid_until" in note.front else None
        if start is not None and end is not None and end <= start:
            raise ValueError("valid_until must follow valid_from")
    except ValueError:
        return {**result, "state": "invalid"}
    result.update(valid_from=start.isoformat() if start else None, valid_until=end.isoformat() if end else None)
    if end is not None and valid_on >= end:
        result["state"] = "expired"
    elif start is not None:
        result["state"] = "not_yet" if valid_on < start else "active"
    return result


def _tree_omissions(entries: list[tuple[str, str, str, str]]) -> int:
    omitted = 0
    seen: set[str] = set()
    directories: set[str] = set()
    for mode, kind, _oid, path in entries:
        parts = git_diff._safe_parts(path)
        if kind != "blob" or mode == "120000" or parts is None:
            omitted += 1
            continue
        normalized = "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)
        parents = ["/".join(normalized.split("/")[:i]) for i in range(1, len(parts))]
        if (any(part.casefold() == ".git" for part in parts) or normalized in seen
                or normalized in directories or any(parent in seen for parent in parents)):
            raise ValueError("snapshot has unsafe or ambiguous tree paths")
        seen.add(normalized)
        directories.update(parents)
    return omitted


def knowledge_at(
    root: Path, *, valid_on: dt.date | str, recorded_revision: str = "HEAD", target: str | None = None,
    max_nodes: int = 1000, allowed_sensitivities: set[str] | None = None,
) -> dict[str, Any]:
    """Read Git objects into a confined temporary tree; never check out a revision.

    `recorded_at` is the commit's asserted committer timestamp, not an external
    clock attestation. Validity is only an explicit interval assertion; active
    does not mean factually true, approved, currently binding or authorized.
    """
    day = _date(valid_on)
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be an integer >= 1")
    where = str(Path(root).resolve())
    if git("replace", "--list", root=where).strip():
        raise ValueError("Git replacement objects are unsupported for pinned snapshots")
    _require_revisions(where, recorded_revision)
    commit = git_diff._short(recorded_revision, where)
    prefix = _git_prefix(where)
    recorded_at = git("show", "-s", "--format=%cI", commit, root=where).strip()
    entries = git_diff._list_tree(commit, prefix, where)
    omitted = _tree_omissions(entries or [])
    with tempfile.TemporaryDirectory(prefix="whykit-temporal-") as directory:
        snapshot = Path(directory)
        present = git_diff.materialize(commit, prefix, where, snapshot) and is_vault_root(snapshot)
        index = VaultIndex.load(snapshot) if present else VaultIndex(snapshot, [], {}, {})
        index, nodes, edges, signals = _view_graph(snapshot, index, allowed_sensitivities)
        notes = {f"document:{index.relative(note.path)}": note for note in index.notes}
        for node_id, node in nodes.items():
            node["validity"] = _validity(notes.get(node_id), day)
            if node["validity"]["state"] in {"invalid", "unknown"}:
                signals.append({"node": node_id, "reason": f"validity_{node['validity']['state']}"})
        for edge in edges:
            if edge["type"] == "supersedes" and all(nodes[key]["validity"]["state"] == "active" for key in (edge["from"], edge["to"])):
                signals.append({"node": edge["to"], "reason": "overlapping_supersession"})
        selected, ambiguous = _target_node(index, nodes, target) if target is not None else (None, False)
        chosen = ([selected] if selected else []) if target is not None else sorted(nodes)
        truncated = len(chosen) > max_nodes
        chosen = chosen[:max_nodes]
        included = set(chosen)
        return {"contract_version": 1, "recorded_revision": commit, "recorded_at": recorded_at,
                "recorded_time_source": "git_committer_assertion", "valid_on": day.isoformat(),
                "target": target, "exists": selected is not None if target is not None else bool(present),
                "ambiguous": ambiguous, "nodes": [nodes[key] for key in chosen],
                "edges": [edge for edge in edges if edge["from"] in included and edge["to"] in included],
                "review_signals": sorted((item for item in signals if item["node"] in included),
                                         key=lambda item: (item["node"], item["reason"])),
                "max_nodes": max_nodes, "truncated": truncated,
                "omitted_tree_entries": omitted if allowed_sensitivities is None else None,
                "stats": {"view_nodes": len(nodes), "view_edges": len(edges), "returned_nodes": len(chosen)}}
