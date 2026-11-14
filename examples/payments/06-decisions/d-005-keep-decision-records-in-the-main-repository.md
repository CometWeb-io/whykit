---
title: "Keep decision records in the main repository"
aliases: []
type: decision
decision_id: D-005
status: approved
owner: "Engineering"
created: 2026-05-10
last_updated: 2026-05-10
source_of_truth: false
sensitivity: public
source_ids: ["E-004"]
tags: []
review_by: 2027-05-10
---
# Decision record: Keep decision records in the main repository

## Decision ID

D-005

## Status

Accepted

## Context

Agents and reviewers need the reasons next to the code they change; the duplicate-refund incident (E-004) was a change that ignored an earlier decision.

## Decision

Keep this vault in the application repository under `decisions/` and gate pull requests with WhyKit.

## Rationale

Decisions reviewed in the same pull request as the code stay current.

## Evidence

- E-004

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Separate wiki | Easy to edit | Drifts from the code | Nobody reads it during a change |

## Consequences

- Pull requests that change decision records need a decision owner's review.

## Ownership and review

- Owner: Engineering
