---
title: "Company context"
aliases: ["Northline"]
type: strategy
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-09-01
source_of_truth: true
sensitivity: public
source_ids: ["E-001", "E-011", "E-014"]
tags: ["company", "context"]
workstream: "00-context"
---

# Company context

> Synthetic fixture: Northline and every fact, source, metric, role, event, and
> system named here are invented for documentation. They are not verified
> company information and must not be reused as factual evidence.

The fictional profile every other sample document builds on. Inside this
scenario, claims are labelled as facts, decisions, or open questions to
demonstrate the format; those labels do not make the claims true outside it.

## What the company does

Northline sells **Pulse**, a condition-monitoring product for discrete manufacturers. A sensor kit on the machine reports vibration and current-signature; the product's job is to put tomorrow's at-risk machines on a list the shift supervisor already opens.

> [!fact] Synthetic scenario fact
> Pulse is sold as a product, not a project. Implementation is a fixed onboarding, not a statement of work. Source: offer architecture, E-014.

It does not sell a "reliability platform" and it does not sell to IT as the primary buyer. That is a decision, not a fact about the market — see [[06-decisions/d-002-icp-ops|D-002]].

## Legal identity

This teaching example intentionally has no legal entity, postal address,
registration number, or contact mailbox. Do not infer or invent those details
from this sample. In a real vault, store verified legal data only when necessary
and keep the vault's repository access appropriately restricted.

## Scale and constraints

> [!fact] Synthetic scenario fact
> Headcount is 28 people as of 31 August 2026, of whom 11 are delivery (solutions + support). Source: people-ops export, E-001.

> [!fact] Verified fact
> Delivery can run four parallel customer onboardings. A fifth waits. Source: delivery capacity review, E-011.

Numbers that describe different things — employees, contractors, people on a delivery team — are not merged into one figure.

## Markets and languages

Primary market: DACH. Active secondary: Nordics and Benelux. Working languages internally: English. Customer-facing: German in DACH, English elsewhere. Canonical strategy language is English — [[06-decisions/d-004-english|D-004]].

## Systems of record

| Surface | System | Owner |
|---|---|---|
| CRM | HubSpot | Operations Lead |
| Analytics | Plausible | Growth Lead |
| Tag management | none | — |
| Website | `web` repository | Growth Lead |
| Knowledge | this repository | Product Lead |

> [!decision] Decision
> HubSpot remains CRM of record. [[06-decisions/d-007-hubspot|D-007]].

## Open questions

> [!question] Open question
> Whether a new regional market requires localized onboarding material. Owner:
> Product Lead. Due: 2026-10-01.
