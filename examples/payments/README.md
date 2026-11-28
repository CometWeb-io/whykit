---
title: Harbor payments example vault
aliases: []
type: guide
status: approved
owner: "Payments"
created: 2026-03-01
last_updated: 2026-03-01
source_of_truth: false
sensitivity: public
tags: []
---

# Harbor payments example vault

A small, fictional payments team keeps its decisions next to its code. Every
company, person, source and URL here is synthetic, and every note is `public`,
so the whole vault can be published as an Explorer demo.

Each decision declares the code it covers with `applies_to`, so
`whykit affected` can map a change to the decisions it touches:

| Decision | Covers | State on 2026-11-12 |
|---|---|---|
| [[06-decisions/d-001-store-money-as-integer-cents\|D-001]] | `src/payments/**` | Needs review: its audit checklist (E-001) was retired for a 2026 version |
| [[06-decisions/d-002-keep-the-ledger-on-a-single-postgres-primary\|D-002]] | `src/payments/db/**`, `migrations/**` | Needs review: its review date passed |
| [[06-decisions/d-003-make-every-webhook-handler-idempotent\|D-003]] | `src/webhooks/**` | Applies |
| [[06-decisions/d-004-render-the-checkout-page-on-the-server\|D-004]] | `src/web/checkout/**` | Superseded by D-006, so it no longer applies |
| [[06-decisions/d-005-keep-decision-records-in-the-main-repository\|D-005]] | (no scope) | Applies to the process, not to files; reported as unscoped |
| [[06-decisions/d-006-render-checkout-on-the-client-with-server-side-validation\|D-006]] | `src/web/checkout/**` | Applies |

Try it from a checkout of WhyKit; the files do not need to exist:

```bash
whykit affected --root examples/payments --today 2026-11-12 \
  --files src/payments/ledger.py src/webhooks/refunds.py README.md
```

The decisions, sources and review log were created with the WhyKit CLI and
approved with `whykit review approve`; the dates are pinned for the example.
Reviewer names are editable data, not authenticated identities.
