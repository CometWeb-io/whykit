---
title: "Terminology"
aliases: []
type: guide
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-07-09
source_of_truth: true
sensitivity: public
source_ids: []
tags: ["terminology"]
workstream: "00-context"
---

# Terminology

Shared definitions. Two documents using one word differently is the most common way this repository starts lying.

Add a term when you catch two people meaning different things by it.

| Term | Definition | Not to be confused with |
|---|---|---|
| Pulse | The product. Sensor kit + the machine list + the exception workflow. | "the platform", which we do not say in public. |
| Machine list | The primary artefact a supervisor sees: which machines are at risk on the next shift. | A dashboard, a score, a health index. |
| SQL | Sales-qualified lead: an ops or maintenance buyer at a discrete plant, 200–2,000 employees, with a CMMS in place, who has completed a qualified demo. | MQL. An MQL is a form fill. |
| Demo request | A form submission asking for a product walkthrough. Counted in HubSpot. | A marketing-qualified visit. |
| Discrete manufacturing | Parts, assemblies, serialised products. Stamping, machining, packaging machinery, electronics assembly. | Process industry. |
| CMMS | Computerised maintenance management system already in the plant (SAP PM, Maximo, MaintMaster, etc.). | Pulse. Pulse does not replace the CMMS. |
| Organic | Direct, referral, or unpaid search / answer-engine visit, as Plausible and HubSpot agree. | Branded paid. We have none. |

## Metric definitions

A metric is its definition plus the tool that produced it. The same word measured in two tools is two metrics.

| Metric | Exact definition | Source tool | Owner |
|---|---|---|---|
| Unique visitors | Plausible unique visitors, excluding known-bot traffic, calendar month | Plausible | Growth Lead |
| Demo requests | HubSpot contacts with form = `demo_request`, created in period | HubSpot | Operations Lead |
| SQL | Opportunity stage ≥ SQL, close date in period or open | HubSpot | Operations Lead |
| Closed-won | Opportunity stage = Closed Won, new logo, booked amount > 0 | HubSpot | Product Lead |
| Parallel onboardings | Active Pulse implementations not yet in "steady state" | Linear, project = Delivery | Delivery Lead |
