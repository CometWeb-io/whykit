---
title: "Backup restore current policy"
aliases: ["D-004"]
type: decision
decision_id: D-004
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-10-01
source_of_truth: false
sensitivity: public
source_ids: ["E-002"]
tags: []
supersedes: D-003
valid_from: 2026-10-01
valid_until: 2027-01-01
claim_ids: ["C-002"]
provenance: 
  claim_versions: "{\"C-002\":\"sha256:a57e31df077344963f65930e55a21abcf8f4539d98c8271ca607935d1b70edc1\"}"
  human_reviewed: true
review_by: 2026-12-31
---

# Decision record: Backup restore current policy

## Decision ID

D-004

## Status

Accepted

## Context

A synthetic team needs a backup restore policy and operating procedure.

## Decision

Restore into an isolated directory and verify database integrity before replacing live storage.

## Rationale

This backup restore choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-002

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
- Supersedes: D-003
- Superseded by: —
