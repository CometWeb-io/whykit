# Structural agent preflight

Inspect the decisions an agent explicitly names as dependencies of a proposed
action, using current vault bytes. This read-only check runs offline, requires
only the standard library and does not execute the proposal or run Git.

From a source checkout:

```bash
uv run whykit agent preflight --root /path/to/vault --proposal /path/to/proposal.json --json
```

The proposal file is a JSON object:

```json
{"proposal_id":"dependency-upgrade","decision_ids":["D-001"],"summary":"Review the recorded decision before editing."}
```

`proposal_id` is 1–80 ASCII letters, digits, underscores or hyphens, beginning
with a letter or digit. `decision_ids` contains 1–64 unique ASCII D-NNN IDs.
The optional `summary` is at most 4096 characters and is never emitted or used
to determine the result. Unknown fields, duplicate JSON keys, non-finite JSON
constants, malformed UTF-8 and files over 64 KiB are refused. Input paths must be
regular files; symlinks, FIFOs and devices are refused before a blocking read.

The Python entrypoint is `whykit.agent_preflight.check_proposal(root,
proposal_path, today=...)`. The standalone script additionally accepts
`--today YYYY-MM-DD`; this evaluates **current bytes**, not a historical snapshot.

| Verdict | Meaning | Script exit |
|---|---|---|
| `pass` | Named records have matching local approval receipts, are binding, and have current review dates and evidence within configured age limits. | 0 |
| `needs_review` | Only overdue reviews, stale evidence or absent evidence age limits were found. Refresh or review those inputs first. | 1 |
| `blocked` | Missing, ambiguous, malformed, nonbinding or superseded records; unusable evidence; missing/mismatched receipts; unsupported claim metadata. | 1 |

Malformed or unavailable inputs produce a redacted error object and exit 2.
All successful reports match the additive
[result schema](../schemas/agent-preflight-result.schema.json). They contain
only the declared proposal ID, its exact-byte SHA-256, evaluation date, declared
decision IDs and stable problem codes. Hard blockers take precedence over
`needs_review` findings. Unrelated notes, titles, paths and reviewer labels are
not emitted. The script does not change existing CLI or MCP contracts.

Every verdict, including `pass`, sets `review_required:true`,
`execution_authorized:false`, `semantics_checked:false` and
`trusted_history_checked:false`. A caller must never interpret exit 0 as
permission to write, merge, publish or deploy. Sensitivity labels are not an
authorization boundary; run this local reader only with access to its vault.

Local receipts bind record content to editable log entries. A forged reviewer
label or an attacker rewriting both the record and its receipt cannot be
authenticated by this check. The existing [history and baseline policy
gate](security-model.md#decision-approval-and-trusted-policy) remains a separate check
against an independently trusted Git base. This preflight neither replaces it
nor changes approval or transaction behavior.

Coverage is limited to the declared IDs. The check cannot find decisions an
agent omitted, determine whether the action falls within their scope, verify
source truth or decide semantic compliance. Structured `claim_ids` are verified
against the canonical claim ledger and exact captured anchors. New decisions
must pin their reviewed claim versions before approval, as described in
[claims and captures](claims.md#bind-a-claim-version-before-decision-approval).
Missing pins, unknown validity, non-current intervals, unlinked claims,
contradicting assessments or model-only support require review. Changed pins,
missing records and unverified anchors block the result. Indirect claim sources
also undergo the ordinary evidence-age, retirement and duplicate checks.
Source observations are checked separately from the old byte anchors. A newer
version, absent ledger, tied latest versions or future UTC day requires review;
corrupt observation metadata or a missing latest capture blocks verification.
The `today` cutoff includes the whole UTC day, including its later hours. It is
not a trusted timestamp or a reconstruction of what was known at a precise
instant. A → B → A can match the pin again after an explicit new observation,
while the intermediate events remain in the ledger. It does not reverse an
approval, rewrite a claim or establish that the source still supports it.
Identifiers mentioned only in prose are not interpreted or assessed. A
structural `pass` does not establish semantic support or authenticated identity;
a human must assess relevance, completeness and authorization independently.

## Require compiled context

The installed CLI can require a current [compiled context](compiled-context.md)
covering every declared decision:

```bash
uv run whykit agent preflight --root /path/to/vault --proposal /path/to/proposal.json --context /path/to/context.json --max-sensitivity restricted --json
```

The caller chooses the same explicit ceiling used to compile the context.
An altered, stale or differently scoped report is blocked. A current report
that omits a declared decision is also blocked. This checks the input binding;
it neither executes the proposal nor certifies the caller's permission.

The reader holds the existing shared vault lock and rejects symlinked critical
inputs before indexing. Editors that ignore advisory locks remain outside its
consistency guarantee. No signed identity, organization access control,
compliance certificate or model-quality benchmark is provided.

Context coverage counts only canonical decision IDs whose compiled
`applicability.premise` is true. An explicit draft, future, expired, historical
or review-required reference does not satisfy the proposal's required decision
coverage, even when its report is byte-current. A current report missing an
admitted premise is blocked with `context_missing_decision`. Standalone
preflight remains the same structural report; neither route grants execution
authority.

Explicit decision validity is also checked without a compiled report. A future,
expired or malformed declared interval blocks with `decision_not_yet_valid`,
`decision_expired` or `invalid_decision_validity`. Legacy missing intervals
remain unknown; absence does not invent a date or remove an existing local
approval. These checks use the same half-open validity helper as temporal
queries and the compiler.

Review obligations use the latest declared review date on or before the
evaluation day, with visible append order breaking ties. Backdating a later
appended confirmation cannot clear a newer-dated `update-required` or
`supersede-required` request. Those return `needs_review` with
`review_update_required` or `review_supersede_required`; an effective archived
review is nonbinding. Dates remain local declarations, not a trusted clock.
