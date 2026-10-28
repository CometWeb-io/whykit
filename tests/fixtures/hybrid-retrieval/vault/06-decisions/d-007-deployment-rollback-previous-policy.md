---
title: "Deployment rollback previous policy"
aliases: ["D-007"]
type: decision
decision_id: D-007
status: superseded
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-004"]
tags: []
valid_from: 2026-01-01
valid_until: 2026-10-01
claim_ids: ["C-004"]
provenance: 
  claim_versions: "{\"C-004\":\"sha256:dc02a11716b1a8ccdd9f6430405f028cb150dd834146acd0703528fcda1dd704\"}"
  human_reviewed: true
review_by: 2026-12-31
superseded_by: D-008
---

# Decision record: Deployment rollback previous policy

## Decision ID

D-007

## Status

Accepted

## Context

A synthetic team needs a deployment rollback policy and operating procedure.

## Decision

Delete the previous release before checking the health gate.

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
- Supersedes: —
- Superseded by: —
