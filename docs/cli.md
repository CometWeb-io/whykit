# Command reference

Every `whykit` command, what it does and the options it takes. Run
`whykit <command> --help` for the full help text of one command; this page is
checked against the same argument parser by the test suite, so a flag listed
here exists and every flag that exists is listed.

From a source checkout, prefix commands with `uv run` (see
[How-to guides](guide.md#run-whykit-from-a-checkout)).

## Global behaviour

- `whykit --version` (or `-V`) prints the version; `whykit --help` lists the
  commands grouped by job: **author** (`init`, `adopt`, `new`, `review`,
  `evidence`), **check** (`lint`, `check`, `status`, `workspace`, `trace`, `history`,
  `diff`, `snapshot`, `verify-snapshot`), **explore** (`query`, `context`,
  `pack`, `graph`, `backlinks`, `impact`), **integrate** (`install-hooks`,
  `explorer-index`, `serve`, `completion`) and **maintain** (`policy`, `rules`,
  `doctor`). The table below follows the same order.
- `--root DIR` selects one vault. Single-vault commands accept it
  after the command name, before the command name (`whykit --root DIR lint`),
  and, for `new`, `review` and `evidence`, before or after the action. `adopt`
  also spells it `--into`, and `serve` also takes the vault as an argument.
  Without it, WhyKit walks up from the current directory to the nearest
  `Home.md` plus `00-context/`.
- `--today YYYY-MM-DD` evaluates review dates as of a fixed day, so a result
  does not change overnight. Use it in CI when you need reproducibility.
- `--json` writes one JSON document to stdout; the contract, schemas and error
  objects are in [Automation](automation.md). Commands with several output
  formats take `--format` too, and there `--json` is the same as
  `--format json`. `snapshot` and `explorer-index` always write JSON and accept
  `--json` only for symmetry; `snapshot --format` picks the snapshot version,
  not an output format.
- An unknown option prints a `hint:` naming the help of the exact command you
  ran, e.g. `whykit review list -h`.
- Errors go to stderr with a `hint:` line naming the fix. Set `WHYKIT_DEBUG=1`
  to get a Python traceback for a bug report instead.
- Read commands keep a parse cache in the vault's `.whykit/cache/`, so a second
  run on a large vault skips notes that did not change. Output is identical
  with or without it. `--no-cache`, placed before the command
  (`whykit --no-cache lint`), or `WHYKIT_NO_CACHE=1` turns it off; deleting
  the directory is always safe. See [Performance](performance.md#parse-cache).

## Commands

| Command | Does | Options | More |
|---|---|---|---|
| `init <dir>` | Create a vault from the bundled template | `--force`, `--profile`, `--full`, `--minimal`, `--json` | [How-to](guide.md#create-a-vault) |
| `adopt <source>` | Inventory existing Markdown; stage it with `--write` or compare preservation with `--compare DIR` | `--into` (or `--root`), `--profile`, `--owner`, `--write`, `--compare`, `--json` | [How-to](guide.md#already-have-a-pile-of-markdown) |
| `new decision <title>` | Create a decision record and its decision-log row; with `--from FILE`, promote an existing ADR into one (a dry run that prints the mapping until `--write`) | `--from`, `--write`, `--owner`, `--status`, `--source`, `--supersedes`, `--review-by`, `--sensitivity`, `--json`, `--root` | [How-to](guide.md#promote-an-adopted-adr) |
| `new evidence` | Append a source to the evidence register | `--source`, `--type`, `--location`, `--claims`, `--date`, `--accessed`, `--sensitivity`, `--json`, `--root` | [Concepts](concepts.md#evidence) |
| `new note <title>` | Create a draft note in a workstream | `--workstream`, `--type`, `--owner`, `--sensitivity`, `--link-from`, `--json`, `--root` | [How-to](guide.md#create-records) |
| `review list` | Show upcoming and overdue reviews | `--due-days`, `--overdue-only`, `--owner`, `--today`, `--json`, `--root` | [How-to](guide.md#record-a-review) |
| `review approve <target>` | Preview the record, evidence and approval diff; apply only the reviewed snapshot | `--reviewer`, `--next-review`, `--today`, `--write`, `--expect-hash`, `--json`, `--root` | [How-to](guide.md#approve-a-decision) |
| `review record <target>` | Append a review event; `confirmed` moves `review_by` forward | `--reviewer`, `--outcome`, `--next-review`, `--note`, `--today`, `--json`, `--root` | [Concepts](concepts.md#the-review-cycle) |
| `evidence list` | List active and retired evidence | `--state`, `--json`, `--root` | [How-to](guide.md#retire-evidence) |
| `evidence retire <E-NNN>` | Retire a source without deleting its ID | `--why`, `--replaced-by`, `--today`, `--json`, `--root` | [How-to](guide.md#retire-evidence) |
| `lint [paths…]` | Check the vault, or some files in it; `--format` picks `text`, `json`, `sarif` (SARIF 2.1.0 for code scanning) or `github` (workflow annotations) | `--strict`, `--quiet`, `--json`, `--format`, `--no-orphans`, `--no-secrets`, `--today`, `--root` | [Rules](rules.md), [CI](ci.md) |
| `check` | Run a named policy gate (`local`, `ci`, `release` or your own; default `ci`); `--format` picks `text`, `json` or `github` | `--profile`, `--base`, `--head`, `--today`, `--json`, `--format`, `--root` | [CI](ci.md) |
| `status` | Summarize vault health and the review queue | `--due-days`, `--strict`, `--today`, `--json`, `--root` | [Concepts](concepts.md#the-review-cycle) |
| `workspace <roots…>` | Report independent vault health and review queues; explicit roots instead of `--root` | `--due-days`, `--strict`, `--today`, `--json` | [How-to](guide.md#check-several-vaults) |
| `trace` | Trace each decision to its evidence; flag missing, retired or stale sources | `--decision`, `--gaps-only`, `--max-age-days`, `--strict`, `--today`, `--json`, `--root` | [How-to](guide.md#trace-decisions-to-their-evidence) |
| `history` | Verify that accepted reasoning and earlier review events were not rewritten between two commits, or (`--staged`) in the staged changes, for pre-commit | `--base`, `--head`, `--staged`, `--json`, `--root` | [Concepts](concepts.md#lifecycle) |
| `diff` | Show what a change does to the decisions: new, superseded and archived decisions with their supersession chain, status and review-date moves, evidence added, retired or re-sourced, decisions citing changed evidence, and lint findings introduced or fixed. Reads both commits from Git without a checkout; `--format` picks `text`, `json`, `markdown` (a pull request comment) or `github` (workflow annotations) | `--base`, `--head`, `--today`, `--json`, `--format`, `--root` | [CI](ci.md#decision-diff-comment) |
| `snapshot` | Write a deterministic fingerprint of governed content; `--format v2` (default) normalizes line endings and a byte-order mark, `v1` hashes raw bytes | `--output`, `--compact`, `--format`, `--today`, `--json`, `--root` | [How-to](guide.md#snapshot-and-detect-drift) |
| `verify-snapshot <snapshot>` | Compare the vault with a snapshot | `--today`, `--json`, `--root` | [How-to](guide.md#snapshot-and-detect-drift) |
| `query [text]` | Search and filter records | `--type`, `--status`, `--owner`, `--sensitivity`, `--source`, `--tag`, `--canonical-only`, `--limit`, `--json`, `--root` | [How-to](guide.md#hand-bounded-context-to-an-agent) |
| `context <target>` | Build a bounded context pack for one record; a note over the `--max-chars` budget starts with its body, not its front matter | `--max-chars`, `--no-body`, `--json`, `--root` | [How-to](guide.md#hand-bounded-context-to-an-agent) |
| `pack [targets…]` | Build a budgeted multi-record bundle for an agent | `--query`, `--max-docs`, `--max-chars`, `--canonical-only`, `--format`, `--for`, `--json`, `--root` | [How-to](guide.md#hand-bounded-context-to-an-agent) |
| `graph` | Export typed relations as JSON, DOT, Mermaid or Obsidian-style JSON | `--format`, `--canonical-only`, `--output`, `--json`, `--root` | [How-to](guide.md#hand-bounded-context-to-an-agent) |
| `backlinks <target>` | List what links to a note, decision or evidence ID | `--json`, `--root` | [Obsidian](obsidian.md#graph-view-and-whykits-graph) |
| `impact <target>` | Show what depends on evidence, a decision or a document | `--json`, `--root` | [How-to](guide.md#retire-evidence) |
| `install-hooks` | Install a pre-commit hook that runs the `local` profile | `--force`, `--root` | [CI](ci.md#local-hooks) |
| `explorer-index` | Export the Explorer's vault index (always JSON) | `--today`, `--json`, `--root`, `--private` | [Automation](automation.md#schemas) |
| `serve [vault]` | Run the optional Explorer from a source checkout; an installed copy names the checkout it came from, or the source to clone | `--root`, `--host`, `--port`, `--allow-sensitive-network` | [How-to](guide.md#browse-the-vault-in-the-explorer) |
| `lsp` | Run the read-only language server over stdio, for editors: lint diagnostics, wikilink and ID completion, hover, go-to-definition and document links | `--root`, `--debounce`, `--today`, `--stdio` | [Editors](editors.md) |
| `completion <shell>` | Print a `bash`, `zsh` or `fish` completion script | | `eval "$(whykit completion zsh)"` |
| `policy` | Show the effective repository policy | `--json`, `--root` | [Configuration](configuration.md) |
| `rules [code]` | List the lint rules, or explain one; with a vault, its custom rules and overrides too | `--json`, `--markdown`, `--root` | [Rules](rules.md), [Team rules](configuration.md#team-rules) |
| `doctor` | Check prerequisites, integrity and review hygiene | `--today`, `--json`, `--root` | [Troubleshooting](troubleshooting.md) |

## `whykit-mcp`

The optional read-only MCP server is a separate command, installed with the
`mcp` extra: `whykit-mcp --root DIR [--max-sensitivity LABEL]
[--watch-interval SECONDS] [--http [--host HOST] [--port PORT] [--token-file PATH]]`.
It speaks stdio unless `--http` is given. See the [MCP server guide](mcp.md).

## Exit codes

| Code | Means |
|---|---|
| `0` | Success: no errors (and no warnings, under `--strict` or a strict profile) |
| `1` | The command ran and found a problem: findings that should fail a build, rewritten history, a missing target |
| `2` | The command could not run as asked: no vault, invalid configuration or input, a refused path, an unreadable Git baseline |
| `70` | WhyKit crashed: a bug, not a problem with the vault or the job (error code `internal_error`) |
| `130` | Interrupted (Ctrl-C) |

[Automation](automation.md#error-codes) maps every error code to its exit code.
