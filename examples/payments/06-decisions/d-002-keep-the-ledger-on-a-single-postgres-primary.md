---
title: "Keep the ledger on a single Postgres primary"
aliases: []
type: decision
decision_id: D-002
status: approved
owner: "Platform"
created: 2026-03-25
last_updated: 2026-03-25
source_of_truth: false
sensitivity: public
source_ids: ["E-002"]
tags: []
applies_to: ["src/payments/db/**", "migrations/**"]
review_by: 2026-09-23
---
# Decision record: Keep the ledger on a single Postgres primary

## Decision ID

D-002

## Status

Accepted

## Context

Checkout volume doubles every year; we needed to know whether one database is enough.

## Decision

Keep the ledger on one Postgres primary with a synchronous replica.

## Rationale

The March load test (E-002) shows headroom of four times peak volume.

## Evidence

- E-002

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Shard by merchant | Scales further | Cross-shard transactions for transfers | Not needed at current volume |

## Consequences

- Revisit when peak volume reaches half of the tested capacity.

## Ownership and review

- Owner: Platform
