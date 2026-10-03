# Writing to a vault from another system

WhyKit is a sink for durable reasoning. Plenty of systems produce reasoning —
decision-support workflows, research agents, migration scripts, meeting
transcribers — and most of them keep their own state in their own store. This
document is about the seam between the two.

## The design rule

**WhyKit stores the record a person will read in two years. The producer keeps
its own machine state. The `provenance` block links them.**

That means a producer does *not* get its state machine mirrored into the format.
If your system has verdicts, gates, confidence scores, vote tallies or rubric
outputs, those belong in your store, referenced by `provenance.payload_ref`. What
lands here is the decision, the rationale, the evidence and the alternatives — in
prose a human can read without your product installed.

This is a deliberate constraint. A ledger that grows a field every time a new
producer appears stops being a format and becomes an integration surface, and
every vault in existence has to migrate when it changes.

```yaml
---
title: "D-001 — Adopt the sample workflow"
decision_id: D-001
type: decision
status: approved
owner: "Product lead"
created: 2026-09-14
last_updated: 2026-09-14
review_by: 2027-03-14
source_of_truth: false
sensitivity: internal
source_ids: [E-001, E-002]
provenance:
  producer: "example.decision-review/v1"
  producer_run: "run_example_001"
  payload_ref: "ledger://decisions/example-001"
  snapshot_hash: "sha256:9f2c…"
  upstream: ["evidence-pack-example-001"]
  human_reviewed: true
---
```

Unknown keys inside `provenance` are allowed and ignored by the linter, so a
producer can extend it without a format change. See
[`schemas/provenance.schema.json`](../schemas/provenance.schema.json).

## What does not belong here

**Mutable validity overlays.** Several decision systems keep a live field —
`VALID` / `WATCH` / `STALE` / `REOPEN` — that is updated as the world changes.
That is useful, and it is the opposite of what this repository guarantees. An
approved record's reasoning is append-only: it says what was decided and why, as
of a date. Only lifecycle metadata may later move the next review date or mark
the record superseded/archived; the rationale itself does not change.

Model staleness the way the ledger already does:

| Producer concept | WhyKit equivalent |
|---|---|
| `validity: VALID` | `status: approved`, `review_by` still in the future |
| `validity: WATCH` / `STALE` | `review_by` has passed — the linter reports it |
| `validity: REOPEN` | A new `D-NNN` in `draft` |
| `validity: SUPERSEDED` | New record with `supersedes: D-NNN`; old one set to `superseded` |

If your system needs the live field, keep it in your system. Point at it with
`payload_ref`.

## Worked mapping: CW-AIP v2 → WhyKit

[CW-AIP](https://github.com/CometWeb-io/agent-skills) is CometWeb's
interchange protocol for agent skills. Its envelopes are ephemeral — they pass
between workflow steps and die with the session. That is exactly the gap this
repository fills.

### DecisionEnvelope → decision record

| CW-AIP field | Lands as |
|---|---|
| `decision_question` | `title` and the `## Context` section |
| `option` | The `## Decision` section — one sentence |
| `verdict` (`GO`/`NO_GO`/`TEST`/`DEFER`) | Prose in `## Decision`; `status` reflects acceptance, not the verdict |
| `gates`, `controls`, `blockers` | `## Consequences` → trade-offs and conditions |
| `evidence_deps` | `source_ids`, after each pack item gets an `E-NNN` row |
| `snapshot_hash` | `provenance.snapshot_hash` |
| `profile`, `human_approval` | `provenance` (extension keys) |
| `validity` | Not stored — see the table above |
| `as_of` | `created` |

`verdict` maps to prose rather than a field on purpose. `NO_GO` is not a status —
a rejected option is still a decision that was made, and it is recorded as one.

### EvidenceEnvelope → evidence register

Each claim-supporting item becomes one `E-NNN` row with a source and a location.
An item with neither is a placeholder, and the linter will not count it. When a
source is later found wrong or superseded, retire the row rather than editing it:
retired rows keep their ID, record why, and name their replacement.

### Where the adapter lives

**In the producer, not here.** The conversion from CW-AIP to Markdown belongs in
the CometWeb skill tree, for the same reason the format has no `verdict` field:
this repository stays useful to people who have never heard of CW-AIP, and
CometWeb's protocol stays free to change without a format migration.

## Reading a vault from another system

WhyKit exposes bounded, versioned machine-readable surfaces instead of requiring
an agent to ingest the whole vault for every task:

```bash
whykit new note "Research" --workstream notes --link-from Home.md --json
whykit lint --json --quiet              # deterministic structural findings
whykit status --json                    # counts + due review queue
whykit query "pricing" --json           # discovery/filtering
whykit context D-018 --json             # bounded context pack around one target
whykit pack D-018 --query "pricing"      # budgeted multi-record context bundle
whykit graph --format json              # typed bulk topology
whykit impact E-018 --json              # reverse dependency / blast radius
whykit trace --gaps-only --json         # decisions resting on missing/retired/stale evidence
whykit evidence list --json             # active + retired evidence lifecycle
whykit review list --json               # operational review queue
whykit snapshot --compact               # immutable content fingerprint
whykit check --profile ci --json        # named policy gate
whykit policy --json                    # effective repository policy
whykit rules --json                     # stable rule-code catalog
```

Every machine report carries `contract_version: 1`, and every command's JSON
output has a schema in `schemas/`. Under `--json`, a command that cannot run
writes a structured error object with a stable code instead of a report. Treat
rule codes, error codes and contract fields as the stable interface;
human-facing messages may be reworded. [Automation](automation.md) has the exit
codes, the error codes and the stability policy.

Which command fits which job (discovery, a single-record handoff, a budgeted
bundle, topology, blast radius) is in
[How-to: hand bounded context to an agent](guide.md#hand-bounded-context-to-an-agent).
`context` and `pack` cap embedded bodies and report truncation, so token use is
explicit. For MCP hosts, the [MCP server](mcp.md) exposes the same views
read-only.

The typed graph distinguishes ordinary document links from evidence support and
decision supersession. Missing or ambiguous targets remain explicit instead of
being guessed.

For full content, Markdown remains canonical. These reports are derived views,
not a second database.
