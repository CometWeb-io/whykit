# WhyKit documentation

WhyKit helps you record decisions, link their sources and review them later.
It keeps those records in Markdown and provides a CLI that checks their links.
Choose a page below for the task you want to do.

Install the published CLI with `uv tool install whykit` (Python 3.11+ and
uv 0.13.4+). Commands are written as `whykit …`. From a source checkout, run them as
`uv run whykit …` (see [Run WhyKit from a checkout](guide.md#run-whykit-from-a-checkout)).

## Start

Learn by doing. Every command in these pages runs in the test suite.

| Page | You will |
|---|---|
| [Quickstart](../README.md#try-it) | Create a vault, cite a source, record a decision |
| [Connect your AI agents](agents.md) | Give Claude Code, Cursor, Codex or VS Code read-only access to your decisions and tell them to check before changing code |
| [Catch decision drift](tutorials/catch-decision-drift.md) | Map a code change to the decisions it touches and see a decision flagged when its source is replaced |
| [Import, review and export](tutorials/review-and-export.md) | Import an ADR as a draft, trace changed evidence and export public data with the review warning intact |
| [Record, check and supersede a decision](tutorials/record-and-supersede.md) | Change your mind with a new decision and watch the history check refuse a rewrite |
| [Gate pull requests with `whykit check`](tutorials/gate-pull-requests.md) | Add the GitHub Action and see a good and a bad pull request |
| [Declared outcomes example](../examples/outcomes/README.md) | Inspect synthetic baseline, target and observed values with exact source pins |
| [Captured provenance example](../examples/provenance/README.md) | Inspect a synthetic captured source, restricted claim and content-pinned approval |

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
| [Structural agent preflight](agent-preflight.md) | Inspect declared decision dependencies without authorizing execution |
| [Local multi-vault status](workspace.md) | A private local manifest and lint/review counts across explicitly listed vaults |
| [Saved GitHub exports](github-import.md) | Import one untrusted issue, PR or workflow-run REST payload with a bound E-ID |
| [Claims and captured sources](claims.md) | Exact byte anchors, immutable local captures and content-bound draft claims |
| [Declared outcomes](outcomes.md) | Record measurements separately from targets and causal conclusions |
| [Knowledge v2 migration](knowledge-v2.md) | Inspect five typed objects and preserve raw bytes through a private bundle and restore |
| [Field evaluation](field-evaluation.md) | What replaying four real ADR repositories showed, what changed because of it and what is still unknown |
| [Affected decisions](affected.md) | `applies_to` scopes, which decisions a change touches, their review signals and a pull-request recipe |
| [Compiled context](compiled-context.md) | A total output byte budget, scoped manifest and current-input verification |
| [Dependency graph](dependency-graph.md) | Bounded multi-level reverse dependency paths and explicit review signals |
| [Temporal graph](temporal-graph.md) | Pinned Git snapshots and separately declared validity intervals |
| [Writing to a vault from another system](integrations.md) | The `provenance` block and how a producer maps its output onto records |

## Reference

Exact behaviour, checked against the code by the test suite.

| Page | Lists |
|---|---|
| [Command reference](cli.md) | Every command and option, and the exit codes |
| [Configuration](configuration.md) | `whykit.toml` keys and profiles, document front matter, evidence access age |
| [Data classification and exports](data-classification.md) | Evidence row labels, public exports and explicit private viewers |
| [Lint rules](rules.md) | Every rule code, its severity and the default fix |
| [Automation](automation.md) | The `--json` contract: error codes, exit codes, schemas and the stability policy |
| [Opt-in retrieval](retrieval.md) | BM25 and typed-graph retrieval, explicit temporal scope and reproducible comparisons |
| [MCP tools, resources and prompts](mcp.md#tools) | Input schemas, return shapes and error codes of the MCP server |
| [Offline agent evaluation](agent-evaluation.md) | Supplied-response rubric, citation and abstention scoring, and benchmark limits |

## Operations

| Page | For |
|---|---|
| [Troubleshooting and FAQ](troubleshooting.md) | Error messages, surprising results and quick answers |
| [Performance](performance.md) | Measured times on large vaults and how to reproduce them |
| [Migrating to 0.3](migration-0.3.md) | Upgrading a vault, scripts and CI from 0.2.0 or the 0.3.0 preview |
| [Upgrading from 0.3.4 to 0.4](migration-0.4.md) | Back up a vault, test an upgrade and recover a pre-upgrade copy |
| [0.4 feature status](feature-status.md) | Core and preview features and the limits of their guarantees |
| [Releasing](releasing.md) | The package release checklist and reproducible builds |
| [Security model](security-model.md) | What WhyKit protects, the trust boundaries, and what it does not guarantee |
| [Security policy](../SECURITY.md) | Reporting a vulnerability and keeping a real vault private |
| [Contributing](../CONTRIBUTING.md) | Repository layout, checks to run and how docs are tested |
