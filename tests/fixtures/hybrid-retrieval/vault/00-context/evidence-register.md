---
title: Evidence register
aliases: []
type: reference
status: draft
owner: Ada Example
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: []
tags: []
---

# Evidence register

The index of sources behind important claims. Every entry gets an ID that other
documents cite in their `source_ids` front matter.

ID format: `E-NNN`, assigned in order, never reused.

| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |
|---|---|---|---|---|---|---| --- |
| E-001 | Retry queue declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/retry | The retry queue accepts at most three delivery attempts. Backoff separates attempts and duplicate delivery keys are rejected. | public |
| E-002 | Backup restore declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/restore | The backup restore rehearsal used an isolated directory. Database integrity was checked before live storage replacement. | public |
| E-003 | Data retention declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/retention | The data retention declaration sets thirty days for payload deletion. Aggregate counts omit original payload content. | public |
| E-004 | Deployment rollback declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/rollback | The deployment rollback rehearsal retained the previous release. A failed health gate switched traffic back to that release. | public |
| E-005 | Capture provenance declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/capture | The capture provenance declaration binds bytes to a digest. An immutable version retains the exact quoted byte range. | public |
| E-006 | Request budgets declaration | fixture | 2026-09-17 | 2026-09-17 | https://source.example/budgets | The request budgets declaration counts the complete UTF-8 JSON envelope and newline. A tiny limit produces an explicit refusal. | public |

## Retired sources

When a source is superseded or found to be wrong, move it here rather than
deleting the row. Documents already cite the ID, and a dangling citation is worse
than a retired one.

| ID | Source | Retired on | Why | Replaced by |
|---|---|---|---|---|
|  |  |  |  |  |
