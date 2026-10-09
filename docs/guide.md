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

Every command that reads a vault accepts `--root`, before or after the command
name, and after the action of `new`, `review` and `evidence` too
(`whykit new decision … --root ../my-ledger`). Without it, WhyKit
walks up from the current directory to the nearest `Home.md` plus `00-context/`.

## Create a vault

`whykit init <dir>` writes the vendor-neutral default layout. A fresh vault has
zero lint errors; its warnings point at the unanswered questions in `AGENTS.md`,
the contract agents work under, so answer those before relying on agents. The
exact warning count and line numbers can change as the template evolves.

- `whykit init --profile gtm <dir>` adds optional go-to-market workstream
  folders (strategy, website, research and others). `--full` is its legacy
  alias; `--minimal` is a compatibility alias for the default.
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
Only UTF-8 Markdown files are scanned: a name ending in `.md` in any letter
case, so `docs/old.MD` counts, the same rule `lint` and every other command
apply. A file is `heading-only` when it has nothing to keep: only headings,
placeholders such as `TODO`, `TBD` or `Lorem ipsum`, empty list items or
template fields left blank (`Date:`). Two real words are enough to make a note
(`Shipped the export.`), and so is a link; files with front matter, files that
look like ADRs and anything over 4,000 characters are never stubs. Known evidence-register and decision-log
table layouts are flagged for manual mapping, and so is a `decision_id` claimed
by two imported files or already used in the vault.

`--write` never writes into a workstream. It copies the sources into a new dated
batch under the vault's gitignored `.import-staging/`, with a SHA-256 for each
file, and appears only once complete: if a source changes mid-import or a write
fails, nothing from that run is left behind. The ingestion record lands in
`notes/`; link it from a map or lint reports `note.orphan`. A person decides
what becomes canonical.

### Check preservation after normalization

```bash
whykit adopt ../old-docs --into . --compare ../normalized-docs --json
```

`--compare DIR` is read-only and cannot be combined with `--write`. It compares
all in-scope source Markdown at the same relative paths, including files that
the adoption inventory would skip as duplicates or stubs. Renamed or moved
files are reported missing; this check does not infer a path or ID mapping.

The optional `preservation` report records source/target SHA-256, byte equality,
missing supported wikilink occurrences (including labels and anchors), missing
E-/D-IDs, and changes to native `decision_id` and `source_ids` declarations.
Inline/fenced code examples do not count as graph links or ID references.
Escaped table pipes and canonically equivalent Unicode are normalized for comparison. A recognized WhyKit
`review-log.md` must retain its earlier table rows as an unchanged prefix;
new rows and padded headers are allowed. Byte-identical files preserve their
contents without certifying their validity.

Exit 1 means loss, an unreadable file, a source changed since inventory, or an
empty Markdown inventory. Exit 2 means invalid options/directories. This is a
local owner report: filenames and link labels may contain private metadata.
It does not check non-Markdown assets, full prose equivalence, all foreign
history formats, Git commit history or whether the migrated vault passes lint.
Keep the baseline and use `whykit lint`/`history` for those separate gates.

## Promote an adopted ADR

Once a person has decided that a staged ADR should become a decision record,
`new decision --from` does the retyping. It is a dry run that prints the
mapping until you pass `--write`:

```bash
whykit new decision --from .import-staging/2026-09-17/adr/0007-use-queues.md
whykit new decision --from .import-staging/2026-09-17/adr/0007-use-queues.md --write
```

It recognises four shapes and copies each section it knows into the matching
WhyKit section:

| Shape | Recognised by | Mapped |
|---|---|---|
| MADR | `Context and Problem Statement`, `Decision Drivers`, `Considered Options`, `Decision Outcome` | context and drivers, decision, the `because` clause as rationale, unchosen options as alternatives, `Good, because` / `Bad, because` as positive and negative consequences |
| Nygard (also adr-tools, log4brains) | `Status`, `Context`, `Decision`, `Consequences` | the same sections; consequences stay one paragraph |
| Y-statement | `In the context of …, facing …, we decided for … and neglected …, to achieve …, accepting …` | context, decision, rationale, alternatives, negative consequences |
| Polish headings | `Kontekst`, `Decyzja`, `Uzasadnienie`, `Rozważane opcje`, `Konsekwencje`, `Status` (also the Polish Y-statement) | as above |

The title comes from the source's title or first heading, without ADR
numbering (`7. Use queues` becomes `Use queues`); pass a title to override it.
The next free `D-NNN` is allocated, and the record and its decision-log row are
written in one journalled transaction. The next write recovers an interrupted
commit before it starts; readers may see a partial batch until recovery runs.
Staged copies are retained until the commit marker is persisted. The record keeps:

- the whole source text under `## Original record`, in a fence lint does not
  parse (line endings normalized to LF);
- a `provenance` block: the staged path, the path inside the adopted folder,
  the import batch, the SHA-256 of the source bytes, and the status, date and
  number the source claimed;
- every link in the source under `## Evidence` as a TODO candidate. No
  evidence ID is invented; register the sources that support a claim with
  `whykit new evidence` and cite them.

IDs (`E-012`, `D-004`) and wikilinks copied from another ledger would not
resolve in this one, so in the mapped sections they become code spans and the
output says so. The record starts as `draft` (or `--status`) whatever the
source claimed: an ADR marked "Accepted" was accepted by a process WhyKit
cannot see, so approval stays a person's step. Sections the source does not
have keep their prompt, which `decision.placeholder` reports until someone
answers it. Promoting the same bytes twice is refused with `target_exists`.

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
  --owner Product --status draft --source E-001

whykit new note "Interview synthesis" \
  --workstream notes \
  --link-from Home.md \
  --json
```

File names are ASCII kebab case derived from the title, so they survive every
filesystem and Git setting: accents are folded (`Łódź` becomes `lodz`), and a
title with no Latin letters at all, such as `会议记录`, gets a stable
`record-` name with a short hash of the title. The title itself is kept as
written.

`--link-from` is explicit on purpose: WhyKit never rewrites `Home.md` or another
map unless asked. When it is given, the note and the link are created in one
atomic operation, and the note is rolled back if the map update fails.

A draft with `--supersedes D-NNN` leaves its predecessor unchanged until the
new record is approved. See [Concepts](concepts.md#changing-your-mind-supersede-do-not-rewrite).

## Approve a decision

Create a draft or in-review decision, then write the Context, Decision,
Rationale, Evidence, Alternatives considered and Consequences. Approval
requires a real owner and cited active evidence. It refuses empty sections,
placeholders, stale evidence and failed vault checks.

```bash
whykit review approve D-001 --reviewer "Ada Example" --json
```

This previews the complete record, cited register rows and exact changes without
writing. Read the record, inspect its sources and check every diff. After you
accept that specific preview, copy its `expected_sha256` into:

```bash
whykit review approve D-001 --reviewer "Ada Example" \
  --write --expect-hash HASH_FROM_PREVIEW
```

Keep `--today`, `--next-review` and reviewer identical if you supplied them.
The hash binds the draft bytes, register, policy, ledgers, predecessor and
proposed changes. Changed inputs or parameters require a new preview. The
record, decision log, approval event and any predecessor lifecycle change are
written in one recoverable transaction. The default review deadline comes from
`defaults.decision_review_days`.

The new `approved` event stores `record-sha256:<hash>; snapshot-sha256:<hash>`.
The snapshot hash identifies the original draft bytes; the record hash binds
reasoning and provenance while excluding the mutable lifecycle keys `status`,
`review_by`, `last_updated` and `superseded_by`, normalizing line endings and BOM.
`whykit history` requires a matching newly appended event for a new acceptance;
editing only the metadata or replaying an old event fails.

Reviewer names and hashes establish attribution and consistency, not identity
or truth. WhyKit does not fetch or freeze remote source contents during approval.
Use your repository's protected review process for authenticated acceptance.
MCP stays read-only and has no approval tool.

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

## Check several vaults

```bash
whykit workspace ~/vault-a ~/vault-b --today 2026-09-17
whykit workspace ~/vault-a ~/vault-b --json --strict
```

Each root uses its own policy, lint rules and default review window. Explicit
`--due-days` overrides the window for this run. Aliases of the same resolved
root are counted once; overlapping roots are rejected to keep vault scopes
separate. No notes are copied or joined and this command writes no parse cache.

JSON contains a separate `status` report or `error` for each root. A missing,
unreadable or misconfigured vault does not hide the others. Exit code precedence
is 70 (internal error), then 2 (a vault could not be checked), then 1 (lint
errors, or warnings under `--strict`), otherwise 0. `--root` is for single-vault
commands; `workspace` requires explicit roots.

This is a local owner report. Paths, findings and review queues may contain
private metadata: do not publish its output as a public vault export. It does
not provide a cross-vault MCP search, shared database or cloud synchronization.

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
large note cannot silently fill an agent's context window. When a note does not
fit, its front matter is left out of the content first (its fields are already
in `record`), and `front_matter_omitted` is `true`. The budget then goes to a
`summary` (or `description`) front-matter value, if the note has one, followed
by the body from its first heading, so `--max-chars 200` returns the note's
opening rather than its tags. For tools that speak
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

Static `explorer-index` and npm builds default to public. Private local
viewing requires `--private` or `WHYKIT_EXPLORER_PRIVATE=1`; `serve` opts in
automatically and keeps its network guard. Public export withholds whole notes
referring to hidden records and unclassified attachments rather than rewriting
approved reasoning.

The Explorer is an optional, read-only viewer and not part of the data
contract. It needs Node.js and runs only from a source checkout. A copy
installed from a checkout (`uv tool install ./whykit`) names that checkout and
the command to run there; one installed from Git or a package index prints the
`git clone` to run instead:

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

## Review a claim graph

Use [the claim workflow](claims.md#author-and-review) to preview opt-in, author local fragments, approve C, explain disputed dependencies in D and apply the reviewed decision preview. `confirmed` on C or D with claims requires a fresh preview/hash; legacy D review behavior is preserved.
