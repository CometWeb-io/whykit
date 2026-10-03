"""Deterministic vault snapshots and drift verification."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .contract import emit_error, vault_not_found
from .graph import build_graph
from .io import atomic_write_text, safe_vault_target
from .lint import collect_markdown, find_vault_root, is_vault_root, load_note, rel, path_cache
from .status import build_status
from .vault_index import VaultIndex
from .console import emit_machine

SNAPSHOT_FORMAT = "whykit.snapshot/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(root: Path, vault: VaultIndex | None = None) -> list[Path]:
    files = {note.path for note in vault.notes} if vault is not None else set(collect_markdown(root, []))
    config = root / "whykit.toml"
    if config.is_file():
        files.add(config)
    relative = vault.relative if vault is not None else (lambda path: rel(root, path))
    return sorted(files, key=relative)


def _file_entry(root: Path, path: Path, vault: VaultIndex | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": vault.relative(path) if vault is not None else rel(root, path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }
    if path.suffix.lower() == ".md":
        note = (vault.note_for(path) if vault is not None else None) or load_note(path)
        entry.update({
            "title": str(note.front.get("title") or path.stem),
            "type": str(note.front.get("type") or ""),
            "status": str(note.front.get("status") or ""),
            "owner": str(note.front.get("owner") or ""),
            "sensitivity": str(note.front.get("sensitivity") or ""),
            "source_of_truth": note.front.get("source_of_truth") is True,
            "decision_id": str(note.front.get("decision_id") or "") or None,
            "review_by": str(note.front.get("review_by") or "") or None,
        })
    return entry


def _snapshot_id(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in entries:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


@path_cache()
def build_snapshot(root: Path, *, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    root = root.resolve()
    # One parse feeds the file table, status and graph; each used to re-read
    # every note, which tripled the cost of a snapshot on a large vault.
    vault = VaultIndex.load(root)
    entries = [_file_entry(root, path, vault) for path in _snapshot_files(root, vault)]
    status = build_status(root, today=today, vault=vault)
    graph = build_graph(root, vault=vault)
    edges_by_type: dict[str, int] = {}
    for edge in graph.get("edges", []):
        edge_type = str(edge.get("type") or "wikilink")
        edges_by_type[edge_type] = edges_by_type.get(edge_type, 0) + 1
    return {
        "format": SNAPSHOT_FORMAT,
        "contract_version": 1,
        "snapshot_id": _snapshot_id(entries),
        "as_of": today.isoformat(),
        "files": entries,
        "summary": {
            "documents": status["documents"],
            "canonical": status["canonical"],
            "decisions": status["decisions"],
            "evidence_active": status["evidence_active"],
            "evidence_retired": status["evidence_retired"],
            "errors": status["errors"],
            "warnings": status["warnings"],
            "review_due": len(status["review_queue"]),
            "graph_nodes": len(graph.get("nodes", [])),
            "graph_edges": len(graph.get("edges", [])),
            "unresolved_links": len(graph.get("unresolved", [])),
            "edges_by_type": dict(sorted(edges_by_type.items())),
        },
    }


@path_cache()
def compare_snapshot(root: Path, baseline: dict[str, Any], *, today: dt.date | None = None) -> dict[str, Any]:
    if baseline.get("format") != SNAPSHOT_FORMAT:
        raise ValueError(f"unsupported snapshot format: {baseline.get('format')!r}")
    current = build_snapshot(root, today=today)
    before = {item["path"]: item for item in baseline.get("files", []) if isinstance(item, dict) and item.get("path")}
    after = {item["path"]: item for item in current["files"]}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after) if before[path].get("sha256") != after[path].get("sha256"))
    metadata_changed = sorted(
        path for path in set(before) & set(after)
        if before[path].get("sha256") == after[path].get("sha256") and before[path] != after[path]
    )
    content_matches = not (added or removed or changed or metadata_changed)
    baseline_summary = baseline.get("summary", {}) if isinstance(baseline.get("summary"), dict) else {}
    current_summary = current["summary"]
    health_keys = ("errors", "warnings", "review_due", "unresolved_links")
    health_delta = {
        key: {
            "before": baseline_summary.get(key),
            "after": current_summary.get(key),
        }
        for key in health_keys
        if baseline_summary.get(key) != current_summary.get(key)
    }
    return {
        "contract_version": 1,
        "baseline_snapshot_id": baseline.get("snapshot_id"),
        "current_snapshot_id": current["snapshot_id"],
        # `matches` remains content identity for backwards compatibility. A
        # vault can become review-due as time advances without a byte changing.
        "matches": content_matches,
        "content_matches": content_matches,
        "health_changed": bool(health_delta),
        "health_delta": health_delta,
        "added": added,
        "removed": removed,
        "changed": changed,
        "metadata_changed": metadata_changed,
        "baseline_summary": baseline_summary,
        "current_summary": current_summary,
    }


def _load_baseline(path: Path) -> dict[str, Any]:
    hint = "hint: create a baseline with `whykit snapshot --output <file>`"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"snapshot not found: {path}\n{hint}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read snapshot {path}: {getattr(exc, 'strerror', None) or exc}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"not a WhyKit snapshot (invalid JSON at line {exc.lineno}, column {exc.colno}): {path}\n{hint}"
        ) from None
    if not isinstance(payload, dict):
        raise ValueError(f"not a WhyKit snapshot (expected a JSON object): {path}\n{hint}")
    return payload


def main_snapshot(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit snapshot", description="Create a deterministic content snapshot of a WhyKit vault.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--today", help="evaluate health/review status as of this ISO date")
    parser.add_argument("--output", help="write JSON to this path instead of stdout")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)
    # Without --output the command's stdout is JSON, so failures are too.
    json_mode = not args.output
    requested_root = Path(args.root).expanduser().absolute() if args.root else find_vault_root()
    root = requested_root.resolve() if requested_root is not None else None
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=json_mode)
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=json_mode)
    payload = build_snapshot(root, today=today)
    rendered = json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=args.compact)
    if args.output:
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
            return emit_error("unsafe_path", f"--output must be a safe path inside the vault: {exc}", json_mode=json_mode)
        atomic_write_text(target, rendered + "\n")
        print(f"snapshot {payload['snapshot_id']}: {rel(root, target)}")
    else:
        emit_machine(rendered)
    return 0


def main_verify(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit verify-snapshot", description="Compare the vault with a prior WhyKit snapshot.")
    parser.add_argument("snapshot", help="baseline snapshot JSON")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--today", help="evaluate health/review status as of this ISO date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)
    snapshot_path = Path(args.snapshot).expanduser()
    if not snapshot_path.is_absolute():
        snapshot_path = (root / snapshot_path).resolve()
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    try:
        report = compare_snapshot(root, _load_baseline(snapshot_path), today=today)
    except ValueError as exc:
        return emit_error("invalid_argument", str(exc), json_mode=args.json)
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        state = "MATCH" if report["matches"] else "DRIFT"
        print(f"snapshot verification: {state}")
        print(f"  baseline  {report.get('baseline_snapshot_id') or '—'}")
        print(f"  current   {report['current_snapshot_id']}")
        for key in ("added", "removed", "changed", "metadata_changed"):
            values = report[key]
            print(f"  {key:<16} {len(values)}")
            for value in values[:30]:
                print(f"    {value}")
            if len(values) > 30:
                print(f"    … {len(values) - 30} more")
        if report["health_changed"]:
            print("  health changes")
            for key, delta in report["health_delta"].items():
                print(f"    {key}: {delta['before']} -> {delta['after']}")
    return 0 if report["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main_snapshot())
