# Connect your AI agents

Coding agents are good at changing code and bad at knowing why it is the way it
is. They see the current files, not the support tickets, the benchmark or the
meeting that settled a choice, so they "fix" things that were deliberate.

`whykit connect` closes that gap in one command. It gives the agent read-only
access to your decisions through the MCP server and adds a short block to the
code repository's `AGENTS.md` or `CLAUDE.md` telling it to check them before
changing what they cover.

## Two minutes, end to end

Install the CLI with the MCP extra, create a vault next to your code, and
connect it:

```bash
uv tool install --upgrade 'whykit[mcp]'
whykit init ../decisions
whykit connect --root ../decisions --host claude-code
```

The last command changes nothing yet. It prints:

1. the server configuration for the host you named, with the absolute path of
   `whykit-mcp` and of the vault filled in; and
2. a preview of the instruction block for the repository in the working
   directory (`CLAUDE.md` for Claude Code, `AGENTS.md` for every other host).

Paste the configuration into your host, then write the block:

```bash
whykit connect --root ../decisions --host claude-code --write
```

Commit the block with your code. Run the same command again whenever you
upgrade WhyKit: it replaces only what sits between `<!-- whykit:start -->` and
`<!-- whykit:end -->`, reports `unchanged` when there is nothing to do, and
never touches the rest of the file. Use `--repo DIR` to target a repository
other than the working directory.

## What the agent is told

The block is short on purpose. It asks the agent to:

1. run `whykit affected --files …` on the files it plans to change, to list
   the decisions that govern them and any flagged for review (see
   [affected decisions](affected.md));
2. `query` the vault for wider context, then `trace` or `context` the
   decisions it finds;
3. follow an accepted decision, or stop and name it (`D-NNN`) when a change
   would contradict it or the decision is flagged for review;
4. never edit an accepted decision's reasoning, and draft a successor with
   `whykit new decision "<title>" --supersedes D-NNN --status draft` instead,
   leaving approval to a person; and
5. cite the decision and evidence IDs that informed a change in the commit
   message or pull request.

The vault path in the block is relative to the repository (for example
`../decisions`), so it never commits anyone's home directory.

## Hosts

| `--host` | Paste the configuration into | Instruction file |
|---|---|---|
| `claude-code` | your terminal: the printed `claude mcp add whykit -- …` command | `CLAUDE.md` |
| `cursor` | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | `AGENTS.md` |
| `vscode` | `.vscode/mcp.json` | `AGENTS.md` |
| `codex` | `~/.codex/config.toml` | `AGENTS.md` |
| `claude-desktop` | `claude_desktop_config.json` | `AGENTS.md` |
| `generic` | your host's MCP configuration (the common `mcpServers` JSON shape) | `AGENTS.md` |

Host configuration formats change; when in doubt, check the host's own MCP
documentation and keep the command and arguments `whykit connect` printed.
WhyKit never writes host configuration files: they hold user-specific absolute
paths and often other servers.

## Choose what the agent may read

The server returns records up to `--max-sensitivity` (default `internal`):

```bash
whykit connect --root ../decisions --host cursor --max-sensitivity public
```

The ceiling is a response filter, not access control: the server runs with
your operating-system permissions, and repository permissions remain the real
boundary. See [MCP server](mcp.md#sensitivity) and
[data classification](data-classification.md).

## Gate what the agent changes

Instructions are a request, not a guarantee. Two checks hold the line even when
an agent ignores them:

- the history check refuses edits to accepted decisions in a pull request; see
  [gate pull requests](tutorials/gate-pull-requests.md);
- `whykit agent preflight` checks a proposed change against the decisions it
  declares it depends on; see [agent preflight](agent-preflight.md).

## Machine output

`whykit connect --json` writes one object following
`connect-result.schema.json`: the host, the instructions file, the `action`
(`created`, `updated` or `unchanged`), whether it was `written`, the MCP
`command`, `args` and pasteable `config`, and the full `block`. See the
[automation contract](automation.md).
