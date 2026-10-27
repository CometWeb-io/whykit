---
title: "Data retention previous policy"
aliases: ["D-005"]
type: decision
decision_id: D-005
status: superseded
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-003"]
tags: []
valid_from: 2026-01-01
valid_until: 2026-10-01
claim_ids: ["C-003"]
provenance: 
  claim_versions: "{\"C-003\":\"sha256:82442782564d6be248ef9c774498a624e55d4249b393bc6fb7cf6ccf6fa30d4a\"}"
  human_reviewed: true
review_by: 2026-12-31
superseded_by: D-006
---

# Decision record: Data retention previous policy

## Decision ID

D-005

## Status

Accepted

## Context

A synthetic team needs a data retention policy and operating procedure.

## Decision

Keep every original payload forever in the aggregate table.

## Rationale

This data retention choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-003

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
- Supersedes: —
- Superseded by: —
