"""What changed in the decisions between two Git revisions.

``whykit diff --base REV`` answers the reviewer's question on a pull request:
which decisions were added, superseded or archived, whose review date moved,
which evidence was added, retired or re-sourced, which decisions now rest on
evidence that changed, and which lint findings the change introduces or fixes.

Both sides are read straight from Git objects (``git ls-tree`` and ``git
cat-file``) into private temporary directories; the working tree, the index
and ``HEAD`` are never touched, so the command is safe in a dirty checkout and
compares exactly what was committed. Like ``whykit history`` it compares the
merge base of the two revisions with the head, which is what a pull request
shows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .console import emit_machine
from .contract import CONTRACT_VERSION, emit_error
from .immutability import _diff_entries, _git_arg, _git_prefix, _require_revisions, git
from .lint import (
    DECISION_ID_RE,
    EVIDENCE_ID_RE,
    TEXT_SECRET_EXTENSIONS,
    Finding,
    _as_list,
    _parse_date,
    evidence_register,
    find_vault_root,
    is_vault_root,
    lint,
)

FORMATS = ("text", "json", "markdown", "github")
# The first line of every Markdown report. The Action finds its own sticky
# comment by this marker, so it must stay stable.
MARKER_PREFIX = "<!-- whykit-diff"
# A pull request comment is capped at 65,536 characters by GitHub.
MARKDOWN_LIMIT = 60000
# Entries shown per Markdown section before "... and N more".
SECTION_LIMIT = 25
# Evidence fields whose change means the claim now rests on another source.
SOURCE_FIELDS = ("source", "location")
OTHER_FIELDS = ("type", "date", "accessed", "claims")
_LINE_REF_RE = re.compile(r"\bline \d+\b")


@dataclass
class _Side:
    """One revision of the vault, read from Git."""

    present: bool = False
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    active: dict[str, dict[str, str]] = field(default_factory=dict)
    retired: dict[str, dict[str, str]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


# --------------------------------------------------------------------------
# Reading a revision without checking it out


def _tree_spec(rev: str, prefix: str) -> str:
    return f"{rev}:{prefix.rstrip('/')}" if prefix else f"{rev}^{{tree}}"


def _list_tree(rev: str, prefix: str, root: str) -> list[tuple[str, str, str, str]] | None:
    """``(mode, type, object, path)`` for every entry below the vault, or None.

    None means the vault directory does not exist in *rev* (a vault added or
    removed by the change). ``--full-tree`` matters: run from a subdirectory,
    ``ls-tree`` would otherwise filter the listing by the working directory.
    """
    spec = _tree_spec(rev, prefix)
    try:
        if git("cat-file", "-t", spec, root=root).strip() != "tree":
            return None
    except subprocess.CalledProcessError:
        return None
    out = git("ls-tree", "-r", "-z", "--full-tree", spec, root=root)
    entries: list[tuple[str, str, str, str]] = []
    for token in out.split("\0"):
        if not token:
            continue
        meta, _, path = token.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path:
            raise subprocess.CalledProcessError(
                128, ["git", "ls-tree", "-r", "-z"], output=out,
                stderr="could not parse `git ls-tree -r -z` output",
            )
        entries.append((parts[0], parts[1], parts[2], path))
    return entries


def _read_blobs(oids: list[str], root: str) -> dict[str, bytes]:
    """Read many blobs with one ``git cat-file --batch`` process."""
    if not oids:
        return {}
    unique = list(dict.fromkeys(oids))
    result = subprocess.run(
        ["git", "-C", _git_arg(root), "cat-file", "--batch"],
        input=("\n".join(unique) + "\n").encode("ascii"),
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["git", "cat-file", "--batch"], output="",
            stderr=result.stderr.decode("utf-8", "replace"),
        )
    data = result.stdout
    blobs: dict[str, bytes] = {}
    at = 0
    for oid in unique:
        newline = data.index(b"\n", at)
        header = data[at:newline].decode("ascii", "replace").split()
        at = newline + 1
        if len(header) != 3:
            raise subprocess.CalledProcessError(
                128, ["git", "cat-file", "--batch"], output="", stderr=f"cannot read object {oid}",
            )
        size = int(header[2])
        blobs[oid] = data[at : at + size]
        at += size + 1  # the content is followed by a newline
    return blobs


def _safe_parts(path: str) -> tuple[str, ...] | None:
    """The path's components, or None for one that must not be written."""
    parts = tuple(path.split("/"))
    if any(part in ("", ".", "..", ".git") for part in parts):
        return None
    if os.name == "nt" and any(("\\" in part or ":" in part) for part in parts):
        return None
    return parts


def _wanted_content(name: str) -> bool:
    """Whether lint reads the file; anything else only needs to exist."""
    suffix = PurePosixPath(name).suffix.lower()
    return name.startswith(".env") or suffix in TEXT_SECRET_EXTENSIONS


def materialize(rev: str, prefix: str, root: str, dest: Path) -> bool:
    """Write the vault as it is in *rev* under *dest*; False when it is absent.

    Only Git objects are read. Symbolic links and submodules are skipped (the
    linter refuses to follow links anyway), and files the linter never opens
    are written empty so a large attachment costs nothing.
    """
    entries = _list_tree(rev, prefix, root)
    if entries is None:
        return False
    files: list[tuple[tuple[str, ...], str, bool]] = []
    for mode, kind, oid, path in entries:
        if kind != "blob" or mode == "120000":
            continue
        parts = _safe_parts(path)
        if parts is None:
            continue
        files.append((parts, oid, _wanted_content(parts[-1])))
    blobs = _read_blobs([oid for _, oid, wanted in files if wanted], root)
    base = dest.resolve()
    for parts, oid, wanted in files:
        target = base.joinpath(*parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                if wanted:
                    handle.write(blobs[oid])
        except (OSError, UnicodeError, ValueError):
            # A name this filesystem cannot hold (surrogates on Windows, a
            # case-insensitive clash) is skipped, not fatal.
            continue
    return True


# --------------------------------------------------------------------------
# What a revision says


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip()


def _read_side(directory: Path, present: bool, today: dt.date) -> _Side:
    if not present or not is_vault_root(directory):
        return _Side(present=False)
    from .vault_index import VaultIndex

    vault = VaultIndex.load(directory)
    decisions: dict[str, dict[str, Any]] = {}
    for note in sorted(vault.notes, key=lambda item: vault.relative(item.path)):
        own = _text(note.front.get("decision_id"))
        if not DECISION_ID_RE.fullmatch(own) or _text(note.front.get("status")) == "template":
            continue
        if own in decisions:
            continue  # lint reports the duplicate; the first record wins here
        decisions[own] = {
            "id": own,
            "title": _text(note.front.get("title")),
            "status": _text(note.front.get("status")),
            "review_by": _text(note.front.get("review_by")) or None,
            "supersedes": sorted({sid for sid in _as_list(note.front.get("supersedes")) if DECISION_ID_RE.fullmatch(sid)}),
            "superseded_by": sorted({sid for sid in _as_list(note.front.get("superseded_by")) if DECISION_ID_RE.fullmatch(sid)}),
            "path": vault.relative(note.path),
            "cites": list(note.cited_evidence),
        }
    active, retired, _ = evidence_register(directory)
    _, findings = lint(directory, today=today, vault=vault)
    return _Side(True, decisions, active, retired, findings)


# --------------------------------------------------------------------------
# Comparing two revisions


def _id_key(identifier: str) -> tuple[int, str]:
    digits = identifier.split("-", 1)[-1]
    return (int(digits) if digits.isdigit() else 0, identifier)


def _chain(decision_id: str, decisions: dict[str, dict[str, Any]]) -> list[str]:
    """The supersession line through *decision_id*, newest first.

    Both directions count: a newer record's ``supersedes`` and an older
    record's optional ``superseded_by``.
    """
    newer: dict[str, set[str]] = {}
    older: dict[str, set[str]] = {}
    for record in decisions.values():
        for old in record["supersedes"]:
            newer.setdefault(old, set()).add(record["id"])
            older.setdefault(record["id"], set()).add(old)
        for new in record["superseded_by"]:
            newer.setdefault(record["id"], set()).add(new)
            older.setdefault(new, set()).add(record["id"])
    tip = decision_id
    seen = {tip}
    while newer.get(tip):
        candidates = sorted(newer[tip] - seen, key=_id_key)
        if not candidates:
            break
        tip = candidates[-1]
        seen.add(tip)
    chain = [tip]
    while older.get(chain[-1]):
        candidates = sorted(older[chain[-1]] - set(chain), key=_id_key)
        if not candidates:
            break
        chain.append(candidates[-1])
    return chain


def _decision_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("id", "title", "status", "path")}


def _review_direction(before: str | None, after: str | None) -> str:
    if not before:
        return "set"
    if not after:
        return "cleared"
    old, new = _parse_date(before), _parse_date(after)
    if old is None or new is None:
        return "changed"
    return "later" if new > old else "earlier"


def _compare_decisions(base: _Side, head: _Side) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "added": [], "removed": [], "superseded": [], "archived": [],
        "status_changed": [], "review_moved": [], "moved": [],
    }
    for did in sorted(set(base.decisions) | set(head.decisions), key=_id_key):
        before, after = base.decisions.get(did), head.decisions.get(did)
        if after is None and before is not None:
            out["removed"].append(_decision_summary(before))
            continue
        assert after is not None
        if before is None:
            out["added"].append({
                **_decision_summary(after),
                "supersedes": after["supersedes"],
                "chain": _chain(did, head.decisions),
            })
            continue
        if before["status"] != after["status"]:
            change = {**_decision_summary(after), "from": before["status"] or None, "to": after["status"] or None}
            if after["status"] in ("superseded", "archived"):
                replaced_by = sorted(
                    set(after["superseded_by"])
                    | {other["id"] for other in head.decisions.values() if did in other["supersedes"]},
                    key=_id_key,
                )
                out[after["status"]].append({**change, "superseded_by": replaced_by, "chain": _chain(did, head.decisions)})
            else:
                out["status_changed"].append(change)
        if before["review_by"] != after["review_by"]:
            out["review_moved"].append({
                **_decision_summary(after),
                "from": before["review_by"],
                "to": after["review_by"],
                "direction": _review_direction(before["review_by"], after["review_by"]),
            })
        if before["path"] != after["path"]:
            out["moved"].append({"id": did, "title": after["title"], "from": before["path"], "to": after["path"]})
    return out


def _evidence_row(eid: str, row: dict[str, str]) -> dict[str, Any]:
    return {"id": eid, **{key: row.get(key, "") for key in ("source", "type", "date", "accessed", "location", "claims")}}


def _retired_row(eid: str, row: dict[str, str]) -> dict[str, Any]:
    replaced = row.get("replaced_by", "").strip()
    return {
        "id": eid,
        "source": row.get("source", ""),
        "retired_on": row.get("retired_on", ""),
        "why": row.get("why", ""),
        "replaced_by": replaced if EVIDENCE_ID_RE.fullmatch(replaced) else None,
    }


def _changes(before: dict[str, str], after: dict[str, str], fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    return {
        key: {"from": before.get(key, ""), "to": after.get(key, "")}
        for key in fields
        if before.get(key, "") != after.get(key, "")
    }


def _compare_evidence(base: _Side, head: _Side) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"added": [], "retired": [], "resourced": [], "updated": [], "removed": []}
    ids = set(base.active) | set(base.retired) | set(head.active) | set(head.retired)
    for eid in sorted(ids, key=_id_key):
        if eid in head.retired:
            if eid not in base.retired:
                out["retired"].append(_retired_row(eid, head.retired[eid]))
            continue
        if eid in head.active:
            after = head.active[eid]
            if eid in base.active:
                before = base.active[eid]
                resourced = _changes(before, after, SOURCE_FIELDS)
                if resourced:
                    out["resourced"].append({"id": eid, "changes": resourced | _changes(before, after, OTHER_FIELDS)})
                elif updated := _changes(before, after, OTHER_FIELDS):
                    out["updated"].append({"id": eid, "changes": updated})
            elif eid in base.retired:
                # Back from retirement: a restored ID is a change to review.
                out["updated"].append({"id": eid, "changes": {"state": {"from": "retired", "to": "active"}}})
            else:
                out["added"].append(_evidence_row(eid, after))
            continue
        # Registered before, gone now: IDs are never supposed to disappear.
        row = base.active.get(eid) or base.retired.get(eid) or {}
        out["removed"].append({"id": eid, "source": row.get("source", "")})
    return out


def _affected_decisions(head: _Side, base: _Side, evidence: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    changed: dict[str, str] = {}
    for change in ("removed", "updated", "resourced", "retired"):
        for item in evidence[change]:
            changed[item["id"]] = change
    if not changed:
        return []
    affected = []
    decisions = head.decisions if head.present else base.decisions
    for did in sorted(decisions, key=_id_key):
        record = decisions[did]
        hits = [{"id": eid, "change": changed[eid]} for eid in record["cites"] if eid in changed]
        if hits:
            affected.append({**_decision_summary(record), "evidence": hits})
    return affected


def _finding_key(finding: Finding, rename: dict[str, str]) -> tuple[str, str, str, str]:
    path = rename.get(finding.path, finding.path)
    return (finding.level, finding.code, path, _LINE_REF_RE.sub("line N", finding.message))


def _compare_findings(base: _Side, head: _Side, rename: dict[str, str]) -> dict[str, Any]:
    def counts(side: _Side) -> dict[str, int]:
        return {
            "errors": sum(1 for finding in side.findings if finding.level == "error"),
            "warnings": sum(1 for finding in side.findings if finding.level == "warning"),
        }

    before = Counter(_finding_key(finding, rename) for finding in base.findings)
    after = Counter(_finding_key(finding, {}) for finding in head.findings)
    introduced: list[dict[str, Any]] = []
    budget = before.copy()
    for finding in head.findings:
        key = _finding_key(finding, {})
        if budget[key] > 0:
            budget[key] -= 1
        else:
            introduced.append(asdict(finding))
    fixed: list[dict[str, Any]] = []
    budget = after.copy()
    for finding in base.findings:
        key = _finding_key(finding, rename)
        if budget[key] > 0:
            budget[key] -= 1
        else:
            fixed.append({**asdict(finding), "path": rename.get(finding.path, finding.path)})

    def order(item: dict[str, Any]) -> tuple:
        return (item["level"] != "error", item["path"], item["line"] or 0, item["code"])

    return {
        "base": counts(base),
        "head": counts(head),
        "introduced": sorted(introduced, key=order),
        "fixed": sorted(fixed, key=order),
    }


def _renames(merge_base: str, head: str, root: str) -> dict[str, str]:
    out = git("diff", "--relative", "--name-status", "-z", "--no-ext-diff", "-M", merge_base, head, "--", ".", root=root)
    return {old: new for status, old, new in _diff_entries(out) if status.startswith("R")}


def _short(rev: str, root: str) -> str:
    return git("rev-parse", "--verify", f"{rev}^{{commit}}", root=root).strip()


def _merge_base(base: str, head: str, root: str) -> str:
    try:
        return git("merge-base", base, head, root=root).strip()
    except subprocess.CalledProcessError as exc:
        try:
            shallow = git("rev-parse", "--is-shallow-repository", root=root).strip() == "true"
        except subprocess.CalledProcessError:
            shallow = False
        detail = f"no merge base between {base} and {head}" + (" in this shallow clone" if shallow else "")
        hint = (
            "hint: fetch full history (actions/checkout `fetch-depth: 0`, or `git fetch --unshallow`)"
            if shallow
            else "hint: the two revisions share no history; pass a --base on the same line of history"
        )
        raise subprocess.CalledProcessError(exc.returncode, exc.cmd, output="", stderr=f"{detail}\n{hint}") from None


def build_diff(root: Path, base: str, head: str = "HEAD", *, today: dt.date | None = None) -> dict[str, Any]:
    """Compare the vault at the merge base of *base* and *head* with *head*."""
    today = today or dt.date.today()
    where = str(root)
    _require_revisions(where, base, head)
    merge_base = _merge_base(base, head, where)
    head_commit = _short(head, where)
    prefix = _git_prefix(where)
    with tempfile.TemporaryDirectory(prefix="whykit-diff-") as scratch:
        base_dir, head_dir = Path(scratch) / "base", Path(scratch) / "head"
        base_dir.mkdir()
        head_dir.mkdir()
        base_side = _read_side(base_dir, materialize(merge_base, prefix, where, base_dir), today)
        head_side = _read_side(head_dir, materialize(head_commit, prefix, where, head_dir), today)
    rename = _renames(merge_base, head_commit, where)
    decisions = _compare_decisions(base_side, head_side)
    evidence = _compare_evidence(base_side, head_side)
    affected = _affected_decisions(head_side, base_side, evidence)
    findings = _compare_findings(base_side, head_side, rename)
    changed = any(decisions.values()) or any(evidence.values()) or bool(findings["introduced"] or findings["fixed"])
    return {
        "contract_version": CONTRACT_VERSION,
        "root": str(root),
        "prefix": prefix,
        "base": base,
        "head": head,
        "merge_base": merge_base,
        "head_commit": head_commit,
        "today": today.isoformat(),
        "vault_at_base": base_side.present,
        "vault_at_head": head_side.present,
        "changed": changed,
        "decisions": decisions,
        "evidence": evidence,
        "affected_decisions": affected,
        "lint": findings,
    }


# --------------------------------------------------------------------------
# Rendering


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _summary_parts(report: dict[str, Any]) -> list[str]:
    decisions, evidence, findings = report["decisions"], report["evidence"], report["lint"]
    parts = []
    for key, label in (("added", "added"), ("superseded", "superseded"), ("archived", "archived"), ("removed", "removed")):
        if decisions[key]:
            parts.append(f"{len(decisions[key])} decision(s) {label}")
    if decisions["status_changed"]:
        parts.append(_plural(len(decisions["status_changed"]), "status change"))
    if decisions["review_moved"]:
        parts.append(_plural(len(decisions["review_moved"]), "review date move"))
    for key in ("added", "retired", "resourced", "updated", "removed"):
        if evidence[key]:
            parts.append(f"{len(evidence[key])} evidence {'re-sourced' if key == 'resourced' else key}")
    if report["affected_decisions"]:
        parts.append(f"{len(report['affected_decisions'])} decision(s) cite changed evidence")
    if findings["introduced"]:
        parts.append(f"{len(findings['introduced'])} new lint finding(s)")
    if findings["fixed"]:
        parts.append(f"{len(findings['fixed'])} lint finding(s) fixed")
    return parts


def _chain_text(chain: list[str]) -> str:
    return " → ".join(chain)


def _lines(report: dict[str, Any], fmt: str) -> list[tuple[str, list[str]]]:
    """Section title and entries, shared by the text and Markdown renderers."""
    esc = _md if fmt == "markdown" else (lambda value: value)
    code = (lambda value: f"`{_md_code(value)}`") if fmt == "markdown" else (lambda value: value)

    def name(item: dict[str, Any]) -> str:
        # Titles often repeat the ID ("D-003 — No paid acquisition").
        title = re.sub(rf"^{re.escape(item['id'])}\s*[\u2014\u2013:-]?\s*", "", item.get("title") or "")
        return f"**{esc(item['id'])}** {esc(title)}".rstrip() if fmt == "markdown" else f"{item['id']} {title}".rstrip()

    decisions, evidence = report["decisions"], report["evidence"]
    sections: list[tuple[str, list[str]]] = []
    entries = []
    for item in decisions["added"]:
        extra = f" (status {esc(item['status'])})" if item["status"] else ""
        chain = f"; chain {esc(_chain_text(item['chain']))}" if len(item["chain"]) > 1 else ""
        entries.append(f"{name(item)}{extra}{chain}")
    sections.append(("New decisions", entries))
    entries = []
    for key in ("superseded", "archived"):
        for item in decisions[key]:
            by = f" by {esc(', '.join(item['superseded_by']))}" if item["superseded_by"] else ""
            chain = f"; chain {esc(_chain_text(item['chain']))}" if len(item["chain"]) > 1 else ""
            entries.append(f"{name(item)}: {esc(item['from'] or 'none')} → {key}{by}{chain}")
    sections.append(("Superseded or archived", entries))
    entries = [f"{name(item)}: {esc(item['from'] or 'none')} → {esc(item['to'] or 'none')}" for item in decisions["status_changed"]]
    sections.append(("Status changes", entries))
    entries = [
        f"{name(item)}: {esc(item['from'] or 'none')} → {esc(item['to'] or 'none')} ({item['direction']})"
        for item in decisions["review_moved"]
    ]
    sections.append(("Review dates moved", entries))
    entries = [f"{name(item)} (record deleted)" for item in decisions["removed"]]
    entries += [f"{name(item)} moved {code(item['from'])} → {code(item['to'])}" for item in decisions["moved"]]
    sections.append(("Removed or moved records", entries))

    entries = [f"{name({'id': item['id'], 'title': item['source']})} added ({esc(item['type'])})" for item in evidence["added"]]
    for item in evidence["retired"]:
        replaced = f", replaced by {esc(item['replaced_by'])}" if item["replaced_by"] else ""
        why = f": {esc(item['why'])}" if item["why"] else ""
        entries.append(f"{name({'id': item['id'], 'title': item['source']})} retired{replaced}{why}")
    for key, verb in (("resourced", "re-sourced"), ("updated", "updated")):
        for item in evidence[key]:
            fields = ", ".join(
                f"{field_name} {esc(change['from'] or 'none')} → {esc(change['to'] or 'none')}"
                for field_name, change in item["changes"].items()
            )
            entries.append(f"{name({'id': item['id'], 'title': ''})} {verb}: {fields}")
    entries += [f"{name({'id': item['id'], 'title': item['source']})} removed from the register" for item in evidence["removed"]]
    sections.append(("Evidence", entries))

    entries = []
    for item in report["affected_decisions"]:
        cited = ", ".join(f"{esc(hit['id'])} {hit['change']}" for hit in item["evidence"])
        status = f" ({esc(item['status'])})" if item["status"] else ""
        entries.append(f"{name(item)}{status} cites {cited}")
    sections.append(("Decisions whose evidence changed", entries))

    def finding(item: dict[str, Any]) -> str:
        where = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
        return f"{item['level']} {code(item['code'])} {code(where)} {esc(item['message'])}"

    sections.append(("New lint findings", [finding(item) for item in report["lint"]["introduced"]]))
    sections.append(("Fixed lint findings", [finding(item) for item in report["lint"]["fixed"]]))
    return sections


def render_text(report: dict[str, Any]) -> str:
    out = [f"whykit diff: {report['merge_base'][:12]}..{report['head_commit'][:12]} ({report['root']})"]
    for title, entries in _lines(report, "text"):
        if entries:
            out.append(f"\n{title}")
            out.extend(f"  {entry}" for entry in entries)
    parts = _summary_parts(report)
    lint = report["lint"]
    out.append("")
    out.append("; ".join(parts) if parts else "no decision, evidence or lint changes")
    out.append(
        f"lint: {lint['base']['errors']} → {lint['head']['errors']} error(s), "
        f"{lint['base']['warnings']} → {lint['head']['warnings']} warning(s)"
    )
    return "\n".join(out)


def _md(value: object) -> str:
    """Make vault text inert in a pull request comment.

    Titles and messages come from the change under review. They must not open
    HTML, break a list item, mention people or teams, or inject links.
    """
    text = " ".join(str(value).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"([\\`*_\[\]#|~!])", r"\\\1", text)
    return text.replace("@", "&#64;")


def _md_code(value: object) -> str:
    """Text inside a code span: no backticks, no line breaks."""
    return " ".join(str(value).split()).replace("`", "'")


def marker(report: dict[str, Any]) -> str:
    """The hidden first line that identifies this vault's sticky comment."""
    vault = report["prefix"].rstrip("/") or "."
    return f"{MARKER_PREFIX} root={_md_code(vault).replace('--', '-')} -->"


def render_markdown(report: dict[str, Any]) -> str:
    vault = report["prefix"].rstrip("/") or "."
    out = [
        marker(report),
        "### WhyKit: what changed in the decisions",
        "",
        f"Vault `{_md_code(vault)}`, comparing `{report['merge_base'][:12]}` (merge base) with `{report['head_commit'][:12]}`.",
        "",
    ]
    parts = _summary_parts(report)
    out.append(("**Summary:** " + "; ".join(parts) + ".") if parts else "No decision, evidence or lint changes.")
    lint = report["lint"]
    out.append("")
    out.append(
        f"Lint: {lint['base']['errors']} → {lint['head']['errors']} error(s), "
        f"{lint['base']['warnings']} → {lint['head']['warnings']} warning(s) "
        f"(as of {report['today']})."
    )
    for title, entries in _lines(report, "markdown"):
        if not entries:
            continue
        out.append("")
        out.append(f"#### {title}")
        out.append("")
        out.extend(f"- {entry}" for entry in entries[:SECTION_LIMIT])
        if len(entries) > SECTION_LIMIT:
            out.append(f"- … and {len(entries) - SECTION_LIMIT} more (run `whykit diff` locally for the full list)")
    out.append("")
    out.append("<sub>Generated by `whykit diff`; this comment is updated on every push.</sub>")
    text = "\n".join(out) + "\n"
    if len(text) > MARKDOWN_LIMIT:
        cut = text.rfind("\n", 0, MARKDOWN_LIMIT - 200)
        text = text[:cut] + "\n\n… truncated; run `whykit diff` locally for the full report.\n"
    return text


def render_github(report: dict[str, Any]) -> str:
    from .ci_formats import _source_path, rule_help_uri, workflow_command

    prefix = report["prefix"]
    out: list[str] = []
    decisions = report["decisions"]
    for item in decisions["added"]:
        chain = f"; chain {_chain_text(item['chain'])}" if len(item["chain"]) > 1 else ""
        out.append(workflow_command("notice", f"{item['id']} added: {item['title']}{chain}", file=_source_path(item["path"], prefix), title="WhyKit new decision"))
    for key in ("superseded", "archived"):
        for item in decisions[key]:
            by = f" by {', '.join(item['superseded_by'])}" if item["superseded_by"] else ""
            out.append(workflow_command("notice", f"{item['id']} {key}{by}: {item['title']}", file=_source_path(item["path"], prefix), title=f"WhyKit decision {key}"))
    for item in decisions["review_moved"]:
        out.append(workflow_command(
            "notice", f"{item['id']} review_by {item['from'] or 'none'} -> {item['to'] or 'none'} ({item['direction']})",
            file=_source_path(item["path"], prefix), title="WhyKit review date moved",
        ))
    for item in report["affected_decisions"]:
        cited = ", ".join(f"{hit['id']} {hit['change']}" for hit in item["evidence"])
        out.append(workflow_command(
            "warning", f"{item['id']} cites evidence that changed: {cited}",
            file=_source_path(item["path"], prefix), title="WhyKit evidence changed",
        ))
    for finding in report["lint"]["introduced"]:
        out.append(workflow_command(
            "error" if finding["level"] == "error" else "warning",
            f"{finding['message']} ({rule_help_uri(finding['code'])})",
            file=_source_path(finding["path"], prefix), line=finding["line"],
            title=f"WhyKit {finding['code']} (new)",
        ))
    parts = _summary_parts(report)
    out.append("whykit diff: " + ("; ".join(parts) if parts else "no decision, evidence or lint changes"))
    return "\n".join(out)


# --------------------------------------------------------------------------
# Command line


def _resolve_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return find_vault_root() or Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whykit diff",
        description="Show what changed in decisions, evidence and lint findings between two Git revisions.",
    )
    parser.add_argument("--base", required=True, help="base commit or ref, e.g. origin/main")
    parser.add_argument("--head", default="HEAD", help="head commit or ref (default: HEAD)")
    parser.add_argument("--root", help="vault root (may be a subdirectory of the Git work tree)")
    parser.add_argument("--today", help="evaluate lint review dates as of this ISO date")
    parser.add_argument("--format", choices=FORMATS, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.json and args.format not in (None, "json"):
        return emit_error(
            "usage",
            f"--json conflicts with --format {args.format}\nhint: pass one of them to `whykit diff`",
            json_mode=True,
        )
    fmt = "json" if args.json else (args.format or "text")
    json_mode = fmt == "json"
    today = None
    if args.today:
        today = _parse_date(args.today)
        if today is None:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=json_mode)
    root = _resolve_root(args.root)
    if not root.is_dir():
        return emit_error(
            "invalid_argument",
            f"--root is not a directory: {args.root}\nhint: pass the vault directory as it exists in the working tree",
            json_mode=json_mode,
        )
    try:
        report = build_diff(root, args.base, args.head, today=today)
    except subprocess.CalledProcessError as exc:
        return emit_error("git_error", (exc.stderr or str(exc)).rstrip(), json_mode=json_mode)
    except FileNotFoundError:
        return emit_error(
            "missing_dependency",
            "git is not installed or not on PATH\nhint: `whykit diff` reads both revisions from Git",
            json_mode=json_mode,
        )
    if not report["vault_at_base"] and not report["vault_at_head"]:
        print(
            f"warning: no WhyKit vault at {report['prefix'] or './'} in either revision; nothing was compared\n"
            "hint: pass --root <vault> when the vault is a subdirectory of the repository",
            file=sys.stderr,
        )
    if fmt == "json":
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    elif fmt == "markdown":
        emit_machine(render_markdown(report).rstrip("\n"))
    elif fmt == "github":
        emit_machine(render_github(report))
    else:
        print(render_text(report))
    return 0
