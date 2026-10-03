# WhyKit

**The layer that remembers why.**

> Git-native evidence and decision ledger for teams and AI agents.

WhyKit keeps sources, decisions, their reasoning, and review events together in
plain Markdown and Git. It gives people and tools a shared, inspectable record of
what is known, what was decided, and what still needs checking.

![WhyKit links evidence to a decision, then checks its structure and review date.](docs/media/overview.svg)

**Python 3.11+ · No runtime dependencies · Apache-2.0**

**Status:** source preview `0.3.0.dev0`. No GitHub Release and no public PyPI release yet. Install from a source checkout. This checkout is newer than the internal 0.2.0 milestone and is not a tagged release.

[![CI](https://github.com/CometWeb-io/whykit/actions/workflows/ci.yml/badge.svg)](https://github.com/CometWeb-io/whykit/actions/workflows/ci.yml)

## 60-second quickstart

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
WhyKit is not on PyPI yet, so run it from a checkout. The vault goes *next to*
the checkout, never inside it.

<!-- quickstart:start -->
```bash
git clone https://github.com/CometWeb-io/whykit.git
cd whykit
uv sync --locked

# 1. Create a vault.
uv run whykit init ../my-ledger

# 2. Register a source, then a decision that cites it.
uv run whykit new --root ../my-ledger evidence \
  --source "Support ticket export, Q3" --type dataset \
  --location "https://example.com/exports/q3-tickets.csv" \
  --claims "Most onboarding tickets mention SSO"
uv run whykit new --root ../my-ledger decision "Ship SSO before audit logs" \
  --owner Platform --status approved --source E-001

# 3. Check the vault and see what is due for review.
uv run whykit lint --root ../my-ledger
uv run whykit review --root ../my-ledger list --due-days 120
```
<!-- quickstart:end -->

What you should see:

```text
created E-001: 00-context/evidence-register.md
created D-001: 06-decisions/d-001-ship-sso-before-audit-logs.md
...
24 files — 0 error(s), 5 warning(s)
DUE     <today + 90 days>  06-decisions/d-001-ship-sso-before-audit-logs.md  (Platform)
```

The vault now holds an evidence row (`E-001`), a decision record that cites it
(`D-001`), a matching decision-log entry, and a review date 90 days out. Four of
the warnings are the questions in the vault's `AGENTS.md` that only you can
answer (reply language, branching rule, tone-of-voice owner, safety rules).
The fifth, `decision.placeholder`, says the new record still holds the
scaffold's prompts: open it and write the real context, decision, rationale,
alternatives and consequences. Do both before you let an agent write in the
vault, or before you turn on the strict `ci` gate.

`--root` can go before or after the subcommand (`whykit new --root ../my-ledger
decision ...` and `whykit new decision ... --root ../my-ledger` both work).
Inside the vault directory you can drop `--root` altogether.

Next: write the real context and rationale into `06-decisions/d-001-*.md`,
`git init` the vault, and commit it. Then follow
[Record, check and supersede a decision](docs/tutorials/record-and-supersede.md)
(ten minutes) and [Gate pull requests in GitHub Actions](docs/tutorials/gate-pull-requests.md).

## Why WhyKit

Teams can find old documents but rarely tell which evidence supported a
decision, what replaced an earlier choice, or when anyone last checked that the
reasoning still holds. WhyKit makes those links explicit and checks them:

```text
source (E-001) → decision (D-001) → dated review events
```

- **Evidence stays traceable.** Each source gets a stable ID and a location a
  person can inspect.
- **Accepted reasoning stays intact.** You supersede a decision instead of
  rewriting it, and CI rejects a diff that edits accepted reasoning.
- **Reviews have dates.** Every approved decision carries a `review_by` date,
  and overdue ones fail the strict gate.
- **The same rules run everywhere.** One `whykit.toml` drives local runs, CI,
  the pre-commit hook and agents, with a versioned JSON contract and a
  read-only MCP server for tools.

WhyKit checks structure and traceability, **not whether a source or conclusion
is true**. It is not an AI memory service, a vector database or an
access-control boundary; [Positioning](docs/project-positioning.md) says when
plain ADRs are the better choice.

## Documentation

Start at the [documentation index](docs/README.md). The most used pages:

- [Concepts](docs/concepts.md): evidence, decisions, supersession and the review cycle.
- [How-to guides](docs/guide.md): adopt existing Markdown, manage evidence, hand context to agents.
- [CI integration](docs/ci.md) and [troubleshooting](docs/troubleshooting.md).
- [Command reference](docs/cli.md), [configuration](docs/configuration.md) and [lint rules](docs/rules.md).
- [Automation (JSON contract)](docs/automation.md) and the [MCP server](docs/mcp.md).

The example vaults show the format at two scales: [tiny](examples/tiny/) and
the fully linked, fictional [Northline](examples/northline/).

[Contributing](CONTRIBUTING.md) · [Code of Conduct](CODE_OF_CONDUCT.md) ·
[Security policy](SECURITY.md) · [Release checklist](.github/RELEASE.md)

Copyright 2026 Maciej Zmitrukiewicz (hello@cometweb.io), licensed under
[Apache-2.0](LICENSE). [CometWeb](https://cometweb.io) is the product name, not
the copyright holder.
