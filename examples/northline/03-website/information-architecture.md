---
title: "Information architecture"
aliases: ["IA"]
type: specification
status: approved
owner: "Growth Lead"
created: 2026-07-09
last_updated: 2026-07-09
source_of_truth: false
sensitivity: public
source_ids: []
tags: ["ia", "website"]
workstream: "03-website"
---

# Information architecture

> [!decision] Decision
> The homepage is product-led — it leads with the machine list, not the company. [[06-decisions/d-005-product-led-site|D-005]].

## Surfaces

| URL | Purpose | Primary conversion |
|---|---|---|
| `/` | Make a supervisor recognise the list | Demo request |
| `/product` | How Pulse works, including hardware | Demo request |
| `/for` | ICP in public language | Self-disqualify or demo |
| `/glossary` | Canonical public definitions | None — authority |
| `/de` | German equivalent of the above | Demo request |

## What is not in the nav

About, blog, careers, "platform", "AI", customer logos until two signed references exist.

## Language

German and English are siblings, not a default-plus-translation. A DACH visitor landing on `/` is offered `/de` once, not a banner every page load.

Code lives in `web/`. This note records the decision, not the markup.
