"""Build bounded, evidence-aware context packs for people and AI agents."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .impact import analyze_impact
from .lint import (
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    _resolve,
    evidence_register,
    find_vault_root,
    is_vault_root,
    lint,
    rel,
)
from .vault_index import VaultIndex


def _resolve_note(root: Path, target: str, vault: VaultIndex):
    if DECISION_ID_RE.fullmatch(target):
        matches = [
            note for note in vault.notes
            if str(note.front.get("decision_id") or "").strip() == target
        ]
        return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)
    path, ambiguous = _resolve(root, target, vault.link_index)
    if path is None:
        return None, ambiguous
    return vault.note_for(path), ambiguous


def _evidence_details(root: Path, ids: list[str]) -> list[dict]:
    active, retired, _ = evidence_register(root)
    out = []
    for evidence_id in sorted(set(ids)):
        row = active.get(evidence_id) or retired.get(evidence_id)
        out.append({
            "id": evidence_id,
            "state": "active" if evidence_id in active else "retired" if evidence_id in retired else "missing",
            "record": row,
        })
    return out


def build_context(
    root: Path,
    target: str,
    *,
    max_chars: int = 20_000,
    include_body: bool = True,
    vault: VaultIndex | None = None,
) -> dict:
    if max_chars < 0:
        raise ValueError("max_chars must be >= 0")
    vault_index = vault or VaultIndex.load(root)
    impact = analyze_impact(root, target, vault=vault_index)
    if not impact.get("exists"):
        return {
            "contract_version": 1,
            "target": target,
            "exists": False,
            "ambiguous": bool(impact.get("ambiguous")),
            "kind": impact.get("kind"),
        }

    if EVIDENCE_ID_RE.fullmatch(target):
        return {
            "contract_version": 1,
            "target": target,
            "exists": True,
            "kind": "evidence",
            "evidence": _evidence_details(root, [target])[0],
            "references": impact.get("references", []),
            "replacement": impact.get("replacement"),
        }

    note, ambiguous = _resolve_note(root, target, vault_index)
    if note is None:
        return {
            "contract_version": 1,
            "target": target,
            "exists": False,
            "ambiguous": ambiguous,
            "kind": impact.get("kind", "document"),
        }

    content = note.text
    truncated = False
    if include_body and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    if not include_body:
        content = ""

    source_ids = sorted(set(EVIDENCE_ID_RE.findall(note.text)))
    note_path = rel(root, note.path)
    _, scoped_findings = lint(root, [note_path], orphans=False, secrets=False, vault=vault_index)
    scoped_findings = [item for item in scoped_findings if item.path == note_path]
    return {
        "contract_version": 1,
        "target": target,
        "exists": True,
        "kind": impact.get("kind", "document"),
        "record": impact.get("record"),
        "content": content,
        "content_truncated": truncated,
        "content_chars": len(note.text),
        "evidence": _evidence_details(root, source_ids),
        "incoming": impact.get("incoming", []),
        "outgoing": impact.get("outgoing", []),
        "supersedes": impact.get("supersedes"),
        "superseded_by": impact.get("superseded_by", []),
        "findings": [asdict(item) for item in scoped_findings],
    }


def _human(report: dict) -> None:
    if not report.get("exists"):
        print(f"{report['target']}: {'ambiguous' if report.get('ambiguous') else 'not found'}")
        return
    print(f"Context for {report['target']} [{report['kind']}]")
    if report["kind"] == "evidence":
        evidence = report["evidence"]
        print(f"  state        {evidence['state']}")
        print(f"  references   {len(report.get('references', []))}")
        if evidence.get("record"):
            row = evidence["record"]
            print(f"  source       {row.get('source') or '—'}")
            print(f"  location     {row.get('location') or '—'}")
        return
    record = report.get("record") or {}
    print(f"  document     {record.get('path', '—')}")
    print(f"  owner        {record.get('owner') or '—'}")
    print(f"  status       {record.get('status') or '—'}")
    print(f"  evidence     {len(report.get('evidence', []))}")
    print(f"  backlinks    {len(report.get('incoming', []))}")
    print(f"  outgoing     {len(report.get('outgoing', []))}")
    print(f"  findings     {len(report.get('findings', []))}")
    if report.get("content"):
        print("\n--- document ---")
        print(report["content"])
        if report.get("content_truncated"):
            print("\n[content truncated]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit context", description="Build a bounded evidence-aware context pack for one WhyKit target.")
    parser.add_argument("target", help="E-NNN, D-NNN, path, stem or alias")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--max-chars", type=int, default=20_000)
    parser.add_argument("--no-body", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_chars < 0:
        print("--max-chars must be >= 0", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    report = build_context(root, args.target, max_chars=args.max_chars, include_body=not args.no_body)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _human(report)
    return 0 if report.get("exists") else 1


if __name__ == "__main__":
    raise SystemExit(main())
