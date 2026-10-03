# Configuration

Reference for `whykit.toml`, document front matter and the flags that override
policy for one run. Every command and flag is in the [command reference](cli.md).

WhyKit has no hidden user-global configuration. A vault may carry one explicit,
versioned `whykit.toml` in its root so the same checkout behaves the same for a
person, CI and an agent.

Configuration is deliberately split by responsibility:

1. **`whykit.toml`** — repository-wide defaults and named quality-gate profiles.
2. **Document front matter** — lifecycle, ownership, sensitivity and provenance.
3. **`AGENTS.md`** — what automated agents may and may not do in this vault.
4. **CLI flags** — explicit one-run overrides such as a fixed `--today` date.

There is no home-directory config and no environment-dependent fallback policy.

## `whykit.toml`

A fresh vault contains:

```toml
format_version = 1

[defaults]
owner = "TODO"
sensitivity = "internal"
decision_review_days = 90
status_due_days = 30
require_hub_links = false

[profiles.local]
strict = false
orphans = true
secrets = true
require_git = false
require_clean_tree = false
require_configured = false
require_hub_links = false
history = "optional"

[profiles.ci]
strict = true
orphans = true
secrets = true
require_git = false
require_clean_tree = false
require_configured = false
require_hub_links = false
history = "optional"

[profiles.release]
strict = true
orphans = true
secrets = true
require_git = true
require_clean_tree = true
require_configured = true
require_hub_links = false
history = "required"
```

`format_version` is fail-closed. An unsupported version is an error rather than
being interpreted approximately. Unknown keys are rejected too, so a typo
cannot silently switch a check off.

| Key | Meaning |
|---|---|
| `defaults.owner` | Owner written into records created by `whykit new` without `--owner`. `TODO` fails the `release` profile. |
| `defaults.sensitivity` | Sensitivity label for new records without `--sensitivity`. |
| `defaults.decision_review_days` | Days ahead for an approved decision's `review_by`; see [Review defaults](#review-defaults). |
| `defaults.status_due_days` | Look-ahead window of `status` and `review list`. |
| `defaults.require_hub_links` | Make plain `whykit lint` report top-level folders that `Home.md` does not link (`hub.unlinked_workstream`). |
| `profiles.<name>.strict` | Warnings fail the gate. |
| `profiles.<name>.orphans`, `.secrets` | Run the orphan-note and credential checks. |
| `profiles.<name>.require_git` | The vault must be inside a Git work tree. |
| `profiles.<name>.require_clean_tree` | The working tree must have no uncommitted changes. |
| `profiles.<name>.require_configured` | `whykit.toml` may not contain placeholders such as `owner = "TODO"`. |
| `profiles.<name>.require_hub_links` | As `defaults.require_hub_links`, for this gate. |
| `profiles.<name>.history` | `off`, `optional` (checked when `--base` is given) or `required` (fails without `--base`). |

You can add profiles of your own and select them with `--profile <name>`.

`history` accepts `off`, `optional` or `required`. The release profile is meant
to be the strongest built-in gate: it requires a Git work tree, a clean working
tree, a concretely configured repository policy (for example, `defaults.owner` may not still be `TODO`) and an explicit immutable-history baseline.

Inspect the effective policy without opening the file:

```bash
whykit policy
whykit policy --json
```

Run a named gate:

```bash
whykit check --profile local
whykit check --profile ci --today 2026-09-22
whykit check --profile release --base origin/main --head HEAD
```

Profiles are deterministic policy, not quality scores. A passing gate means the
configured mechanical checks passed; it does not certify that evidence is true
or a decision was good.

## Core document metadata

Every governed note carries these required keys:

```yaml
---
title: "Example"
type: research
status: draft
owner: "Example owner"
created: 2026-09-22
last_updated: 2026-09-22
source_of_truth: false
sensitivity: internal
source_ids: []
tags: []
---
```

Allowed lifecycle states are `template`, `draft`, `in_review`, `approved`,
`superseded` and `archived`. Allowed sensitivity labels are `public`, `internal`,
`confidential` and `restricted`.

Optional keys include `review_by`, `decision_id`, `supersedes`, `superseded_by`,
`reviewers`, `delivery_status`, `sent_at`, `workstream` and the open `provenance`
block. See `schemas/` for machine-readable contracts.

## Lint modes

```bash
whykit lint                     # errors fail; warnings are reported
whykit lint --strict            # warnings fail too
whykit lint --json              # versioned machine-readable report
whykit lint --today 2026-09-22  # deterministic review-date evaluation
whykit lint path/to/note.md     # scoped check inside the vault
```

A scoped path may not escape the selected vault root. WhyKit also refuses
wikilinks and relative Markdown links that escape the governed boundary.

`--no-orphans` and `--no-secrets` are explicit one-run escape hatches. Prefer a
named profile for repeatable CI behavior rather than baking many flags into a
workflow.

A run without the secret scan says so. `--no-secrets`, or a profile with
`secrets = false`, prints `policy: secret scan skipped by …` (even with
`--quiet`), adds an entry with rule `secret.*`, level `off` and a `skipped_by`
key to the JSON `overrides` array of `lint` and `check`, a `::notice` to the
`github` format and a configuration notification to SARIF, exactly as a
security-relevant override does.

## Review defaults

Two `[defaults]` keys drive the review cycle:

| Key | Used by |
|---|---|
| `decision_review_days` | `new decision --status approved` without `--review-by`, and a `confirmed` review without `--next-review`, set `review_by` this many days ahead. |
| `status_due_days` | `whykit status` and `whykit review list` show reviews due within this many days unless you pass `--due-days`. |

```bash
whykit status
whykit review list --due-days 14 --owner Product
```

How reviews are recorded is in [Concepts](concepts.md#the-review-cycle).

## Evidence access age

Vaults with fast-changing sources can opt into access-age warnings per evidence
type:

```toml
[evidence_access_age_days]
analytics = 30
"vendor doc" = 180
```

Keys are matched against the register's `Type` column. Lint compares each active
row's `Accessed` date with `--today` (or today) and reports
`evidence.access_stale` when the window has passed, `evidence.access_missing`
when a covered row has no `Accessed` date, and `evidence.access_future` for dates
after the lint date. Types you leave out (typically historic sources) are not
checked.

This is a warning, not proof that a source is still correct. Recent access is
also not permission to publish a claim; publication approval belongs to the
owner's claims or review workflow.

## Team rules

A team can state its own conventions and retune built-in rules in the same
file, under `[rules]`. Nothing here forks WhyKit: a vault without a `[rules]`
table reports exactly what it did before.

```toml
[[rules.custom]]
id = "custom.decision_evidence"
level = "error"
summary = "Approved decisions cite at least two sources."
why = "One source is an anecdote; two can be compared."
fix = "Cite a second E-NNN, or keep the decision in review."
min_evidence = 2

[rules.custom.applies_to]
type = "decision"
status = "approved"

[[rules.custom]]
id = "custom.research_shape"
summary = "Research notes state their method and limits, and are refreshed."
required_sections = ["## Method", "Limitations"]
forbidden_patterns = ["\\bTBD\\b"]
max_age_days = 180

[rules.custom.applies_to]
type = "research"
paths = ["07-research/**"]

[rules.overrides."note.orphan"]
level = "off"
paths = ["99-archive/**"]

[rules.overrides."secret.detected"]
level = "off"
paths = ["fixtures/**"]
reason = "Synthetic credentials used by the parser fixtures."
```

### Custom rules

Each `[[rules.custom]]` table is one rule. Its findings use its `id` as the
rule code, so tooling filters on them exactly like built-in codes.

| Key | Meaning |
|---|---|
| `id` | Required. `custom.` followed by lowercase letters, digits and `_`. Unique. |
| `level` | `warning` (default) or `error`. New rules should start as warnings. |
| `summary` | Required. One line, shown in `whykit rules` and in every finding. |
| `why`, `fix` | Optional explanation and remedy, shown by `whykit rules <code>` and in SARIF. |
| `applies_to.type` | Document types the rule checks (one value or a list). |
| `applies_to.status` | Lifecycle states it checks. Without it, `template` notes are skipped. |
| `applies_to.paths` | Vault-relative globs: `*` and `?` stay in one folder, `**` crosses folders, `dir/` means everything below `dir`. |
| `applies_to.workstream` | Values of the note's `workstream` front-matter key. |
| `required_keys` | Front-matter keys that must be present and non-empty. |
| `required_values` | A table of key = allowed value(s). A list-valued key such as `tags` passes when any item is allowed. |
| `required_sections` | Headings the body must contain. `"Context"` matches at any level; `"## Context"` only at level 2. Case is ignored. |
| `required_patterns` | Regular expressions at least one body line must match. |
| `forbidden_patterns` | Regular expressions no body line may match; the first match is reported with its line. |
| `min_evidence` | Fewest distinct E-NNN citations (front matter or prose) the note must carry. |
| `max_age_days` | Most days allowed between `last_updated` and the lint date (`--today`). |

A rule needs at least one check. It looks only at governed notes (notes with
front matter that parses); every filter in `applies_to` must match. Body
checks ignore inline code and fenced code blocks, so a style guide can quote
the very phrase it forbids.

Patterns run on Python's regular-expression engine, which has no timeout, so
WhyKit bounds them instead. A pattern may be at most 256 characters, and it is
rejected when it uses a backreference or a conditional group, nests a
quantifier inside a repeated group (`(a+)+`), or repeats a group that contains
an alternation (`(a|ab)*`): the shapes that can take exponential time. Patterns
are matched one line at a time, and a line longer than 10,000 characters is
checked on its first 10,000 only, with a finding that says so. Use `(?i)` for
case-insensitive matching.

### Overrides

`[rules.overrides."<code>"]` changes how one rule, built-in or custom, is
reported:

| Key | Meaning |
|---|---|
| `level` | Required. `error`, `warning` or `off`. |
| `paths` | Optional globs (same syntax as `applies_to.paths`); without them the override covers the whole vault. |
| `reason` | Why the team made this choice. Required to lower or switch off a security-relevant rule. |

To treat different folders differently, write the override as an array of
tables (`[[rules.overrides."note.orphan"]]`). For each finding the entries are
tried in order and the first one whose `paths` match decides. Overrides apply
after every check has run, so a rule demoted to `warning` still fails `--strict`
and strict profiles such as `ci`, and a rule promoted to `error` fails every
profile.

Security-relevant rules (listed in [Lint rules](rules.md#team-rules-and-overrides))
cannot be quietly disabled. Lowering one or switching it off without a
`reason` is a configuration error, and an accepted override is printed with
every `lint` and `check` run, together with the number of findings it
suppressed. `config.invalid` cannot be overridden.

### When the policy is invalid

The policy is validated before anything runs, and the error names the key:

```text
rules.custom[0].forbidden_patterns[0] has a nested quantifier (a quantifier inside a repeated group), which can take exponential time: /(a+)+/
```

`whykit check`, `whykit policy`, `whykit rules` and `whykit status` stop with
the `invalid_config` error (exit 2). `whykit lint` reports the same message as
a `config.invalid` finding, so the rest of the vault is still checked.

## Explorer network safety

`whykit serve` binds to `127.0.0.1` by default. If a vault contains
`confidential` or `restricted` documents, WhyKit refuses to bind the Explorer to
a non-loopback host unless you explicitly pass:

```bash
whykit serve --host 0.0.0.0 --allow-sensitive-network
```

The Explorer embeds document bodies in its browser bundle. The override is
therefore a disclosure decision, not a convenience flag.
