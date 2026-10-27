---
title: Captured provenance example
type: guide
status: draft
owner: Ada Example
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: []
---

# Captured provenance example

All people, sources and assessments here are synthetic. The as-of date is pinned
for reproducible checks, not a claim about when this feature was released.
The optional format and its trade-offs are recorded in
[[06-decisions/d-003-keep-claims-and-source-versions-separate-from-approved-reasoning|the format decision]].
The observation format is documented by a separate
[[06-decisions/d-004-record-source-observations-separately-from-immutable-captures|draft design decision]];
the earlier approved decision was not rewritten to add it.

The fixture contains one restricted claim and an exact captured source version.
Its source observation ledger records the synthetic acquisition separately from
the claim pin and approval receipt. Recapturing changed bytes requires review;
a matching old anchor alone cannot establish freshness.
Base64 is reversible storage, not encryption. Public exporters omit the claim;
a private viewer requires an explicit restricted ceiling.

From this source checkout:

```bash
uv run whykit claim verify C-001 --root examples/provenance --max-sensitivity restricted --today 2026-09-17 --json
uv run whykit compile-context D-003 --root examples/provenance --max-sensitivity restricted --today 2026-09-17 --json
uv run whykit agent preflight --root examples/provenance --proposal examples/provenance/proposal.json --today 2026-09-17 --json
uv run whykit lint examples/provenance --strict --today 2026-09-17
```

These checks bind local bytes and declared intervals. They do not authenticate
Ada Example or establish semantic support, authorization or real-world efficacy.
