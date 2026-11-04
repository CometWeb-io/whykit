"""Opt-in bounded lexical/typed-link ranking over a captured permission view.

Scores describe retrieval relevance, never factual support or execution rights.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from . import dependency_graph
from .io import consistent_read
from .knowledge_visibility import canonical_decision_id
from .lint import Note, path_cache
from .query import _summary
from .retrieval import _score_bm25
from .sensitivity import SENSITIVITY_LEVEL, is_claim_note, is_outcome_note, note_sensitivity_level
from .temporal_graph import _validity
from .vault_index import VaultIndex

MAX_CONSTRUCTION_NODES = 25_000
MAX_VISIBLE_BYTES = 64 * 1024 * 1024
MAX_TOKENS = 2_000_000
MAX_CONSTRUCTED_EDGES = 200_000
MAX_QUERY_BYTES = 4096
_WEIGHTS = {"evidence": (1.0, .5), "wikilink": (.5, .25),
            "outcome_decision": (.5, .25), "measurement_claim": (.5, .25)}
_LOGS = {"00-context/review-log.md", "06-decisions/decision-log.md"}
_NOT_DECISION = {"state": "not_decision", "premise": False, "reasons": [],
                 "declared_validity": {"state": "unknown", "valid_from": None, "valid_until": None}}


class HybridRetrievalError(ValueError):
    """Fixed, redacted error for invalid or unavailable inputs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _validate(text, valid_on, validity, decision_role, filters, allowed, canonical_only, limits):
    if (not isinstance(text, str) or type(valid_on) is not dt.date
            or not isinstance(validity, str) or validity not in {"active_or_unknown", "active"}
            or not isinstance(decision_role, str) or decision_role not in {"premise", "reference"} or type(canonical_only) is not bool):
        raise HybridRetrievalError("invalid_inputs")
    try:
        if len(text.encode("utf-8")) > MAX_QUERY_BYTES:
            raise HybridRetrievalError("invalid_inputs")
    except UnicodeError as exc:
        raise HybridRetrievalError("invalid_inputs") from exc
    if any(value is not None and (not isinstance(value, str) or not value or len(value) > 512
                                  or any(ord(c) < 32 for c in value)) for value in filters):
        raise HybridRetrievalError("invalid_inputs")
    if filters[4] is not None and re.fullmatch(r"E-[0-9]{3,}", filters[4]) is None:
        raise HybridRetrievalError("invalid_inputs")
    if filters[3] is not None and filters[3] not in SENSITIVITY_LEVEL:
        raise HybridRetrievalError("invalid_inputs")
    if allowed is not None and (not isinstance(allowed, set) or any(name not in SENSITIVITY_LEVEL for name in allowed)):
        raise HybridRetrievalError("invalid_inputs")
    for value, minimum, maximum in zip(limits, (0, 1, 0, 1, 0), (100, 16, 2, 256, 2048), strict=True):
        if type(value) is not int or not minimum <= value <= maximum:
            raise HybridRetrievalError("invalid_inputs")


def _iter_tokens(text: str):
    for match in re.finditer(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold()):
        yield match.group()


def _hash(value) -> str:
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _route_key(explanation: dict) -> tuple:
    return (tuple((edge["from"], edge["to"], edge["type"], edge["walk_direction"])
                  for edge in explanation["edges"]), explanation["seed"], tuple(explanation["nodes"]))


@path_cache()
def rank_prepared(
    index: VaultIndex, text: str, *, rows: tuple, metadata: dict, document_hashes: dict,
    valid_on: dt.date, validity: str = "active_or_unknown", decision_role: str = "premise",
    doc_type: str | None = None, status: str | None = None, owner: str | None = None,
    sensitivity: str | None = None, allowed_sensitivities: set[str] | None = None,
    source_id: str | None = None, tag: str | None = None, canonical_only: bool = False,
    limit: int = 20, seed_limit: int = 16, max_depth: int = 2, max_nodes: int = 256, max_edges: int = 2048,
    _matched_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Rank an internal, already captured grant without reopening source content.

    Metadata is supplied by the local governance evaluator, not by a wire client.
    The private matched-path collector receives all positive paths before limiting,
    for exact compiler omission accounting; it is never part of the wire report.
    Active E rows alone are intermediates. E rows have unknown valid time.
    """
    _validate(text, valid_on, validity, decision_role, (doc_type, status, owner, sensitivity, source_id, tag),
              allowed_sensitivities, canonical_only, (limit, seed_limit, max_depth, max_nodes, max_edges))
    allowed = {"public"} if allowed_sensitivities is None else set(allowed_sensitivities)
    levels = {SENSITIVITY_LEVEL[name] for name in allowed}
    query = dict(text=text, doc_type=doc_type, status=status, owner=owner, sensitivity=sensitivity,
                 source_id=source_id, tag=tag, canonical_only=canonical_only, allowed_sensitivities=sorted(allowed),
                 validity=validity, decision_role=decision_role)
    limits = dict(limit=limit, seed_limit=seed_limit, max_depth=max_depth, max_nodes=max_nodes, max_edges=max_edges,
                  construction_nodes=MAX_CONSTRUCTION_NODES, visible_bytes=MAX_VISIBLE_BYTES, tokens=MAX_TOKENS,
                  constructed_edges=MAX_CONSTRUCTED_EDGES, query_bytes=MAX_QUERY_BYTES)
    truncation = dict(seeds=False, max_depth=False, max_nodes=False, max_edges=False, results=False)
    report: dict[str, Any] = dict(format="whykit.hybrid-retrieval/v1", contract_version=1, status="ranked", reason=None,
                                 valid_on=valid_on.isoformat(), query=query, limits=limits, results=[], citations=[], stats=None,
                                 truncation=truncation, execution_authorized=False, semantics_checked=False)
    def refuse():
        report.update(status="refused", reason="scope_too_large")
        return report
    from .claim_ledger import claim_visibility
    from .outcome_ledger import outcome_visibility

    active: dict[str, dict] = {}
    for eid, row in rows[0].items():
        if row.get("sensitivity") not in allowed or validity == "active":
            continue
        if sensitivity and row.get("sensitivity") != sensitivity:
            continue
        if len(active) >= MAX_CONSTRUCTION_NODES:
            return refuse()
        active[eid] = row
    summaries: dict[str, dict] = {}
    validities: dict[str, dict] = {}
    eligible: list[Note] = []
    decisions: Counter[str] = Counter()
    claims: Counter[str] = Counter()

    def candidates(outcomes: bool):
        for note in index.notes:
            if is_outcome_note(note) != outcomes:
                continue
            path = index.relative(note.path)
            if path.casefold() in _LOGS or path.casefold() == "00-context/evidence-register.md":
                continue
            level = note_sensitivity_level(note)
            if level not in levels:
                continue
            label = next(name for name, value in SENSITIVITY_LEVEL.items() if value == level)
            summary = {**_summary(index, note), "sensitivity": label}
            summary["source_ids"] = [eid for eid in summary["source_ids"] if re.fullmatch(r"E-[0-9]{3,}", eid)]
            app = metadata.get(note.path, {}).get("applicability", _NOT_DECISION)
            interval = _validity(note, valid_on)
            if interval["state"] not in ({"active", "unknown"} if validity == "active_or_unknown" else {"active"}):
                continue
            is_decision = app["state"] != "not_decision" or summary["type"] == "decision" or summary["decision_id"] is not None
            if decision_role == "premise" and is_decision and app.get("premise") is not True:
                continue
            if (doc_type and (summary["type"] != doc_type or summary["status"] == "template" and status != "template")
                    or status and summary["status"] != status or owner and owner.casefold().strip() not in summary["owner"].casefold()
                    or sensitivity and label != sensitivity or canonical_only and not summary["source_of_truth"]
                    or source_id and source_id not in summary["source_ids"]
                    or tag and not any(tag.casefold().strip() == value.casefold() for value in summary["tags"])):
                continue
            summary["decision_id"] = metadata.get(note.path, {}).get("decision_id", summary["decision_id"])
            yield note, summary, interval

    def admit(note, summary, interval):
        if len(eligible) + len(active) >= MAX_CONSTRUCTION_NODES:
            return False
        eligible.append(note)
        summaries[summary["path"]], validities[summary["path"]] = summary, interval
        return True

    from .impact_contract import is_impact_note
    if any(is_impact_note(note) for note in index.notes):
        from .knowledge_visibility import visible_knowledge_index
        # Permission closure precedes even query summaries. Keep only lexical
        # identities during eligibility closure, never an unbounded summary map.
        granted = index.subset(lambda note: note_sensitivity_level(note) in levels)
        granted.derived["evidence_register"] = (active, {}, [])
        index = visible_knowledge_index(granted, set(active))
        paths = {note.path for outcomes in (False, True) for note, _, _ in candidates(outcomes)}
        narrowed = index.subset(lambda note: note.path in paths)
        narrowed.derived["evidence_register"] = (active, {}, [])
        view = visible_knowledge_index(narrowed, set(active))
        for note in view.notes:
            if len(eligible) + len(active) >= MAX_CONSTRUCTION_NODES:
                return refuse()
            label = next(name for name, value in SENSITIVITY_LEVEL.items() if value == note_sensitivity_level(note))
            summary = {**_summary(index, note), "sensitivity": label}
            summary["source_ids"] = [eid for eid in summary["source_ids"] if re.fullmatch(r"E-[0-9]{3,}", eid)]
            summary["decision_id"] = metadata.get(note.path, {}).get("decision_id", summary["decision_id"])
            admit(note, summary, _validity(note, valid_on))
    else:
        # Native C/O closure is pure. Keep only bounded admitted notes/identity maps;
        # excluded dependency records never consume a construction slot.
        evidence_ids = set(active)
        for note, summary, interval in candidates(False):
            if is_claim_note(note) and not claim_visibility(note, evidence_ids, root=index.root):
                continue
            if not admit(note, summary, interval):
                return refuse()
            did = canonical_decision_id(note, index.root)
            if did is not None:
                decisions[did] += 1
            if is_claim_note(note):
                claims[note.front["claim_id"]] += 1
        decision_ids = {key for key, count in decisions.items() if count == 1}
        claim_ids = {key for key, count in claims.items() if count == 1}
        for note, summary, interval in candidates(True):
            if not outcome_visibility(note, decision_ids, claim_ids, root=index.root):
                continue
            if not admit(note, summary, interval):
                return refuse()
        eligible_paths = {note.path for note in eligible}
        view = index.subset(lambda note: note.path in eligible_paths)
        view.derived["evidence_register"] = (active, {}, [])
    documents: list[tuple[dict, Counter[str]]] = []
    total_bytes = total_tokens = 0
    for note in view.notes:
        if len(note.text) > MAX_VISIBLE_BYTES - total_bytes:
            return refuse()
        summary = summaries[index.relative(note.path)]
        total_bytes += len(note.text.encode("utf-8"))
        if total_bytes > MAX_VISIBLE_BYTES:
            return refuse()
        counts: Counter[str] = Counter()
        for token in _iter_tokens(f"{summary['title']}\n{summary['path']}\n{note.body}"):
            total_tokens += 1
            if total_tokens > MAX_TOKENS:
                return refuse()
            counts[token] += 1
        documents.append((summary, counts))
    for row in active.values():
        total_bytes += len(json.dumps({k: v for k, v in row.items() if k != "line"}, ensure_ascii=False).encode("utf-8"))
        if total_bytes > MAX_VISIBLE_BYTES:
            return refuse()
    terms = sorted(set(_iter_tokens(text)))
    lexical = _score_bm25(documents, terms) if terms else []
    scores = {item["path"]: item["score"] for item in lexical}
    maximum = lexical[0]["score"] if lexical else 1.0
    normalized = {path: score / maximum for path, score in scores.items()}
    seeds = lexical[:seed_limit]
    truncation["seeds"] = len(lexical) > seed_limit
    supports: dict[str, tuple[float, dict]] = {}
    visited: set[str] = set()
    examined = 0
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    if seeds and max_depth:
        from .knowledge_visibility import visible_knowledge_index
        try:
            graph_view = visible_knowledge_index(view, set(active))
            _, nodes, edges, _ = dependency_graph._build_visible_graph(index.root, graph_view, active, {},
                construction_nodes=MAX_CONSTRUCTION_NODES, construction_edges=MAX_CONSTRUCTED_EDGES)
        except dependency_graph.GraphConstructionLimit:
            return refuse()
        adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for edge in edges:
            if edge["type"] not in _WEIGHTS:
                continue
            for direction, source, target, weight in (("forward", edge["from"], edge["to"], _WEIGHTS[edge["type"]][0]),
                                                      ("reverse", edge["to"], edge["from"], _WEIGHTS[edge["type"]][1])):
                adjacency[source].append((target, {**edge, "walk_direction": direction, "weight": weight}))
        for outgoing in adjacency.values():
            outgoing.sort(key=lambda item: (item[0], item[1]["type"], item[1]["walk_direction"]))
        admitted_seeds = seeds[:max_nodes]
        truncation["max_nodes"] = len(seeds) > max_nodes
        visited.update("document:" + seed["path"] for seed in admitted_seeds)
        for seed in admitted_seeds:
            start = "document:" + seed["path"]
            pending: deque[tuple[str, list[str], list[dict[str, Any]], float]] = deque([(start, [start], [], 1.0)])
            while pending:
                current, route, route_edges, product = pending.popleft()
                if len(route_edges) >= max_depth:
                    if any(other not in route for other, _ in adjacency[current]):
                        truncation["max_depth"] = True
                    continue
                for other, edge in adjacency[current]:
                    if examined >= max_edges:
                        truncation["max_edges"] = True
                        break
                    examined += 1
                    if other in route:
                        continue
                    if other not in visited:
                        if len(visited) >= max_nodes:
                            truncation["max_nodes"] = True
                            continue
                        visited.add(other)
                    depth = len(route_edges) + 1
                    next_product = product * float(edge["weight"])
                    explanation = dict(seed=seed["path"], depth=depth, seed_normalized=normalized[seed["path"]],
                                       edge_weight_product=next_product, decay=.5 ** depth,
                                       nodes=[*route, other], edges=[*route_edges, edge])
                    support = normalized[seed["path"]] * .5 ** depth * next_product
                    if other.startswith("document:"):
                        path = other.removeprefix("document:")
                        previous = supports.get(path)
                        if previous is None or support > previous[0] or support == previous[0] and _route_key(explanation) < _route_key(previous[1]):
                            supports[path] = support, explanation
                    pending.append((other, [*route, other], [*route_edges, edge], next_product))
                if examined >= max_edges and pending:
                    truncation["max_edges"] = True
                    pending.clear()
    results = []
    for summary, _ in documents:
        path = summary["path"]
        support = supports[path][0] if path in supports else 0.0
        result_explanation = supports[path][1] if path in supports else None
        score = .6 * normalized.get(path, 0.0) + .4 * support
        if score <= 0:
            continue
        result_note = view.note_for(index.root / path)
        assert result_note is not None
        results.append({**summary, "score": score, "lexical_score": scores.get(path, 0.0),
                        "lexical_normalized": normalized.get(path, 0.0), "graph_support": support,
                        "explanation": result_explanation, "validity": validities[path],
                        "applicability": metadata.get(result_note.path, {}).get("applicability", _NOT_DECISION)})
    results.sort(key=lambda item: (-item["score"], -item["lexical_score"], item["path"]))
    matched = len(results)
    if _matched_paths is not None:
        _matched_paths.update(item["path"] for item in results)
    truncation["results"] = matched > limit
    results = results[:limit]
    citations = {}
    for result in results:
        cited_nodes = ["document:" + result["path"]]
        if result["explanation"]:
            cited_nodes.extend(result["explanation"]["nodes"])
        for node in cited_nodes:
            kind, target = node.split(":", 1)
            if kind == "document":
                digest = document_hashes.get(index.root / target)
                if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise HybridRetrievalError("unavailable_inputs")
            else:
                digest = _hash({k: v for k, v in active[target].items() if k != "line"})
            citations[node] = dict(kind=kind, target=target, sha256=digest)
    report.update(results=results, citations=[citations[key] for key in sorted(citations)],
                  stats=dict(documents=len(documents), evidence_nodes=len(active), corpus_tokens=total_tokens,
                             constructed_nodes=len(nodes), constructed_edges=len(edges), seeds=len(seeds),
                             visited_nodes=len(visited), examined_edges=examined, matched_candidates=matched))
    return report


@consistent_read
@path_cache()
def retrieve_hybrid(
    root: Path, text: str, *, valid_on: dt.date, validity: str = "active_or_unknown", decision_role: str = "premise",
    doc_type: str | None = None, status: str | None = None, owner: str | None = None,
    sensitivity: str | None = None, allowed_sensitivities: set[str] | None = None,
    source_id: str | None = None, tag: str | None = None, canonical_only: bool = False,
    limit: int = 20, seed_limit: int = 16, max_depth: int = 2, max_nodes: int = 256, max_edges: int = 2048,
    vault: VaultIndex | None = None,
) -> dict[str, Any]:
    """Capture once, evaluate local applicability, then rank without source rereads."""
    _validate(text, valid_on, validity, decision_role, (doc_type, status, owner, sensitivity, source_id, tag),
              allowed_sensitivities, canonical_only, (limit, seed_limit, max_depth, max_nodes, max_edges))
    from .compiled_context import CompiledContextError, _capture, _read
    from .config import parse_config
    from .context_applicability import context_metadata

    root = Path(root).resolve()
    allowed = {"public"} if allowed_sensitivities is None else set(allowed_sensitivities)
    try:
        visible, rows, _, hashes, states, reviews = _capture(root, allowed, vault, valid_on)
        policy = root / "whykit.toml"
        if policy.is_symlink():
            raise HybridRetrievalError("unavailable_inputs")
        config = parse_config(_read(policy).decode("utf-8") if policy.exists() else "")
        metadata = context_metadata(visible, reviews[0], reviews[1], reviews[2], rows, states, config, valid_on, hashes)
    except (CompiledContextError, OSError, UnicodeError, ValueError) as exc:
        raise HybridRetrievalError("unavailable_inputs") from exc
    return rank_prepared(visible, text, rows=rows, metadata=metadata, document_hashes=hashes, valid_on=valid_on,
        validity=validity, decision_role=decision_role, doc_type=doc_type, status=status, owner=owner,
        sensitivity=sensitivity, allowed_sensitivities=allowed, source_id=source_id, tag=tag, canonical_only=canonical_only,
        limit=limit, seed_limit=seed_limit, max_depth=max_depth, max_nodes=max_nodes, max_edges=max_edges)
