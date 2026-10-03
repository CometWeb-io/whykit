# Automation: the JSON contract

This page is for scripts, CI jobs and agents that call the `whykit` CLI and
read its output. It covers what a command writes to stdout under `--json`, how a
failure looks, what the exit codes mean, and which parts of all that you can
rely on across releases. For the optional MCP server, see [mcp.md](mcp.md).

## The contract in four rules

1. **stdout holds exactly one JSON document.** On success it is the command's
   report. When the command cannot produce its report, it is an error object.
   Nothing else is printed to stdout, so `json.loads(stdout)` always works.
2. **Every document carries `contract_version`.** It is `1` today, on reports
   and on error objects alike.
3. **Exit codes do not depend on `--json`.** The same failure exits with the same
   code whether or not you asked for JSON.
4. **stderr stays human.** The text a person would see goes to stderr in both
   modes, byte for byte. Log it, never parse it.

Machine output (JSON, and the DOT and Mermaid graph formats) is always UTF-8,
whatever the console's encoding: on a Windows `cp1252` console or under
`LC_ALL=C`, stdout is switched to UTF-8 for the document, error objects
included. Human text on stderr degrades to ASCII spellings instead of crashing.

A command is in JSON mode when you pass `--json`. `graph` and `pack` are also in
JSON mode when `--format` is `json` (their default), `lint` and `check` when
`--format` is `json`, `snapshot` when it writes to stdout (no `--output`), and
`explorer-index` always. The CI formats (`--format sarif` and
`--format github`) are not JSON mode; see [CI formats](#ci-formats).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. The report says everything is fine (or, for read-only queries, here is the answer). |
| `1` | The command ran and the answer is a problem with the vault: lint errors (or warnings under `--strict`), a failed policy gate, rewritten history, a target that does not exist, a vault too broken for the command. |
| `2` | The command could not run as asked: no vault, a malformed option, an invalid `whykit.toml`, a refused path or change. stdout is an **error object**. |
| `70` | WhyKit crashed: a bug, not a problem with the vault or the job. stdout is an error object with code `internal_error`. |
| `130` | Interrupted. stdout is an error object with code `interrupted`. |

With exit code 1, stdout is usually the command's report. It is an error object
only for `not_found` and `vault_invalid`, the two codes that exit 1. Every error
code has exactly one exit code, listed below.

So a consumer only needs one branch:

```python
import json
import subprocess

result = subprocess.run(
    ["whykit", "status", "--json"], capture_output=True, text=True,
)
payload = json.loads(result.stdout)
if "error" in payload:
    raise RuntimeError(f"{payload['error']['code']}: {payload['error']['message']}")
print(payload["review_overdue"], "overdue review(s)")
```

## The error object

```json
{
  "contract_version": 1,
  "error": {
    "code": "vault_not_found",
    "message": "not a WhyKit vault: /srv/ledger",
    "hint": "a vault has Home.md and 00-context/ at its top level; check --root, or create one with `whykit init <dir>`"
  }
}
```

- `code` is stable. Branch on it.
- `message` is for people and may be reworded in any release.
- `hint`, when present, says what to do next. It is `null` when there is
  nothing useful to add. It is never part of `message`.

Schema: [`schemas/error.schema.json`](../schemas/error.schema.json).

### Error codes

| Code | Exit | When |
|---|---|---|
| `usage` | 2 | The command line could not be parsed, or options conflict (`--json` with `--format dot`, `pack` with no target). An invalid choice names the valid values; `--status accepted` also points at `--status approved`. |
| `invalid_argument` | 2 | An option value is malformed or out of range: a date that does not exist, a negative count, an ID in the wrong form, an unknown rule code, an unreadable snapshot file, a lint path outside the vault. |
| `invalid_target` | 2 | The named source or destination exists but cannot be used, e.g. `adopt` pointed at a file, or overlapping source and vault. |
| `vault_not_found` | 2 | No vault at `--root`, or at or above the working directory. |
| `invalid_config` | 2 | `whykit.toml` could not be read or failed validation. |
| `not_found` | 1 | The record named as the command's target does not exist: `evidence retire E-999`, `review record D-999`. See [Missing targets](#missing-targets). |
| `unsafe_path` | 2 | A path was refused because it escapes the vault or passes through a symlink (`--output`, `init`). |
| `target_exists` | 2 | The command refused to overwrite existing content (`init` into a non-empty directory, a record that already exists). |
| `operation_rejected` | 2 | The vault refused a change: evidence that is already retired, an ambiguous target, a reference to an unknown record (`--source E-999`, `--supersedes D-999`, `--replaced-by E-999`), an approved decision without a real owner. |
| `missing_dependency` | 2 | A required tool or bundled file is missing: Git for `history`, the vault template. |
| `git_error` | 2 | Git could not answer: an unknown `--base` or `--head` ref, a directory that is not a repository, or no merge base in a shallow clone (the hint says to fetch full history). |
| `vault_invalid` | 1 | The vault is too broken for the command: lint errors block `explorer-index`, or `new` cannot find the evidence register or decision log. |
| `io_error` | 2 | The filesystem refused a read or write, including a file marked read-only that a change would rewrite (refused before anything is written). Set `WHYKIT_DEBUG=1` to get the traceback instead. |
| `interrupted` | 130 | The command was interrupted. |
| `internal_error` | 70 | WhyKit itself failed with an unexpected exception. This is a bug: the hint links the issue tracker. The message names the exception type; set `WHYKIT_DEBUG=1` to also print the Python traceback on stderr (stdout is still the error object). |

A crash that is a bug in WhyKit is reported like any other failure: under
`--json` stdout is an `internal_error` object, and stderr carries one line
naming the exception plus a hint to report it. No traceback is printed unless
`WHYKIT_DEBUG=1` is set, because a traceback can quote vault paths and content
you may not want in a CI log. Exit code 70 (`EX_SOFTWARE`) keeps "WhyKit broke"
apart from "fix the vault" (1) and "fix the job" (2).

## Missing targets

One rule covers every command: **a vault record named as the command's target
(a path, `D-NNN` or `E-NNN`) that does not exist exits 1.** What stdout holds
depends on whether the command reads or changes the target:

| Command | Missing target gives |
|---|---|
| `backlinks`, `context`, `impact` | the report, with `"exists": false` |
| `pack` | the bundle, with the target listed in `missing` |
| `trace --decision D-NNN` | the report, with an empty `decisions` list |
| `evidence retire`, `review record` | a `not_found` error object |

A record that is only *referenced*, not targeted, is different: `new decision
--source E-999`, `--supersedes D-999` or `evidence retire --replaced-by E-999`
names something that must already exist for the change to make sense, so the
change is refused with `operation_rejected` and exit 2. So is a target that
matches more than one record. Unknown rule codes (`whykit rules no.such_rule`)
are catalog lookups, not vault records, and stay `invalid_argument` with exit 2.

## Reports, not errors

Other outcomes look like failures but are answers, so they come back as a normal
report with exit code 1:

- `doctor` without a vault returns a report with `"passed": false`.
- `lint`, `check`, `history`, `status --strict` and `trace --strict` report what
  they found and exit 1 when it should fail the build.

Some situations are worth a warning but do not change the answer. They go to
stderr only, and the exit code and JSON report are what they would have been:

- `history` finds no decision records or review log under `--root` in either
  revision (usually the wrong `--root` for a vault in a subdirectory).
- `history --head HEAD` runs while Markdown in the vault has uncommitted
  changes, which it does not check.

`history --staged` compares the Git index with `--base` (default `HEAD`), which
is what a pre-commit hook needs. Its report has `"head": "INDEX"` and
`"staged": true`. Before the first commit there is nothing accepted to rewrite,
so it passes.

## Schemas

Every command's JSON output has a JSON Schema (draft 2020-12) in
[`schemas/`](../schemas/). The test suite runs each command against real vaults
and validates the output against its schema, so the schemas describe what the
CLI actually prints.

| Command | Schema |
|---|---|
| `init --json` | `init-result.schema.json` |
| `lint --json` | `lint-report.schema.json` |
| `new decision\|evidence\|note --json` | `record-create.schema.json` |
| `status --json` | `status-report.schema.json` |
| `graph --json` | `graph.schema.json` |
| `backlinks --json` | `backlinks-report.schema.json` |
| `impact --json` | `impact-report.schema.json` |
| `trace --json` | `trace-report.schema.json` |
| `query --json` | `query-result.schema.json` |
| `context --json` | `context-pack.schema.json` |
| `pack --json` | `context-bundle.schema.json` |
| `review list --json` | `review-queue.schema.json` |
| `review record --json` | `review-record-result.schema.json` |
| `snapshot` | `snapshot.schema.json` |
| `verify-snapshot --json` | `snapshot-verify.schema.json` |
| `check --json` | `check-report.schema.json` |
| `policy --json` | `policy.schema.json` |
| `evidence list --json` | `evidence-list.schema.json` |
| `evidence retire --json` | `evidence-retire-result.schema.json` |
| `adopt --json` | `adopt-report.schema.json` |
| `history --json` | `history-report.schema.json` |
| `rules --json` | `rule-catalog.schema.json` |
| `rules <code> --json` | `rule-detail.schema.json` |
| `doctor --json` | `doctor-report.schema.json` |
| `explorer-index` | `explorer-index.schema.json` |
| any failure | `error.schema.json` |

The same table is available to Python callers as
`whykit.contract.OUTPUT_SCHEMAS`, and the error codes as
`whykit.contract.ERROR_CODES`.

## CI formats

Two more output formats exist for tools that already read a standard format.
They render the same findings as `lint --json` and keep its exit codes, but they
are not the JSON contract: no `contract_version` at the top level, and no error
object. When the command cannot run (no vault, a bad option), stdout is empty
and the human error goes to stderr, so an upload step never receives a document
that is not what it expects.

| Format | Output | Schema |
|---|---|---|
| `lint --format sarif` | A SARIF 2.1.0 log for code-scanning dashboards. | `lint-sarif.schema.json` |
| `lint --format github`, `check --format github` | GitHub Actions workflow commands (`::error file=…,line=…::…`), which the runner turns into annotations. | none (GitHub's text protocol) |

The SARIF log has one run. `tool.driver.rules` is the whole rule catalog in
`whykit rules` order, and each rule's `helpUri` is its anchor in the
[rule reference](rules.md), for example `docs/rules.md#evidence.missing`. Each
result has `ruleId` set to the stable rule code (the same value as
`findings[].code`), `level` `error` or `warning`, and a location relative to
`%SRCROOT%`: the Git work tree root, so a vault in `knowledge/` reports
`knowledge/Home.md`. Outside Git, locations are vault-relative.
`partialFingerprints` ignores the line number, so editing text above a finding
does not open a new code-scanning alert. `runs[0].properties` carries
`contract_version`, the file count and the vault's prefix in the work tree.
`whykit.contract.FORMAT_SCHEMAS` maps the format to its schema.

`--format github` prints one workflow command per finding at its own level,
then a plain summary line. When `--strict` (or a strict profile) fails on
warnings alone, a final `::error` says so, because GitHub shows warnings as
warnings. `check --format github` also annotates a rewritten decision record on
its file and prints the usual gate report after the annotations.

`--json` and a different `--format` conflict and exit 2 with a `usage` error.

## Using it in CI

The exit code is the gate; the JSON is for the summary you attach to the run.

```bash
set +e
whykit check --profile ci --json > whykit-check.json
status=$?
set -e
jq -r 'if .error then "whykit could not run: \(.error.code) - \(.error.message)"
       else "profile \(.profile): \(if .passed then "PASS" else "FAIL" end)" end' whykit-check.json
exit $status
```

Tell "the vault is wrong" from "the job is misconfigured" by the exit code: `1`
means fix the vault, `2` means fix the job (wrong `--root`, missing Git
history, a malformed date), `70` means report a WhyKit bug.

## Using it from an agent

Agents should call commands with `--json`, check for `error` first, and branch
on `error.code`:

- `vault_not_found`: ask for the vault path instead of guessing one.
- `invalid_argument`, `usage`: the call itself is wrong; fix it, do not retry
  it unchanged.
- `operation_rejected`, `target_exists`: the vault said no. Report it to the
  person; do not work around it.
- `io_error`, `git_error`, `missing_dependency`: an environment problem; surface
  the `hint`.
- `internal_error`: a WhyKit bug. Do not retry in a loop or work around it;
  surface the message and the issue-tracker hint to the person.

```bash
whykit query "pricing" --json
whykit context D-001 --json
whykit new evidence --source "Example survey" --location https://example.com/survey --type survey --claims "Respondents prefer email" --json
```

## Stability policy

The contract is versioned by `contract_version`. Within one version:

- **Compatible, no version bump:** adding a key to a report or to the error
  object, adding an error code, adding a command and its schema, adding a value
  to an open-ended string field. Consumers must ignore keys they do not know.
- **Breaking, bumps `contract_version`:** removing or renaming a key, changing a
  key's type or meaning, renaming or removing an error code, changing which exit
  code a situation produces. Breaking changes are recorded in `CHANGELOG.md`.
- **Never part of the contract:** `message` and `hint` wording, stderr text,
  key order and whitespace.

Snapshot files have their own `format` (see
[Snapshot formats](#snapshot-formats)); a new snapshot format is introduced
alongside the old one, never in place of it.

Lint rule codes (`findings[].code`) follow the same rule and are listed in the
[rule reference](rules.md). The vault data format (front matter, evidence
rows, decision records) is a separate contract, described by the other schemas
in `schemas/`.

## Snapshot formats

`whykit snapshot` writes a baseline that `whykit verify-snapshot` later
compares the vault against. The baseline's `format` decides how every file is
hashed, and `verify-snapshot` always re-hashes the current vault the same way,
so a baseline keeps meaning what it meant when it was written.

| `format` | Hashes | Written by |
|---|---|---|
| `whykit.snapshot/v2` | The file's text after the normalization recorded in the snapshot's `normalization` object: one leading UTF-8 byte-order mark removed, every CRLF turned into LF. `sha256` and `bytes` describe the normalized content. | `whykit snapshot` (default) |
| `whykit.snapshot/v1` | The raw bytes on disk. | `whykit snapshot --format v1`, and WhyKit releases before v2 |

Why v2: a baseline taken on Linux used to report every file as changed when
verified on a Windows checkout with `core.autocrlf=true`, or after an editor
added a byte-order mark, although nobody had changed a word. v2 treats those
as the same content. Anything else is still a change, including a lone CR, a
BOM that is not at the start of the file, whitespace and any edited character.

```json
{
  "format": "whykit.snapshot/v2",
  "contract_version": 1,
  "normalization": {"utf8_bom": "strip", "line_endings": "crlf-to-lf"},
  "snapshot_id": "…",
  "files": [{"path": "Home.md", "sha256": "…", "bytes": 1234}]
}
```

- Existing v1 baselines keep working unchanged: `verify-snapshot` hashes raw
  bytes for them, exactly as before. Re-create a baseline with
  `whykit snapshot` to get the portable format.
- A file with LF line endings and no BOM has the same `sha256` in both formats,
  and `snapshot_id` is computed the same way from it, so on such a vault the
  two formats give the same ID.
- A v2 snapshot whose `normalization` this WhyKit does not implement, or a
  `format` it does not know, is refused with `invalid_argument` (exit 2),
  never reported as drift.
- `verify-snapshot --json` echoes the baseline's `format`.
- Consumers that pinned `"format": "whykit.snapshot/v1"` should accept v2 as
  well, or produce baselines with `--format v1` until they do.

Schemas: [`snapshot.schema.json`](../schemas/snapshot.schema.json) and
[`snapshot-verify.schema.json`](../schemas/snapshot-verify.schema.json).
