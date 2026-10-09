# WhyKit documentation

WhyKit is a Git-native evidence and decision ledger: sources, decisions and
review events in plain Markdown, with a CLI that checks how they link. The
pages below are grouped by what you are trying to do. Each page has one job,
and the others link to it instead of repeating it.

Commands are written as `whykit …`. From a source checkout, run them as
`uv run whykit …` (see [Run WhyKit from a checkout](guide.md#run-whykit-from-a-checkout)).

## Start

Learn by doing. Every command in these pages runs in the test suite.

| Page | You will |
|---|---|
| [60-second quickstart](../README.md#60-second-quickstart) | Create a vault, cite a source, record a decision |
| [Record, check and supersede a decision](tutorials/record-and-supersede.md) | Change your mind with a new decision and watch the history check refuse a rewrite |
| [Gate pull requests with `whykit check`](tutorials/gate-pull-requests.md) | Add the GitHub Action and see a good and a bad pull request |

## Concepts

Understand the model before you rely on it.

| Page | Explains |
|---|---|
| [Concepts](concepts.md) | Evidence, decisions, supersession, the review cycle, and what the linter cannot check |
| [Positioning](project-positioning.md) | What WhyKit is, when plain ADRs are the better choice, and what it does not promise |

## How-to guides

Recipes for a specific job.

| Page | Covers |
|---|---|
| [How-to guides](guide.md) | Run from a checkout, create a vault, adopt existing Markdown, create records, review, retire evidence, hand context to agents, trace, snapshot, Explorer |
| [Running WhyKit in CI](ci.md) | The GitHub Action, its inputs and outputs, the release gate, other CI systems, the pre-commit hook |
| [Using WhyKit with Obsidian](obsidian.md) | Settings, links, callouts and the graph view |
| [Editor integration](editors.md) | Lint results, completion, hover and go-to-definition in any editor with a language-server client |
| [MCP server](mcp.md) | Exposing a vault read-only to an MCP host, with sensitivity limits |
| [Writing to a vault from another system](integrations.md) | The `provenance` block and how a producer maps its output onto records |

## Reference

Exact behaviour, checked against the code by the test suite.

| Page | Lists |
|---|---|
| [Command reference](cli.md) | Every command and option, and the exit codes |
| [Configuration](configuration.md) | `whykit.toml` keys and profiles, document front matter, evidence access age |
| [Lint rules](rules.md) | Every rule code, its severity and the default fix |
| [Automation](automation.md) | The `--json` contract: error codes, exit codes, schemas and the stability policy |
| [MCP tools, resources and prompts](mcp.md#tools) | Input schemas, return shapes and error codes of the MCP server |

## Operations

| Page | For |
|---|---|
| [Troubleshooting and FAQ](troubleshooting.md) | Error messages, surprising results and quick answers |
| [Performance](performance.md) | Measured times on large vaults and how to reproduce them |
| [Private-vault dogfood](dogfood.md) | A source-bound local acceptance record without publishing vault contents |
| [Migrating to 0.3](migration-0.3.md) | Upgrading a vault, scripts and CI from 0.2.0 or the 0.3.0 preview |
| [Releasing](releasing.md) | The package release checklist and reproducible builds |
| [Security model](security-model.md) | What WhyKit protects, the trust boundaries, and what it does not guarantee |
| [Security policy](../SECURITY.md) | Reporting a vulnerability and keeping a real vault private |
| [Contributing](../CONTRIBUTING.md) | Repository layout, checks to run and how docs are tested |

- [Optional reviewed claims](claims.md): opt-in C records, local fragments, four assessment states, receipts and parallel v2 reports.
