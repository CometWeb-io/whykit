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
approved decision (`D-001`). Layout matches the default `whykit init` output.

```bash
uv run whykit lint examples/tiny --strict --today 2026-09-17
```
