---
title: "Data retention current policy"
aliases: ["D-006"]
type: decision
decision_id: D-006
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-10-01
source_of_truth: false
sensitivity: public
source_ids: ["E-003"]
tags: []
supersedes: D-005
valid_from: 2026-10-01
valid_until: 2027-01-01
claim_ids: ["C-003"]
provenance: 
  claim_versions: "{\"C-003\":\"sha256:82442782564d6be248ef9c774498a624e55d4249b393bc6fb7cf6ccf6fa30d4a\"}"
  human_reviewed: true
review_by: 2026-12-31
---

# Decision record: Data retention current policy

## Decision ID

D-006

## Status

Accepted

## Context

A synthetic team needs a data retention policy and operating procedure.

## Decision

Delete expired payloads after thirty days while retaining aggregate counts without payload content.

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
- Supersedes: D-005
- Superseded by: —
