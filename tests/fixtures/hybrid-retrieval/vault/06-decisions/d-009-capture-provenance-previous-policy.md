---
title: "Capture provenance previous policy"
aliases: ["D-009"]
type: decision
decision_id: D-009
status: superseded
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-005"]
tags: []
valid_from: 2026-01-01
valid_until: 2026-10-01
claim_ids: ["C-005"]
provenance: 
  claim_versions: "{\"C-005\":\"sha256:980420df224a965dc3e6150a0ebf3d58cf1247869d3601e307acc03b27b3074d\"}"
  human_reviewed: true
review_by: 2026-12-31
superseded_by: D-010
---

# Decision record: Capture provenance previous policy

## Decision ID

D-009

## Status

Accepted

## Context

A synthetic team needs a capture provenance policy and operating procedure.

## Decision

Replace the captured version in place and keep only its current filename.

## Rationale

This capture provenance choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-005

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
