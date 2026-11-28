---
title: Decision log
aliases: []
type: decision
status: draft
owner: "Payments"
created: 2026-03-01
last_updated: 2026-09-08
source_of_truth: false
sensitivity: public
source_ids: []
tags: []
---

# Decision log

The index of material decisions. One row per decision; the reasoning lives in the
linked record.

ID format: `D-NNN`, assigned in order, never reused.

| ID | Decision | Date | Owner | Status | Record |
|---|---|---|---|---|---|
| D-001 | Store money as integer cents | 2026-03-05 | Payments | accepted | [[06-decisions/d-001-store-money-as-integer-cents]] |
| D-002 | Keep the ledger on a single Postgres primary | 2026-03-25 | Platform | accepted | [[06-decisions/d-002-keep-the-ledger-on-a-single-postgres-primary]] |
| D-003 | Make every webhook handler idempotent | 2026-04-20 | Payments | accepted | [[06-decisions/d-003-make-every-webhook-handler-idempotent]] |
| D-004 | Render the checkout page on the server | 2026-05-04 | Web | superseded | [[06-decisions/d-004-render-the-checkout-page-on-the-server]] |
| D-005 | Keep decision records in the main repository | 2026-05-10 | Engineering | accepted | [[06-decisions/d-005-keep-decision-records-in-the-main-repository]] |
| D-006 | Render checkout on the client with server-side validation | 2026-09-08 | Web | accepted | [[06-decisions/d-006-render-checkout-on-the-client-with-server-side-validation]] |

## What belongs here

A decision is material when reversing it would cost real work, when it commits
the company publicly, or when someone six months from now would otherwise ask
"why on earth is it like this?".

Routine choices do not need a record. If you are unsure, the test is whether you
would want the reasoning in a year.

## Review

TODO — set a cadence for reviewing accepted decisions whose assumptions may have
expired, and name who runs it.
