---
title: "Make every webhook handler idempotent"
aliases: []
type: decision
decision_id: D-003
status: approved
owner: "Payments"
created: 2026-04-20
last_updated: 2026-04-20
source_of_truth: false
sensitivity: public
source_ids: ["E-003", "E-004"]
tags: []
applies_to: ["src/webhooks/**"]
review_by: 2027-04-20
---
# Decision record: Make every webhook handler idempotent

## Decision ID

D-003

## Status

Accepted

## Context

The provider retries webhooks for 24 hours (E-003); a retried refund paid a customer twice (E-004).

## Decision

Every webhook handler records the provider event ID and ignores repeats.

## Rationale

Retries are guaranteed by the provider, so duplicates are normal traffic, not an edge case.

## Evidence

- E-003
- E-004

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Disable provider retries | No duplicates | Lost events during our outages | Losing a payment event is worse |

## Consequences

- Handlers need a unique index on the provider event ID.

## Ownership and review

- Owner: Payments
