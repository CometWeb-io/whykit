---
title: "Keep claims and source versions separate from approved reasoning"
aliases: []
type: decision
decision_id: D-003
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: internal
source_ids: ["E-004"]
tags: []
claim_ids: ["C-001"]
provenance:
  claim_versions: '{"C-001":"sha256:4a74b2ac695da370835bccdefcf2bcfe8b1283ed9b139a5f04271ad5a1108117"}'
  human_reviewed: true
review_by: 2026-12-16
---

# Decision record: Keep claims and source versions separate from approved reasoning

## Decision ID

D-003

## Status

Accepted

## Context

An approved decision already has a content-bound receipt. Adding a new claim must not rewrite its earlier rationale or provenance.

## Decision

Use optional canonical claim notes and immutable captured source envelopes. Pin reviewed claim hashes in new decision provenance; leave old decision records untouched.

## Rationale

This keeps Markdown and Git inspectable, preserves original bytes, and makes later claim changes visible to preflight without treating a quoted fragment or reviewer label as proof of truth or identity.

## Evidence

- E-004

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Keep the old process | Familiar | Missing audit trail | Does not meet the requirement |

## Consequences

### Positive

- Explicit byte anchors and separately reviewable observations.

### Negative and trade-offs

- Older clients do not understand the optional claim type. Claims containing quotes remain restricted and need an explicit reader ceiling.

## Ownership and review

- Owner: Ada Example
- Decided on: 2026-09-17
- Review on: TBD
- Supersedes: —
- Superseded by: —

## Related claim

[[00-context/claims/c-001|Pinned claim C-001]].
