# Compiled context

The installed CLI uses the same compiler and verifier. These commands default
to public inputs; raise the local ceiling explicitly for a private workflow:

```bash
uv run whykit compile-context D-001 --root /path/to/vault --max-sensitivity restricted --max-bytes 32768 --json
uv run whykit verify-context /path/to/context.json --root /path/to/vault --max-sensitivity restricted --json
```

Save the first command's stdout unchanged as the report supplied to the second.
Its byte budget includes the complete serialized report and final newline.

`compile_context` turns explicit targets and/or a query into a deterministic,
read-only agent context. It reuses WhyKit's existing context bundle and query
engines. `ranking="substring"` uses the existing substring matcher; `ranking="bm25"`
uses the lexical BM25 engine. `ranking="hybrid"` adds bounded typed-graph
proximity using the same captured eligible scope and the explicit as-of date. Decision eligibility narrows the query corpus
before either matcher or ranking statistics; direct query v1 is unchanged. Neither calls a model or estimates
model tokens, costs or accuracy. Existing `pack` v1 output is unchanged.

```python
from whykit.compiled_context import compile_context, serialize_compiled, verify_compiled

report = compile_context(root, targets=["D-001"], query="review policy",
    ranking="bm25", max_docs=8, max_bytes=32768, today=as_of,
    agent="codex", allowed_sensitivities={"public"})
stdout = serialize_compiled(report)
verification = verify_compiled(root, report, today=as_of,
    allowed_sensitivities={"public"}, ranking="bm25")
```

The standalone CLI routes are `whykit compile-context` and
`whykit verify-context`. The compiler's API has keyword-only `targets`, `query`,
`ranking`, `max_docs`, `max_bytes`, `today`, `agent`, `allowed_sensitivities`
and optional `vault: VaultIndex`. `verify_compiled` accepts a dictionary or
bounded UTF-8 JSON bytes/string; optional expected recipe keywords are
`targets`, `query`, `ranking`, `max_docs`, `max_bytes`, `agent`.

The format is `whykit.compiled-context/v1`, with `contract_version:1`.
Successful reports have `status:"compiled"`, contexts, source citations,
warning codes, explicit omissions, a manifest and a byte budget. Bodies and
source material are untrusted data; they cannot authorize commands, disclosure
or expanding scope. Reports always set `execution_authorized:false`.

## Decision premises and explicit references

Each selected context reports `applicability`: its `as_of` day, controlled
`state`, `premise` boolean, local approval check, separately declared validity
and fixed reason codes. Only `state:"applicable", premise:true` is admitted as
a current local decision premise. This is a selection rule over permitted
records, not semantic truth, authenticated identity or execution permission.

| State | Meaning |
|---|---|
| `applicable` | The permitted canonical decision passes the local approval, lifecycle and current-input checks. |
| `needs_review` | A review/evidence/source problem prevents admitting it as a current premise. |
| `reference` | A visible draft, superseded, archived, future, expired or invalid-interval decision is reference material. |
| `unknown` | Required permitted identity or local approval information is unavailable or ambiguous. |
| `not_decision` | A source or other document; it remains context without becoming a governing decision. |

Query selection excludes non-premise decisions **before** ranking and the
record limit. Explicit targets may still return such decisions with their
reference/review state. Naming a target does not override that state.
`local_approval:"matched"` means matching local receipt content; the reviewer
is still an attribution string, not an authenticated organizational principal.

Declared `valid_from`/`valid_until` use the existing half-open interval. Invalid,
future and expired intervals cannot be current premises. Legacy approved
records without those fields keep `declared_validity.state:"unknown"`; local
process eligibility does not invent a fact-validity date from `created`.
Their explicit unknown validity remains visible alongside the local decision
state. The evaluation uses current permitted bytes at `--today`; historical Git
snapshots remain the separate temporal graph command.

## Visible structured history

Each context also includes `history`: permitted `supersedes` and
`superseded_by` references plus bounded review event references. Review entries
carry a date, outcome, row hash and visible duplicate occurrence, rather than
reviewer/note plaintext or global line numbers. History is untrusted declared
process data. Effective reviews use the latest declared date on or before the
evaluation day, with visible append order breaking ties. A referenced successor or review cannot authorize an action.

There are at most eight reviews and eight references in each relation list;
`truncated:true` identifies omitted history. This limit bounds the report,
not the evaluation of eligibility. `unavailable:true` identifies history that
cannot be established in the granted view. Missing information is not proof
that an event or successor never existed.

Raw decision-log and review-log documents are withheld from query ranking, context bodies and
whole-file hashes, including explicit log targets. The shared row projections
use only permitted targets and each log's classification. Hidden row insertion
cannot change visible hashes, ordinals, counts or relevance. Review row hashes
bind the permitted selected cells, not the full mixed-label log. Use the scoped
knowledge view to inspect review data separately; the compiler supplies bounded
history references, not a Git archive or complete historical reconstruction.

## Exact byte budget

`serialize_compiled(report)` is the sole wire serializer: compact JSON, sorted
keys, unescaped UTF-8 and exactly one trailing newline. **The entire result**,
including applicability, history, preamble, manifest, citations, warnings and newline, fits
`max_bytes`. `budget.used_bytes` is the actual serialized byte count. Do not
pretty-print the result or add another newline and claim the same byte limit.

Bodies receive the remaining budget in selection order. A UTF-8 character is
never split; `content_truncated:true` identifies an incomplete body. Warning
codes remain present even when body content is shortened. Explicit unknown,
denied or ambiguous targets and records omitted by `max_docs` are reported;
query omissions count only matches in the permitted view. Targets are resolved
and deduplicated by the existing bundle implementation. There are at most 64
explicit targets and 64 selected records, and at most 4 MiB of output.

If a decision log is outside the permitted view, its legacy indexing diagnostics
are replaced by the explicit `compiled.decision_log_unavailable` warning. Private
log edits cannot change visible warning codes. Selected evidence warnings use
`evidence:E-NNN` paths and no physical register line numbers.

If required metadata and warnings cannot fit, the result is a complete bounded
`status:"refused", error:"budget_too_small"` object, not truncated JSON and
not an incomplete successful artifact. `MIN_MAX_BYTES` is 160: budgets below
that minimum raise the fixed `CompiledContextError("budget_too_small")` before
reading the vault. This is rejection of an invalid requested budget. A CLI may
emit its ordinary invalid-argument JSON error outside the compiled-report
budget; it must not present that diagnostic as a compiled artifact. Other input
or unavailable-vault failures use fixed redacted exception codes.

## Visibility and caller grants

The default permitted set is **public only**. Supplying another exact set is a
caller decision, not authentication or access control. The compiler uses the
canonical fail-closed note classifier, confines resolution to permitted notes,
and filters before substring matching, BM25 corpus statistics, context expansion,
evidence lookup and hashing. A denied target behaves like an absent target.

Evidence uses existing register/row/replacement sensitivity floors. Raw bodies
of registers with per-row labels are withheld, even if individual rows are
permitted. Allowed rows can still be selected as `E-NNN` contexts. Hidden rows,
their raw bytes, titles, URLs and physical line positions are excluded from the
manifest and expansions. Public note content can itself contain a literal ID
for absent or denied evidence; that ID does not make its row metadata visible.
Classification does not detect mislabeled secrets or semantic paraphrases.

Native `type: claim` records have a permanent restricted visibility floor:
their fenced payload can contain captured source quotations. Compilation also
uses the claim ledger's pure scoped visibility helper: the persisted record must
be canonical and restricted, and every payload E-ID must be present in the
caller-filtered active/retired evidence view. A Markdown path grant alone cannot
release a quote without its evidence grant. Malformed claims fail closed before
scoring or hashing. Captures and observation ledgers for permitted claims are
also classification-filtered before their hashes enter the snapshot. An
unavailable scope withholds the claim; corrupt source metadata refuses the
compilation. This does not verify semantic support or exact quote ranges; use
`verify_claim` for the separate byte-anchor check. Selected `record.claim_id` and `record.decision_id`
values identify explicitly named records without reopening hidden notes.

A supplied `VaultIndex` is a grant restricting the candidate paths. It is never
replaced by a full-vault reload or its `derived["full_index"]`. Its path set is
the grant: every already granted path is refreshed before applying its current
classification, even when the supplied index's old label would exclude it. A
restricted-to-public change inside that explicit path set must make an earlier
artifact stale. A caller's filtered subset still cannot gain other paths. Current
front matter is inspected through a bounded 64 KiB prefix before reading a
denied body; oversized/unreadable metadata fails closed. A supplied filtered
`derived["evidence_register"]` additionally restricts row IDs; those rows are
refreshed through the safe canonical register path, without granting other IDs.
New ungranted notes remain outside that caller's scope. Symlinked/escaping note
paths are excluded; an unsafe policy or granted register fails closed.
Individual compiler refresh reads are capped at 16 MiB and use the existing
regular-file opener, which refuses special files/FIFOs without blocking.

## Current versus stale

The manifest binds current raw bytes of permitted notes, the filtered evidence
projection, effective policy, explicit inputs, ranking, selections, budget,
evaluation date and complete report. Raw per-row register/review-log bytes and denied notes are
not hashed; review history is bound by its permitted row projection. Changes confined to denied notes or denied row content do not change
the artifact or manifest. The snapshot conservatively covers **all** permitted
notes outside those raw mixed logs, so adding a matching public query note or changing a public note outside
the final selection makes an earlier artifact stale. It is not a historical Git
snapshot or a proof that a source statement is true.

For permitted native claims, the snapshot also binds the pinned/latest capture
envelopes and full observation ledger. A new observation makes an old context
stale even when deduplication keeps the same capture bytes, or A → B → A returns
to the original version. No ungranted claim's source paths are opened or hashed.
Selected claims and decisions referencing permitted claims receive
`compiled.source_observation_changed`, `_unknown`, `_ambiguous` or `_future`
warnings as applicable. These fixed codes do not classify semantic contradictions.

`verify_compiled` returns `current`, `stale` or `refused` with fixed reason codes.
It checks the bounded untrusted structure and content digest, then recompiles
the recipe over the current permitted scope. Altered bodies/manifests, changed
source or policy bytes, changed expected parameters and another evaluation date
cannot return `current`. Unexpected or non-finite input is refused or fails the
full recompilation comparison. Paths and URLs in supplied context fields are
never opened. Verification is read-only and holds the existing cooperating
read lock; editors that ignore advisory locks remain outside its guarantee.

Pass the same independently chosen sensitivity grant to verification. The
report cannot enlarge the verifier's default public scope. Expected recipe
keywords let the caller bind its own intended parameters, rather than trust
parameters supplied by an artifact's producer.

**This is unsigned content binding, not a signature.** Anyone who can recompute
a valid artifact over the same accessible inputs can produce an equivalent one.
No reviewer or producer identity is authenticated; verification reports
`signature_verified:false` and `execution_authorized:false`. A current result
neither grants permission to execute nor prevents a later edit between
verification and execution. A separate trusted approval/execution boundary is
required for those decisions.

Scoped history admits only original canonical relative target paths. It does
not resolve short aliases against a narrowed index, where a hidden ambiguity
could falsely turn a private event into a public one. Unsupported alias rows
are omitted before counts or hashes; history availability describes recognized
permitted rows, not proof that no other legacy event exists. A capped review
list retains the controlling effective event so backdated or future references
cannot crowd out the reason for the current review state.


## Hybrid query explanations

With `--ranking hybrid`, the report also includes retrieval limits/truncation
and per-query-context score components plus a bounded typed path. Visible path
nodes have citations; graph proximity does not establish semantic support or
matching evidence versions. All added fields and citations count toward the
complete UTF-8 JSON+newline budget and freshness verification. The default
substring and opt-in BM25 recipes omit these fields. A hybrid corpus overflow
returns the bounded refusal `error=scope_too_large`; it never falls back to a
different ranker. See [retrieval](retrieval.md#hybrid-retrieval) for filters,
fixed score weights and construction/traversal bounds.
