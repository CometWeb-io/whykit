---
title: "Retry queue previous policy"
aliases: ["D-001"]
type: decision
decision_id: D-001
status: superseded
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-001"]
tags: []
valid_from: 2026-01-01
valid_until: 2026-10-01
claim_ids: ["C-001"]
provenance: 
  claim_versions: "{\"C-001\":\"sha256:fd7528080caecf6293f38f4efdcfa89bacf5e6a70d4322cf87137d785f035c98\"}"
  human_reviewed: true
review_by: 2026-12-31
superseded_by: D-002
---

# Decision record: Retry queue previous policy

## Decision ID

D-001

## Status

Accepted

## Context

A synthetic team needs a retry queue policy and operating procedure.

## Decision

Retry every request forever and accept duplicate delivery keys.

## Rationale

This retry queue choice is the stipulated policy for this synthetic task. The observed duration is recorded separately.

## Evidence

- E-001

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
