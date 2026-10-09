---
title: Evidence register
aliases: []
type: reference
status: draft
owner: TODO
created: 2026-10-09
last_updated: 2026-10-09
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
| E-001 | Example observation | report | 2026-10-09 | 2026-10-09 | https://source.example/observation | Offline reads | public |
| E-002 | Example observation | report | 2026-10-09 | 2026-10-09 | https://source.example/observation | Offline reads | public |
| E-003 | Synthetic format design note | report | 2026-10-09 | 2026-10-09 | [[00-context/claim-format]] | Optional format preserves legacy source identifiers. | public |

## Retired sources

When a source is superseded or found to be wrong, move it here rather than
deleting the row. Documents already cite the ID, and a dangling citation is worse
than a retired one.

| ID | Source | Retired on | Why | Replaced by |
|---|---|---|---|---|
|  |  |  |  |  |
