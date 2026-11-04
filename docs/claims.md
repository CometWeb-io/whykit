# Offline artifacts and canonical claims

The optional capture → draft claim → anchor verification workflow stores real
records without migrating existing vault files. Markdown remains the canonical
claim record. Captures preserve source versions; byte matches do not establish
truth, semantic support, reviewer identity, approval or execution permission.

## Capture a local version

The installed CLI exposes the same preview/apply workflow:

```bash
uv run whykit artifact capture --root /path/to/vault --evidence E-001 --source-file /path/to/source.txt --recorded-at 2026-10-27T10:00:00Z --json
```

Review its metadata, then repeat the same command with `--write --expect-hash`
and the returned hash. A different source, classification, target or timestamp
requires a fresh preview. No source location is fetched over the network.

`whykit.artifact_store.capture_artifact(root, evidence_id, source,
recorded_at=..., write=False)` previews a bounded regular local file for an
existing, uniquely populated E-ID. `recorded_at` is an explicit UTC ISO timestamp
with seconds (`Z` or `+00:00`, serialized as `Z`); it is not the date the source's
claims became true. The API never opens the register's location or follows URLs.

```python
from pathlib import Path
from whykit.artifact_store import capture_artifact

root = Path("my-vault").resolve()
source = Path("local-source.bin").absolute()
plan = capture_artifact(root, "E-001", source,
                        recorded_at="2026-10-27T10:00:00Z")
# Review the E-ID, raw digest, size, classification and capture time in plan.
result = capture_artifact(root, "E-001", source,
                          recorded_at="2026-10-27T10:00:00Z", write=True,
                          expected_sha256=plan["expected_sha256"])
```

The immutable envelope lives at
`00-context/artifacts/E-001/<raw-sha256>.json` using
[`evidence-artifact.schema.json`](../schemas/evidence-artifact.schema.json).
It includes exact raw bytes as canonical base64, SHA-256, byte count, capture
time and the current evidence classification floor. BOM, newlines, Unicode and
binary bytes are preserved. Base64 is reversible storage, not encryption.
Do not commit confidential source captures into a public repository.

The preview returns metadata and an input-bound `expected_sha256`, never the
bytes or base64. Preview creates no vault files or directories. Apply recomputes
the source/register/target binding under the existing mutation lock and writes
one transaction. Stale previews are refused. Repeating the same E-ID and content
does not rewrite the existing envelope or its original capture time; a changed
version creates a new digest file. Malformed/tampered existing envelopes are
refused rather than overwritten or silently accepted as deduplicated data.
The reader checks the path E-ID/digest, envelope, canonical base64 and raw hash.
These are integrity checks, not signatures: a trusted operator can still replace
a self-consistent capture, timestamp or classification outside WhyKit.

`read_artifact(root, evidence_id, source_version, allowed_sensitivities=...)`
is an internal raw-bytes API, returning `None` for a missing or filtered capture.
Use it only within a host's established scope. Do not serialize its result into
MCP, note bodies, indexes or context packs. Its effective floor is the stricter
of the stored capture label and the current register row (including register
and replacement-chain floors). Invalid/duplicate E-IDs fail closed.

## Record source observations

Each capture also records a metadata event in
`00-context/observations/E-NNN.json`, described by
[`source-observations.schema.json`](../schemas/source-observations.schema.json).
Capture bytes and the observation ledger are applied in one recovery transaction.
The preview hash binds both prior states; a competing observation makes it stale.
An exact retry is idempotent, while an older preview cannot undo a newer event.
The API adds events without replacing old captures or changing approved records.

Events contain only a source version, the supplied UTC timestamp and its stored
sensitivity floor. They contain no source path, URL, quote or base64. A new
observation of A after A → B → A is retained even though A's captured bytes are
deduplicated. Backdated events do not replace newer ones. Ledgers are limited to
64 KiB and 256 events; reaching the limit refuses the write, without pruning.
They are local records, not signed attestations or protection against an operator
rewriting a self-consistent Git history.

`read_source_observation(root, evidence_id, pinned_version, today=...,
allowed_sensitivities=...)` compares a pin with the latest declared observation:

| State | Meaning |
|---|---|
| `current` | The pin matches the unique latest observed version. |
| `changed` | The latest observation names different bytes; review the premise. |
| `unknown` | No observation ledger exists; freshness has not been established. |
| `ambiguous` | Distinct versions share the latest timestamp. |
| `future` | The latest timestamp is on a UTC day after `today`. |

Ordering uses UTC datetimes, including fractional seconds. `today` is a whole
UTC calendar day, not an exact instant: a later hour on that same day is included.
The timestamp is supplied by the operator, not a trusted clock. `current` proves
neither real-world freshness nor semantic support. A byte change alone does not
establish a contradiction. Invalid ledgers or unavailable latest captures fail
closed. Filtered dependencies return `None` without exposing their hashes,
timestamps or counts; stored and current classification floors apply.
Observation metadata requires every component's label in the supplied set: the
current E-row, each event, and the pinned/latest captures. A sparse set can
therefore withhold observations even when the raw artifact reader's effective
classification permits the capture. CLI ceilings include all lower labels.

Existing captures without a ledger are **unknown**, never automatically current.
Review the actual source, then repeat `artifact capture` with its observation
timestamp and the ordinary preview/apply flow. The original capture stays intact;
the new observation is recorded separately. Do not rewrite an approved decision
or its receipt to add this metadata.

## Persist a draft claim

`claim create` reads the anchor array from a local JSON file. It previews by
default; applying requires its reviewed hash:

```bash
uv run whykit claim create "The source states the cache works." --owner "Ada Example" --links /path/to/anchors.json --valid-from 2026-10-27 --root /path/to/vault --json
uv run whykit claim show C-001 --root /path/to/vault --max-sensitivity restricted --json
uv run whykit claim verify C-001 --root /path/to/vault --max-sensitivity restricted --json
```

Listings and reads default to a public ceiling; restricted records require the
explicit higher ceiling. This is a local filtering choice, not an ACL grant.
The [synthetic example](../examples/provenance/README.md) includes a captured
source, restricted claim and approved decision with a content-bound pin.

`whykit.claim_ledger.create_claim(root, statement, links, title=..., owner=...,
today=..., sensitivity="internal", valid_from=None, valid_until=None,
write=False, expected_sha256=None)` allocates the next C-ID from the canonical
claim directory. Preview returns the allocated ID, path, front matter, complete
claim payload and expected hash. Apply must receive the same content, parameters,
dependencies and ID allocation under the mutation lock; competing writes make
the second preview stale. A draft is never promoted to approved by this API.

```python
from whykit.claim_ledger import create_claim, verify_claim

links = [{
    "evidence_id": "E-001", "source_version": result["source_version"],
    "location": {"start_byte": 0, "end_byte": 12, "quote": "Cache works."},
    "assessment": {"kind": "model", "by": "example-model/v1", "relation": "supports"},
}]
plan = create_claim(root, "The captured text says the cache works.", links,
                    title="Cache statement", owner="Ada Example", valid_from="2026-10-27")
# Review plan["record"], plan["payload"] and plan["expected_sha256"].
create_claim(root, "The captured text says the cache works.", links,
             title="Cache statement", owner="Ada Example", valid_from="2026-10-27", write=True,
             expected_sha256=plan["expected_sha256"])
report = verify_claim(root, plan["claim_id"])
```

The fragment above must actually exist at that byte range in the captured
version. A linked capture may be absent, in which case verification explicitly
reports `source_unavailable`; its E-ID must still exist in the register.

Records use `00-context/claims/c-001.md`, required note front matter plus
`claim_id` and optional validity dates. The body is exactly a heading
`# Claim C-001` and one `whykit-claim` fenced JSON envelope with the existing
`whykit.claim-provenance/v1` payload. Duplicate JSON keys, nonfinite constants,
extra fences, inconsistent source_ids, noncanonical C-IDs or identity mismatches
are refused. IDs, filenames and payload IDs agree. New claims never rewrite
approved decisions, approval receipts, evidence rows or old claims.
[`claim-record.schema.json`](../schemas/claim-record.schema.json) describes the
front matter; [`claim-provenance.schema.json`](../schemas/claim-provenance.schema.json)
describes the payload.

**All persisted claim notes are restricted in this version.** They contain source
quotes. A caller's lower requested label is visibly upgraded in the preview;
capture/evidence floors are also validated. The shared note classifier applies
the restricted floor to `type: claim`, a `claim_id` marker or the reserved
`00-context/claims` directory, even if somebody changes its type or stored label.
Public Explorer exports and MCP ceilings below restricted therefore omit
the whole record. Explicit private viewers and a restricted-ceiling MCP can
include it. The canonical claim reader additionally refuses a downgraded stored
label. A future finer-grained export needs a separately reviewed dependency-aware
reader contract; editing the label is not an export workflow.

`read_claim`, `list_claims` and `verify_claim` accept optional allowed label sets;
labels remain filters, not authenticated caller permissions. `read_claim` returns
front matter, payload and the raw Markdown SHA-256; it never returns capture
base64. Verification only reads artifacts at the confined E-ID/hash path. It does
not open source URLs, claim text or register locations as paths. Every returned
anchor status is `matched`, `source_unavailable`, `version_mismatch` or
`location_mismatch`; malformed captures are errors. Assessments preserve the
producer's `human`/`model` and `supports`/`contradicts` declarations without an
aggregate factual verdict. Opposing assessments can coexist.

Each link also reports `source_state` independently of its anchor `status`.
An old quote can still match its immutable capture while a newer source version
requires review. `claim verify --today YYYY-MM-DD` exits 0 only for a linked claim
whose anchors all match and whose source observations are all current. Unknown,
changed, ambiguous, future or filtered observations cannot produce success.
This remains a structural check, not semantic validation or permission to act.

Hosts that already have a scoped index can use the pure
`claim_visibility(note, visible_evidence_ids, root=canonical_vault_root)` helper after their normal note
ceiling. It strictly parses only the supplied bounded note text, checks actual
restricted metadata and canonical identity/path, and requires every linked E-ID
in the host's already-filtered active/retired set. Invalid or hidden dependencies
return false. It reads no files and produces no register/artifact hashes.
Pass the host's canonical root to reject look-alike records inside nested vault
directories; omitting root checks only the final canonical path components.
`claim_evidence_ids(text)` exposes the same strict parser's E-ID set and raises
`ValueError` for malformed records. These helpers do not establish a scope or
verify capture bytes; the host supplies the former and `verify_claim` checks
the latter.

## Bind a claim version before decision approval

An action preflight can rely structurally on a claim only when its exact raw
Markdown hash was pinned in the **draft** decision before the normal decision
approval preview/apply workflow. Do not backfill or rewrite an approved record
to add pins: its existing receipt binds its original reasoning and provenance.

The current front-matter parser supports one nested mapping level. Therefore
`provenance.claim_versions` is a **single-line JSON object encoded as a YAML
string scalar**, not a third-level YAML mapping:

```yaml
claim_ids: [C-001]
provenance:
  claim_versions: '{"C-001":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}'
  human_reviewed: false
```

Replace the illustrative digest with `"sha256:" +
hashlib.sha256(claim_path.read_bytes()).hexdigest()`. Hash the exact complete
Markdown bytes, including front matter, quoting, newlines and payload. The
ordinary approval workflow binds this provenance scalar through the existing
approval record hash and sets its own human-review flag. A digest does not
authenticate the claim's assessor or prove its statement.

Preflight parses at most 16 KiB of scalar JSON and 64 unique canonical C-IDs per
decision. Duplicate JSON keys, nonfinite values, mismatched map/list keys and
anything other than `sha256:` plus 64 lowercase hex digits are refused. A missing
map requires review; a present map must match `claim_ids` exactly. A changed raw
claim snapshot blocks, including changes that render its Markdown malformed.
Missing/unreadable claims, corrupt captures and unmatched anchors also block.
Unlinked claims, explicit `contradicts` assessments and model-only assessed
anchors require review. A declared human assessment remains unauthenticated
attribution. Current evidence lifecycle/age policy also applies to the claim's
linked E-IDs, including indirect dependencies absent from decision source_ids.
Missing `valid_from` requires review as `claim_validity_unknown`; preflight never
substitutes created/last_updated. A pinned claim before its explicit start or at
or beyond its exclusive end requires review as `claim_not_current`. Matching
captured bytes and a receipt do not establish that a premise is current.

`read_claim` and `verify_claim` accept an optional `expected_sha256` containing
the bare 64-hex raw Markdown digest. They raise `ClaimSnapshotChanged` before
parsing a changed bounded record. Existing unpinned read/verify APIs still inspect
the current record. A passing preflight remains structural:
`execution_authorized`, `semantics_checked` and `trusted_history_checked` stay
false, and human review is still required.

`valid_from` and `valid_until` are optional real ISO calendar dates defining a
half-open interval `[valid_from, valid_until)`. Missing bounds mean unknown, not
an inferred creation, evidence or review date. A present end must be later than
a present start. This is a basis for explicit validity queries, not a complete
bitemporal model or independently confirmed factual validity.

## Bounds and trust

Sources are limited to 8 MiB; the API's `max_bytes` may only lower that limit.
Artifact JSON is limited to 12 MiB, evidence registers to 2 MiB, claim Markdown
to 1 MiB and 256 links, and the claim directory to 10,000 records / 16 MiB total
Markdown. Sources, captures and claims must be regular files. Symlink paths,
FIFOs/devices and sources changing during the bounded read are refused. The
shared safe opener uses no-follow and nonblocking OS flags where available,
then checks the actual descriptor. Windows keeps the same regular-file checks;
no-follow protection depends on platform support.

The process/operator and filesystem permissions remain trusted. No file-size
limit or hash makes arbitrary external editors obey the advisory lock, and this
does not claim atomicity against hostile replacement of ancestor directories.
There are no network/provider calls, embeddings, model extraction, source
credential discovery, SSO, RBAC or automatic semantic contradiction detection.
Unlinked claims and contradictory assessments remain explicit review inputs.
