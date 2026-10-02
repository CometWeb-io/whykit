---
title: "D-011 — Direct to ops directors, evidence recorded"
aliases: ["D-011"]
type: decision
decision_id: D-011
supersedes: D-010
status: approved
owner: "Lena Krüger"
created: 2026-09-15
last_updated: 2026-09-15
review_by: 2027-02-01
source_of_truth: false
sensitivity: public
source_ids: ["E-013"]
tags: ["decisions", "motion"]
workstream: "06-decisions"
---

# Decision record: direct to ops directors, with the partner-channel evidence on record

## Decision ID

D-011

## Status

Accepted. Supersedes [[06-decisions/d-010-direct|D-010]].

## Context

D-010 moved the primary motion from partners to direct, but it was approved without citing the evidence that forced the change. A trace of the ledger flagged it as a live decision with no evidence. Approved reasoning is append-only, so the fix is a successor record, not an edit.

## Decision

The decision itself is unchanged: the primary motion is direct to ops and maintenance leads. Partners may introduce, and are paid for a closed-won they originated. They are not the plan.

## Rationale

The partner channel was given six months as the primary motion and did not produce pipeline. Two signed partners produced three meetings and zero SQL. [E-013] Direct is slower and more in our control. It matches D-002.

## Evidence

- E-013 — Partner channel review, March–August 2026

## Alternatives considered

| Alternative | Upside | Risk | Why rejected |
|---|---|---|---|
| Leave D-010 without evidence | No new record | The ledger keeps a live decision nobody can trace back to a source | Defeats the point of the ledger |
| Edit D-010 in place | One record | Rewrites approved reasoning after the fact | Approved reasoning is append-only |

## Consequences

### Positive

- Every live decision in the ledger now traces to evidence.

### Negative and trade-offs

- One extra record for the same decision. D-010 stays readable as history.

## Ownership and review

- Owner: Lena Krüger
- Decided on: 2026-09-15
- Supersedes: D-010
- Review on: 2027-02-01
