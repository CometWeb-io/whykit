# Contributing to WhyKit

WhyKit optimizes for a small, auditable core rather than feature count.
Contributions should preserve the invariant that Markdown is the canonical data.

## Layout

| Path | What it is |
|---|---|
| `src/whykit/` | CLI, linter, history check, adopt |
| `src/whykit/template/` | The vault `whykit init` writes |
| `schemas/` | The data contract for integrations |
| `examples/tiny/` | Smallest example vault, same layout as `whykit init` |
| `examples/northline/` | Fully linked synthetic example vault, linted strictly in CI |
| `docs/` | User documentation |
| `apps/explorer/` | Optional read-only viewer |
| `tests/` | Regression suite |

The repository is not itself a vault. The two example vaults and a freshly
generated vault are what CI checks.

## Before opening a pull request

Requires [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run ruff check
uv run mypy
uv run zizmor --offline .   # when touching .github/ or action.yml
uv run whykit lint examples/northline --strict --today 2026-09-17
fresh_parent="$(mktemp -d)"
fresh_vault="$fresh_parent/vault"
uv run whykit init "$fresh_vault"
uv run whykit lint --root "$fresh_vault"
```

`scripts/whykit.py` still works without an install if you prefer
`python3 scripts/whykit.py …` from a source checkout.

A generated vault must show **zero errors**. The only warnings it may show are
the unanswered `AGENTS.md` questions the adopter still owes.

`whykit check --profile release` additionally requires a clean Git working tree
and a configured `whykit.toml` (no `TODO` placeholders). Run it only against the
exact clean candidate commit you want to verify. Do not stash uncommitted work to
satisfy the clean-tree check: those stashed changes are not checked. While
editing, use the `local` profile; use `ci` for the stricter pull-request gate.

For changes to the optional MCP server, also install and run its real stdio
client test (the core dependency set intentionally omits the SDK):

```bash
uv sync --locked --extra mcp
uv run python -m unittest discover -s tests -p 'test_mcp_runtime.py' -v
```

For Explorer changes:

```bash
npm ci --prefix apps/explorer
npm --prefix apps/explorer run check
npm --prefix apps/explorer run build
npm --prefix apps/explorer exec -- playwright install chromium
npm --prefix apps/explorer run e2e
```

See [`apps/explorer/README.md`](apps/explorer/README.md) for what the
end-to-end suite covers.

## Changing documentation

Documentation is tested like code. `tests/test_docs.py` parses every `whykit`
command in the README, `CONTRIBUTING.md`, `docs/` and the example READMEs against
the real argument parser, executes the README quickstart in a temporary
directory, and checks that relative links resolve. If you document a new flag or
command, that test tells you when the docs and the CLI disagree. Write commands
the way a reader will type them, and use reserved example domains.

## Good contribution areas

- a failing linter fixture plus a focused fix;
- a false positive you hit in a real vault — these matter more than new rules,
  because a noisy linter teaches people to pass `--no-verify`;
- schema compatibility and migration tests;
- provenance-preserving import adapters;
- accessibility/performance improvements to Explorer;
- synthetic example vaults, using reserved domains only (`example.com`,
  `.example`, `.invalid`, `.test`) — a realistic-looking domain in a public
  fixture usually belongs to somebody;
- documentation that removes ambiguity in a governance rule.

## Boundaries

Do not add:

- a second database or source of truth for vault content;
- telemetry without an explicit design discussion and opt-in model;
- generated claims presented as verified facts;
- private corpora, customer data, credentials or internal IDs;
- an AI provider requirement for core linting/navigation.

## Changing a lint rule

1. New rules land as warnings. Promote to error only with the reason written down.
2. Every rule needs a fixture for what it catches *and* for what it must not.
3. Rule codes are a public API. Renaming one is a breaking change and belongs in
   `CHANGELOG.md`.

## Changing the format

`src/whykit/template/`, `schemas/` and the directory layout migrate every vault
in existence when they change. Prefer an optional key inside the `provenance`
block, which is explicitly open for extension — see `docs/integrations.md`.

## Decision records

Accepted `06-decisions/d-NNN-*.md` files are treated as historical records. Do not
rewrite an accepted record in place. Create a new decision that supersedes it and
update the index. CI checks modifications to existing decision records on pull
requests.

## Pull requests

Keep PRs narrow. Include:

1. the problem and failure mode;
2. evidence or a reproducible fixture;
3. the behavior before and after;
4. commands used to verify the change;
5. any format/schema compatibility impact.
