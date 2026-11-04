---
title: "Record source observations separately from immutable captures"
aliases: []
type: decision
decision_id: D-004
status: draft
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: internal
source_ids: ["E-004"]
tags: []
review_by: 2026-12-16
---

# Decision record: Record source observations separately from immutable captures

## Decision ID

D-004

## Status

Proposed

## Context

Content-addressed captures deduplicate the same bytes. A second observation of version A after B must remain visible without rewriting the original A capture or an approved decision.

## Decision

Keep an optional per-source metadata observation ledger beside immutable captures. Capture and event append use one reviewed transaction. Compare the pinned version with the unique latest explicitly recorded UTC observation; an absent ledger is unknown and tied versions are ambiguous.

## Rationale

The synthetic source E-004 requires original capture bytes to remain stable. Separate observations preserve that constraint while exposing changes to preflight and invalidating compiled context. Different bytes indicate review work, not a proven semantic contradiction.

## Evidence

- E-004

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Infer latest from capture timestamps | No extra ledger | Dedup loses A → B → A | Does not preserve repeated observations |
| Rewrite a current pointer | Small state | Loses uncommitted observation history | Cannot explain intervening changes |

## Consequences

### Positive

- Old capture bytes and approvals remain stable; later acquisitions remain inspectable.

### Negative and trade-offs

- UTC timestamps remain operator assertions. The reader uses calendar-day precision, no trusted clock, and no semantic inference. Ledgers have bounded size and refuse overflow.

## Ownership and review

- Owner: Ada Example
- Decided on: TBD
- Review on: 2026-12-16
- Supersedes: —
- Superseded by: —
