---
title: "D-001 — Knowledge lives in Git and Markdown"
aliases: ["D-001"]
type: decision
decision_id: D-001
status: approved
owner: "Maya Chen"
created: 2026-03-12
last_updated: 2026-03-12
review_by: 2027-03-12
source_of_truth: false
sensitivity: public
source_ids: []
tags: ["decisions", "tooling"]
workstream: "06-decisions"
---

# Decision record: knowledge lives in Git and Markdown

## Decision ID

D-001

## Status

Accepted

## Context

Northline's GTM reasoning was in Notion, Slack and two Google docs titled "strategy v3". New joiners could not tell which was current. Agents could not be pointed at a single tree.

## Decision

Durable GTM knowledge lives in this Git repository, in Markdown, with the conventions in [[AGENTS]] and [[OBSIDIAN]]. Notion is not a source of truth.

## Rationale

A knowledge base that an agent cannot lint will rot in the same way a wiki rots. Git gives history, review, and a file tree that matches how the work is actually done. Markdown is readable on a phone, in a PR, and in Obsidian.

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Stay in Notion | Familiar | No real review, agents guess, export is lossy | The rot was already visible |
| A dedicated wiki product | Nicer reading | Another system of record, same sync problem | We already have Git |
| Only the website CMS | Public-ready | Mixes claims we may not publish with ones we must keep | Public and canonical are different jobs |

## Consequences

### Positive

- Decisions have IDs and cannot be silently rewritten.
- Agents have a file they are required to read.

### Negative and trade-offs

- Non-writers on the team need a reading surface. That is this app.
- Images are less convenient than in Notion. They live in `assets/`.

## Ownership and review

- Owner: Maya Chen
- Decided on: 2026-03-12
- Review on: 2027-03-12
