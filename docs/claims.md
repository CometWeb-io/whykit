# Optional reviewed claims

WhyKit keeps sources (`E-NNN`) separate from claim records (`C-NNN`). An E row's
`claims` cell remains a source description. Decisions keep `source_ids` for E
citations and may add `claim_ids` for reviewed C dependencies. Existing vaults
and v1 schemas retain their meanings and bytes until explicit opt-in.

```toml
[claims]
format_version = 1
```

Use `whykit claims enable --root VAULT --json` for a preview. Apply the reviewed
result with the same command plus `--write --expect-hash HASH`. The hash binds
configuration and inventory bytes. The report includes the original config as
base64, before/after hashes and unchanged D/E counts. Only configuration changes;
no decision, source or receipt is inferred or rewritten. Disable refuses any
remaining C records, claim references or unreadable records. It never deletes
claims. Keep the preview's config backup for a deliberate manual rollback.

## Author and review

```sh
whykit new --root VAULT claim "Offline reads" \
  --statement "Cached records can be read offline." --scope "Desktop v2.1" \
  --valid-from 2026-10-01 --owner "Ada Example"
```

This writes an unreviewed draft at `00-context/claims/c-NNN-slug.md` with an empty
Evidence table. Supply `valid_to` when validity has an end. Review scheduling is
separate from the validity interval. There is no writable `verification_status`:
the current report computes it from captured material and a matching receipt.

Add explicit `supports` or `contradicts` rows under exactly one `## Evidence`:

| evidence_id | relation | snapshot | source_snapshot_hash | fragment | observed_at | rationale |
| --- | --- | --- | --- | --- | --- | --- |

Each relation names a real active E source and a local hash-named UTF-8 snapshot
under `00-context/claim-snapshots/<sha256>.txt`. Normalize a beginning BOM and
CRLF to LF, hash those normalized bytes, and use the same SHA-256 in the filename
and row. Lone CR and other content remain significant. `fragment` is
`lines:N-M`, inclusive and one-based. `observed_at` dates the observation;
`rationale` explains the human-reviewed relationship. Repeated E+snapshot+fragment
rows are rejected even if they claim opposite relationships.

A snapshot has a **1 MiB raw-byte limit**, each C at most **256 relations**.
Symlinks, directories, traversal, invalid UTF-8, mismatched hashes, missing or
out-of-range fragments and future observations fail closed. The CLI does not
fetch URLs, convert PDFs or silently truncate source material. Explorer may
carry up to 20,000 fragment characters per C; pack shares its explicit text
budget across note bodies and fragments. Both are untrusted source data.

```sh
whykit review --root VAULT approve C-001 --reviewer "Ada Example" --json
# Review all proposed changes, then repeat with the returned hash:
whykit review --root VAULT approve C-001 --reviewer "Ada Example" \
  --write --expect-hash HASH --json
whykit new --root VAULT decision "Evaluate offline reads" --claim C-001
```

Complete the existing decision sections and remove every placeholder. `unknown`
C blocks decision approval. For every `disputed` or `unsupported` dependency,
write one non-placeholder explanation in a single section:

```markdown
## Claim assessment

- C-001: The conflict is bounded to the two environments; verify before expanding support.
```

Duplicate entries, unreferenced IDs and empty/placeholder rationales are rejected.
All C relations must remain usable for D approval, including a third unresolved
relation in an otherwise established dispute.
Preview/apply D approval through the same workflow, then use `trace`, `impact`,
`context` or `pack`. E impact includes direct C and dependent D with provenance;
trace keeps legacy E citations separate from claim assessments.
`trace --gaps-only` includes claim gaps. Canonical graph filtering records omitted
claims as `unresolved` with reason `filtered`; a pack that reaches `max_docs`
reports omitted C dependencies in `missing` with reason `max_docs` and marks its
budget exhausted. Text trace and Markdown packs also show the computed state.

## Meaning of the current assessment

| State | Meaning |
| --- | --- |
| `supported` | Every bound relation is usable and only supports is present. |
| `disputed` | Usable reviewed supports and contradicts coexist, even if a third relation is unresolved. |
| `unsupported` | Every bound relation is usable and only contradicts is present. |
| `unknown` | Review is absent/invalid/overdue, validity does not cover the date, or material is unresolved without an established two-sided conflict. |

Supported is **not truth**. Unknown or stale material is not false. Assessment
uses current captured bytes; `history_reconstructed: false` states that
`--today` does not reconstruct past Git data. A reviewed dispute is report data,
not a structural lint failure. A decision's explicit treatment of that dispute
can remain valid; source/receipt drift schedules it for review.

## Integrity and privacy

`claim-receipt/v1:<base64url>` binds the semantic C record, exact E rows,
snapshot hashes, selected fragments and review dates. A decision's
`decision-claim-receipt/v1:<base64url>` binds the semantic D record plus C record
and receipt hashes. Receipts and reviewer strings are **not authenticated**
identity, signatures or proof of truth. Trusted Git base policy is a separate
boundary. Receipt payloads are bounded and canonically decoded; malformed,
replayed, reset or duplicate events do not become approvals.

`review record C-NNN --outcome confirmed` previews a new review. Apply with
`--write --expect-hash`; the same applies to D with claim dependencies. Confirmation
revalidates sources/fragments and appends a fresh receipt. Legacy D without
claims retain their existing record-review behavior. Accepted semantic edits
require a new C and fresh approval with `--supersedes C-NNN`; the predecessor is
marked superseded atomically. Do not reset it, drop contradictory material or
rewrite its accepted rationale. Git history guards accepted records and their
referenced normalized snapshots; raw-byte changes still invalidate write previews.
The latest `update-required` or `supersede-required` review makes C unknown and
schedules dependent D for review. An `archived` review also archives the record;
use its preview/apply workflow instead of manually changing accepted C status.
An unchanged record can regain a current assessment through `confirmed` after
all sources are revalidated. Archived records remain historical.

The sensitivity floor is the maximum of C and every source it uses. A snapshot
inherits every referring C/E floor, including shared snapshots. Dependents are
withheld as whole records before assessment; raw/code IDs, hashes and links also
propagate privacy. Unknown/malformed dependencies are withheld, not optimistically
classified as public. A private snapshot shared with a public C can therefore
withhold that public C and its dependents. MCP remains read-only and does not
expose receipts, host paths or hidden artifact counts. Explorer receives computed
fields; it does not infer support from text or reconstruct receipt state in JS.

## Parallel report versions

Claims-enabled vaults use `contract_version: 2` for graph, backlinks, trace,
impact, query, context, pack, status, review, history, lint, check and Explorer.
Pack's format is `whykit.context-bundle/v2`. Error envelopes and unchanged commands
stay v1; workspace stays v1 with each nested vault's actual report version.
Parallel `*-v2.schema.json` files preserve the original v1 schema IDs. MCP declares
both accepted shapes and each result uses one actual version. C/E/config/snapshot/review
changes invalidate affected cursors; metadata never silently falls back to v1.

The [synthetic claims example](../examples/claims/README.md) demonstrates the
normal C/D preview/apply workflow and a separate accepted format decision. It
proves local deterministic mechanisms, not real product behavior or model quality.
