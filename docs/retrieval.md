# Opt-in retrieval

The Python function `whykit.retrieval.retrieve_bm25` retrieves notes using BM25
term ranking. `whykit query --ranking bm25` and `compile-context --ranking bm25`
opt in to that lexical engine. Their defaults remain substring matching.
The separate `retrieve` route combines BM25 with typed graph proximity and
explicit time and decision-role filters; see [Hybrid retrieval](#hybrid-retrieval).
MCP query and legacy context/pack retain their existing behavior.

```python
from pathlib import Path
from whykit.retrieval import retrieve_bm25

hits = retrieve_bm25(
    Path("examples/northline"),
    "review evidence",
    status="approved",
    allowed_sensitivities={"public"},
    limit=5,
)
for hit in hits:
    print(hit["path"], hit["score"])
```

The result is a list of query-style note summaries, each with a positive
floating-point `score`. It is not a new version of the CLI JSON report. Scores
are relative to the filtered corpus and query; they are neither confidence nor
evidence-quality measures and cannot be compared across different corpora.

## Matching and filtering

The function normalizes Unicode to NFC, case-folds text, and splits it into
Unicode alphanumeric terms. Underscores and punctuation separate terms.
Repeated query terms count once. A note matches if any query term occurs in
its title, vault-relative path or Markdown body after front matter. Other
front-matter values are available through filters, rather than indexed as text.
Markdown code and links in the body are searchable text, not instructions.

The fixed BM25 parameters are `k1=1.2` and `b=0.75`, with positive IDF
`log(1 + (N - df + 0.5) / (df + 0.5))`. Documents have one combined title,
path and body term stream, without field weights or status/canonical boosts.
Ties sort by path. Empty or punctuation-only queries return an empty list;
`limit=0` returns no hits and negative limits raise `ValueError`.

Metadata filters retain `query_vault` semantics for `doc_type`, `status`,
`owner`, `sensitivity`, `source_id`, `tag`, and `canonical_only`. When either
`sensitivity` or `allowed_sensitivities` is set, the shared fail-closed
classifier first excludes unreadable labels and malformed front matter,
including duplicate keys and differently cased sensitivity keys. The
`allowed_sensitivities` filter uses canonical label classification, including
case-insensitive label values, rather than the legacy query's raw comparison.
These filters apply before tokenization, document frequencies, and
average length are computed. Excluded notes cannot influence scores or result
order. An unlabelled note defaults to `internal` for `allowed_sensitivities`,
and a type filter excludes templates unless `status="template"` is explicit.
An optional request-scoped `vault=VaultIndex` is supported, including confined
subsets. As with other readers, a supplied index must be a trusted snapshot
of the requested root.

Sensitivity labels do not authenticate or authorize callers. A host must
establish the allowed scope itself. See the [security model](security-model.md).

## Reproduce the development comparison

From a source checkout:

```bash
uv run python tests/retrieval_benchmark.py
uv run python -m unittest discover -s tests -p test_retrieval.py -v
```

The fixed [synthetic corpus](../tests/fixtures/retrieval-corpus.json) has 12 notes,
eight queries with explicit relevance grades and one no-match control. The
script prints its SHA-256, per-query paths, macro Recall@3 and nDCG@3, and warm
median/p95 latency. The no-match control is checked separately and excluded
from metric averages. nDCG uses gain `2**grade - 1` and logarithmic discounts;
Recall counts every positive grade as relevant.

On this development fixture, substring ranking scores 0.5625 Recall@3 and
0.5766 nDCG@3; BM25 scores 1.0 for both. Several queries deliberately place
related words apart, exposing the baseline's whole-substring limitation.
These are regression results on a corpus used during development, not an
independent benchmark or evidence of improved decisions, agent behavior or
real-model accuracy. The small, easy corpus does not justify changing defaults.

The script alternates algorithm order and records 30 samples per query after
a warm-up. Both use the same preloaded index and filters. Timing includes
filtering and ranking but excludes file reads/parsing; it does not characterize
large-vault or end-to-end performance. No network, models or provider calls are
involved. No persistent retrieval index is written.

## BM25 limits

The `retrieve_bm25` function performs lexical ranking. There is no
stemming, synonym expansion, phrase/negation understanding, embedding, graph
boost, temporal validity filter, or permission service. A positive score does
not mean a note supports the query's claim. OR matching can retrieve irrelevant
partial matches. The implementation scans and tokenizes the filtered corpus
per call and retains term counts in memory; evaluate realistic corpus sizes
before connecting it to an interactive host.

A default-ranking change needs separately reviewed relevance judgments on a
held-out corpus, per-query regressions (including phrase and negation cases),
filter-isolation checks, and measured end-to-end cost and latency. See
[performance](performance.md) for the existing CLI measurements.


## Hybrid retrieval

```bash
uv run whykit retrieve "source observations" --root examples/outcomes \
  --valid-on 2026-09-17 --max-sensitivity restricted --limit 5
uv run whykit compile-context --query "source observations" --ranking hybrid \
  --root examples/outcomes --today 2026-09-17 --max-sensitivity restricted
```

The API is `whykit.hybrid_retrieval.retrieve_hybrid(root, text, valid_on=...)`.
The CLI always emits `whykit.hybrid-retrieval/v1` JSON, described by
[`hybrid-retrieval.schema.json`](../schemas/hybrid-retrieval.schema.json).
A valid date is required. The default classification ceiling is public;
`--max-sensitivity` only narrows an already trusted local scope, without
establishing user identity or filesystem authorization. Existing metadata
filters apply before statistics and graph construction. A supplied API index
and evidence-row grant cannot be widened by a new filesystem scan.

`--decision-role premise` requires locally applicable decisions, using the
same approval, review, source-currentness and validity guards as the compiler.
`reference` explicitly permits nonbinding decision references; their returned
applicability state still shows whether they can be a premise. Reference mode
still applies the requested validity filter; it does not admit future or expired
intervals for that day. Intervals are
half-open `[valid_from, valid_until)`. `--validity active_or_unknown` admits
unknown fact dates with an explicit label; `active` requires a declared active
interval. Created, acquired and Git dates never fill missing validity.
Evidence rows have unknown fact validity, so active-only search excludes those
intermediary paths. Retired evidence and excluded records cannot bridge paths.

Ranked results are Markdown document paths. Permitted evidence rows can be
intermediaries and citations, without consuming hidden extra result slots.
Document citations hash captured raw bytes; evidence citations hash the
permitted projected row as sorted compact UTF-8 JSON with one final newline.
Raw mixed evidence/decision/review tables, binary captures and imported REST
payloads are not lexical documents. Canonical global logs are projected only
for permitted governance inputs; noncanonical case variants remain unavailable.

For each positive lexical seed, the engine explores bounded typed paths.
Normalized lexical score is `L = BM25 / max_positive_BM25`. Graph support is
the maximum `seed_L * 0.5**depth * product(edge_weights)`, and the combined score
is `0.6*L + 0.4*G`. Forward/reverse weights are 1/0.5 for evidence links and
0.5/0.25 for wikilinks and Outcome relations. Supersedes is not expanded by
current search. Original edge direction and traversal direction are explicit.
Cycles, diamonds and repeated links add no path-count bonus; empty lexical
seeds give no graph dump. Scores break ties by lexical score and path.

These weights are fixed structural heuristics. A shared evidence ID or a
nearby graph node does not establish matching source versions, semantic
support, confidence or truth. Results retain `semantics_checked=false` and
`execution_authorized=false`.

Default search work is bounded to 16 seeds, depth 2, 256 visited nodes and
2048 examined edges across all seeds. Corpus construction is bounded to
25000 eligible graph objects including evidence intermediaries, 64 MiB of
visible UTF-8 text, 2 million tokens and 200000 edges. Queries are at most
4096 UTF-8 bytes; results are limited to 0–100. Construction overflow returns
`status=refused`, `reason=scope_too_large` with empty results and no partial
statistics. Traversal and result caps have explicit `truncation` flags.
These bounds cover the filtered ranking/graph work; initial filesystem
discovery and coherent capture remain separate local I/O costs. There is no
persistent vector index, provider call or model dependency.

The hybrid route is experimental. Construction caps bound the admitted work,
not elapsed time. Measure larger vaults separately before using this route in
an interactive workflow; the synthetic corpus is not a latency guarantee.

Compiler `ranking=hybrid` consumes its already captured eligible query view.
Per-context score components and explanations, their visible citations and
top-level retrieval limits/truncation count toward the full JSON+newline byte
budget and are bound by revalidation. Other ranking recipes omit these fields.
A graph-construction overflow refuses compilation rather than silently
falling back to a different ranking.

The graph/time evaluation corpus and benchmark are separate from the old
12-note lexical comparison above. Report filtering and graph gains separately,
include every per-query loss and negative control, and measure fresh-process
read-to-serialization separately from warm preloaded ranking. Synthetic
regression results do not establish real-user or model quality.
