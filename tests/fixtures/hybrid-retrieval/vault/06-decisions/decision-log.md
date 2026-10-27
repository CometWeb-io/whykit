---
title: Decision log
aliases: []
type: decision
status: draft
owner: Ada Example
created: 2026-09-17
last_updated: 2026-09-17
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
| D-001 | Retry queue previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-001-retry-queue-previous-policy]] |
| D-002 | Retry queue current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-002-retry-queue-current-policy]] |
| D-003 | Backup restore previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-003-backup-restore-previous-policy]] |
| D-004 | Backup restore current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-004-backup-restore-current-policy]] |
| D-005 | Data retention previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-005-data-retention-previous-policy]] |
| D-006 | Data retention current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-006-data-retention-current-policy]] |
| D-007 | Deployment rollback previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-007-deployment-rollback-previous-policy]] |
| D-008 | Deployment rollback current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-008-deployment-rollback-current-policy]] |
| D-009 | Capture provenance previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-009-capture-provenance-previous-policy]] |
| D-010 | Capture provenance current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-010-capture-provenance-current-policy]] |
| D-011 | Request budgets previous policy | 2026-09-17 | Ada Example | superseded | [[06-decisions/d-011-request-budgets-previous-policy]] |
| D-012 | Request budgets current policy | 2026-09-17 | Ada Example | accepted | [[06-decisions/d-012-request-budgets-current-policy]] |

## What belongs here

A decision is material when reversing it would cost real work, when it commits
the company publicly, or when someone six months from now would otherwise ask
"why on earth is it like this?".

Routine choices do not need a record. If you are unsure, the test is whether you
would want the reasoning in a year.

## Review

TODO — set a cadence for reviewing accepted decisions whose assumptions may have
expired, and name who runs it.
