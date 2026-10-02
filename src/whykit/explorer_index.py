"""Canonical Explorer index export — one Python parser for the UI contract."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from .messages import print_no_vault
from .lint import (
    _split_table_row,
    decision_log_rows,
    evidence_register,
    find_vault_root,
    is_vault_root,
    lint,
    rel,
)
from .vault_index import VaultIndex


def _summary(body: str) -> str:
    lines = body.split("\n")
    seen_h1 = False
    buf: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not seen_h1 and line.startswith("# "):
            seen_h1 = True
            continue
        if not seen_h1 or not line:
            if buf:
                break
            continue
        if re.match(r"^(#|\||>|-|\*|\d+\.)", line):
            if buf:
                break
            continue
        buf.append(line)
        if len(" ".join(buf)) > 220:
            break
    return " ".join(buf)[:240]


def _wiki_target(cell: str) -> str:
    match = re.search(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]", str(cell))
    return match.group(1).strip() if match else ""


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def build_explorer_index(root: Path, *, today: dt.date | None = None) -> dict:
    """Build the Explorer vault.json payload from the canonical Python parser."""
    root = root.resolve()
    index = VaultIndex.load(root)
    docs = []
    for note in index.notes:
        doc_id = rel(root, note.path).removesuffix(".md")
        top = doc_id.split("/", 1)[0] if "/" in doc_id else "root"
        body = note.body
        docs.append({
            "id": doc_id,
            "title": str(note.front.get("title") or note.path.stem),
            "aliases": _as_list(note.front.get("aliases")),
            "type": str(note.front.get("type") or "guide"),
            "status": str(note.front.get("status") or "draft"),
            "owner": str(note.front.get("owner") or ""),
            "created": str(note.front.get("created") or ""),
            "lastUpdated": str(note.front.get("last_updated") or ""),
            "reviewBy": str(note.front.get("review_by") or ""),
            "sourceOfTruth": note.front.get("source_of_truth") is True,
            "sensitivity": str(note.front.get("sensitivity") or "internal"),
            "sourceIds": _as_list(note.front.get("source_ids")),
            "tags": _as_list(note.front.get("tags")),
            "workstream": str(note.front.get("workstream") or top),
            "decisionId": str(note.front.get("decision_id") or "") or None,
            "supersedes": str(note.front.get("supersedes") or "") or None,
            "summary": _summary(body),
            "body": body.lstrip("\n"),
        })
    docs.sort(key=lambda item: item["id"])

    active, retired, _ = evidence_register(root)
    evidence = []
    for eid, row in sorted(active.items()):
        evidence.append({
            "id": eid,
            "state": "active",
            "source": str(row.get("source") or ""),
            "type": str(row.get("type") or ""),
            "date": str(row.get("date") or ""),
            "accessed": str(row.get("accessed") or ""),
            "location": str(row.get("location") or ""),
            "claims": str(row.get("claims") or ""),
        })
    for eid, row in sorted(retired.items()):
        replaced = str(row.get("replaced_by") or "")
        evidence.append({
            "id": eid,
            "state": "retired",
            "source": str(row.get("source") or ""),
            "type": "",
            "date": "",
            "accessed": "",
            "location": "",
            "claims": "",
            "retiredOn": str(row.get("retired_on") or ""),
            "why": str(row.get("why") or ""),
            "replacedBy": None if replaced in {"", "—"} else replaced,
        })

    decisions = []
    for row in decision_log_rows(root):
        decisions.append({
            "id": str(row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "date": str(row.get("date") or ""),
            "owner": str(row.get("owner") or ""),
            "status": str(row.get("status") or ""),
            "recordId": str(row.get("record") or ""),
        })
    # Attach supersedes / supersededBy from decision documents.
    by_decision = {doc["decisionId"]: doc for doc in docs if doc.get("decisionId")}
    for row in decisions:
        doc = by_decision.get(row["id"])
        if doc:
            row["supersedes"] = doc.get("supersedes")
            if not row.get("recordId"):
                row["recordId"] = doc["id"]
    for row in decisions:
        newer = next((other for other in decisions if other.get("supersedes") == row["id"]), None)
        if newer:
            row["supersededBy"] = newer["id"]

    review_path = root / "00-context" / "review-log.md"
    reviews = []
    if review_path.is_file():
        text = review_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        header = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
        at = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if at is not None:
            for line in lines[at + 2 :]:
                if not line.lstrip().startswith("|"):
                    break
                cells = _split_table_row(line)
                if len(cells) < 7:
                    continue
                date, target = cells[0], cells[1]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not target:
                    continue
                reviews.append({
                    "date": date,
                    "target": target,
                    "targetId": _wiki_target(target),
                    "reviewer": cells[2],
                    "outcome": cells[3],
                    "previousReview": cells[4],
                    "nextReview": cells[5],
                    "note": cells[6],
                })

    lint_files, findings = lint(root, orphans=True, secrets=True, today=today, vault=index)
    lint_payload = {
        "files": len(lint_files),
        "errors": sum(1 for item in findings if item.level == "error"),
        "warnings": sum(1 for item in findings if item.level == "warning"),
        "findings": [
            {
                "path": item.path,
                "line": item.line,
                "level": item.level,
                "code": item.code,
                "message": item.message,
            }
            for item in findings
        ],
    }

    vault_name = next((doc["title"] for doc in docs if doc["id"] == "README"), "WhyKit vault")
    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "vaultName": vault_name,
        "docs": docs,
        "evidence": evidence,
        "decisions": decisions,
        "reviews": reviews,
        "lint": lint_payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit explorer-index",
        description="Export the Explorer vault index from the canonical Python parser.",
    )
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--json", action="store_true", default=True, help="emit JSON (default)")
    parser.add_argument("--today", help="evaluate lint review dates as of this ISO date")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print_no_vault(args.root)
        return 2
    today = dt.date.fromisoformat(args.today) if args.today else None
    payload = build_explorer_index(root, today=today)
    if payload["lint"]["errors"] > 0:
        print(
            f"Vault has {payload['lint']['errors']} lint error(s); Explorer index not generated.",
            file=sys.stderr,
        )
        for item in payload["lint"]["findings"]:
            if item["level"] == "error":
                print(f"- {item['path']}: {item['message']}", file=sys.stderr)
        return 1
    # JSON escapes preserve Unicode content without requiring a UTF-8 console.
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
