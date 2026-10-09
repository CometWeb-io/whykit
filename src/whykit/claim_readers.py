"""Request-scoped claim assessment and parallel reader projections."""

from __future__ import annotations
import datetime as dt
import re
import tomllib
from functools import wraps
from typing import Any
from pathlib import Path
from .config import ConfigError, load_config
from .vault_index import VaultIndex


def claim_view(index: VaultIndex) -> dict | None:
    from .claims import capture_claims, claims_enabled

    if "claims_view" in index.derived:
        view = index.derived["claims_view"]
        return view if view is not None and claims_enabled(view["config"]) else None
    try:
        config, _ = load_config(index.root)
    except ConfigError:
        config_path = index.root / "whykit.toml"
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        try:
            declares_claims = "claims" in tomllib.loads(text.removeprefix("\ufeff"))
        except tomllib.TOMLDecodeError:
            declares_claims = bool(re.search(r"(?m)^\s*\[\s*claims\s*\]", text))
        if any(note.front.get("claim_id") or note.front.get("claim_ids") for note in index.notes) or (
            declares_claims
        ):
            raise
        index.derived["claims_view"] = None
        return None
    view = capture_claims(index, config) if claims_enabled(config) else None
    index.derived["claims_view"] = view
    return view


def report_version(root: Path) -> int:
    try:
        return 2 if load_config(root)[0].get("claims") == {"format_version": 1} else 1
    except ConfigError:
        return 1


def assessments(index: VaultIndex, *, today: dt.date) -> dict:
    from .claims import evaluate_claims

    view = claim_view(index)
    if view is None:
        return {}
    key = ("claim_assessments", today.isoformat())
    if key not in index.derived:
        index.derived[key] = {
            cid: {
                **item,
                "relations": [
                    {k: v for k, v in row.items() if k != "fragment_text"}
                    for row in item["relations"]
                ],
            }
            for cid, item in evaluate_claims(view, today=today).items()
        }
    return index.derived[key]


def claim_reader(command: str):
    """Share one captured index across existing builders and their nested calls."""

    def decorate(build):
        @wraps(build)
        def read(root: Path, *args, claims: dict | None = None, **kwargs):
            index = kwargs.get("vault") or VaultIndex.load(root)
            kwargs["vault"] = index
            if claims is not None:
                index.derived["claims_view"] = claims
            view = claim_view(index)
            allowed = kwargs.get("allowed_sensitivities")
            if kwargs.get("sensitivity"):
                allowed = {kwargs["sensitivity"]} if allowed is None else allowed & {kwargs["sensitivity"]}
            if view is not None and allowed is not None:
                from .sensitivity import visible_claim_view
                index, view = visible_claim_view(index, {**view, "root": index.root}, ceiling="restricted", allowed_sensitivities=allowed)
                kwargs["vault"] = index
            result = build(root, *args, **kwargs)
            if view is None:
                return result
            return project_report(
                command, result, index, today=kwargs.get("today") or dt.date.today()
            )

        return read

    return decorate


def _note_summary(index: VaultIndex, note) -> dict:
    from .impact import _note_summary as summarize

    return {
        **summarize(index.root, note),
        "claim_id": note.front.get("claim_id"),
        "decision_id": note.front.get("decision_id"),
    }


def project_report(
    command: str, result: dict, index: VaultIndex, *, today: dt.date
) -> dict:
    view = claim_view(index)
    if view is None:
        return result
    states = assessments(index, today=today)
    result["contract_version"] = 2
    paths = {
        cid: record["path"].removesuffix(".md")
        for cid, record in view["records"].items()
    }
    by_path = {index.relative(n.path): n for n in index.notes}
    note: Any

    def selected(note):
        ids = (
            [note.front["claim_id"]]
            if note and note.front.get("claim_id")
            else note.front.get("claim_ids", [])
            if note
            else []
        )
        return [states[cid] for cid in ids if cid in states]

    if command == "graph":
        known = {n["id"] for n in result["nodes"]}
        claim_paths = set(paths.values())
        result["edges"] = [
            e
            for e in result["edges"]
            if not (e["from"] in claim_paths and e["type"] == "evidence")
        ]
        for note in index.notes:
            source = index.relative(note.path).removesuffix(".md")
            if source not in known:
                continue
            for cid in note.front.get("claim_ids", []):
                destination = paths.get(cid)
                if destination in known:
                    result["edges"].append(
                        {"from": source, "to": destination, "type": "claim"}
                    )
                else:
                    result["unresolved"].append(
                        {
                            "from": source,
                            "target": cid,
                            "type": "claim",
                            "reason": "filtered" if destination else "missing",
                        }
                    )
            cid = note.front.get("claim_id")
            if cid in states:
                node = next(n for n in result["nodes"] if n["id"] == source)
                node.update(
                    claim_id=cid, verification_status=states[cid]["verification_status"]
                )
                for row in view["records"][cid]["relations"]:
                    eid = row["evidence_id"]
                    destination = "evidence:" + eid
                    if destination not in known:
                        data = view["evidence"].get(eid, {})
                        result["nodes"].append(
                            {
                                "id": destination,
                                "kind": "evidence",
                                "evidence_id": eid,
                                "title": data.get("source") or eid,
                                "status": data.get("state", "missing"),
                                "type": data.get("type", ""),
                                "owner": "",
                                "sensitivity": data.get("sensitivity", ""),
                                "source_of_truth": False,
                            }
                        )
                        known.add(destination)
                    result["edges"].append(
                        {
                            "from": source,
                            "to": destination,
                            "type": row["relation"],
                            "source_snapshot_hash": row["source_snapshot_hash"],
                            "fragment": row["fragment"],
                        }
                    )
        result["edges"].sort(
            key=lambda e: (e["from"], e["to"], e["type"], e.get("fragment", ""))
        )
        result["nodes"].sort(key=lambda n: n["id"])
        result["stats"].update(nodes=len(result["nodes"]), edges=len(result["edges"]), unresolved=len(result["unresolved"]))
        counts: dict[str, int] = {}
        for e in result["edges"]:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        result["stats"]["edges_by_type"] = counts
    elif command == "trace":
        from .claim_review import decision_claim_review_current

        for item in result["decisions"]:
            note = by_path.get(item["path"])
            item["claims"] = selected(note)
            item["direct_evidence"] = [
                row for row in item["evidence"] if row.get("via") is None
            ]
            ids = note.front.get("claim_ids", []) if note else []
            item["claim_gaps"] = [
                cid
                for cid in ids
                if cid not in states
                or states[cid]["verification_status"] == "unknown"
                or not states[cid]["binding_valid"]
            ]
            if ids and not decision_claim_review_current(
                {**view, "root": index.root}, note, today=today
            ):
                item["claim_gaps"].append("decision_review_binding_changed")
            if item["claims"] and not item["claim_gaps"]:
                item["gaps"] = [gap for gap in item["gaps"] if gap != "no_evidence"]
            item["evidence_view"] = "legacy_citations"
        live = [r for r in result["decisions"] if r["live"]]

        result["summary"]["live_with_gaps"] = sum(
            bool(r["gaps"] or r["claim_gaps"]) for r in live
        )
        result["summary"]["gaps"] = {
            g: sum(g in r["gaps"] for r in live) for g in result["summary"]["gaps"]
        }
    elif command == "impact":
        target = result["target"]
        if result["kind"] == "evidence":
            refs = {r["path"]: r for r in result["references"]}
            linked = {
                cid
                for cid, r in view["records"].items()
                if any(edge["evidence_id"] == target for edge in r["relations"])
            }
            for note in index.notes:
                cid = note.front.get("claim_id")
                if cid in linked:
                    refs[index.relative(note.path)] = {
                        **_note_summary(index, note),
                        "claim": states[cid],
                    }
                for used in note.front.get("claim_ids", []):
                    if used in linked:
                        path = index.relative(note.path)
                        entry = refs.setdefault(path, _note_summary(index, note))
                        entry["via_claim"] = used
                        entry["via_claims"] = sorted(
                            set(entry.get("via_claims", [])) | {used}
                        )
            result["references"] = sorted(refs.values(), key=lambda r: r["path"])
            result["reference_count"] = len(refs)
        elif result.get("exists"):
            note = by_path.get(result["record"]["path"])
            result["claims"] = selected(note)
            if note and note.front.get("claim_id") in states:
                cid = note.front["claim_id"]
                result["kind"] = "claim"
                result["claim"] = states[cid]
                dependencies = [
                    _note_summary(index, n)
                    for n in index.notes
                    if cid in n.front.get("claim_ids", [])
                ]
                existing = {
                    (r.get("path") or r["id"]).removesuffix(".md"): r
                    for r in result.get("incoming", [])
                }
                existing.update(
                    {r["path"].removesuffix(".md"): r for r in dependencies}
                )
                result["incoming"] = [existing[key] for key in sorted(existing)]
                result["reference_count"] = len(existing)
    elif command == "query":
        for item in result["results"]:
            note = by_path[item["path"]]
            if note.front.get("claim_id"):
                item["claim_id"] = note.front["claim_id"]
                item["claim"] = states.get(item["claim_id"])
            if note.front.get("claim_ids"):
                item["claim_ids"] = note.front["claim_ids"]
    elif command == "context" and result.get("exists") and result["kind"] != "evidence":
        note = by_path.get(result.get("record", {}).get("path"))
        result["claims"] = selected(note)
        if note and note.front.get("claim_id") in states:
            result["kind"] = "claim"
            result["claim"] = states[note.front["claim_id"]]
    elif command == "pack":
        from .context import build_context

        result["format"] = "whykit.context-bundle/v2"
        seen = {c.get("record", {}).get("path") for c in result["contexts"]}
        ids = {a["claim_id"] for c in result["contexts"] for a in c.get("claims", [])}
        used = result["budget"]["used_chars"]
        for cid in sorted(ids):
            path = view["records"][cid]["path"]
            if (
                path not in seen
                and len(result["contexts"]) < result["budget"]["max_docs"]
            ):
                context = build_context(
                    index.root,
                    cid,
                    max_chars=max(0, result["budget"]["max_chars"] - used),
                    vault=index,
                )
                used += len(context.get("content", ""))
                result["contexts"].append(
                    {"origin": "claim", "content_trust": "untrusted_data", **context}
                )
                result["selected"].append(cid)
                seen.add(path)
            elif path not in seen:
                result["missing"].append({"target": cid, "origin": "claim", "reason": "max_docs"})
        for context in result["contexts"]:
            if context["kind"] != "claim":
                continue
            cid = context["claim"]["claim_id"]
            fragments = []
            for row in view["records"][cid]["relations"]:
                available = max(0, result["budget"]["max_chars"] - used)
                text = row.get("fragment_text", "")[:available]
                used += len(text)
                fragments.append(
                    {
                        "evidence_id": row["evidence_id"],
                        "relation": row["relation"],
                        "fragment": row["fragment"],
                        "source_snapshot_hash": row["source_snapshot_hash"],
                        "content": text,
                        "content_truncated": len(text)
                        < len(row.get("fragment_text", "")),
                        "content_trust": "untrusted_data",
                    }
                )
            context["fragments"] = fragments
        result["claims"] = [states[cid] for cid in sorted(ids)]
        result["resolved"] = len(result["contexts"])
        result["budget"].update(
            used_chars=used,
            remaining_chars=max(0, result["budget"]["max_chars"] - used),
            exhausted=(used >= result["budget"]["max_chars"] and bool(result["contexts"]))
            or any(row["reason"] == "max_docs" for row in result["missing"]),
        )
    elif command == "status":
        result["claims"] = list(states.values())
    return result
