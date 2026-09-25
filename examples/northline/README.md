---
title: "WhyKit — Northline example vault"
aliases: ["WhyKit example README"]
type: guide
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-09-17
source_of_truth: true
sensitivity: public
source_ids: []
tags: ["governance", "template"]
workstream: "root"
---

# WhyKit — Northline example

> [!warning] Synthetic example
> **Synthetic teaching fixture:** the organization, people (represented by
> role labels), records, metrics, events, systems, interviews, and sources below
> are invented examples. No real interviews, company exports, analytics
> properties, CRM records, legal entity, or customer evidence are represented.
> Do not cite this vault as evidence or treat its claims as verified.

A repository for the durable knowledge behind a company's work: context,
strategy, decisions, research, and specifications. It can be opened in Obsidian
and used by coding agents.

This worked sample uses **Northline**, a fictional condition-monitoring company,
to demonstrate how linked records can look in practice. Its scenarios and
numbers are fabricated for documentation; the template itself ships empty.
The sample demonstrates the structure and conventions, not a validated
operating model or market research.

## What problem it solves

Most GTM knowledge lives in places that cannot answer "why did we decide this?" six months later. Chat threads scroll away. Task managers record what, never why. CRMs hold records, not reasoning. Dashboards show numbers with no memory of what changed underneath them.

This repository is the layer those tools do not have. It keeps the reasoning, and it keeps it in plain Markdown that both a person and an agent can read.

The division of labour it assumes:

| Where | What belongs there |
|---|---|
| **This repository** | Canonical strategy, definitions, decisions, research, reusable briefs |
| **Agents / automations** | Produce proposed evidence, drafts and typed handoffs; they do not own canonical truth. |
| **Task manager** | Owners, deadlines, statuses, execution tracking |
| **CRM** | Leads, accounts, opportunities, activities, pipeline |
| **Analytics / BI** | Measured performance and reporting |
| **Code repositories** | The systems themselves — see [[INTEROP]] |

## Adopting it

1. Copy the structure. The WhyKit format is portable Markdown and can be renamed after adoption.
2. Replace every placeholder. Start with [[00-context/company|company]], [[00-context/goals|goals]], [[00-context/terminology|terminology]].
3. Read [[AGENTS]] before letting an agent write anything here.
4. Read [[INTEROP]] if the company has other repositories or agent workflows.
5. Run the conventions checker before every commit. Mechanical objections should never be what a reviewer spends attention on.

The numbered spine is a convention, not a law. Sections can be renamed or dropped, but keep the numbering: it is what makes the order obvious in a file listing.

## The five information types

The single discipline this repository runs on. Never let them blur.

- **Verified fact** — supported by a reviewed source, with the source named.
- **Decision** — someone with the authority chose this, on a date.
- **Hypothesis** — plausible, unproven, and labelled as such.
- **Recommendation** — what the author would do; not yet a decision.
- **Open question** — known unknown, owned by someone.

Anything unsupported is marked `Needs verification`. An unmarked claim is read as a verified fact, which is how a guess becomes folklore.

## Document status

- `template` — reusable structure, not content
- `draft` — incomplete, not approved
- `in_review` — awaiting review
- `approved` — accepted source of truth
- `superseded` — kept for history, no longer current
- `archived` — no longer maintained

Only `approved` documents are canonical. Keep superseded documents and say what replaced them; a decision log with the wrong turns deleted teaches nothing.

## Working rules

1. Read [[AGENTS]] before creating or editing documents.
2. Never invent metrics, clients, partnerships, capabilities or results.
3. Link to the canonical document instead of copying it.
4. Record accepted strategic decisions in [[06-decisions/decision-log|the decision log]].
5. Register every imported source batch in `07-research/sources/`.
6. Keep credentials out. This repository is knowledge, not a vault for secrets.

## Licence

Apache-2.0. A knowledge-repository template with no licence is not open source, whatever the repository name says.
