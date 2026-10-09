---
title: "Bind approval to reviewed record bytes"
aliases: []
type: decision
decision_id: D-001
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-001"]
tags: []
review_by: 2026-12-16
---

# Decision record: Bind approval to reviewed record bytes

## Decision ID

D-001

## Status

Accepted

## Context

Editable approval labels alone cannot show which record was reviewed. A local workflow must bind acceptance to specific bytes (E-001).

## Decision

Preview the complete draft and sources, then accept only that preview hash in one recoverable record-and-ledger transaction.

## Rationale

Binding draft, policy and register bytes detects changes between preview and acceptance. A canonical record hash lets Git history reject a receipt attached to different reasoning. Reviewer labels remain assertions rather than authenticated identity.

## Evidence

- E-001

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Edit status directly | Fewer steps | Does not bind a review to content | Fails the acceptance requirement |

## Consequences

### Positive

- Acceptance identifies the reviewed record and changes its ledgers together.

### Negative and trade-offs

- Review takes time; hashes do not establish source truth or reviewer identity.

## Ownership and review

- Owner: Ada Example
- Decided on: 2026-09-17
- Review on: TBD
- Supersedes: —
- Superseded by: —
