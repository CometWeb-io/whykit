---
title: Evidence register
aliases: []
type: reference
status: draft
owner: Vault owner
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: internal
source_ids: []
tags: []
---

# Evidence register

The index of sources behind important claims. Every entry gets an ID that other
documents cite in their `source_ids` front matter.

ID format: `E-NNN`, assigned in order, never reused.

| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | Public RFC 2606 reserved domains | vendor doc | 2026-09-01 | 2026-09-17 | https://www.rfc-editor.org/rfc/rfc2606 | example.com is reserved for documentation |
| E-002 | Team interview 2026-09-10 | interview | 2026-09-10 | 2026-09-17 | notes/README | The team wants a small ledger before a large wiki |

## Retired sources

When a source is superseded or found to be wrong, move it here rather than
deleting the row. Documents already cite the ID, and a dangling citation is worse
than a retired one.

| ID | Source | Retired on | Why | Replaced by |
|---|---|---|---|---|
|  |  |  |  |  |
