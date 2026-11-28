---
title: "Render checkout on the client with server-side validation"
aliases: []
type: decision
decision_id: D-006
status: approved
owner: "Web"
created: 2026-09-08
last_updated: 2026-09-08
source_of_truth: false
sensitivity: public
source_ids: ["E-002"]
tags: []
supersedes: D-004
applies_to: ["src/web/checkout/**"]
review_by: 2027-09-08
---
# Decision record: Render checkout on the client with server-side validation

## Decision ID

D-006

## Status

Accepted

## Context

Since D-004, every supported browser runs our scripts and the payment form needs inline validation.

## Decision

Render checkout on the client and validate every submission again on the server.

## Rationale

Inline validation cuts failed payments, and server validation keeps the guarantee D-004 protected.

## Evidence

- E-002

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Keep server rendering | No change | No inline validation | Failed payments cost more than the change |

## Consequences

- The server stays the authority on whether a payment request is valid.

## Ownership and review

- Owner: Web
