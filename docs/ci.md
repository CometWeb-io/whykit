# Running WhyKit in CI

The same checks you run locally can gate pull requests. There are two ways to
wire them up: the composite GitHub Action in this repository (`action.yml`), or
the CLI called directly from any CI system. Both read the policy from the vault's
own `whykit.toml`, so local runs, CI and agents agree on what "passing" means.

## Before you start

- **Answer the `AGENTS.md` questions first.** A fresh vault has zero errors but
  four `agents.unconfigured` warnings. The `ci` profile treats warnings as
  failures, so `profile: ci` fails on an untouched vault. That is deliberate:
  answer the questions, delete the *Configure before use* section, and the gate
  goes green.
- **Fetch full history.** The history check compares the pull request against
  its base commit. With a shallow clone it cannot, and the Action fails closed
  rather than skipping the check.
- **Pin by commit SHA.** WhyKit has no tagged release yet. Reference a reviewed
  40-character commit SHA, never a branch.

## GitHub Actions: the composite Action

The Action installs WhyKit from the exact revision you referenced (not from a
package index), then runs either `whykit check` (profile mode) or `whykit lint`
plus `whykit history` (legacy mode). It installs into a private virtual
environment under the runner's temp directory, so it never adds packages to the
Python your later steps use. It needs Python 3.11+ on `PATH`, so set Python up
explicitly.

### Pull request gate

```yaml
# .github/workflows/whykit.yml in the vault repository
name: WhyKit
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  vault:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - uses: CometWeb-io/whykit@<reviewed-40-character-commit-sha>
        with:
          root: .
          profile: ci
```

On `pull_request` events the Action passes the PR's base SHA to
`whykit check --base`, so accepted decision reasoning and earlier review-log rows
are verified against the diff. On `push` events there is no base, and the `ci`
profile (where history is `optional`) runs the lint gate only.

If the vault lives in a subdirectory of a larger repository, set `root` to that
directory, for example `root: knowledge`.

### Inputs

| Input | Default | Meaning |
|---|---|---|
| `root` | `.` | Vault root, relative to the repository. |
| `profile` | empty | A profile from `whykit.toml` (`local`, `ci`, `release`, or your own). Empty selects legacy mode. |
| `history` | `"true"` | On pull requests, check that accepted reasoning and review events were not rewritten. Needs `fetch-depth: 0`. |
| `base` | empty | Explicit Git baseline for `whykit check --base` in profile mode. Empty means "the PR base on pull requests, nothing otherwise". |
| `strict` | `"false"` | Legacy mode only: treat warnings as failures. Prefer `profile: ci`. |
| `today` | empty | Evaluate review dates as of this `YYYY-MM-DD` instead of the runner clock. Useful for example or archived vaults. |

`history` and `strict` accept only the strings `"true"` and `"false"`, and
`today` must be a real `YYYY-MM-DD` date. Anything else exits with code 2 so a
typo cannot silently turn a check off.

### Outputs

| Output | Meaning |
|---|---|
| `version` | The WhyKit version the Action installed and ran, as printed by `whykit --version`. Read it as `${{ steps.whykit.outputs.version }}` when the step has `id: whykit`. |

### Release gate

The `release` profile also requires a Git work tree, a clean working tree, a
`whykit.toml` with no `TODO` placeholders (set `defaults.owner`), and an explicit
history baseline. Outside a pull request, give it one:

```yaml
- uses: CometWeb-io/whykit@<reviewed-40-character-commit-sha>
  with:
    root: .
    profile: release
    base: HEAD^
```

Run it against the exact commit you mean to publish. Do not stash local edits to
get a clean tree: stashed changes are not checked.

### Legacy mode

Workflows that leave `profile` empty keep the original behaviour: `whykit lint`
(with `--strict` when `strict: "true"`) and, on pull requests, `whykit history`
against the PR base. New workflows should use `profile`, so the policy lives in
`whykit.toml` instead of in workflow flags.

## Any CI system: call the CLI

The CLI has no runtime dependencies, so any image with Python 3.11+ works.
Install WhyKit from a pinned checkout, then run the named gate:

```bash
python -m pip install "git+https://github.com/CometWeb-io/whykit@<reviewed-40-character-commit-sha>"
whykit check --root . --profile ci --base origin/main --head HEAD
```

`--base` must be a ref the CI clone actually has. In GitLab CI or similar, that
usually means fetching the target branch first (for example
`git fetch origin main`).

Use `--today YYYY-MM-DD` if you need the result to be reproducible regardless of
when the job runs. Without it, a `review_by` date passing overnight can turn a
green pipeline red with no commit, which is usually what you want on a scheduled
job and rarely what you want when bisecting.

For machine-readable output, add `--json`.

## Exit codes

| Code | Means |
|---|---|
| `0` | The gate passed. |
| `1` | The vault has findings that should fail the build. |
| `2` | The check could not run: no vault at `--root`, invalid `whykit.toml`, an unknown profile, or a bad input value. `whykit history` also exits `2` when it cannot read the `--base` ref. |

One asymmetry to know about: `whykit check` reports an unreadable `--base` ref as
a failed `history` check and exits `1`, not `2`. Either way the build fails; read
the `history` line of the output to tell a rewritten record from a missing ref.

Treat `2` as an infrastructure failure, not as "the vault has problems". A
pipeline that confuses the two will eventually report a check that never ran as
a check that failed, and people will stop reading it.

## Local hooks

`whykit install-hooks` writes a Git `pre-commit` hook that runs
`whykit check --profile local` before every commit. The `local` profile reports
warnings without blocking, so the hook only stops commits that introduce errors.

The hook calls the `whykit` executable on `PATH`. If you run WhyKit from a source
checkout with `uv run`, `whykit` is not on `PATH` and the hook prints a notice
and lets the commit through. Install a `whykit` binary (for example
`uv tool install --from /path/to/whykit-checkout whykit`) if you want the hook
to enforce anything.
