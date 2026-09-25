---
title: "Source ingestion — 2 September 2026"
aliases: []
type: research
status: approved
owner: "Growth Lead"
created: 2026-09-02
last_updated: 2026-09-02
source_of_truth: false
sensitivity: public
source_ids: ["E-003", "E-009", "E-010"]
tags: ["ingestion"]
workstream: "07-research"
---

# Source ingestion batch 2026-09-02

## Purpose

Record how the August analytics export was assessed and normalized.

## Results

| Uploaded file | SHA-256 (prefix) | Assessment | Evidence ID | Destination |
|---|---|---|---|---|
| plausible-august-2026.csv | 9f3c…a12e | useful | E-003 | [[00-context/evidence-register]] |
| gsc-90d-2026-09.csv | 1aa0…77c4 | useful | E-009 | [[02-discoverability/search-and-answers]] |
| hubspot-demos-2026-08.csv | c41d…90ab | useful | E-010 | [[00-context/goals]] |
| screenshot-slack-pipeline.png | — | unsupported | — | discarded: no context, no date |
| old-ga-export.csv | — | empty / wrong tool | — | discarded: we do not use GA |

## Normalization performed

- [x] Platform-specific markup removed.
- [x] YAML front matter added on destination notes.
- [x] Facts separated from the August interpretation in the pipeline meeting.
- [x] Metric definitions checked against [[00-context/terminology]] before the numbers were copied.

## Review required

None. Numbers match the tools.
