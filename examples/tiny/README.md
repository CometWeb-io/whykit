---
title: README
aliases: []
type: guide
status: approved
owner: Vault owner
created: 2026-09-17
last_updated: 2026-09-17
source_of_truth: false
sensitivity: public
tags: []
---

# Tiny example vault

Smallest CI-checked WhyKit vault: two evidence rows (`E-001`, `E-002`) and one
approved decision (`D-002`). It supersedes `D-001`, which was accepted with the
scaffold's placeholder text still in it; approved records are superseded, not
rewritten. Layout matches the default `whykit init` output.

```bash
uv run whykit lint examples/tiny --strict --today 2026-09-17
```
