"""Pure dependency closure over an already classified and granted note index."""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection
from pathlib import Path

from .immutability import RECORD_RE
from .lint import DECISION_FILE_RE, EVIDENCE_ID_RE, Note, _decision_own_id, _relative_text
from .vault_index import VaultIndex


def canonical_decision_id(note: Note, root: Path) -> str | None:
    if note.front_error or note.front.get("type") != "decision":
        return None
    try:
        relative = note.path.relative_to(root).as_posix()
    except ValueError:
        return None
    if not RECORD_RE.fullmatch(relative):
        return None
    decision_id = _decision_own_id(note)
    name = DECISION_FILE_RE.match(note.path.name)
    return decision_id if name is not None and decision_id == "D-" + name.group(1) else None


def visible_knowledge_index(index: VaultIndex, evidence_ids: Collection[str]) -> VaultIndex:
    """Close captured native references over exactly this already classified grant.

    Identity maps are fixed before pruning. One reverse queue propagates missing
    dependencies, including native Outcomes; supported closed cycles survive.
    Pins, approval, currentness and validity do not grant or remove visibility.
    This never opens or hashes a source or reloads a larger index.
    """
    from .claim_ledger import _claim_id, claim_visibility
    from .impact_contract import is_impact_note, parse_impact_note
    from .outcome_ledger import _parse_text
    from .sensitivity import classified_evidence, is_claim_note, is_outcome_note

    inherited = index.derived.get("evidence_register")
    if inherited is None:
        active, retired, occurrences, _ = classified_evidence(index)
    else:
        active, retired, occurrences = inherited
    evidence = set(evidence_ids)
    notes = index.notes
    paths: dict[str, list[int]] = defaultdict(list)
    decisions: dict[str, list[int]] = defaultdict(list)
    claims: dict[str, list[int]] = defaultdict(list)
    canonical_decisions: dict[int, str | None] = {}
    invalid: set[int] = set()
    for number, note in enumerate(notes):
        relative = _relative_text(note.path, index.root)
        if relative is None:
            invalid.add(number)
        else:
            paths[relative].append(number)
        if note.front.get("type") == "decision" and not note.front_error:
            canonical_decisions[number] = canonical_decision_id(note, index.root)
            identity = canonical_decisions[number]
            if identity is not None:
                decisions[identity].append(number)
        if is_claim_note(note):
            identity = note.front.get("claim_id")
            if isinstance(identity, str):
                try:
                    _claim_id(identity)
                    if note.path.relative_to(index.root).parts == ("00-context", "claims", identity.lower() + ".md"):
                        claims[identity].append(number)
                except ValueError:
                    pass
    reverse: dict[int, set[int]] = defaultdict(set)
    # An ambiguity must not turn into uniqueness when one conflicting holder
    # is pruned. Virtual group nodes propagate that loss in O(group size).
    group = len(notes)
    for mapping in (paths, decisions, claims):
        for members in mapping.values():
            if len(members) > 1:
                reverse[group].update(members)
                for member in members:
                    reverse[member].add(group)
                group += 1

    def require(holder: int, kind: str, target: str) -> None:
        if kind == "evidence":
            if target not in evidence:
                invalid.add(holder)
            return
        candidates = {"document": paths, "decision": decisions, "claim": claims}[kind].get(target, [])
        if len(candidates) != 1:
            invalid.add(holder)
            return
        dependency = candidates[0]
        if kind == "decision" and canonical_decisions.get(dependency) != target:
            invalid.add(holder)
        reverse[dependency].add(holder)

    for number, note in enumerate(notes):
        if is_claim_note(note) and not claim_visibility(note, evidence, root=index.root):
            invalid.add(number)
        if is_outcome_note(note):
            try:
                record, payload = _parse_text(note.text)
                if note.path.relative_to(index.root).parts != ("00-context", "outcomes", record["outcome_id"].lower() + ".md"):
                    raise ValueError("noncanonical Outcome")
                require(number, "decision", payload["decision_id"])
                for key in ("baseline", "observed"):
                    if payload[key] is not None and payload[key]["claim"] is not None:
                        require(number, "claim", payload[key]["claim"]["claim_id"])
            except (ValueError, UnicodeError):
                invalid.add(number)
        if is_impact_note(note):
            try:
                declaration = parse_impact_note(note, index.root)
                if declaration is None:
                    raise ValueError("unavailable impact declaration")
                source_ids = note.front.get("source_ids", [])
                if (not isinstance(source_ids, list) or len(source_ids) > 256
                        or any(not isinstance(eid, str) or not EVIDENCE_ID_RE.fullmatch(eid) for eid in source_ids)):
                    raise ValueError("invalid structured evidence references")
                if any(str(key).strip().casefold() in {"source_ids", "claim_ids"}
                       and key not in {"source_ids", "claim_ids"} for key in note.front):
                    raise ValueError("aliased structured references")
                for evidence_id in source_ids:
                    require(number, "evidence", evidence_id)
                if declaration["artifact_kind"] is None:
                    claim_ids = note.front.get("claim_ids", [])
                    if not isinstance(claim_ids, list) or len(claim_ids) > 64:
                        raise ValueError("invalid structured claim references")
                    for claim_id in claim_ids:
                        _claim_id(claim_id)
                    if len(set(claim_ids)) != len(claim_ids):
                        raise ValueError("duplicate structured claim references")
                    for claim_id in claim_ids:
                        require(number, "claim", claim_id)
                for dependency in declaration["dependencies"]:
                    kind, target = dependency["target"].split(":", 1)
                    require(number, kind, target)
            except ValueError:
                invalid.add(number)
    pending = deque(invalid)
    while pending:
        for holder in reverse[pending.popleft()]:
            if holder not in invalid:
                invalid.add(holder)
                pending.append(holder)
    retained = {id(note) for number, note in enumerate(notes) if number not in invalid}
    result = index.subset(lambda note: id(note) in retained)
    result.derived["evidence_register"] = (
        {key: row for key, row in active.items() if key in evidence},
        {key: row for key, row in retired.items() if key in evidence},
        [(key, line) for key, line in occurrences if key in evidence],
    )
    return result
