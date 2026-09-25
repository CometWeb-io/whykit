---
title: "Evidence register"
aliases: []
type: reference
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-09-11
source_of_truth: true
sensitivity: public
source_ids: []
tags: ["evidence"]
workstream: "00-context"
---

# Evidence register

> Synthetic fixture: every row and referenced source below is invented to
> demonstrate the format. The source files, interviews, dashboards, reports, and
> measurements are not real evidence and must not be relied on or cited.

The index of sources behind important claims. Every entry gets an ID that other documents cite in their `source_ids` front matter.

ID format: `E-NNN`, assigned in order, never reused.

| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | People-ops headcount export | internal | 2026-08-31 | 2026-09-01 | `assets/people-ops-2026-08.pdf` | Headcount 28; delivery 11 |
| E-002 | Customer interview set, Q2 2026 (n=12) | interview | 2026-06-30 | 2026-07-02 | [[07-research/interviews/example-region-maintenance-lead-2026-06-18]] | ICP: ops, not IT; CMMS already present |
| E-003 | Plausible monthly unique visitors | analytics | 2026-09-01 | 2026-09-01 | Plausible, property northline.example | 4,812 unique visitors in August |
| E-004 | Win/loss notes, 9 lost deals H1 2026 | internal | 2026-07-15 | 2026-07-15 | HubSpot lost-reason field | 7 of 9 cited an existing IT-bought reliability tool |
| E-005 | Synthetic Vendor A site and pricing brief | vendor doc | 2026-06-12 | 2026-06-12 | [[07-research/competitors/vendor-a]] | Demonstrates linking a fictional vendor brief to a decision |
| E-006 | Synthetic Vendor B site brief | vendor doc | 2026-06-14 | 2026-06-14 | [[07-research/competitors/vendor-b]] | Demonstrates comparing fictional product workflows |
| E-007 | Interview, maintenance lead, Example region stamping | interview | 2026-06-18 | 2026-06-18 | [[07-research/interviews/example-region-maintenance-lead-2026-06-18]] | Machine list is the artefact they would open |
| E-008 | Interview, ops director, Swedish packaging OEM | interview | 2026-06-24 | 2026-06-25 | `07-research/interviews/` | English product UI is acceptable; German sales is not optional in DACH |
| E-009 | Google Search Console, 90-day | analytics | 2026-09-01 | 2026-09-01 | GSC, domain northline.example | 38 queries with impressions > 10 |
| E-010 | HubSpot demo-request report, August | analytics | 2026-09-02 | 2026-09-02 | HubSpot dashboard | 11 demo requests, 6 organic |
| E-011 | Delivery capacity review | internal | 2026-08-20 | 2026-08-20 | Linear doc, project Delivery | Four parallel onboardings is the hard cap |
| E-012 | Automatica 2026 booth notes | report | 2026-06-28 | 2026-06-30 | `07-research/audits/` | Process-industry visitors were curious and unconvertible |
| E-013 | Partner channel review, March–August 2026 | internal | 2026-08-10 | 2026-08-11 | CRM partner-source report | Two signed partners produced three meetings and zero SQL |
| E-014 | Offer architecture (synthetic) | internal | 2026-08-20 | 2026-08-20 | [[01-strategy/offer-architecture]] | Demonstrates a product offer and fixed-scope onboarding |

## Retired sources

When a source is superseded or found to be wrong, move it here rather than deleting the row. Documents already cite the ID.

| ID | Source | Retired on | Why | Replaced by |
|---|---|---|---|---|
| E-000 | Early HubSpot dashboard (pre-definition) | 2026-03-01 | Demo-request definition changed; numbers are not comparable | E-010 |
