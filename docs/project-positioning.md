# Positioning: what WhyKit is, and when to use something else

## What WhyKit is

WhyKit is a **Git-native evidence and decision ledger for teams and AI agents**:
a file format, a CLI and linter, JSON Schemas, a vault template and optional
integrations. Sources, decisions, reasoning and review events stay in plain
Markdown in Git, which is canonical. Every other surface (queries, graphs,
context packs, the MCP server, the Explorer) is derived from it, and none of
them is a second copy of the facts.

Obsidian, AI providers, MCP and the Explorer are optional ways to edit or
inspect a vault. None is required.

## Why not just ADRs?

Architecture Decision Records solve part of this. If that part is all you need,
use them; `whykit adopt --profile adr-only` recognises existing ones if you
outgrow them.

| | ADR / MADR / adr-tools | WhyKit |
|---|---|---|
| Scope | Architecture decisions | Any material decision: pricing, positioning, hiring, architecture |
| Evidence | Prose links, if any | A register with stable `E-NNN` IDs, retirement and replacement |
| Immutability | A convention people mean to follow | Enforced against the diff in CI |
| Staleness | Nothing expires | `review_by` per decision; overdue reviews are reported |
| Claim types | Undifferentiated prose | Fact / decision / hypothesis / recommendation / open question |
| Secrets | Not addressed | Sensitivity labels and a heuristic credential scanner |
| Agents | Not addressed | `AGENTS.md` contract, `--json` output, provenance block, read-only MCP |

## When not to use WhyKit

- **You only record architecture decisions, and plain ADRs work for you.** Keep
  them. WhyKit earns its keep when decisions cite evidence that changes, span
  more than architecture, or need enforced review dates.
- **You want something that tells you whether a decision is right.** WhyKit
  checks that reasoning is cited, indexed, dated and not rewritten. It does not
  judge the reasoning.
- **You need access control per document.** Sensitivity labels are metadata.
  Repository permissions are the only boundary; split vaults if audiences differ.
- **You want semantic search or chat over your notes.** WhyKit has no embeddings
  and no AI dependency. Its `query`, `context` and `pack` commands are
  deterministic, and other tools can build on them.
- **Nobody will own the review dates.** The tool reports overdue reviews; it
  cannot run them.

## What WhyKit does not promise

These limits are part of the product contract, not temporary disclaimers.

- **Shape, not truth.** A lint pass says a claim is cited; it cannot say the
  source is honest, the reasoning sound or the decision good.
- **No re-checking on its own.** The failure mode worth naming: a decision is
  recorded correctly, the world moves, and the record stays valid-looking
  because nobody re-read it. WhyKit insists that every accepted decision has a
  `review_by` date and reports the ones that passed. The re-check is yours.
- **Labels are not permissions.** Sensitivity labels limit what the MCP server
  returns and on which interface the Explorer may listen; repository permissions
  still govern who can read a vault.
  Keep real company vaults private. See the [security policy](../SECURITY.md).
- **Not a memory service.** WhyKit is not an agent memory service, a vector
  database, a CRM, a task manager or chat history. Those keep execution and
  activity; WhyKit keeps the reasoning that has to outlive them.

## Deterministic and offline by construction

WhyKit contacts nothing: no telemetry, no accounts, no service, no runtime
dependencies. A vault holds the material a company is least willing to hand to
a third party, so this is enforced by a test that breaks the socket layer and
runs every vault command, not by a sentence in a README. There is one
documented front-matter parser and no optional parser path, so the same vault
reads the same way in every environment; richer YAML is rejected explicitly.

## Maintainer and usage claims

Copyright 2026 Maciej Zmitrukiewicz, released under Apache-2.0. CometWeb
(https://cometweb.io) is the product name, not a company and not the copyright
holder. The repository does not claim that a real operating vault has verified
the product. Make that claim only after the dogfooding gate in the
[release checklist](../.github/RELEASE.md) has actually been met.

## The name

"WhyKit" combines the question the ledger preserves, *why?*, with the toolkit
that ships: a file format, CLI, linter, schemas, template and optional
integrations.
