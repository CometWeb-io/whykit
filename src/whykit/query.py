"""Metadata-aware vault queries for humans and agents."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .messages import print_no_vault
from .lint import EVIDENCE_ID_RE, find_vault_root, is_vault_root
from .vault_index import VaultIndex


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _summary(index: VaultIndex, note) -> dict:
    path = index.relative(note.path)
    inline_ids = set() if path == "00-context/evidence-register.md" else set(EVIDENCE_ID_RE.findall(note.text))
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
) -> dict:
    index = vault or VaultIndex.load(root)
    needle = (text or "").casefold().strip()
    owner_needle = (owner or "").casefold().strip()
    tag_needle = (tag or "").casefold().strip()
    matches: list[tuple[int, dict]] = []
    for note in index.notes:
        summary = _summary(index, note)
        record_sensitivity = summary["sensitivity"] or "internal"
        if allowed_sensitivities is not None and record_sensitivity not in allowed_sensitivities:
            continue
        if doc_type and summary["type"] != doc_type:
            continue
        if status and summary["status"] != status:
            continue
        if sensitivity and summary["sensitivity"] != sensitivity:
            continue
        if canonical_only and not summary["source_of_truth"]:
            continue
        if source_id and source_id not in summary["source_ids"]:
            continue
        if owner_needle and owner_needle not in summary["owner"].casefold():
            continue
        if tag_needle and not any(tag_needle == value.casefold() for value in summary["tags"]):
            continue

        score = 0
        if needle:
            title = summary["title"].casefold()
            path_text = summary["path"].casefold()
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
        if summary["source_of_truth"]:
            score += 5
        if summary["status"] == "approved":
            score += 2
        matches.append((score, summary))

    matches.sort(key=lambda item: (-item[0], item[1]["path"]))
    total = len(matches)
    limited = [item for _, item in matches[: max(0, limit)]]
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
        print("--limit must be >= 0", file=sys.stderr)
        return 2
    if args.source_id and not EVIDENCE_ID_RE.fullmatch(args.source_id):
        print("--source must be an E-NNN identifier", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print_no_vault(args.root)
        return 2
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
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["results"]:
            decision = f" {item['decision_id']}" if item.get("decision_id") else ""
            canonical = " canonical" if item["source_of_truth"] else ""
            print(f"{item['path']}  [{item['status'] or 'no-status'}/{item['type'] or 'no-type'}{decision}{canonical}]  {item['title']}")
        print(f"\n{report['returned']} returned / {report['total']} matched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
