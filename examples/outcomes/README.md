---
title: Declared outcomes example
type: guide
status: draft
owner: Ada Example
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
source_ids: []
---

# Declared outcomes example

All people, sources, values and assessments are synthetic. The fixed day makes
checks reproducible; this fixture supplies no evidence of product effectiveness.
The restricted [[00-context/outcomes/o-001|Outcome O-001]] declares a baseline
of `1.000`, target `3.000` and observation `2.000` items for approved D-003.
Both historical samples pin [[00-context/claims/c-002|claim C-002]], whose quote
binds exact captured fixture bytes. These checks do not compare numeric meaning,
prove causality, authenticate Ada Example or authorize any execution.

A separate [[06-decisions/d-005-keep-measured-outcomes-separate-from-approvals-and-causal-claims|draft format decision]]
records the change. Existing approved reasoning and receipts were not rewritten.
Public exports omit restricted declarations and claims. Base64 capture storage
is reversible and is not encryption.

From this unreleased source checkout:

```bash
uv run whykit outcome verify O-001 --root examples/outcomes --max-sensitivity restricted --today 2026-09-17 --json
uv run whykit knowledge view --root examples/outcomes --max-sensitivity restricted --json
uv run whykit lint examples/outcomes --strict --today 2026-09-17
```

[outcome-input.json](outcome-input.json) is the actual input used to create O-001.
A new create preview allocates the next O-ID; it does not modify O-001.
See `docs/outcomes.md` in the source checkout for preview/apply and measurement limits,
and `docs/knowledge-v2.md` for private bundle/restore commands.
A bundle preserves raw bytes but does not transfer Git history or establish a
trusted approval baseline. Safe export/restore currently require POSIX support.
