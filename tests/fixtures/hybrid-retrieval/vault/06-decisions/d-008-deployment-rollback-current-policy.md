---
title: "Deployment rollback current policy"
aliases: ["D-008"]
type: decision
decision_id: D-008
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-10-01
source_of_truth: false
sensitivity: public
source_ids: ["E-004"]
tags: []
supersedes: D-007
valid_from: 2026-10-01
valid_until: 2027-01-01
claim_ids: ["C-004"]
provenance: 
  claim_versions: "{\"C-004\":\"sha256:dc02a11716b1a8ccdd9f6430405f028cb150dd834146acd0703528fcda1dd704\"}"
  human_reviewed: true
review_by: 2026-12-31
---

# Decision record: Deployment rollback current policy

## Decision ID

D-008

## Status

Accepted

## Context

A synthetic team needs a deployment rollback policy and operating procedure.

## Decision

Keep the previous release available and switch back when the health gate fails.

## Rationale

This deployment rollback choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-004

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
- Supersedes: D-007
- Superseded by: —
