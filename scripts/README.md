# scripts/

Convenience entry points for working from a source checkout. The tooling itself
lives in `src/whykit/`.

## `whykit.py`

A three-line shim that puts `src/` on the import path and calls the same code the
installed `whykit` command runs. Use it when you are hacking on the tool and do
not want to reinstall between edits.

```bash
python3 scripts/whykit.py lint examples/northline --strict --today 2026-09-17
python3 scripts/whykit.py init /tmp/fresh-vault
```

Preferred from this checkout (matches CI):

```bash
uv sync --locked
uv run whykit lint examples/northline --strict --today 2026-09-17
```

WhyKit is not published to PyPI yet. Do not run `uv tool install whykit` or
`pipx install whykit` until a release is announced; those commands would resolve
the public package index, not install this source checkout. For now, clone this
repository and use the checkout-based commands above or the [README quick
start](../README.md#60-second-quickstart). After publication, the package install will be:

```bash
uv tool install whykit
```

## `bench.py`

Times the read commands on a vault, each in a fresh interpreter, and can record
or compare their output byte for byte. Without `--vault` it writes a
deterministic synthetic vault with `tests/synthetic_vault.py`. `--cache off`
(the default), `cold` or `warm` selects the state of the parse cache, and
`--runs N` keeps the best of N runs. See
[docs/performance.md](../docs/performance.md) for the numbers and method.

```bash
python3 scripts/bench.py --notes 5000                      # timings
python3 scripts/bench.py --notes 5000 --record /tmp/before # save outputs
python3 scripts/bench.py --notes 5000 --compare /tmp/before
python3 scripts/bench.py --vault examples/northline --profile /tmp/prof
python3 scripts/bench.py --notes 5000 --mcp                # MCP tool latency, cold and repeated
python3 scripts/bench.py --notes 5000 --memory             # peak traced memory per command
```

## `check_dist.py`

Checks the archives from `uv build` against the project's packaging promises:
version, `py.typed`, every vault-template file, PEP 639 licence metadata, zero
runtime dependencies, PyPI-safe README links and the sdist allowlist. With
`--reproducible-against` it compares two builds byte for byte; with `--sbom` it
checks a CycloneDX SBOM of a clean install. CI and the release workflow run it;
[docs/releasing.md](../docs/releasing.md) shows the local rehearsal.

```bash
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
uv build --out-dir dist && uv build --out-dir dist-rebuild
python3 scripts/check_dist.py dist --reproducible-against dist-rebuild
```

## `release_rehearsal.py`

A local dry run of a package release against one clean commit: version from
the changelog, two reproducible builds, `check_dist.py`, `twine check`, a
wheel install and smoke test on every supported Python installed here, release
notes, and the exact tag commands for the maintainer. It never tags, pushes or
uploads. See step 3 of [docs/releasing.md](../docs/releasing.md).

```bash
python3 scripts/release_rehearsal.py                  # full rehearsal, offline
python3 scripts/release_rehearsal.py --online         # first run on a cold uv cache
python3 scripts/release_rehearsal.py --skip-build     # version, notes and commands only
```

## `pr_comment.py`

Used by the composite Action when `comment: "true"`. Creates or updates the
single pull request comment that carries the output of
`whykit diff --format markdown`, found by the marker on its first line and
edited only when a bot account wrote it. It reads the token from
`GITHUB_TOKEN`, talks to the API at `GITHUB_API_URL` over HTTPS, and reports API
refusals as workflow warnings instead of failing the job. Standard library
only.

## CI helpers

`release-baseline.sh` prints the Git ref that the release-policy job in
`ci.yml` compares against: the pull request base, the previous version tag, or
the repository root. `check_commit_attribution.py` fails CI when a commit
identity or trailer names a code-generation tool instead of a person.

## Git hooks

```bash
sh scripts/install-hooks.sh          # for this repository
whykit install-hooks               # for a vault created by `whykit init`
```

`install-hooks.sh` links `.git/hooks/pre-commit` to `scripts/pre-commit`, which
lints the example vault with the repository policy.

Both run a whole-vault lint rather than checking only the staged files. Secrets
and broken cross-references do not respect a staging area: a credential pasted
into a CSV escapes a staged-files-only check the moment someone commits a
Markdown file alongside it.

## Lint rules

Rule codes are a public API — tooling filters on them, so renaming one is a
breaking change. Messages are human-facing and may be reworded.

| Check | Code prefix | Level |
|---|---|---|
| Required front matter, allowed `status` / `type` / `sensitivity` | `frontmatter.`, `status.`, `type.`, `sensitivity.` | error |
| `source_of_truth: true` only on approved documents | `canonical.unapproved` | error |
| ISO date validity and ordering | `date.` | error |
| Point-in-time reports carry a full date in the filename | `report.undated` | error |
| Wikilinks resolve; ambiguous stems need a path | `wikilink.` | error |
| `source_ids` map to populated evidence rows | `evidence.` | error |
| Decision record has one own ID matching its filename | `decision.id_` | error |
| Decision log and records agree on ID, status and record link | `decision_log.` | error |
| `delivery_status: sent` has `sent_at` | `delivery_status.` | error |
| Credential-shaped strings in text assets | `secret.detected` | error |
| Approved decision with no `review_by` | `decision.review_missing` | warning |
| Overdue `review_by` on an approved document | `review_by.overdue` | warning |
| Unanswered `TODO:` in `AGENTS.md`, or no contract at all | `agents.` | warning |
| `[!fact]` callout with no inline `E-NNN` | `fact.inline_evidence` | warning |
| Canonical document with no real owner | `canonical.owner` | warning |
| Note with no inbound wikilink | `note.orphan` | warning |
| Two note paths that differ only by case or Unicode normalization | `path.case_collision` | warning |

The linter checks structure and provenance links. It does not check whether a
claim is true.
