---
title: Evidence register
aliases: []
type: reference
status: draft
owner: "Payments"
created: 2026-03-01
last_updated: 2026-10-28
source_of_truth: false
sensitivity: public
source_ids: []
tags: []
---

# Evidence register

The index of sources behind important claims. Every entry gets an ID that other
documents cite in their `source_ids` front matter.

ID format: `E-NNN`, assigned in order, never reused.

| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-002 | Ledger load test, March | load test | 2026-09-15 | 2026-09-15 | https://example.com/harbor/load-test-march.csv | One Postgres primary sustains 4x peak checkout volume with p95 under 40 ms |
| E-003 | Payment provider webhook guide | vendor doc | 2026-04-02 | 2026-04-02 | https://example.com/harbor/provider-webhooks.html | The provider retries for 24 hours and expects idempotent handlers |
| E-004 | Incident review: duplicate refunds | incident | 2026-04-14 | 2026-04-14 | https://example.com/harbor/incident-0412.md | A retried refund webhook paid one customer twice |
| E-005 | Finance audit checklist, 2026 | document | 2026-10-28 | 2026-10-28 | https://example.com/harbor/audit-2026.pdf | Auditors now also require currency codes on every stored amount |

## Retired sources

When a source is superseded or found to be wrong, move it here rather than
deleting the row. Documents already cite the ID, and a dangling citation is worse
than a retired one.

| ID | Source | Retired on | Why | Replaced by |
|---|---|---|---|---|
|  |  |  |  |  |
| E-001 | Finance audit checklist, 2025 | 2026-10-28 | Replaced by the 2026 audit checklist | E-005 |
