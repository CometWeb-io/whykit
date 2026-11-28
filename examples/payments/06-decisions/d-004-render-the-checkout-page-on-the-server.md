---
title: "Render the checkout page on the server"
aliases: []
type: decision
decision_id: D-004
status: superseded
owner: "Web"
created: 2026-05-04
last_updated: 2026-09-08
source_of_truth: false
sensitivity: public
source_ids: ["E-002"]
tags: []
applies_to: ["src/web/checkout/**"]
review_by: 2027-05-04
superseded_by: D-006
---
# Decision record: Render the checkout page on the server

## Decision ID

D-004

## Status

Accepted

## Context

The checkout page must work when client scripts fail to load.

## Decision

Render the checkout page on the server.

## Rationale

Server rendering keeps checkout usable without client scripts.

## Evidence

- E-002

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Client rendering | Richer UI | Blank page when scripts fail | Checkout must not depend on scripts |

## Consequences

- Interactive features are limited on the checkout page.

## Ownership and review

- Owner: Web
