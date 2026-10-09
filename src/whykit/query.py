"""Metadata-aware vault queries for humans and agents."""
from __future__ import annotations

from .io import consistent_read

import argparse
import heapq
import json
from collections.abc import Callable
from pathlib import Path

from .claim_readers import claim_reader
from .contract import emit_error, vault_not_found
from .lint import EVIDENCE_ID_RE, Note, find_vault_root, is_vault_root, path_cache
from .vault_index import VaultIndex, _NOTE_CACHE
from .console import emit_machine


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _summary(index: VaultIndex, note) -> dict:
    path = index.relative(note.path)
    inline_ids = set() if path == "00-context/evidence-register.md" else set(note.cited_evidence)
    source_ids = sorted(set(_as_list(note.front.get("source_ids"))) | inline_ids)
    return {
        "path": path,
        "title": str(note.front.get("title") or note.path.stem),
        "type": str(note.front.get("type") or ""),
        "status": str(note.front.get("status") or ""),
        "owner": str(note.front.get("owner") or ""),
        "sensitivity": str(note.front.get("sensitivity") or ""),
        "source_of_truth": note.front.get("source_of_truth") is True,
        "decision_id": str(note.front.get("decision_id") or "") or None,
        "review_by": str(note.front.get("review_by") or "") or None,
        "source_ids": source_ids,
        "tags": _as_list(note.front.get("tags")),
    }


@consistent_read
@path_cache()
@claim_reader("query")
def query_vault(
    root: Path,
    *,
    text: str | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    sensitivity: str | None = None,
    allowed_sensitivities: set[str] | None = None,
    source_id: str | None = None,
    tag: str | None = None,
    canonical_only: bool = False,
    limit: int = 100,
    vault: VaultIndex | None = None,
    _select: Callable[[list[tuple[int, str, Note]]], list[tuple[int, str, Note]]] | None = None,
) -> dict:
    index = vault or VaultIndex.load(root)
    needle = (text or "").casefold().strip()
    owner_needle = (owner or "").casefold().strip()
    tag_needle = (tag or "").casefold().strip()
    matches: list[tuple[int, str, Note]] = []
    cache = _NOTE_CACHE.get()
    metadata: dict[str, str | bool] = {}
    for field, value in (('kind', doc_type), ('state', status), ('label', sensitivity)):
        if value:
            metadata[field] = value
    if canonical_only:
        metadata['canonical'] = True
    candidates = cache.candidates(index, needle, metadata) if cache is not None and (len(needle) >= 3 or metadata) else index.notes
    for note in candidates:
        front = note.front
        raw_sensitivity = str(front.get("sensitivity") or "")
        record_sensitivity = raw_sensitivity or "internal"
        if allowed_sensitivities is not None and record_sensitivity not in allowed_sensitivities:
            continue
        if doc_type and str(front.get("type") or "") != doc_type:
            continue
        # A template declares the type it is a starter for, but it is not a
        # record of that type: `--type decision` lists decisions. Ask for
        # `--status template` to see templates.
        record_status = str(front.get("status") or "")
        if doc_type and record_status == "template" and status != "template":
            continue
        if status and record_status != status:
            continue
        if sensitivity and raw_sensitivity != sensitivity:
            continue
        canonical = front.get("source_of_truth") is True
        if canonical_only and not canonical:
            continue
        if owner_needle and owner_needle not in str(front.get("owner") or "").casefold():
            continue
        if tag_needle and not any(tag_needle == value.casefold() for value in _as_list(front.get("tags"))):
            continue
        path = index.relative(note.path)
        if source_id:
            inline_ids = set() if path == "00-context/evidence-register.md" else set(note.cited_evidence)
            if source_id not in set(_as_list(front.get("source_ids"))) | inline_ids:
                continue

        score = 0
        if needle:
            title = str(front.get("title") or note.path.stem).casefold()
            path_text = path.casefold()
            body = note.text.casefold()
            if needle not in title and needle not in path_text and needle not in body:
                continue
            if needle == title:
                score += 100
            elif needle in title:
                score += 50
            if needle in path_text:
                score += 20
            score += min(body.count(needle), 20)
        if canonical:
            score += 5
        if record_status == "approved":
            score += 2
        matches.append((score, path, note))

    total = len(matches)
    def rank(item):
        return -item[0], item[1]
    # MCP needs the complete order to bind its cursor; one-shot queries need only top-K.
    if _select is not None:
        matches.sort(key=rank)
        selected = _select(matches)
    else:
        selected = heapq.nsmallest(max(0, limit), matches, key=rank)
    limited = [_summary(index, note) for _, _, note in selected]
    return {
        "contract_version": 1,
        "query": {
            "text": text,
            "type": doc_type,
            "status": status,
            "owner": owner,
            "sensitivity": sensitivity,
            "allowed_sensitivities": sorted(allowed_sensitivities) if allowed_sensitivities is not None else None,
            "source_id": source_id,
            "tag": tag,
            "canonical_only": canonical_only,
            "limit": limit,
        },
        "total": total,
        "returned": len(limited),
        "results": limited,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit query", description="Query WhyKit notes by metadata, evidence and text.")
    parser.add_argument("text", nargs="?", help="case-insensitive text to find in title, path or body")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--type", dest="doc_type")
    parser.add_argument("--status")
    parser.add_argument("--owner")
    parser.add_argument("--sensitivity")
    parser.add_argument("--source", dest="source_id", help="filter to documents using E-NNN")
    parser.add_argument("--tag")
    parser.add_argument("--canonical-only", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 0:
        return emit_error("invalid_argument", "--limit must be >= 0", json_mode=args.json)
    if args.source_id and not EVIDENCE_ID_RE.fullmatch(args.source_id):
        return emit_error("invalid_argument", "--source must be an E-NNN identifier", json_mode=args.json)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)
    report = query_vault(
        root,
        text=args.text,
        doc_type=args.doc_type,
        status=args.status,
        owner=args.owner,
        sensitivity=args.sensitivity,
        source_id=args.source_id,
        tag=args.tag,
        canonical_only=args.canonical_only,
        limit=args.limit,
    )
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["results"]:
            decision = f" {item['decision_id']}" if item.get("decision_id") else ""
            canonical = " canonical" if item["source_of_truth"] else ""
            print(f"{item['path']}  [{item['status'] or 'no-status'}/{item['type'] or 'no-type'}{decision}{canonical}]  {item['title']}")
        print(f"\n{report['returned']} returned / {report['total']} matched")
    return 0
