# Migrating to WhyKit 0.3

WhyKit 0.3.0 is the first public package release. If you ran WhyKit from a
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
| `secret.scan_skipped_large_file` | error | a text asset over 5 MiB, which used to be skipped silently |

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
whykit init --full company-vault
```

`--minimal` is still accepted and now means the default, so a setup script
that passes it keeps working. Existing vaults are not touched by any of this.

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
