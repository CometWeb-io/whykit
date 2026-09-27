# Project positioning

## What WhyKit is

WhyKit is a **Git-native evidence and decision ledger for teams and AI agents**.
It keeps sources, decisions, reasoning, and review events in plain Markdown, with
a CLI and deterministic checks that help keep their relationships intact.

The storage format stays vendor-neutral. Markdown in Git is canonical; Obsidian,
AI providers, MCP, and the Explorer are optional ways to work with or inspect it.

## What WhyKit does not promise

- A lint pass cannot prove that evidence is accurate or that a decision is wise.
- Sensitivity labels are not access control; repository permissions still govern
  who can read a vault.
- WhyKit is not an agent memory service, a vector database, a CRM, or a task
  manager.

These limits are part of the product contract, not temporary disclaimers.

## Maintainer and usage claims

Copyright 2026 Maciej Zmitrukiewicz, released under Apache-2.0. CometWeb
(https://cometweb.io) is the product name, not a company and not the copyright
holder. The repository does not claim that a real operating vault has verified
the product. Make that claim only after the dogfooding gate in the
[release checklist](../.github/RELEASE.md) has actually been met.

## The name

“WhyKit” combines the question the ledger preserves — *why?* — with the practical
toolkit that ships: a file format, CLI, linter, schemas, template, and optional
integrations.
