---
title: "Keep measured outcomes separate from approvals and causal claims"
aliases: []
type: decision
decision_id: D-005
status: draft
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: internal
source_ids: ["E-004", "E-005"]
tags: []
review_by: 2026-12-16
---

# Decision record: Keep measured outcomes separate from approvals and causal claims

## Decision ID

D-005

## Status

Proposed

## Context

A target, an observed value and an approved decision are different declarations. Capturing metric values must not silently rewrite an older approval or imply a causal business effect.

## Decision

Use a separate restricted Outcome record with exact decimal strings, units, definitions and measurement dates. Pin supporting claims and keep baseline, target and observed values distinct. Convert to the v2 interchange model with a full raw bundle and restore only to a new destination.

## Rationale

Synthetic E-005 makes the fixture values inspectable at exact captured bytes, while E-004 preserves immutable source versions. Source binding is checked separately from numeric agreement, source freshness, causal effect and authorization.

## Evidence

- E-004
- E-005

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Put results into the approved decision | One file | Rewrites accepted reasoning | Violates history constraints |
| Treat target attainment as causality | Simple score | Confounds declaration and inference | Unsupported by source binding |

## Consequences

### Positive

- Original approval bytes remain stable; data and provenance stay independently inspectable.

### Negative and trade-offs

- Native records require an explicit restricted view. Portable bundles include private raw bytes and do not transfer Git history; safe export/restore currently requires POSIX.

## Ownership and review

- Owner: Ada Example
- Decided on: TBD
- Review on: 2026-12-16
- Supersedes: —
- Superseded by: —
