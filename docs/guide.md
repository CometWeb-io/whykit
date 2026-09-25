# WhyKit

**The layer that remembers why.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](../LICENSE)

WhyKit is a Git-native evidence and decision ledger for teams and AI agents.
It keeps durable context, sources, decisions and their rationale in plain
Markdown, and enforces the parts that rot first: that evidence is cited, that
accepted decisions are never quietly rewritten, and that somebody named a date on
which each one gets re-checked.

Developed by **[CometWeb](https://cometweb.io)**. WhyKit is in preview and has
no public PyPI release yet. See the [release checklist](../.github/RELEASE.md).
Apache-2.0.

It is deliberately not a vector database, CRM, task manager or chat history.
Those keep execution and activity. WhyKit keeps the reasoning that has to
survive them.

## Why it exists

Six months after a material change, a team can usually find *what* happened but
not which evidence supported it, which assumptions were never proven, who
accepted it, what was rejected, what superseded it, or whether any of it is still
true. Those relationships are what WhyKit makes explicit and reviewable — by
people and by the agents now writing half of this material.

## Quick start

WhyKit requires Python 3.11 or newer and has no runtime dependencies. Install
[uv](https://docs.astral.sh/uv/getting-started/installation/), then install the
editable CLI from the checkout:

```bash
git clone https://github.com/CometWeb-io/whykit.git
cd whykit
uv tool install --editable .
whykit init ../my-company-context
cd ../my-company-context
whykit lint
```

The examples below assume this editable tool install and that commands run from
the vault root. If `whykit` is not found, add uv's tool executable directory to
your `PATH`. You can instead run commands through the checkout with `uv run
--project /path/to/whykit whykit ...` and pass `--root` when the target vault is
not the current directory.

A fresh vault has zero lint errors and uses the vendor-neutral default layout.
Use `whykit init --full ../my-company-context` for the optional GTM-oriented
workstream starter. `--minimal` remains as a compatibility alias. Any warnings
point to unanswered questions in `AGENTS.md`, the contract agents work under;
answer those before relying on agents. The exact warning count and line numbers
can change as the template evolves.

### Already have a pile of Markdown

Nobody starts from nothing. `adopt` inventories what you have, hashes every file,
flags duplicates and spots existing ADRs:

```bash
whykit adopt ../old-docs          # dry run
whykit adopt ../old-docs --write  # stage it + write an ingestion record
```

It never writes into a workstream. Raw exports are staged under
`.import-staging/` with a SHA-256 for each source, and a person decides what
becomes canonical. The inventory is the boring half, and the half everybody skips.

### Establish repository policy

A generated vault contains a versioned `whykit.toml`. There is no user-global
configuration, so a checkout carries the policy that will be used locally, in CI
and by an agent.

```bash
whykit policy
whykit check --profile local
whykit check --profile ci --today 2026-09-22
```

The default profiles are intentionally different:

- `local` reports warnings but lets normal drafting continue;
- `ci` treats warnings as failures;
- `release` additionally requires Git, a clean tree and an explicit history
  baseline (`--base`).

Edit the repository-local profiles if your team needs a different repeatable
policy. `whykit policy --json` shows the effective merged configuration.

### Keep evidence and reviews alive

Create records through the CLI so stable IDs and indexes move together:

The commands below use fictional example records. Replace them with evidence
that is safe to store in the repository where the vault lives.

```bash
whykit new evidence \
  --source "Customer interview set" --type interview \
  --location "07-research/interviews/" \
  --claims "Repeated procurement delay"

whykit new decision "Narrow the first ICP" \
  --owner Product --status approved --source E-001

whykit new note "Interview synthesis" \
  --workstream notes \
  --link-from Home.md \
  --json
```

`--link-from` is explicit on purpose: WhyKit will not silently rewrite `Home.md` or
another map, but a note created for a strict CI/release workflow can be linked in
the same atomic operation. If the map update fails, the newly created note is
rolled back. `new decision`, `new evidence` and `new note` all support `--json`
for agent/tooling workflows.

Approved decisions receive a review date from the repository policy when one is
not supplied. The due queue is separate from the historical event log:

```bash
whykit review list --due-days 30
whykit review record D-001 \
  --reviewer Product \
  --outcome confirmed \
  --note "Evidence rechecked"
```

The review log is append-only under `whykit history`; old review events cannot be
rewritten or deleted.

Evidence is also lifecycle-managed rather than deleted:

```bash
whykit evidence list --state active
whykit evidence retire E-001 \
  --why "Corrected source is now canonical" \
  --replaced-by E-014
```

### Use bounded machine context

Agents should not ingest the whole vault blindly when a smaller deterministic
handoff is sufficient:

```bash
whykit query "onboarding" --type decision --json
whykit context D-014 --max-chars 12000 --json
whykit graph --format json
whykit impact E-014 --json
```

`query` discovers candidate records. `context` returns one resolved target plus
its evidence, relationships, supersession lineage and scoped findings. `graph`
exports typed `wikilink`, `evidence` and `supersedes` relations. `impact` answers
the reverse-dependency question before a source or record is changed.

### Snapshot and verify drift

A snapshot fingerprints governed Markdown plus `whykit.toml`:

```bash
whykit snapshot --output .whykit/snapshot.json --today 2026-09-22
whykit verify-snapshot .whykit/snapshot.json --today 2026-10-22 --json
```

Content identity and time-based health are reported separately. A vault can have
identical bytes while its review queue changes simply because a due date passed.

### Put it in CI

The composite action installs the exact checked-out action revision rather than a
possibly different package release. It requires Python 3.11+ and `pip` on
`PATH`; set up that runtime explicitly. Pin actions to reviewed commit SHAs and
fetch full history when immutable-history verification is enabled:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  with:
    fetch-depth: 0
- uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
  with:
    python-version: "3.12"
- uses: CometWeb-io/whykit@<reviewed-40-character-commit-sha>
  with:
    root: knowledge
    profile: ci
    history: "true"
```

For a non-PR release/tag workflow, provide an explicit baseline when the selected
profile requires history verification:

```yaml
- uses: CometWeb-io/whykit@<reviewed-40-character-commit-sha>
  with:
    root: knowledge
    profile: release
    base: HEAD^
```

For repositories that invoke the CLI directly, use the named gate instead of
recreating its semantics in shell:

```bash
whykit check --root knowledge --profile ci --base origin/main --head HEAD
```

The Action supports `profile: ci` / `profile: release`, so the repository-local
`whykit.toml` is the policy authority instead of duplicated shell flags. Existing
workflows that omit `profile` retain the legacy `strict` + `history` behavior.
The history gate protects accepted decision reasoning and the append-only review
log. If full Git history is unavailable when history was requested, the Action
fails closed instead of silently skipping the check.

The built-in `release` profile additionally requires `whykit.toml` to be concretely configured: generated placeholders such as `defaults.owner = "TODO"` are valid starter syntax but are not accepted as release policy.

## Why not just ADRs?

Architecture Decision Records solve one part of this, and if that part is all you
need, use them — `adopt` will recognise your existing ones.

| | ADR / MADR / adr-tools | WhyKit |
|---|---|---|
| Scope | Architecture decisions | Any material decision: pricing, positioning, hiring, architecture |
| Evidence | Prose links, if any | A register with stable `E-NNN` IDs, retirement and replacement |
| Immutability | A convention people mean to follow | Enforced against the diff in CI |
| Staleness | Nothing expires | `review_by` per decision; overdue reviews are reported |
| Claim types | Undifferentiated prose | Fact / decision / hypothesis / recommendation / open question |
| Secrets | Not addressed | Sensitivity labels + a heuristic text scanner |
| Agents | Not addressed | `AGENTS.md` contract, `--json` output, provenance block |

WhyKit does not replace an editor. It works with ordinary Markdown in Git;
Obsidian is one optional editor, and no community plugin is required.

## The five information types

Do not let these blur:

- **Verified fact** — supported by reviewed evidence, cited as `E-NNN`.
- **Decision** — an authorized choice made at a known point in time.
- **Hypothesis** — plausible but not demonstrated.
- **Recommendation** — proposed; not a decision until accepted.
- **Open question** — a known unknown with an owner.

If a statement cannot be verified yet, label it. An unsupported claim should not
become organizational memory merely because an agent wrote it confidently.

## Evidence and decisions

Evidence IDs are stable and never reused. A row with no source and no location is
a placeholder, not evidence, and the linter treats it as such.

Accepted decision records are historical documents. To reverse one:

1. create a new `D-NNN` record;
2. cite the earlier decision in `supersedes`;
3. update the decision-log index;
4. on the old record change **only** `status` to `superseded`, `last_updated`,
   and optionally `superseded_by`. Leave its reasoning exactly as written.

`whykit history` enforces step 4 against the diff: that one transition is allowed
and every other edit to an accepted record is refused, so history cannot be
quietly rewritten into agreement with the present. A decision that turned out to
be wrong stays on the record as what was decided, with a newer record saying what
replaced it.

## What this does not solve

It contacts nothing. No telemetry, no accounts, no service, no runtime
dependencies — a vault holds the material a company is least willing to hand to
a third party, so this is enforced by a test that breaks the socket layer and
runs every command, rather than by a sentence in a README.

It is deterministic by construction: one documented front-matter parser, no optional
runtime parser path, and no network. The same supported YAML subset is interpreted
the same way whether or not unrelated Python packages happen to be installed; richer
YAML is rejected explicitly instead of changing semantics by environment.

The linter checks **shape, not truth**. It will tell you a claim has no evidence.
It cannot tell you the source is honest, the reasoning was sound, or the decision
was any good.

The failure mode worth naming: a decision is recorded correctly, the world moves,
and the record stays valid-looking forever because nobody re-read it. Production
drifts from the decision, and when someone finally checks, it turns out the
*decision* was the stale artifact — not the system. Shape cannot catch that.

What it can do is insist that every accepted decision carries a `review_by` date,
and report the ones that have passed. The re-check is still yours to run. A tool
that claimed otherwise would be lying.

## One source of truth

The Markdown vault is canonical. Everything else is derived from it.

```text
Markdown vault
     │
     ├──> linter / schemas / CI
     ├──> graph + search index
     └──> Explorer UI
```

Do not keep a second copy of facts or decisions in application code.

## Security model

**A real company vault should normally be private.** Every governed note carries
`sensitivity: public | internal | confidential | restricted`.

The built-in scanner catches common credential patterns, but it is not a
substitute for repository permissions, provider-side secret scanning or human
review. Read [SECURITY.md](../SECURITY.md) before importing CRM exports,
transcripts, screenshots or customer material.

## Repository layout

| Path | Purpose |
|---|---|
| `src/whykit/` | The CLI, linter and history check |
| `src/whykit/template/` | The vault `whykit init` writes |
| `schemas/` | Machine-readable contracts for integrations |
| `examples/northline/` | A complete worked vault, linted strictly in CI |
| `apps/explorer/` | Optional read-only viewer |
| `tests/` | Linter and CLI regression tests |

## Commands

| Command | Does |
|---|---|
| `whykit init <dir>` | Create a vault |
| `whykit adopt <dir>` | Inventory existing Markdown and stage it |
| `whykit new decision/evidence/note` | Create records and keep IDs/indexes in sync |
| `whykit lint` | Check the vault (`--strict`, `--json`, `--today`) |
| `whykit status` | Summarize health plus upcoming/overdue review work |
| `whykit graph` | Export typed document/evidence/supersession relations as JSON or DOT |
| `whykit impact <target>` | Show reverse dependency/blast radius for evidence, decisions or documents |
| `whykit query [text]` | Search and filter records with a versioned JSON contract |
| `whykit context <target>` | Produce a bounded agent/person context pack |
| `whykit pack [targets…] --query …` | Produce a budgeted multi-record agent handoff bundle |
| `whykit review list/record` | Show due work and append review events |
| `whykit evidence list/retire` | Inspect and retire evidence while preserving stable IDs |
| `whykit snapshot` / `verify-snapshot` | Fingerprint governed content and detect later drift |
| `whykit check --profile …` | Run a named local/CI/release policy gate |
| `whykit policy` | Show the effective repository-local policy |
| `whykit history --base <ref>` | Verify accepted decision reasoning and prior review events were not rewritten |
| `whykit rules [code]` | List the rules, or explain one and why it exists |
| `whykit doctor` | Check prerequisites, integrity and review hygiene |
| `whykit install-hooks` | Install the pre-commit vault check |
| `whykit serve <vault>` | Run the optional Explorer (source checkout only) |

Exit codes are stable, because CI depends on them:

| Code | Means |
|---|---|
| `0` | No errors (and no warnings, under `--strict`) |
| `1` | Findings that should fail the build |
| `2` | The tool could not run: no vault, invalid configuration/input, or an unreadable Git baseline |

`2` is deliberately distinct from `1`. A pipeline that cannot tell "the vault has
problems" from "the check never ran" will eventually report the second as the
first and stop looking.

## Explorer

Optional, read-only, and not part of the data contract — it is a viewer, and its
CI job cannot block a fix to the format. Obsidian is an optional editor; the
Explorer exists so a vault is legible in a browser without installing a vault app.

```bash
whykit serve ../whykit/examples/northline
```

This path is relative to the `my-company-context` directory created in the
quick start; adjust it if you cloned WhyKit elsewhere.

## Agents and other systems

`AGENTS.md` inside a vault defines safe default behavior for coding agents, and
`INTEROP.md` defines the boundary between a knowledge repository and the code
repositories it describes.

Any system that writes Markdown can write here. The optional `provenance` block
([schema](../schemas/provenance.schema.json)) records which system produced a note
without WhyKit having to model that system's internals. See
[docs/integrations.md](../docs/integrations.md) for the design rule and a worked
mapping from CometWeb's CW-AIP envelopes.

## Project status

WhyKit has no public PyPI release yet. Install it from the repository checkout
as shown in the quick start.
The data contract, linter and single-source architecture are the priority.
Semantic search, embeddings, hosted accounts and chat-over-vault are out of scope
until real usage shows they are needed.

Contributions that help most are concrete: linter regression fixtures, format
compatibility tests, import adapters that preserve provenance, Explorer
accessibility fixes, and example vaults built from synthetic data. Start with
[CONTRIBUTING.md](../CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
