---
title: Controlled approval example
aliases: []
type: reference
status: draft
owner: Ada Example
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: []
tags: []
---

# Controlled approval example

This fictional vault demonstrates the new approval event format, pinned to
2026-09-17. The evidence URL is a reserved synthetic fixture, not a verified
external source. The reviewed decision is [[06-decisions/d-001-bind-approval-to-reviewed-record-bytes]].

The `approved` row in [[00-context/review-log]] records the canonical record
hash and the original draft byte hash. Reviewer attribution is editable data;
this example does not authenticate Ada Example or prove an actual human review.

Create a draft, complete all sections, inspect `review approve` and apply its
exact preview hash. The resulting decision and both ledger changes are atomic.

The second reviewed decision, [[06-decisions/d-002-classify-individual-evidence-rows-without-lowering-their-register-floor]], demonstrates optional evidence row labels. All sources and classifications are synthetic; inspect the private index to see the retired fixture.
