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
start](../README.md#start-here). After publication, the package install will be:

```bash
uv tool install whykit
```

## Git hooks

```bash
sh scripts/install-hooks.sh          # for this repository
whykit install-hooks               # for a vault created by `whykit init`
```

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

The linter checks structure and provenance links. It does not check whether a
claim is true.
