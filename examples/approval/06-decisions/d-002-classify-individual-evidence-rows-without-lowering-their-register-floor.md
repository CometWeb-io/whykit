---
title: "Classify individual evidence rows without lowering their register floor"
aliases: []
type: decision
decision_id: D-002
status: approved
owner: "Ada Example"
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: ["E-003"]
tags: []
review_by: 2026-12-16
---

# Decision record: Classify individual evidence rows without lowering their register floor

## Decision ID

D-002

## Status

Accepted

## Context

A registry can contain sources that need stricter handling than its general description. Exporting its raw table bypasses row filtering (E-003).

## Decision

Allow an optional final Sensitivity column in both active and retired source tables. A row cannot lower its register floor; a retired row also inherits its replacement's ceiling. Keep raw per-row tables out of filtered views.

## Rationale

One captured register snapshot keeps labels and contents consistent. Missing labels in a labeled table, unknown values and duplicate IDs are withheld. Legacy tables retain inheritance; retirement preserves the explicit label.

## Evidence

- E-003

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Classify the whole register only | Simple | Cannot publish a safe subset | Loses row granularity |

## Consequences

### Positive

- Public rows can be exported independently from stricter rows.

### Negative and trade-offs

- Filtered viewers cannot return the raw labeled register body.
- Sensitivity labels remain editable assertions rather than authentication.

## Review

Review labels and reader behavior when the evidence format changes.
