---
title: "Review log"
aliases: []
type: reference
status: approved
owner: "Payments"
created: 2026-03-01
last_updated: 2026-09-08
source_of_truth: false
sensitivity: public
source_ids: []
tags: ["review", "governance"]
---

# Review log

Append-only operational record of document and decision reviews. A review event
records that somebody re-checked a record; it does not prove the underlying
claim is true.

| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |
|---|---|---|---|---|---|---|
| 2026-03-05 | [[06-decisions/d-001-store-money-as-integer-cents]] | Payments | approved | — | 2027-03-05 | record-sha256:5e5dec95810aaa5ab1b86a1eaace124440e40e352cc434866ffe49ef00842332; snapshot-sha256:27db294ae2a0b3e60b4bafac8a13cb08bcd5ff6765295034e4f88eb492db5fc7 |
| 2026-03-25 | [[06-decisions/d-002-keep-the-ledger-on-a-single-postgres-primary]] | Platform | approved | — | 2026-09-23 | record-sha256:d636f6497429bd34dfffb390765f7368502646e046562dba37ade4c8d4251280; snapshot-sha256:0f98822880f9c8ec764b467ec873b0123d445a382f47d260238549cc540d8e3e |
| 2026-04-20 | [[06-decisions/d-003-make-every-webhook-handler-idempotent]] | Payments | approved | — | 2027-04-20 | record-sha256:90ce731be015e04a890d03b034dff174e6651c61018496097c4fbbb019db0922; snapshot-sha256:93cdead732260151ecae296349758397ce84ae49740760bdbcb4495beba5ddf5 |
| 2026-05-04 | [[06-decisions/d-004-render-the-checkout-page-on-the-server]] | Web | approved | — | 2027-05-04 | record-sha256:0ea39b9c5ae3c2bad5023b1d829f9afe9918ab0580d27df1a87893aec6d713e0; snapshot-sha256:69f74001474b5edb4e6ed94ea5a277906ee3b861e4ec73771204d7aa84cea944 |
| 2026-05-10 | [[06-decisions/d-005-keep-decision-records-in-the-main-repository]] | Engineering | approved | — | 2027-05-10 | record-sha256:e31986755604018eea52acfb08062f4f91179a79bfc4bfb4744f9cb1dcc16242; snapshot-sha256:5f39a147234f4930546b1edb539c6d9bb7d490d7c8fa19f9d2543de5b0b13428 |
| 2026-09-08 | [[06-decisions/d-006-render-checkout-on-the-client-with-server-side-validation]] | Web | approved | — | 2027-09-08 | record-sha256:31649cadadde9884072f2c23be8a5bf42efdde5aa480cbd60a3392d89d857481; snapshot-sha256:c0b6858517b3c89a7935caf8c62231dbd5c084df5ff934e9aec5503965dbc20e |
