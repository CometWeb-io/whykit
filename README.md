# WhyKit

**The layer that remembers why.**

> Git-native evidence and decision ledger for teams and AI agents.

WhyKit keeps sources, decisions, their reasoning, and review events together in
plain Markdown and Git. It gives people and tools a shared, inspectable record of
what is known, what was decided, and what still needs checking.

![WhyKit links evidence to a decision, then checks its structure and review date.](docs/media/overview.svg)

**Python 3.11+ · No runtime dependencies · Apache-2.0**

**Status:** source preview `0.3.0.dev0`. No GitHub Release and no public PyPI release yet. Install from a source checkout. This checkout is newer than the internal 0.2.0 milestone and is not a tagged release.

[![CI](https://github.com/CometWeb-io/whykit/actions/workflows/ci.yml/badge.svg)](https://github.com/CometWeb-io/whykit/actions/workflows/ci.yml)

## 60-second quickstart

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
WhyKit is not on PyPI yet, so run it from a checkout. The vault goes *next to*
the checkout, never inside it.

<!-- quickstart:start -->
```bash
git clone https://github.com/CometWeb-io/whykit.git
cd whykit
uv sync --locked

# 1. Create a vault.
uv run whykit init ../my-ledger

# 2. Register a source, then a decision that cites it.
uv run whykit new --root ../my-ledger evidence \
  --source "Support ticket export, Q3" --type dataset \
  --location "https://example.com/exports/q3-tickets.csv" \
  --claims "Most onboarding tickets mention SSO"
uv run whykit new --root ../my-ledger decision "Ship SSO before audit logs" \
  --owner Platform --status approved --source E-001

# 3. Check the vault and see what is due for review.
uv run whykit lint --root ../my-ledger
uv run whykit review --root ../my-ledger list --due-days 120
```
<!-- quickstart:end -->

What you should see:

```text
created E-001: 00-context/evidence-register.md
created D-001: 06-decisions/d-001-ship-sso-before-audit-logs.md
...
24 files — 0 error(s), 4 warning(s)
DUE     <today + 90 days>  06-decisions/d-001-ship-sso-before-audit-logs.md  (Platform)
```

The vault now holds an evidence row (`E-001`), a decision record that cites it
(`D-001`), a matching decision-log entry, and a review date 90 days out. The
four warnings are the questions in the vault's `AGENTS.md` that only you can
answer (reply language, branching rule, tone-of-voice owner, safety rules).
Answer them before you let an agent write in the vault, or before you turn on
the strict `ci` gate.

Note that `new`, `review` and `evidence` take `--root` **before** their
subcommand. Inside the vault directory you can drop `--root` altogether.

Next:

1. Open the vault's `06-decisions/d-001-*.md` and write the actual context,
   rationale and alternatives. Then `git init` the vault and commit it.
2. Read [Concepts](docs/concepts.md) for how evidence, decisions and reviews fit
   together, including how to supersede a decision instead of rewriting it.
3. Add the [CI gate](docs/ci.md) so accepted reasoning cannot be quietly edited.

## Why WhyKit

Teams can find old documents but often cannot tell which evidence supported a
decision, which assumptions remain untested, what replaced an earlier choice, or
when anyone last checked that the reasoning still holds. WhyKit makes those
relationships explicit and reviewable:

```text
source (E-001) → decision (D-001) → dated review events
```

- **Keep evidence traceable.** Give each source a stable ID and record where a
  person can inspect it.
- **Keep accepted reasoning intact.** Supersede an old decision instead of
  quietly rewriting its rationale; record reviews as append-only events.
- **Check the same rules locally and in CI.** Start with warnings while drafting,
  then apply the repository's configured policy at stronger gates.
- **Give tools bounded context.** Search, graph, context-pack, and read-only MCP
  interfaces expose structured views without replacing the Markdown source.

WhyKit checks structure and traceability, **not whether a source or conclusion is
true**. A citation can be present and still be wrong. Human review remains
essential.

## When not to use WhyKit

- **You only record architecture decisions, and plain ADRs work for you.** Keep
  them. WhyKit earns its keep when decisions cite evidence that changes, span
  more than architecture, or need enforced review dates. If you outgrow ADRs,
  `whykit adopt` inventories the ones you have.
- **You want something that tells you whether a decision is right.** WhyKit
  checks that reasoning is cited, indexed, dated and not rewritten. It does not
  judge the reasoning.
- **You need access control per document.** Sensitivity labels are metadata.
  Repository permissions are the only boundary; split vaults if audiences differ.
- **You want semantic search or chat over your notes.** WhyKit has no embeddings
  and no AI dependency. Its `query`, `context` and `pack` commands are
  deterministic, and other tools can build on them.
- **Nobody will own the review dates.** The tool reports overdue reviews; it
  cannot run them. A ledger nobody re-reads becomes a confident record of stale
  decisions.

## Adopting existing Markdown

Inventory first, then decide what to adopt:

```bash
uv run whykit adopt ../old-docs --into ../my-ledger --profile obsidian-loose --json
uv run whykit adopt ../old-docs --into ../my-ledger --profile generic --write
```

The first command is a dry run. `--write` copies the notes into a new dated batch
under the vault's gitignored `.import-staging/` area, with SHA-256 hashes and an
ingestion record. It never turns the inventory into canonical decisions on its
own. The [usage guide](docs/guide.md#already-have-a-pile-of-markdown) covers the
profiles and what the readiness score does and does not measure.

## The working loop

1. **Capture a source** with a stable evidence ID and a checkable location.
2. **Record a decision** that cites the evidence, states its rationale, and names
   a review date when accepted.
3. **Re-check it later.** Record a review event; if the reasoning changes, create
   a superseding decision instead of editing accepted rationale in place.
4. **Run the gate** that matches the moment: `local` while editing, `ci` in a
   pull request, and `release` only against the clean candidate commit you mean
   to publish. The release profile rejects a dirty working tree; stashing edits
   does not test them.

The example vaults show the format at two scales: [tiny](examples/tiny/) for the
smallest working setup, and [Northline](examples/northline/) for a fictional,
fully linked organization. All Northline people, companies, vendors, evidence,
metrics, and decisions are synthetic teaching material. Lint either one to see a
clean vault:

```bash
uv run whykit lint examples/tiny --strict --today 2026-09-17
uv run whykit status --root examples/northline --today 2026-09-17
```

## What WhyKit is — and is not

WhyKit is a **file format, CLI, linter, schemas, and optional integrations**. The
canonical records stay as ordinary Markdown in a Git repository. Obsidian is an
optional editor, not a requirement; agents and AI providers are optional too.

WhyKit is not an AI memory service, a vector database, a CRM, a task manager, or
an access-control boundary. Sensitivity labels are metadata, not permissions.
Keep real company vaults private and grant repository access deliberately. See
the [security policy](SECURITY.md).

## Common tasks

| Need | Start with |
|---|---|
| Create a vault or import Markdown | `whykit init`, `whykit adopt` |
| Add a decision, source, or note | `whykit new decision`, `evidence`, `note` |
| Check integrity and policy | `whykit lint`, `whykit check`, `whykit history` |
| Find related records or prepare agent context | `whykit query`, `context`, `pack`, `graph`, `impact` |
| Check which decisions rest on missing or stale evidence | `whykit trace` |
| Review decisions and source lifecycle | `whykit review`, `whykit evidence`, `whykit snapshot` |
| Enable shell tab completion | `whykit completion bash\|zsh\|fish` |

Run `uv run whykit --help` for the full CLI. The optional [MCP server](docs/mcp.md)
exposes read-only query, context, impact, status, and pack tools.

## Documentation

| If you want to… | Read |
|---|---|
| Understand evidence, decisions and the review cycle | [Concepts](docs/concepts.md) |
| Follow the complete workflows | [Usage guide](docs/guide.md) |
| Gate pull requests with the GitHub Action or any CI | [CI integration](docs/ci.md) |
| Edit the vault in Obsidian | [Obsidian](docs/obsidian.md) |
| Fix an error or find a quick answer | [Troubleshooting and FAQ](docs/troubleshooting.md) |
| Configure policy profiles | [Configuration](docs/configuration.md) |
| Understand lint findings | [Rule reference](docs/rules.md) |
| Connect an MCP client | [MCP server guide](docs/mcp.md) |
| Connect producers or machine consumers | [Integrations](docs/integrations.md) |
| Explore the optional local viewer | [Explorer](apps/explorer/) |
| Contribute a change | [Contributing](CONTRIBUTING.md) |
| Read the community expectations | [Code of Conduct](CODE_OF_CONDUCT.md) |
| Understand the project boundary | [Positioning](docs/project-positioning.md) |

Copyright 2026 Maciej Zmitrukiewicz (hello@cometweb.io). [CometWeb](https://cometweb.io) is the product name, not the copyright holder. See the [Apache-2.0
license](LICENSE), [security policy](SECURITY.md), and [release checklist](.github/RELEASE.md).
