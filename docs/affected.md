# Affected decisions: which decisions does this change touch?

`whykit affected` answers the question a reviewer, or an agent about to edit
files, actually has:

> Before I change these files, which accepted decisions govern them, and has
> anything those decisions rest on changed since they were approved?

It maps changed or planned files to decisions, then checks each decision's
premises. It never says a change is safe or consistent with a decision; that
judgement stays with a person. Walk through it end to end in the
[decision drift tutorial](tutorials/catch-decision-drift.md).

## Declare what a decision governs

A decision lists the code it covers in an optional `applies_to` front-matter
key: one glob or a list of globs, relative to the root of the code repository.

```yaml
applies_to: ["src/payments/**", "migrations/**"]
```

Set it when you create the decision with `--applies-to` (repeatable):

```bash
whykit new decision "Store money as integer cents" \
  --owner Payments --status draft --source E-001 --applies-to 'src/payments/**'
```

| Pattern | Matches |
|---|---|
| `src/payments/**` | everything below `src/payments/` |
| `src/payments/` | the same: a trailing `/` means everything below |
| `src/*.py` | Python files directly in `src/`, not in subdirectories |
| `**/*.sql` | SQL files anywhere |
| `Dockerfile` | only the `Dockerfile` at the repository root |

Patterns are anchored at the repository root. `*` and `?` stay within one path
segment, `**` spans any number of segments, and matching is case-sensitive.
Absolute paths, backslashes and `.`/`..` segments are rejected; at most 64
patterns per decision.

### Let Git suggest the scope

You rarely need to write scopes from scratch. The repository already knows
where a decision has travelled: the commits that changed its record (when the
vault lives in the code repository) and the commits whose message names it,
such as `Fix rounding, see D-001` (which `whykit connect` asks agents to
write). `scope suggest` groups the files those commits changed, outside the
vault, into a few patterns:

```bash
whykit scope suggest --root decisions                   # every live decision without a scope
whykit scope suggest --root decisions D-001 D-004 --all # specific decisions, even scoped ones
whykit scope suggest --root decisions --write --min-commits 2
```

```text
WhyKit scope suggestions — 1 decision(s), 1 with suggestions
  D-001  Store money as integer cents
      src/payments/**                  2 commit(s), 2 file(s)
      bench/load.csv                   1 commit(s), 1 file(s)
```

It also proposes `watch_terms` from the decision's rejected alternatives,
but only names that look like code: `backticked` names, file-like names such
as `Fuse.js` and package names such as `date-fns`. Prose options ("Monolith"),
licence names and dates are skipped. These are shown as `watch term?` lines and
never written; in the field evaluation about five in seven were plausible.

Each pattern lists the commits and files behind it. Commits that change more
than `--max-files` files (default 25: imports, formatting sweeps, mass
renames) are ignored and counted, because in field testing they were noise. Files in two or more
places under the same two-level directory become `dir/**`; a lone file stays
exact; lock files and changelogs are ignored. A suggestion is evidence for a
person to confirm, not a verdict. `--write` adds the patterns only to
decisions that have no `applies_to` yet, and `--min-commits` drops weakly
supported ones. Commit messages must name the exact ID: `D-0010` and `XD-001`
do not match `D-001`.

**Scope is routing, not reasoning.** Code moves, so an approved decision may
change its `applies_to` without being superseded: the history check allows it,
and it is excluded from the approval hash. Everything else about an approved
decision stays frozen. A decision about process rather than files can leave
`applies_to` out; it is then reported as unscoped, never silently ignored.

### Keep scopes honest

A scope written once decays silently: code moves, a package is renamed, a
term is misspelt. `scope check` compares every live decision's scope with the
repository as it is now:

```bash
whykit scope check --root decisions
whykit scope check --root decisions --strict   # in CI: fail on dead or invalid patterns
```

```text
WhyKit scope check — 5 live decision(s), 12657 tracked files: 1 dead pattern(s), 1 broad, 2 unseen term(s), 1 unscoped
  D-003  Use node-fetch on the backend
      dead_pattern packages/core/**: matches no tracked file; the code may have moved
      unseen_term cross-fech: appears in no scanned source file; fine for a rejected option, but check the spelling
```

| Finding | Meaning | Fails `--strict` |
|---|---|---|
| `dead_pattern` | The pattern matches no tracked file | yes |
| `invalid` | The pattern or term was refused | yes |
| `broad_pattern` | The pattern covers a whole top-level folder | no |
| `unseen_term` | The term occurs in no source file: expected for a library the decision rejected, suspicious for one it chose | no |
| `unscoped` | No `applies_to` and no `watch_terms` | no |

On a 12,600-file repository it took about five seconds; terms are searched in
up to 20,000 source files.

### Watch for words, not only paths

Many decisions are about a library or a convention rather than a folder: *use
Luxon, not Moment*, *avoid default exports*. Declare the words that would mean
someone is going against them:

```bash
whykit new decision "Use Luxon for dates" --owner Web --status draft \
  --source E-003 --watch-term moment --watch-term date-fns
```

```yaml
watch_terms: ["moment", "date-fns"]
```

When a change's added or removed **code** lines contain a term (whole words,
case-insensitive), the decision applies, and the report says which term in
which file. Comment-only lines, prose files (`.md`, `.mdx`, `.rst`, `.txt`,
`.adoc`), lock files and changelogs are not scanned: in the
[field evaluation](field-evaluation.md) every match there was noise. Write terms
as code spells them (`React.FC`, `export default`, `date-fns`).

Terms need the change itself, not just file names, so they are checked in Git
mode and with `--diff-file` (a planned patch, or `-` for standard input), and
reported as unchecked with `--files`. An agent can pass its planned patch to
the MCP tool's `diff` argument before it applies it. Like `applies_to`,
`watch_terms` may change on an approved decision without supersession.

### Broad decisions

A decision matched only through a pattern that covers a whole top-level
folder (`src/**`, `packages/**`) applies to most changes. It is still
reported, marked `broad`, counted separately in the summary line and listed
after the specific ones, so it does not bury them.

## Start without a vault: plain ADRs

Already keep ADRs (MADR, Nygard, adr-tools)? Point `affected` at the folder and
get a first answer in a minute, with no migration:

```bash
whykit affected --adr docs/decisions --files src/payments/ledger.py src/web/home.html
```

Without `--root` or `--adr`, a repository with no vault but a conventional ADR
folder (`doc/adr`, `docs/adr`, `docs/decisions`, `docs/architecture/decisions`,
`adr` or `decisions`) gets a hint naming it.

In this mode:

- **Which ADRs apply.** ADRs whose status is proposed, draft, rejected,
  deprecated or superseded are skipped; accepted ones and ones without a
  readable status are kept. `README`, `index` and template files are not ADRs.
- **Scope.** An ADR may declare `applies_to` and `watch_terms` in YAML front
  matter. A `[superseded]`, `[deprecated]` or `[rejected]` marker in the title
  counts as its status. Otherwise
  WhyKit **infers** a scope from repository paths the ADR mentions that exist
  today and start at a real top-level folder (a directory becomes `dir/**`),
  and says so in a caveat. Field data says real ADRs rarely name paths, so
  expect most to be unscoped: add `applies_to` or `watch_terms`.
- **Moved code.** A mentioned path that no longer exists is a review signal:
  the code moved and the decision may not have followed it.
- **What is missing.** There is no evidence register or review log, so source
  and review checks do not run. `whykit adopt` and `whykit new decision --from`
  bring the ADRs into a vault when you want them.

Unlabelled ADRs count as `internal` for `--max-sensitivity`, as notes do in a
vault. In JSON the report has `"mode": "adr"`, decisions are identified as
`adr:<path>`, and an `adr` object counts files, live ADRs, unknown statuses and
unreadable files.

## Ask about a change

Name the files you are about to change (this is what an agent does before it
edits), or let Git list them:

```bash
whykit affected --root decisions --files src/payments/ledger.py src/web/home.html
whykit affected --root decisions                              # changes since HEAD, plus untracked files
whykit affected --root decisions --base origin/main           # a branch or pull request
git diff | whykit affected --root decisions --diff-file -     # a planned patch, before committing
```

`--repo DIR` sets the code repository the paths are relative to (default: the
working directory). When the vault lives inside that repository, its own files
are left out of the change.

Only live decisions apply: approved and not superseded. For each one whose
scope covers a changed file, WhyKit runs the same checks as
[agent preflight](agent-preflight.md) and reports any review signal:

| Signal | Meaning |
|---|---|
| a cited source was retired | `retired_evidence`: the register retired a source the decision cites |
| a cited source is missing from the register | `missing_evidence` |
| a cited source is older than its allowed age | `stale_evidence`, against `[evidence_access_age_days]` |
| a captured source changed since its claim was pinned | `claim_source_changed`: the bytes behind a pinned claim differ; see [claims](claims.md) |
| its review date has passed | `review_overdue` |
| its last review asked for an update or a successor | `review_update_required`, `review_supersede_required` |
| no matching approval event in the review log | `unverified_local_approval` |
| a source it cites is a file this change modifies | `cited_source_changed`: see below |

### Sources that live in the repository

Many decisions rest on a file next to the code: a benchmark result, a spec,
a threat model, a config. When an evidence row's location has no URL scheme
(`bench/load.csv`, not `https://…`), WhyKit reads it as a path relative to the
repository root, or to the vault when the vault sits inside the repository.
If the change modifies that file or anything below that folder, every live
decision that cites the source is reported with `cited_source_changed`, even
when its scope does not cover the code being changed. The report names the
evidence and the files in `matched_sources`.

That is the narrowest honest version of evidence drift: the bytes a decision
rested on are changing in this pull request, so its reasoning should be read
again before merge. Whether the new numbers still support it is for a person
to judge.

Every possible code is enumerated in `schemas/agent-preflight-result.schema.json`;
the JSON report lists the codes in `problems` and plain-language `reasons`. A source
type without a maximum age is reported once as a caveat rather than on every
decision; set `[evidence_access_age_days]` in `whykit.toml` to check staleness.

## Read the report honestly

The report says what was checked and what was not:

- **decisions** that apply, with the files and patterns that matched and their
  review signals;
- **uncovered files**: changed files that no decision covers. That is not a
  problem, but it is not a clean bill of health either;
- **unscoped decisions**: live decisions without `applies_to`, which cannot be
  matched to files at all;
- **invalid scopes**: patterns WhyKit refused, with the reason.

What it cannot tell you:

- whether the change is consistent with the decision. Matching is by path,
  not by meaning; `semantics_checked` is always `false`;
- whether a decision that should have a scope is missing one;
- whether a source changed in the world. Only changes recorded in the vault
  (a retired row, a new capture) are visible.

## Run it in pull requests

The report is meant for the place where the next decision is made: the pull
request. The GitHub Action does it with one input. With `comment` as well, the
report joins the decision diff in one pull request comment that is updated on
every push (that needs `pull-requests: write`; see
[Decision diff comment](ci.md#decision-diff-comment)):

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  with:
    fetch-depth: 0
    persist-credentials: false
- uses: CometWeb-io/whykit@<reviewed-40-character-commit-sha>
  with:
    root: decisions
    profile: ci
    affected: "true"
    comment: "true"
```

The Action's report is advisory: it never fails the job, and decisions above
`affected-sensitivity` (default `public`) are only counted. To block a merge
while a decision needs review, call the CLI with `--strict` instead. This
workflow writes the report to the job summary and fails while an applicable
decision has a review signal:

```yaml
name: Decisions

on:
  pull_request:

permissions:
  contents: read

jobs:
  affected:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: python -m pip install "git+https://github.com/CometWeb-io/whykit@<reviewed-40-character-commit-sha>"
      - name: Decisions this change touches
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          whykit affected --root decisions --base "$BASE_SHA" --format markdown --strict \
            --max-sensitivity public >> "$GITHUB_STEP_SUMMARY"
```

`--max-sensitivity` keeps the titles of decisions above that label out of the
summary and reports only how many were withheld. Pick the label that everyone
who can read the job may see. Pair this with the
[history gate](tutorials/gate-pull-requests.md), which stops accepted reasoning
from being rewritten in the same pull request.

## Machine output

`--json` writes one object following `affected-report.schema.json`; see the
[automation contract](automation.md). Exit status is `0` when the report was
produced, `1` with `--strict` when `review_required` is true, and `2` when the
command could not run (no vault, a bad path or ref).
