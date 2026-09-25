---
title: "Page brief — home"
aliases: []
type: specification
status: in_review
owner: "Growth Lead"
created: 2026-07-09
last_updated: 2026-09-11
source_of_truth: false
sensitivity: public
source_ids: ["E-007"]
tags: ["page-brief", "home"]
workstream: "03-website"
---

# Page brief: home

## Business goal

A qualified demo request from an ops or maintenance buyer. Unqualified IT browsers should bounce; that is success, not failure.

## Primary audience

The champion in [[01-strategy/icp]] — a maintenance lead who still keeps a paper list.

## Visitor situation and intent

They searched for a way to know which machine to look at tomorrow, or they were sent the URL by a colleague. They did not come to evaluate a platform.

## Primary conversion

Demo request form, three fields: name, work email, plant type. No "company size" field until we have evidence it filters.

## Secondary conversion

Self-serve "is this for us" on the ICP page.

## Core message

Tomorrow's machine list, before the shift starts.

## Evidence the page must carry

- The list is the artefact. E-007.
- Not a dashboard screenshot. A list.

> [!warning] Needs verification
> Growth Lead still needs a Plausible event for "machine list viewed" before we can say the fold works. Until then, scroll depth is a proxy, and a weak one.

## Questions the page must answer

1. What will I see in the morning?
2. Do I need an IT project?
3. What kind of plant is this for?

## Proposed structure

1. The list, populated with a named (fictionalised) stamping line
2. One sentence of positioning
3. Three answers to the questions above
4. Demo form

## Measurement

| Event | Definition | Tool | Owner |
|---|---|---|---|
| Demo request | HubSpot form `demo_request` | HubSpot | Operations Lead |
| Machine list viewed | Custom event, 2s visibility | Plausible | Growth Lead — not shipped |
