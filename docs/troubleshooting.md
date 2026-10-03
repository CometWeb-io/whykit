# Troubleshooting and FAQ

Each entry starts with what you see, then explains why and what to do. Commands
are written as `whykit …`; from a source checkout, use `uv run whykit …`.

## Running the CLI

### `whykit: command not found`

WhyKit is not on PyPI yet, so there is no global `whykit` until you install one.
Pick one:

- From the WhyKit checkout: `uv run whykit …`, with `--root` pointing at your
  vault.
- From anywhere: `uv run --project /path/to/whykit whykit …`.
- Install a binary from the checkout: `uv tool install --from /path/to/whykit whykit`.
  After that, the bare `whykit` works from inside any vault.

### `error: unrecognized arguments: --root …` with `new`, `review` or `evidence`

Older checkouts only accepted `--root` on the parent of these
three commands. Current versions accept it in either position, so update your
checkout, or put it **before** the subcommand, which works in every version:

```bash
whykit new --root ../my-ledger decision "Ship SSO before audit logs"
whykit review --root ../my-ledger list --due-days 30
whykit evidence --root ../my-ledger list --state active
```

Inside the vault directory you can leave `--root` out entirely.

### `not a WhyKit vault` or `no WhyKit vault found` (exit code 2)

WhyKit recognizes a vault by `Home.md` plus a `00-context/` directory. Without
`--root` it walks up from the current directory looking for them. Either `cd`
into the vault, pass `--root`, or create one with `whykit init <dir>`.

### `refusing to write into non-empty directory`

`whykit init` will not scatter starter files into a directory that already has
content. If you have reviewed the destination, `whykit init --force <dir>` adds
only the missing starter files. It never replaces existing notes, `AGENTS.md` or
`whykit.toml`, and it keeps your `.gitignore` rules while appending WhyKit's
protective defaults. It is not a reset or an upgrade command.

`init` also refuses to write through a symlink or into the WhyKit package source
tree. Create the vault somewhere else, preferably as its own Git repository.

## Lint and policy gates

### A brand-new vault shows four `agents.unconfigured` warnings

That is expected. `AGENTS.md` ships with four questions only you can answer
(reply language, branching rule, tone-of-voice owner, extra safety rules).
Answer each `TODO:` and delete the *Configure before use* section; the warnings
go away. See `whykit rules agents.unconfigured`.

### `whykit check --profile ci` fails on a vault with zero errors

The `ci` profile sets `strict = true`, so warnings fail the gate. On a new vault
those are the `AGENTS.md` questions above. Use `--profile local` while drafting;
use `ci` once the warnings are real work rather than setup.

### `whykit check --profile release` fails on `policy_configuration`, `clean_tree` or `history`

The release profile is the strictest built-in gate:

- `policy_configuration`: `whykit.toml` still has a placeholder. Set
  `defaults.owner` to a real person or team.
- `clean_tree`: commit or discard local changes. Do not stash them to get past
  the check: stashed edits are simply not checked.
- `history`: the profile requires a baseline. Pass `--base <ref>`, for example
  `--base origin/main`.

### Lint is green locally but red in CI (or the other way round)

The likely cause is review dates. `review_by.overdue` depends on today's date,
so the same commit can pass on Monday and fail on Tuesday. Pass
`--today YYYY-MM-DD` to `lint`, `check` or `status` when you need a reproducible
result. The example vaults in this repository are pinned this way.

### `note.orphan` on a note I just created or imported

Nothing links to the note. Link it from `Home.md` or a folder map. For new notes,
`whykit new note "Title" --workstream notes --link-from Home.md` creates the note
and the link together. `whykit adopt --write` creates an ingestion record under
`notes/`, which needs a link as well.

### `secret.detected` on something that is not a secret

The scanner is heuristic and errs toward flagging. Rewrite the example so it does
not look like a credential (use obviously fake values and reserved domains such
as `example.com`). If a real credential was committed, rotating it matters more
than removing the line: Git history keeps it. `whykit lint --no-secrets` turns
the scan off for one run; do not make that your CI default.

## Decision history

### `Historical decision reasoning is append-only. Supersede; do not rewrite`

A commit changed an `approved`, `superseded` or `archived` decision record beyond
the one allowed transition. Undo the edit to the old record and create a new one
instead:

```bash
whykit new decision "New title" --owner Platform --status approved \
  --source E-001 --supersedes D-001
```

That marks the old record `superseded`, sets `superseded_by`, and updates the
decision log, which is the only change the history check accepts on an accepted
record. A typo fix counts as a rewrite too; if it really matters, record it in a
superseding decision.

### `History check requires actions/checkout with fetch-depth: 0`, `unknown Git revision` or `cannot find the merge base`

The history check needs the pull request's base commit and the history between
it and `HEAD`. Set `fetch-depth: 0` on `actions/checkout`, or run
`git fetch --unshallow` (or `git fetch origin main`) in a shallow clone. These
exit 2 with the error code `git_error`. See [Running WhyKit in CI](ci.md).

### `warning: no decision records or review log under … in either revision`

`history` compared the two commits but found nothing to check under `--root`.
When the vault is a subdirectory of the repository, pass it:
`whykit history --base origin/main --root path/to/vault`.

### `whykit history` passed, but I had edited an accepted record

`history` compares two commits (`--base` and `--head`, default `HEAD`).
Uncommitted edits in the working tree are not part of either. Commit, then run
it, or let the pre-commit hook and CI do it.
With the default `--head HEAD` it says so on stderr (`note: … uncommitted
Markdown change(s) were not checked`).

## Files and Git

### `.whykit/mutation.lock` or `.whykit/transactions/` shows up in `git status`

These are WhyKit's write lock and crash-recovery journal, not vault content.
Vaults created by current versions ignore them. For an older vault, add these
lines to its `.gitignore`:

```gitignore
.whykit/mutation.lock
.whykit/transactions/
```

### `file is read-only; make it writable before WhyKit updates it`

A command that edits the vault (`new`, `review record`, `evidence retire`)
found a file it must rewrite marked read-only. It stops before writing
anything, so nothing is half-applied. Make the file writable and rerun.

### `path.case_collision` warning

Two notes differ only by letter case or Unicode normalization
(`Plan.md` and `plan.md`, or two spellings of `café.md`). Linux keeps both;
a checkout on macOS or Windows keeps one of them. Rename or merge one.

### Odd characters such as `\u0142` or `->` in the terminal on Windows

A console on a legacy code page (`cp1252`) cannot show every character. Human
output falls back to ASCII spellings and escapes instead of crashing. JSON,
DOT and Mermaid output stay UTF-8, so redirect them to a file and read that as
UTF-8. Setting `PYTHONUTF8=1` gives a UTF-8 console.

### Where did my snapshot go?

`whykit snapshot --output` and `whykit graph --output` resolve the path relative
to the **vault root**, not the current directory, and refuse to write outside the
vault. `whykit snapshot --root vault --output .whykit/snapshot.json` writes
`vault/.whykit/snapshot.json`.

### The pre-commit hook never blocks anything

`whykit install-hooks` writes a hook that calls `whykit` on `PATH`. If WhyKit is
only available through `uv run`, the hook prints a notice and lets the commit
through. Install a binary (see the first entry on this page). The hook runs the
`local` profile, which blocks errors but not warnings.

### `whykit-mcp needs the optional MCP extra`

The MCP server ships as an optional extra. From a checkout run
`uv sync --extra mcp`, then `uv run whykit-mcp --root /path/to/vault`. See the
[MCP server guide](mcp.md).

## Questions

### Is WhyKit on PyPI?

Not yet. It is a source preview. Install from a checkout as shown in the
[README](../README.md#60-second-quickstart). Do not install a package called
`whykit` from an index until a release is announced in this repository.

### Does WhyKit send anything over the network?

No. There is no telemetry, no account and no service. The test suite enforces
this by disabling sockets and running every vault command (everything except
`serve`, which starts the optional local Explorer).

### Do I need Obsidian?

No. The vault is plain Markdown and Git. Obsidian is a comfortable editor for it;
see [Using WhyKit with Obsidian](obsidian.md).

### Does a green lint mean the decisions are right?

No. WhyKit checks that evidence is cited, indexes agree, accepted reasoning was
not rewritten and reviews are not overdue. It cannot tell whether a source is
honest or a decision was sound. See [Concepts](concepts.md#what-the-linter-checks-and-what-it-cannot).

### We already have ADRs. Do we start over?

No. `whykit adopt <dir> --into <vault> --profile adr-only` inventories them
first as a dry run. `--write` stages copies under `.import-staging/` with SHA-256
hashes and an ingestion record. A person still decides what becomes canonical.

### Can I keep the vault inside another repository?

Yes. Point `--root` (or the Action's `root` input) at the subdirectory. Keep it
out of the WhyKit source checkout itself, and keep a real company vault private:
sensitivity labels are metadata, not access control.
