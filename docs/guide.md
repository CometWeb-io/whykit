# How-to guides

Short recipes for the jobs you do with a vault once it exists. Each one assumes
you know the three moving parts from [Concepts](concepts.md): evidence,
decisions and the review cycle. For a guided first run, start with the
[tutorials](README.md#start) instead. Every flag is listed in the
[command reference](cli.md).

## Run WhyKit from a checkout

WhyKit requires Python 3.11 or newer and has no runtime dependencies. It is
not on PyPI yet; do not run `uv tool install whykit` or `pipx install whykit`
until a release is announced in this repository. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/CometWeb-io/whykit.git
cd whykit
uv sync --locked
uv run whykit init ../my-ledger
uv run whykit lint --root ../my-ledger
```

The rest of this page writes commands as bare `whykit …`, run from inside the
vault directory. From a source checkout you can get there three ways:

- run `uv run whykit …` from the WhyKit repository and pass `--root` to point at
  the vault;
- run `uv run --project /path/to/whykit whykit …` from inside the vault;
- install a binary once with `uv tool install --from /path/to/whykit whykit`.

Every command accepts `--root`, including after the action of `new`, `review`
and `evidence` (`whykit new decision … --root ../my-ledger`). Without it, WhyKit
walks up from the current directory to the nearest `Home.md` plus `00-context/`.

## Create a vault

`whykit init <dir>` writes the vendor-neutral default layout. A fresh vault has
zero lint errors; its warnings point at the unanswered questions in `AGENTS.md`,
the contract agents work under, so answer those before relying on agents. The
exact warning count and line numbers can change as the template evolves.

- `whykit init --full <dir>` adds optional workstream folders (strategy,
  website, research and others). `--minimal` is a compatibility alias for the
  default.
- `whykit init --force <dir>` adds only missing starter files to a non-empty
  directory. It keeps existing notes, configuration, agent rules and custom
  `.gitignore` patterns. It does not reset or upgrade anything.

Obsidian is an optional editor for the result; see
[Using WhyKit with Obsidian](obsidian.md).

## Already have a pile of Markdown

`adopt` inventories what you have, hashes every file, flags duplicates and spots
existing ADRs. It is a dry run until you pass `--write`:

```bash
whykit adopt ../old-docs --into ../my-ledger --profile obsidian-loose --json
whykit adopt ../old-docs --into ../my-ledger --profile generic --write
```

`--profile` tunes the inventory: `generic` (the default), `adr-only` for an ADR
directory, or `obsidian-loose` for an Obsidian vault with partial front matter.
The readiness percentage checks front matter against the linter, **not** links,
evidence, decision integrity or approval, and the time estimate is heuristic.
Only UTF-8 `*.md` files are scanned. Known evidence-register and decision-log
table layouts are flagged for manual mapping, and so is a `decision_id` claimed
by two imported files or already used in the vault.

`--write` never writes into a workstream. It copies the sources into a new dated
batch under the vault's gitignored `.import-staging/`, with a SHA-256 for each
file, and appears only once complete: if a source changes mid-import or a write
fails, nothing from that run is left behind. The ingestion record lands in
`notes/`; link it from a map or lint reports `note.orphan`. A person decides
what becomes canonical.

## Create records

Create records through the CLI so IDs and indexes move together. The records
below are fictional; store only evidence that is safe for the repository the
vault lives in.

```bash
whykit new evidence \
  --source "Customer interview set" --type interview \
  --location "notes/interviews/" \
  --claims "Repeated procurement delay"

whykit new decision "Narrow the first ICP" \
  --owner Product --status approved --source E-001

whykit new note "Interview synthesis" \
  --workstream notes \
  --link-from Home.md \
  --json
```

`--link-from` is explicit on purpose: WhyKit never rewrites `Home.md` or another
map unless asked. When it is given, the note and the link are created in one
atomic operation, and the note is rolled back if the map update fails.

An approved decision gets a `review_by` date from `defaults.decision_review_days`
unless you pass `--review-by`. `--supersedes D-NNN` replaces an earlier decision
and marks it `superseded` in the same operation; see
[Concepts](concepts.md#changing-your-mind-supersede-do-not-rewrite).

## Record a review

```bash
whykit review list --due-days 30
whykit review record D-001 \
  --reviewer Product \
  --outcome confirmed \
  --note "Evidence rechecked"
```

The review log is append-only under `whykit history`. The four outcomes and what
each does to `review_by` are in [Concepts](concepts.md#the-review-cycle).

## Retire evidence

Evidence is never deleted, so its ID keeps meaning the same source:

```bash
whykit impact E-001
whykit evidence list --state active
whykit evidence retire E-001 \
  --why "Corrected source is now canonical" \
  --replaced-by E-014
```

Run `impact` first to see what cites the source. After retirement, current
documents that still cite it get an `evidence.retired` warning.

## Hand bounded context to an agent

Agents should not ingest the whole vault when a smaller, deterministic handoff
is enough:

```bash
whykit query "onboarding" --type decision --json
whykit context D-014 --max-chars 12000 --json
whykit pack D-014 --query "onboarding" --max-chars 30000
whykit graph --format json
whykit impact E-014 --json
```

| Need | Command |
|---|---|
| Find candidate records | `query` |
| Hand one record, its evidence, relations and findings to an agent | `context` |
| Hand several related records under one body budget | `pack` |
| Export the typed relations (`wikilink`, `evidence`, `supersedes`) | `graph` |
| Check what depends on a record before changing it | `impact` |

`context` and `pack` cap the embedded bodies and report truncation, so a single
large note cannot silently fill an agent's context window. For tools that speak
MCP, the [read-only MCP server](mcp.md) exposes the same views; for scripts, the
[JSON contract](automation.md) defines the output.

`graph --format mermaid` renders the relations as a Mermaid flowchart that GitHub
and Obsidian display inline: wikilinks are solid arrows, evidence citations
dashed, supersession thick, and retired or missing evidence red. Add
`--canonical-only` for a diagram of approved sources of truth only.

## Trace decisions to their evidence

`impact` looks at one record. `trace` checks every decision the other way
round: which evidence it rests on, and whether that evidence can still carry it.

```bash
whykit trace --today 2026-09-22
whykit trace --decision D-014 --json
whykit trace --gaps-only --strict          # CI gate: exit 1 on any gap
whykit trace --max-age-days 180            # fallback age window for untyped policy
```

A decision cites evidence directly (an `E-NNN` in its text or `source_ids`) or
inherits it from a non-decision note it links to, shown as `via <note>`.
Evidence linked through another decision is not inherited, so a successor
cannot lean on the record it superseded.

| Gap | Meaning |
|---|---|
| `no_evidence` | Nothing cited, directly or through a linked note |
| `missing_evidence` | Cites an ID that has no row in the evidence register |
| `retired_evidence` | Cites retired evidence; the replacement ID is shown when recorded |
| `stale_evidence` | Cites active evidence last accessed longer ago than its age window |

The age window comes from `[evidence_access_age_days]` in `whykit.toml`
([Configuration](configuration.md#evidence-access-age)); `--max-age-days`
applies only to source types the policy does not list. Only approved decisions
that nothing supersedes count as live, and `--strict` and the summary consider
live decisions only: a historical record may legitimately cite evidence retired
since.

## Snapshot and detect drift

A snapshot fingerprints governed Markdown plus `whykit.toml`:

```bash
whykit snapshot --output .whykit/snapshot.json --today 2026-09-22
whykit verify-snapshot .whykit/snapshot.json --today 2026-10-22 --json
```

`matches` and `content_matches` report identity of governed content. Line
endings and a leading byte-order mark do not count as content, so a snapshot
taken on one platform verifies on another
([snapshot formats](automation.md#snapshot-formats)).
`health_changed` is separate, because a vault can become review-due as time
passes without a single byte changing. `--output` is resolved relative to the
vault root and may not leave it.

## Gate changes locally and in CI

```bash
whykit policy
whykit check --profile local
whykit check --profile ci --base origin/main --head HEAD
whykit install-hooks
```

The profiles live in the vault's `whykit.toml`
([Configuration](configuration.md#whykittoml)). `install-hooks` adds a
pre-commit hook that runs the `local` profile. For GitHub Actions, follow
[Gate pull requests](tutorials/gate-pull-requests.md); [Running WhyKit in CI](ci.md)
has every Action input and the setup for other CI systems.

## Browse the vault in the Explorer

The Explorer is an optional, read-only viewer and not part of the data
contract. It needs Node.js and runs only from a source checkout:

```bash
npm ci --prefix apps/explorer
uv run whykit serve examples/northline
```

It listens on `127.0.0.1`. It refuses to serve non-public documents on another
interface unless you pass `--allow-sensitive-network`, because the browser
bundle embeds document bodies; see
[Configuration](configuration.md#explorer-network-safety).

## Connect another system

`AGENTS.md` in a vault defines safe default behaviour for agents, and
`INTEROP.md` the boundary between the vault and the code repositories it
describes. A system that writes Markdown can write to a vault and record itself
in the optional `provenance` block; [Integrations](integrations.md) gives the
design rule and a worked mapping.
