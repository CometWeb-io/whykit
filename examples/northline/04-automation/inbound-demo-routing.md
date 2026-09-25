---
title: "Automation spec — inbound demo routing"
aliases: []
type: specification
status: in_review
owner: "Operations Lead"
created: 2026-09-04
last_updated: 2026-09-04
source_of_truth: false
sensitivity: public
source_ids: ["E-010"]
tags: ["automation", "hubspot"]
workstream: "04-automation"
---

# Automation spec: inbound demo routing

## Purpose

A demo request becomes either a conversation with Operations Lead or a polite disqualification within one working day. Nothing sits in a generic inbox.

## Trigger

HubSpot form `demo_request` is submitted.

## Inputs

| Field | Source | Required | Validation | Personal data |
|---|---|---|---|---|
| Name | form | yes | 2+ chars | yes |
| Work email | form | yes | email, reject free-mail | yes |
| Plant type | form enum | yes | one of discrete / process / other | no |

## Logic

1. If plant type = process → disqualify, send the non-goal note, stop. [[06-decisions/d-008-discrete|D-008]].
2. If email domain is a known competitor → notify Product Lead, do not auto-reply.
3. Otherwise create a deal in stage "New demo", owner Operations Lead, due the next working day.

## Ownership

- Business owner: Operations Lead
- Technical owner: Growth Lead
- Exception owner: Operations Lead

## Error handling

- Retry policy: HubSpot native, 3 attempts
- Alerting: Slack #gtm-ops if a request is still unowned after 18 working hours
- Manual fallback: Operations Lead checks the unassigned view every morning
- Rollback: workflow off; form still writes the contact

## Acceptance criteria

- [ ] Process-industry requests never reach the demo calendar
- [ ] Free-mail is rejected at the form, not in the workflow
- [ ] Product Lead is the only person who sees competitor inbound
