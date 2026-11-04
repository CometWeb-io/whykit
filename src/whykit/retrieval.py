"""Opt-in lexical retrieval; the v1 query API keeps its substring ranking."""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import re
import unicodedata

from .io import consistent_read
from .lint import path_cache
from .query import query_vault
from .sensitivity import SENSITIVITY_LEVEL, note_sensitivity_level
from .vault_index import VaultIndex


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold())


@consistent_read
@path_cache()
def retrieve_bm25(
    root: Path,
    text: str,
    *,
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
) -> list[dict]:
    """Rank matching notes by BM25 (k1=1.2, b=0.75), then by path.

    Return query-style summaries with a positive ``score``. All metadata
    filters apply before corpus statistics; excluded notes cannot affect
    scores. Sensitivity labels are filters, not caller authorization.
    Empty/punctuation-only queries return no hits. Terms use OR matching.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    terms = sorted(set(_tokens(text)))
    if not terms or limit == 0:
        return []
    index = vault or VaultIndex.load(root)
    if allowed_sensitivities is not None or sensitivity:
        levels = {level for name, level in SENSITIVITY_LEVEL.items()
                  if (allowed_sensitivities is None or name in allowed_sensitivities)
                  and (not sensitivity or name == sensitivity.lower())}
        index = index.subset(lambda note: note_sensitivity_level(note) in levels)
    candidates = query_vault(
        root, doc_type=doc_type, status=status, owner=owner,
        sensitivity=sensitivity,
        source_id=source_id, tag=tag, canonical_only=canonical_only,
        limit=len(index.notes), vault=index,
    )["results"]
    summaries = {item["path"]: item for item in candidates}
    documents: list[tuple[dict, Counter[str]]] = []
    for note in index.notes:
        summary = summaries.get(index.relative(note.path))
        if summary is None:
            continue
        counts = Counter(_tokens(f"{summary['title']}\n{summary['path']}\n{note.body}"))
        documents.append((summary, counts))
    return _score_bm25(documents, terms)[:limit]



def _score_bm25(documents: list[tuple[dict, Counter[str]]], terms: list[str]) -> list[dict]:
    """Shared deterministic scorer; callers admit/filter/tokenize the corpus first."""
    frequencies: Counter[str] = Counter()
    for _, counts in documents:
        frequencies.update(term for term in terms if term in counts)
    if not documents:
        return []
    average_length = sum(counts.total() for _, counts in documents) / len(documents)
    inverse_frequencies = {
        term: math.log1p((len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
        for term in terms
    }
    hits = []
    for summary, counts in documents:
        normalization = 1.2 * (0.25 + 0.75 * counts.total() / average_length)
        score = sum(
            inverse_frequencies[term] * counts[term] * 2.2 / (counts[term] + normalization)
            for term in terms if counts[term]
        )
        if score > 0:
            hits.append({**summary, "score": score})
    hits.sort(key=lambda item: (-item["score"], item["path"]))
    return hits
