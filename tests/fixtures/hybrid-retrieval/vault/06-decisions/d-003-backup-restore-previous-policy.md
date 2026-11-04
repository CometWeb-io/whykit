---
title: "Backup restore previous policy"
aliases: ["D-003"]
type: decision
decision_id: D-003
status: superseded
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-002"]
tags: []
valid_from: 2026-01-01
valid_until: 2026-10-01
claim_ids: ["C-002"]
provenance: 
  claim_versions: "{\"C-002\":\"sha256:a57e31df077344963f65930e55a21abcf8f4539d98c8271ca607935d1b70edc1\"}"
  human_reviewed: true
review_by: 2026-12-31
superseded_by: D-004
---

# Decision record: Backup restore previous policy

## Decision ID

D-003

## Status

Accepted

## Context

A synthetic team needs a backup restore policy and operating procedure.

## Decision

Overwrite the live database before checking integrity.

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
- Supersedes: —
- Superseded by: —
