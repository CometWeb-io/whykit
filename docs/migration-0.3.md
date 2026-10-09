# Migrating to WhyKit 0.3

WhyKit 0.3.0 will be the first public package release. If you ran WhyKit from a
source checkout of the internal 0.2.0 milestone, or from the `0.3.0.dev0`
source preview, this page lists what changes for your vault, your scripts and
your CI, and what to do about each. The full list of changes is in the
[changelog](../CHANGELOG.md).

Your vault's Markdown does not need rewriting. Every item below is about a
command's output, an exit code, a stricter check or a default.

## Checklist

1. Replace any `python -m whykit.<module>` call with the `whykit` command
   ([removed module entry points](#removed-module-entry-points)).
2. Update scripts that branch on exit codes
   ([exit codes](#exit-codes)) or parse `whykit rules --json`
   ([rules JSON shape](#rules-json-shape)).
3. Decide whether new snapshots should stay in format v1
   ([snapshots](#snapshot-format-v2)).
4. Run `whykit lint --strict` once and fix or accept the new findings before
   you upgrade a strict CI gate ([stricter checks](#stricter-checks)).
5. If you set up vaults with a script, check which layout it expects
   ([default layout](#default-init-layout)).
6. If you run the MCP server, upgrade the SDK and check the sensitivity
   ceiling ([MCP server](#mcp-server)).
7. Reinstall the pre-commit hook in each vault
   ([hooks](#pre-commit-hooks)).

## Review history and interrupted writes

`new decision` now accepts only `draft` or `in_review` as an initial state.
Replace direct `--status approved` creation with a draft, complete its sections,
and [preview/apply approval](guide.md#approve-a-decision). Existing accepted
records in the comparison baseline remain historical; newly added or newly
accepted records, including the first staged commit, require a matching new
`approved` event. Do not reset old accepted records to draft to invent a new
history. Import unverified historical decisions as drafts and preserve their
source status in provenance.

History/check blocked entries may now include `approval_without_event` or
`review_without_event` in the optional `reason` field. Keep unknown optional
fields when forwarding reports.

Review logs gain the `approved` outcome, written only by `review approve`.
Consumers must accept that value and its record/snapshot hashes in the Note
cell. The seven-column table layout stays unchanged. The preview/result JSON
uses `decision-approval-result.schema.json`; no MCP write capability was added.


Imported ADRs must start as `draft` or `in_review`, even when the source says
accepted. Keep that source status in provenance. `decision.unreviewed` is a new
warning when an approved record explicitly denies human review; strict CI fails
it. Review the content through your authorized human workflow. An editable
`human_reviewed: true` field is an assertion, not proof of reviewer identity.
The decision metadata schema now requires `review_by` for approved records and
rejects an explicitly false `human_reviewed` value.

Do not move an approved decision's `review_by` by editing metadata alone.
`whykit history` now requires newly appended, matching confirmed review events
in the same commit or staged change. Run `whykit review record D-NNN --reviewer
"Reviewer" --outcome confirmed --next-review YYYY-MM-DD` and include both the
record and `00-context/review-log.md` in the change. Existing events cannot be
reused to postpone a deadline; multiple new reviews may form one consistent
chain. Padding Markdown table columns preserves the recorded cell values.

Interrupted writes retain staged bytes until COMMITTED. Recovery also reads
older journals that deleted a staged file early, accepting it only when the
target matches its recorded hash. Keep pending journals; see
[interrupted writes](troubleshooting.md#an-interrupted-write-or-cannot-recover-missing-staged-content).

## Removed module entry points

The per-module entry points are gone: `python -m whykit.lint`, `python -m
whykit.graph` and 16 others no longer run anything. Use the `whykit` command,
which has been the documented interface all along. `whykit-mcp` and `python -m
whykit.mcp_server` are unchanged.

| Module you ran | Command to use |
|---|---|
| `python -m whykit.adopt` | `whykit adopt` |
| `python -m whykit.backlinks` | `whykit backlinks` |
| `python -m whykit.check` | `whykit check` |
| `python -m whykit.context` | `whykit context` |
| `python -m whykit.evidence` | `whykit evidence` |
| `python -m whykit.explorer_index` | `whykit explorer-index` |
| `python -m whykit.graph` | `whykit graph` |
| `python -m whykit.immutability` | `whykit history` |
| `python -m whykit.impact` | `whykit impact` |
| `python -m whykit.lint` | `whykit lint` |
| `python -m whykit.pack` | `whykit pack` |
| `python -m whykit.query` | `whykit query` |
| `python -m whykit.review` | `whykit review` |
| `python -m whykit.rules` | `whykit rules` |
| `python -m whykit.scaffold` | `whykit new` |
| `python -m whykit.snapshot` | `whykit snapshot` and `whykit verify-snapshot` |
| `python -m whykit.status` | `whykit status` |
| `python -m whykit.trace` | `whykit trace` |

Option names can differ from what a module accepted; `whykit <command> --help`
and the [command reference](cli.md) list them. Every command takes `--root` to
name the vault.

## Exit codes

Exit codes now follow one rule, documented in [Automation](automation.md#exit-codes):
`0` means success, `1` means the command ran and found a problem with the
vault, `2` means it could not run as asked, and `70` means WhyKit itself
crashed.

| Situation | Before | Now |
|---|---|---|
| An unexpected exception inside WhyKit | a traceback, with exit 1 or 2 depending on where it surfaced | 70, error code `internal_error` |
| `evidence retire` or `review record` names an ID or record that does not exist | 2 | 1, error code `not_found` |
| `new` finds no evidence register or decision log to append to | 2 | 1, error code `vault_invalid` |
| `lint <path>` names a path that does not exist, leaves the vault or is not Markdown | 0, "clean" | 2 |

What to change in a CI script:

- Treat exit 70 as "report a bug", not as a failed gate. stdout is an error
  object with code `internal_error` under `--json`; `WHYKIT_DEBUG=1` prints
  the traceback to attach to the report.
- If you retried or ignored exit 2 from `evidence retire`, `review record` or
  `new` to mean "not there", test for 1 and the error code instead.
- A `lint` step that passed a wrong path was silently green before. It now
  fails, which is the point; fix the path.

With `--json`, every failure prints one error object, so a consumer can branch
on its `code` instead of the exit code alone:

```json
{"contract_version": 1, "error": {"code": "not_found", "message": "...", "hint": "..."}}
```

## `rules` JSON shape

`whykit rules --json` used to print a bare array. It now prints an object with
the contract version and a count, like every other JSON command:

```json
{"contract_version": 1, "count": 76, "rules": [{"code": "agents.absent", "...": "..."}]}
```

Read the `rules` key instead of the top level. With `jq`:

```bash
whykit rules --json | jq -r '.rules[].code'
```

`whykit rules <code> --json` still prints one rule object and now includes
`contract_version`. The schemas are `schemas/rule-catalog.schema.json` and
`schemas/rule-detail.schema.json`.

## Snapshot format v2

`whykit snapshot` now writes `whykit.snapshot/v2` by default. v2 hashes text
with one leading UTF-8 byte-order mark removed and CRLF turned into LF, and
records that in a `normalization` key, so a baseline taken on Linux verifies
on a Windows checkout of the same commit.

- **Existing baselines need nothing.** `verify-snapshot` reads the format from
  the baseline file, so a v1 baseline keeps verifying byte for byte.
- **New baselines are v2.** If a tool of your own parses snapshot files and
  understands only v1, keep writing v1 until it is updated:

  ```bash
  whykit snapshot --format v1 --output .whykit/baseline.json
  ```

- To move a baseline to v2, take a new one from a commit you trust and
  replace the old file in the same change.

## Stricter checks

0.3 checks more, so a vault that passed `whykit lint --strict` on 0.2.0 can
fail it on 0.3.0 without any change to the vault.

New lint findings (see [Lint rules](rules.md) for each one's fix):

| Rule | Severity | Fires on |
|---|---|---|
| `decision.placeholder` | warning | a decision record that still holds the prompts `whykit new decision` writes, or an empty required section |
| `path.case_collision` | warning | two notes whose paths differ only by case or Unicode normalization |
| `frontmatter.empty` | warning | a required key with no value |
| `source_of_truth.invalid` | warning | a `source_of_truth` that is not `true` or `false` |
| `fact.evidence_missing` | warning | a fact callout citing an `E-NNN` with no register row |
| `markdown_link.missing` | warning | a relative link, and now also a local image, that points to a missing file |
| `embed.missing` | error | an Obsidian embed `![[…]]` that does not resolve |
| `secret.scan_non_utf8` | error | a file the secret scan cannot decode as UTF-8 |
| `secret.scan_unreadable` | error | a file the secret scan cannot read |
| `secret.scan_skipped_large_file` | error | a text asset over 5,000,000 bytes (5 MB), which used to be skipped silently |

Front matter with the same key twice is reported as `frontmatter.invalid`
instead of keeping one of the values. An unknown key in `whykit.toml`, at the
top level, in `[defaults]` or in a profile, now fails with error code
`invalid_config` instead of being ignored; usually it is a typo.

Decision lineage is checked too: a supersession cycle
(`decision.supersession_cycle`) or two approved decisions replacing the same
predecessor (`decision.supersession_fork`) is an error.

To roll this out without a red main branch:

```bash
whykit lint --root vault --json > lint-0.3.json
whykit lint --root vault --strict
```

Fix the errors first. Warnings fail only under `--strict` or a strict
profile, so you can keep the gate on the `local` profile while you work
through them, then switch back.

## Default `init` layout

`whykit init` now writes the vendor-neutral layout (`00-context`,
`06-decisions`, `notes` and the registers). The former starter with
go-to-market workstream folders such as `01-strategy` and `07-research` is
opt-in:

```bash
whykit init --profile gtm company-vault
```

`--full` remains a compatibility alias for `--profile gtm`. `--minimal` still
means the default, so setup scripts keep working. The JSON `layout` field keeps
its `minimal`/`full` values; the optional `profile` field names the profile.
Existing vaults are not touched by any of this.

## MCP server

- The `mcp` extra now requires the MCP SDK 2.2 or 2.3 (`mcp>=2.2,<2.4`). Reinstall the
  extra so the SDK is upgraded with WhyKit.
- `whykit-mcp` defaults to `--max-sensitivity internal`. Records marked
  `confidential` or `restricted` are hidden from MCP hosts unless you raise
  the ceiling explicitly, after reading [MCP server](mcp.md):

  ```bash
  whykit-mcp --root vault --max-sensitivity confidential
  ```

- Hidden records look exactly like missing ones, and every tool failure is a
  structured error body. A host prompt that relied on a raw exception message
  should read the error code instead.
- `query`, `trace` and `backlinks` return one page at a time with a
  `next_cursor`. A host that wants more than `limit` items passes it back as
  `cursor`; a cursor from an earlier server process is refused with
  `invalid_cursor`, so start again from the first page.
- A note whose front matter does not parse, or that spells the key
  differently (`Sensitivity:`), is treated as above every ceiling and hidden.
  Evidence register rows are shown only when the register itself is within
  the ceiling. Fix the front matter (`whykit lint` reports it) to make such a
  note visible again.
- `whykit-mcp --http` without a token rejects a request whose `Host` or
  `Origin` header does not name the machine itself, however the loopback
  address was spelt. A client behind a proxy that rewrites `Host` needs a
  token (`--token-file` or `WHYKIT_MCP_TOKEN`).

## Evidence row sensitivity

The active and retired source tables may each have one optional **final**
`Sensitivity` column. Existing tables remain valid and inherit the register's
front-matter label. Existing vaults are not rewritten automatically.

```sh
whykit new evidence --source "Synthetic private source" --type fixture \
  --location https://example.com/source --claims "A fictional claim" \
  --sensitivity restricted
```

The first explicit label extends the active table in that one atomic write and
fills populated legacy rows with their inherited label. Later unlabeled additions
inherit the register floor. Retirement preserves the label and extends the retired
table when needed. The register floor remains a minimum: a `public` row in an
`internal` register is still internal. A retired row also inherits its replacement's
classification, transitively, to avoid exposing a hidden replacement ID.

Labels must use the four lowercase canonical values. A blank/truncated labeled
row, unknown label or duplicate ID is withheld by filtered readers. The new
`evidence.sensitivity` warning reports malformed explicit cells; duplicate IDs
retain the existing error. The raw labeled table is withheld in MCP/public exports;
public Explorer exports keep permitted rows and withhold notes citing hidden rows.
Local full-access CLI reads and `explorer-index --private` remain available.

The reviewed D-002 in [the generic example](../examples/approval/README.md)
demonstrates the format. No label authenticates a reviewer, encrypts data or proves
source truth. Check classification before using a public export.

## Pre-commit hooks

The hook that `whykit install-hooks` writes now runs `whykit check --profile
local`, so local commits use the same versioned policy as CI. Reinstall it in
each vault to pick up the change:

```bash
whykit install-hooks --root vault
```

If you use the [pre-commit](https://pre-commit.com/) framework instead, the
repository now ships `whykit-lint` and `whykit-history` hooks; see
[Running WhyKit in CI](ci.md).

## Other behaviour you may notice

- `query --type decision` no longer lists the decision template; ask for
  `--status template` to find templates.
- `status` counts only real decision records, not the decision log or the
  template, so its decision count can drop.
- Evidence IDs inside inline code or fenced code blocks are examples, not
  citations, everywhere: `graph`, `impact`, `trace`, `context`, `query` and the
  Explorer.
- JSON, DOT and Mermaid output is always UTF-8, whatever the console encoding.
- `whykit new` names a record whose title has no Latin letters or digits
  `record-<8 hex digits>.md` instead of `record.md`, and folds letters such as
  `ł` and `ø` instead of dropping them. Existing files keep their names;
  scripts should read the new file's `path` from the `--json` output of
  `whykit new note`.
- `lint --no-secrets` and a profile with `secrets = false` now say that the
  secret scan was skipped: a `policy:` line in text output (also with
  `--quiet`) and an `overrides` entry in JSON.
- In the Explorer graph, Space selects a note and Enter opens it; Tab moves
  past the graph in one step, and arrow keys move between notes.
- Markdown files are recognised by a `.md` suffix in any letter case, so a
  file such as `notes/Old.MD`, which Linux and macOS used to skip, is now
  linted, indexed, adopted and snapshotted like any other note. A snapshot
  baseline taken before the upgrade reports it as a change.
- Decision and evidence IDs use ASCII digits only. `E-００１` (fullwidth digits)
  is no longer read as an ID; write `E-001`.
- A file that is not valid UTF-8 is reported as `frontmatter.invalid` and read
  with the bad bytes replaced, instead of stopping the command.
- Commands that read every note keep a parse cache in `.whykit/cache/`, which
  carries its own `.gitignore`. Turn it off with `--no-cache` or
  `WHYKIT_NO_CACHE=1`; output is identical either way.
- The top-level `whykit -h` lists commands grouped by job, and `--root DIR`
  may come before the command (`whykit --root vault lint`).

## Public Explorer exports

`explorer-index` and npm builds now default to public. Existing scripts that
need a private local viewer must explicitly add `--private` or
`WHYKIT_EXPLORER_PRIVATE=1`; `serve` already does this. Public notes that refer
to hidden records or unclassified local attachments are withheld as whole
notes. Ledgers, findings and search follow the resulting visibility. Private
export preserves the previous full content, with an added `exportMode` field.
No vault file is rewritten by an export.
