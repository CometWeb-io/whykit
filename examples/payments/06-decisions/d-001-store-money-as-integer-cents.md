---
title: "Store money as integer cents"
aliases: []
type: decision
decision_id: D-001
status: approved
owner: "Payments"
created: 2026-03-05
last_updated: 2026-03-05
source_of_truth: false
sensitivity: public
source_ids: ["E-001"]
tags: []
applies_to: ["src/payments/**"]
review_by: 2027-03-05
---
# Decision record: Store money as integer cents

## Decision ID

D-001

## Status

Accepted

## Context

The 2025 finance audit (E-001) rejects ledgers with floating-point rounding differences.

## Decision

Store and compute every amount as an integer number of minor units (cents).

## Rationale

Integers make rounding explicit and reproducible, which the audit requires.

## Evidence

- E-001

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Decimal floats | Familiar | Rounding drift between services | Fails the audit |

## Consequences

- Display code converts cents to currency only at the edge.

## Ownership and review

- Owner: Payments
