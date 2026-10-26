"""Canonical Explorer index export — one Python parser for the UI contract."""
from __future__ import annotations

from .io import consistent_read, vault_read_lock

from .tables import review_table_header

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from collections import defaultdict, deque
from pathlib import Path

from .config import ConfigError, load_config
from .contract import CONTRACT_VERSION, emit_error, vault_not_found
from .lint import (
    evidence_view,
    _parse_evidence_register_text,
    path_cache,
    strip_markdown_suffix,
    _split_table_row,
    decision_log_rows,
    find_vault_root,
    is_vault_root,
    lint,
    Note,
    rel,
)
from .vault_index import VaultIndex
from .console import emit_machine
from .sensitivity import note_sensitivity_level, classified_evidence, evidence_register_notes


def _marker_pattern(markers: set[str]) -> re.Pattern | None:
    """Share prefixes so matching a path does not scan every vault path."""
    if not markers:
        return None
    nodes: list[tuple[str, list[int]]] = []
    pending = [(0, sorted(markers), 0)]
    next_id = 1
    while pending:
        at, words, depth = pending.pop()
        while len(nodes) <= at:
            nodes.append(("", []))
        if depth >= 32:
            nodes[at] = ("(?:" + "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True)) + ")", [])
            continue
        prefix = os.path.commonprefix((words[0], words[-1]))
        groups: dict[str, list[str]] = defaultdict(list)
        for word in words:
            suffix = word[len(prefix):]
            if suffix:
                groups[suffix[0]].append(suffix)
        children = []
        for group in groups.values():
            child = next_id
            next_id += 1
            children.append(child)
            pending.append((child, group, depth + 1))
        if prefix in words and children:
            children.append(-1)
        nodes[at] = (re.escape(prefix), children)
    parts = {-1: ""}
    for at in range(len(nodes) - 1, -1, -1):
        prefix, children = nodes[at]
        parts[at] = prefix + ("(?:" + "|".join(parts[child] for child in children) + ")" if children else "")
    return re.compile(r"(?<!\w)(?:" + parts[0] + r")(?!\w)")


def _public_payload(payload: dict, index: VaultIndex, *, reasons: dict[str, set[str]] | None = None) -> dict:
    """Withhold non-public notes and their dependent references as whole notes.

    Redacting fragments would invent a different decision or review history.
    This closure also inspects code examples: unlike graph citations, an export
    must not reveal a hidden identifier just because it occurs in a code block.
    """
    docs = {doc["id"]: doc for doc in payload["docs"]}
    paths = {note.path.resolve(): strip_markdown_suffix(index.relative(note.path)) for note in index.notes}
    withheld = {paths[note.path.resolve()] for note in index.notes if note_sensitivity_level(note) != 0}
    def withhold(key: str, reason: str) -> None:
        withheld.add(key)
        if reasons is not None:
            reasons.setdefault(key, set()).add(reason)

    for key in withheld:
        if reasons is not None:
            reasons.setdefault(key, set()).add("non_public_sensitivity")
    markers: dict[str, set[str]] = defaultdict(set)
    titles: dict[str, set[str]] = defaultdict(set)
    for key, doc in docs.items():
        for value in (key, key + ".md", doc["decisionId"]):
            if value:
                markers[str(value)].add(key)
        for value in (doc["title"], *doc["aliases"]):
            if value:
                titles[str(value)].add(key)
    dependents: dict[str, set[str]] = defaultdict(set)
    registers = evidence_register_notes(index)
    register_ids = {paths[note.path.resolve()] for note in registers}
    register_id = paths[registers[0].path.resolve()] if len(registers) == 1 else "00-context/evidence-register"
    evidence_ids = {row["id"] for row in payload["evidence"]}
    for row in payload["evidence"]:
        key = "evidence:" + row["id"]
        markers[row["id"]].add(key)
        if row["sensitivity"] != "public":
            withheld.add(key)
    pattern = _marker_pattern(set(markers))
    title_pattern = _marker_pattern(set(titles))
    _, _, _, per_row = classified_evidence(index)
    if per_row:
        for key in register_ids:
            withhold(key, "per_row_evidence_classification")
    evidence_pattern = re.compile(r"(?<!\w)E-[0-9]{3,}(?!\w)")
    for note in index.notes:
        key = paths[note.path.resolve()]
        if key in withheld:
            continue
        raw = Note(note.path, note.text)
        raw.masked = note.text
        dependencies: set[str] = set()
        for target, _, _ in raw.wikilink_hits:
            if target.startswith(("https://", "http://")):
                continue
            try:
                resolved, ambiguous = index.resolve_link(target)
                target_id = paths.get(resolved.resolve()) if resolved is not None else None
            except (OSError, ValueError, RuntimeError):
                target_id, ambiguous = None, False
            if ambiguous or target_id is None:
                withhold(key, "unresolved_or_ambiguous_wikilink")
            else:
                dependencies.add(target_id)
        for _, target, _ in raw.markdown_link_hits:
            candidate = index.root / target.lstrip("/") if target.startswith("/") else note.path.parent / target
            try:
                target_id = paths.get(candidate.resolve())
            except (OSError, ValueError, RuntimeError):
                target_id = None
            if target_id is None:
                withhold(key, "unclassified_attachment_or_missing_markdown_link")
            else:
                dependencies.add(target_id)
        text = "\n".join(value if isinstance(value, str) else "\n".join(map(str, value)) if isinstance(value, list) else str(value) for value in docs[key].values())
        if pattern is not None:
            for match in pattern.finditer(text):
                dependencies.update(markers[match.group()])
        if title_pattern is not None:
            content = "\n".join((docs[key]["title"], *docs[key]["aliases"], docs[key]["body"]))
            for match in title_pattern.finditer(content):
                dependencies.update(titles[match.group()])
        if key != register_id and evidence_ids.intersection(evidence_pattern.findall(note.text)):
            if not per_row:
                if register_id not in docs:
                    withhold(key, "missing_evidence_register")
                dependencies.add(register_id)
        for target in dependencies - {key}:
            dependents[target].add(key)
        if key != register_id and set(evidence_pattern.findall(note.text)) - evidence_ids:
            withhold(key, "unclassified_evidence_reference")
    # Rows participate in the same closure as notes, including replacement IDs.
    for row in payload["evidence"]:
        key = "evidence:" + row["id"]
        if set(evidence_pattern.findall("\n".join(str(value) for value in row.values()))) - evidence_ids:
            withheld.add(key)
        if not per_row:
            if register_id not in docs:
                withheld.add(key)
            dependents[register_id].add(key)
        if pattern is not None:
            for match in pattern.finditer("\n".join(str(value) for value in row.values())):
                for target in markers[match.group()] - {key}:
                    dependents[target].add(key)
        if title_pattern is not None:
            for match in title_pattern.finditer("\n".join(str(value) for value in row.values())):
                for target in titles[match.group()] - {key}:
                    dependents[target].add(key)
    pending = deque(withheld)
    while pending:
        while pending:
            hidden = pending.popleft()
            for dependent in dependents[hidden]:
                if dependent not in withheld:
                    if dependent in docs:
                        withhold(dependent, "withheld_dependency:" + hidden)
                    else:
                        withheld.add(dependent)
                    pending.append(dependent)
        # A longer visible marker may have masked a hidden prefix/substring.
        hidden_paths = _marker_pattern({marker for marker, targets in markers.items() if targets & withheld})
        hidden_titles = _marker_pattern({marker for marker, targets in titles.items() if targets & withheld})
        for key, item in [(key, doc) for key, doc in docs.items()] + [("evidence:" + row["id"], row) for row in payload["evidence"]]:
            if key in withheld:
                continue
            text = "\n".join(str(value) for value in item.values())
            content = "\n".join((item["title"], *item["aliases"], item["body"])) if key in docs else text
            if ((hidden_paths is not None and hidden_paths.search(text))
                    or (hidden_titles is not None and hidden_titles.search(content))):
                if key in docs:
                    withhold(key, "withheld_reference")
                else:
                    withheld.add(key)
                pending.append(key)
    visible = set(docs) - withheld
    hidden_markers = {marker for group in (markers, titles) for marker, targets in group.items() if targets & withheld}
    hidden_pattern = _marker_pattern(hidden_markers)
    findings = [item for item in payload["lint"]["findings"]
                if strip_markdown_suffix(item["path"]) in visible
                and (hidden_pattern is None or not hidden_pattern.search(item["path"] + "\n" + item["message"]))]
    public_index = index.subset(lambda note: paths[note.path.resolve()] in visible)
    evidence = [row for row in payload["evidence"] if "evidence:" + row["id"] not in withheld]
    return {
        **payload,
        "exportMode": "public",
        "vaultName": _vault_name(index.root, public_index, fallback="WhyKit public vault") if "README" in visible else "WhyKit public vault",
        "docs": [{**doc, "sensitivity": "public"} for doc in payload["docs"] if doc["id"] in visible],
        "evidence": evidence,
        "decisions": payload["decisions"] if "06-decisions/decision-log" in visible else [],
        "reviews": payload["reviews"] if "00-context/review-log" in visible else [],
        "lint": {"files": len(visible), "errors": sum(item["level"] == "error" for item in findings),
                 "warnings": sum(item["level"] == "warning" for item in findings), "findings": findings},
        "policy": {**payload["policy"], "evidenceAccessAgeDays": {
            kind: age for kind, age in payload["policy"]["evidenceAccessAgeDays"].items()
            if kind in {row["type"] for row in evidence}
        }},
    }


@consistent_read
@path_cache()
def build_publication_preview(root: Path, *, today: dt.date | None = None) -> dict:
    """Private diagnostics only; never included in an Explorer publication artifact."""
    root = root.resolve()
    config_path = root / "whykit.toml"
    if config_path.is_symlink():
        raise RuntimeError("unsafe publication input")
    config_bytes = config_path.read_bytes() if config_path.exists() else None
    payload, index = _build_explorer_index(root, today=today)
    reasons: dict[str, set[str]] = {}
    public = _public_payload(payload, index, reasons=reasons)
    files = {note.path for note in index.notes}
    if (root / "whykit.toml").exists():
        files.add(root / "whykit.toml")
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(index.root):
            raise RuntimeError("unsafe publication input")
        data = path.read_bytes()
        note = index.note_for(path)
        if note is not None and data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n") != note.text:
            raise RuntimeError("publication inputs changed during capture; retry")
        digest.update(index.relative(path).encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    if (config_path.read_bytes() if config_path.exists() else None) != config_bytes:
        raise RuntimeError("publication inputs changed during capture; retry")
    return {"contract_version": 1, "format": "whykit.publication-preview/v1", "private": True,
            "source_manifest_sha256": digest.hexdigest(), "publication_ready": payload["lint"]["errors"] == 0,
            "total_docs": len(payload["docs"]), "public_docs": len(public["docs"]),
            "withheld": [{"path": key + ".md", "reasons": sorted(values)} for key, values in sorted(reasons.items())]}


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


_H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


def _vault_name(root: Path, index: VaultIndex, *, fallback: str | None = None) -> str:
    """Name the vault after its README, never after the file name itself.

    A README without a front-matter title (the one `whykit init` writes) or
    with the placeholder title "README" would otherwise label the Explorer
    "README". Fall back to its first heading, then to the directory name.
    """
    note = index.note_for(root / "README.md")
    if note is not None:
        title = str(note.front.get("title") or "").strip()
        if title and title.casefold() != "readme":
            return title
        heading = _H1_RE.search(note.body)
        if heading and heading.group(1).strip().casefold() != "readme":
            return heading.group(1).strip()
    return fallback if fallback is not None else root.name or "WhyKit vault"


def _policy(root: Path) -> dict:
    """The parts of ``whykit.toml`` the Explorer needs to judge freshness.

    A malformed config is a lint error reported elsewhere; the Explorer then
    shows no thresholds rather than guessing at them.
    """
    try:
        config, _ = load_config(root)
    except ConfigError:
        return {"evidenceAccessAgeDays": {}, "decisionReviewDays": None, "statusDueDays": None}
    defaults = config.get("defaults", {})
    return {
        "evidenceAccessAgeDays": {str(k): int(v) for k, v in sorted(config.get("evidence_access_age_days", {}).items())},
        "decisionReviewDays": defaults.get("decision_review_days"),
        "statusDueDays": defaults.get("status_due_days"),
    }


@consistent_read
@path_cache()
def build_explorer_index(root: Path, *, today: dt.date | None = None, private: bool = False) -> dict:
    """Build the Explorer vault.json payload from the canonical Python parser."""
    payload, index = _build_explorer_index(root, today=today)
    return payload if private else _public_payload(payload, index)


@consistent_read
@path_cache()
def _build_explorer_index(root: Path, *, today: dt.date | None = None) -> tuple[dict, VaultIndex]:
    root = root.resolve()
    index = VaultIndex.load(root)
    registers = evidence_register_notes(index)
    register_paths = {note.path for note in registers}
    docs: list[dict] = []
    for note in index.notes:
        doc_id = strip_markdown_suffix(rel(root, note.path))
        # Every E-NNN the note cites, in front matter or body, outside code:
        # `Note.cited_evidence` is the one citation rule `whykit graph`,
        # `impact` and `trace` use. The register itself lists IDs rather than
        # citing them.
        citations = [] if note.path in register_paths else list(note.cited_evidence)
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
            "citations": citations,
            "tags": _as_list(note.front.get("tags")),
            "workstream": str(note.front.get("workstream") or top),
            "decisionId": str(note.front.get("decision_id") or "") or None,
            "supersedes": str(note.front.get("supersedes") or "") or None,
            "summary": _summary(body),
            "body": body.lstrip("\n"),
        })
    docs.sort(key=lambda item: item["id"])

    active, retired, _, _ = classified_evidence(index)
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
            "sensitivity": row["sensitivity"],
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
            "sensitivity": row["sensitivity"],
        })

    decisions = []
    decision_log = index.note_for(root / "06-decisions/decision-log.md")
    for row in decision_log_rows(root, text=decision_log.text if decision_log else ""):
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
    # The first row that supersedes an ID wins, as the old per-row scan did.
    first_successor: dict[str, dict] = {}
    for other in decisions:
        predecessor = other.get("supersedes")
        if predecessor is not None:
            first_successor.setdefault(predecessor, other)
    for row in decisions:
        newer = first_successor.get(row["id"])
        if newer:
            row["supersededBy"] = newer["id"]

    review_path = root / "00-context" / "review-log.md"
    reviews = []
    review_log = index.note_for(review_path)
    if review_log is not None:
        lines = review_log.text.splitlines()
        at = review_table_header(lines)
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

    register = registers[0] if len(registers) == 1 else None
    with evidence_view(root, _parse_evidence_register_text(register.text if register else "")):
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

    vault_name = _vault_name(root, index)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "vaultName": vault_name,
        "docs": docs,
        "evidence": evidence,
        "decisions": decisions,
        "reviews": reviews,
        "lint": lint_payload,
        "policy": _policy(root),
        "exportMode": "private",
    }
    return payload, index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit explorer-index",
        description="Export the Explorer vault index from the canonical Python parser.",
    )
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--json", action="store_true", default=True, help="emit JSON (default)")
    parser.add_argument("--today", help="evaluate lint review dates as of this ISO date")
    parser.add_argument("--publication-preview", action="store_true", help="private withheld-path diagnostics; never publish this report")
    parser.add_argument("--private", action="store_true", help="include non-public content; never publish this index")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=True)
    if args.publication_preview:
        if args.private:
            return emit_error("invalid_argument", "--publication-preview and --private are mutually exclusive", json_mode=True)
        emit_machine(json.dumps(build_publication_preview(root, today=today), ensure_ascii=True, indent=2))
        return 0
    with vault_read_lock(root):
        payload, index = _build_explorer_index(root, today=today)
        if payload["lint"]["errors"] > 0:
            details = "\n".join(
                f"- {item['path']}: {item['message']}"
                for item in payload["lint"]["findings"]
                if item["level"] == "error"
            )
            summary = f"Vault has {payload['lint']['errors']} lint error(s); Explorer index not generated." if args.private else "Vault has lint errors; public Explorer index not generated."
            return emit_error(
                "vault_invalid",
                summary + ("\n" + details if args.private and details else ""),
                json_hint="run `whykit lint` to see every finding",
                json_mode=True,
            )
        # JSON escapes preserve Unicode content without requiring a UTF-8 console.
        if not args.private:
            payload = _public_payload(payload, index)
    emit_machine(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0
