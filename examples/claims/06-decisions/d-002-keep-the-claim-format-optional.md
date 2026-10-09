---
title: "Keep the claim format optional"
aliases: []
type: decision
decision_id: D-002
status: approved
owner: "Ada Example"
created: 2026-10-09
last_updated: 2026-10-09
source_of_truth: false
sensitivity: public
source_ids: ["E-003"]
tags: []
review_by: 2027-01-07
---

# Decision record: Keep the claim format optional

## Decision ID

D-002

## Status

Accepted

## Context

The ledger already uses E-NNN as source identifiers.

## Decision

Keep C-NNN claims behind an explicit opt-in.

## Rationale

A separate claim record preserves the existing source meaning (E-003).

## Evidence

- E-003

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Keep one queue | Simple | Slow replies | Measured too slow |

## Consequences

### Positive

- Existing vaults preserve their report contract and bytes.

### Negative and trade-offs

- Claim relations require explicit human review.

## Ownership and review

- Owner: Ada Example
- Decided on: 2026-10-09
- Review on: TBD
- Supersedes: —
- Superseded by: —
