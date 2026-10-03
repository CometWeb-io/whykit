---
title: "Keep the ledger to evidence and decisions"
aliases: []
type: decision
decision_id: D-002
status: approved
owner: "Vault owner"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: internal
source_ids: ["E-001", "E-002"]
tags: []
review_by: 2027-03-17
supersedes: D-001
---

# Decision record: Keep the ledger to evidence and decisions

## Decision ID

D-002

## Status

Accepted

## Context

D-001 was accepted straight from the `whykit new decision` scaffold, so its
body still holds the template prompts and records no choice. Approved records
are not rewritten, so this record states the decision and supersedes it.

## Decision

The vault holds only an evidence register and decision records until the team
has used it for at least one review cycle.

## Rationale

The team asked for a small ledger before a large wiki (E-002). Two registers
are enough to show whether people cite sources when they decide, and every
example domain stays inside the reserved names documented in E-001.

## Evidence

- E-001
- E-002

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Start with a full wiki | Room for every topic | Pages nobody owns go stale | The team asked for a small ledger first (E-002) |

## Consequences

### Positive

- Every decision has one place for its reasoning and its sources.

### Negative and trade-offs

- Context that is neither a source nor a decision waits in `notes/`, which has
  no review cadence.

## Ownership and review

- Owner: Vault owner
- Decided on: 2026-09-17
- Review on: 2027-03-17
- Supersedes: D-001
- Superseded by: —
