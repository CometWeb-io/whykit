---
title: "Request budgets current policy"
aliases: ["D-012"]
type: decision
decision_id: D-012
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-10-01
source_of_truth: false
sensitivity: public
source_ids: ["E-006"]
tags: []
supersedes: D-011
valid_from: 2026-10-01
valid_until: 2027-01-01
claim_ids: ["C-006"]
provenance: 
  claim_versions: "{\"C-006\":\"sha256:de17073c8118516a640aa5a0a258e6a2f8fb8db08ebd564de81963c0d957b0b9\"}"
  human_reviewed: true
review_by: 2026-12-31
---

# Decision record: Request budgets current policy

## Decision ID

D-012

## Status

Accepted

## Context

A synthetic team needs a request budgets policy and operating procedure.

## Decision

Count the complete UTF-8 JSON envelope and newline against the byte budget, refusing an unrealistically small limit.

## Rationale

This request budgets choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-006

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Unbounded operation | Simple | No safe limit | Fails the declared constraint |

## Consequences

### Positive

- A bounded and inspectable process.

### Negative and trade-offs

- Verification requires time.

## Ownership and review

- Owner: Ada Example
- Decided on: 2026-09-17
- Review on: TBD
- Supersedes: D-011
- Superseded by: —
