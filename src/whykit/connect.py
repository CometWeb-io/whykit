"""Connect a code repository and its AI agents to a WhyKit vault.

``whykit connect`` does two things and nothing else:

* prints the MCP server configuration for one agent host, with absolute paths
  filled in, so the agent can query the vault read-only; and
* previews (or, with ``--write``, writes) a short instruction block in the code
  repository's ``AGENTS.md`` or ``CLAUDE.md`` that tells the agent to check
  decisions before changing what they cover.

The block sits between two HTML comment markers. Writing again replaces only
what is between them, so the command is idempotent and never touches the rest
of the file. Host configuration files are never written: they hold
user-specific absolute paths and often other servers, so the user pastes the
printed snippet themselves.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

START_MARKER = "<!-- whykit:start -->"
END_MARKER = "<!-- whykit:end -->"

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
DEFAULT_SENSITIVITY = "internal"

# Each host, where its MCP configuration lives, and which instruction file its
# agent reads. Codex, Cursor and VS Code read AGENTS.md; Claude Code reads
# CLAUDE.md.
HOSTS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code", "config": "run the command below", "instructions": "CLAUDE.md"},
    "cursor": {"label": "Cursor", "config": ".cursor/mcp.json", "instructions": "AGENTS.md"},
    "vscode": {"label": "VS Code", "config": ".vscode/mcp.json", "instructions": "AGENTS.md"},
    "codex": {"label": "Codex", "config": "~/.codex/config.toml", "instructions": "AGENTS.md"},
    "claude-desktop": {"label": "Claude Desktop", "config": "claude_desktop_config.json", "instructions": "AGENTS.md"},
    "generic": {"label": "any MCP host", "config": "your host's MCP configuration", "instructions": "AGENTS.md"},
}


class ConnectError(Exception):
    """A request that cannot be carried out; ``code`` is a contract error code."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


@dataclass(frozen=True)
class ServerCommand:
    command: str
    args: tuple[str, ...]
    installed: bool


def server_command(vault: Path, max_sensitivity: str) -> ServerCommand:
    """The stdio command that serves ``vault``; absolute when it is installed."""
    found = shutil.which("whykit-mcp")
    command = str(Path(found).absolute()) if found else "whykit-mcp"
    return ServerCommand(command, ("--root", str(vault), "--max-sensitivity", max_sensitivity), found is not None)


def host_config(host: str, server: ServerCommand) -> str:
    """The configuration text a user pastes into ``host``."""
    args = list(server.args)
    if host == "claude-code":
        return shlex.join(["claude", "mcp", "add", "whykit", "--", server.command, *args])
    if host == "codex":
        quoted = ", ".join(json.dumps(arg) for arg in args)
        return (
            "[mcp_servers.whykit]\n"
            f"command = {json.dumps(server.command)}\n"
            f"args = [{quoted}]\n"
            "required = true"
        )
    if host == "vscode":
        value: dict[str, object] = {"servers": {"whykit": {"type": "stdio", "command": server.command, "args": args}}}
    else:
        value = {"mcpServers": {"whykit": {"command": server.command, "args": args}}}
    return json.dumps(value, indent=2)


def _vault_reference(vault: Path, repo: Path) -> str:
    """How the instruction block names the vault: always relative when possible.

    The block is committed to the code repository, so it must not carry one
    person's home directory. A sibling vault becomes ``../vault``.
    """
    try:
        relative = os.path.relpath(vault, repo)
    except ValueError:  # another drive on Windows
        return str(vault)
    return Path(relative).as_posix()


def instruction_block(vault_ref: str) -> str:
    """The marked block written into the repository's agent instructions."""
    root = shlex.quote(vault_ref)
    return "\n".join((
        START_MARKER,
        "## Decisions (WhyKit)",
        "",
        f"This repository's decisions and the sources behind them are kept in a WhyKit vault at `{vault_ref}`.",
        "",
        "Before you change behaviour, architecture, dependencies, pricing or anything else a past decision may cover:",
        "",
        f"1. Call the `whykit` MCP tool `affected` with the files you plan to change (or run `whykit affected --root {root} --files <files>`). It lists the accepted decisions that govern those files and flags any whose sources or reviews need attention.",
        "2. For context beyond file scope, use the same server: `query` for the topic, then `trace` or `context` for the decisions it returns.",
        "3. If an accepted decision covers the change, follow it. If your change would contradict it, or a decision is flagged for review, stop and name the decision (`D-NNN`) and the reason instead of overriding it.",
        "4. Never edit an accepted decision's reasoning. To change one, draft a successor with `whykit new decision \"<title>\" --supersedes D-NNN --status draft` and leave approval to a person.",
        "5. Cite the decision and evidence IDs (`D-NNN`, `E-NNN`) that informed a change in its commit message or pull request. `whykit scope suggest` learns from those messages where each decision applies.",
        "",
        f"Without the MCP server, the CLI answers the same questions: `whykit query --root {root} \"<topic>\"` and `whykit trace --root {root} --decision D-NNN`.",
        "",
        "This block is maintained by `whykit connect`; edit outside the markers.",
        END_MARKER,
    ))


def merge_block(existing: str | None, block: str) -> tuple[str, str]:
    """Return the new file text and the action: created, updated or unchanged."""
    if existing is None:
        return block + "\n", "created"
    start = existing.find(START_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 or end != -1:
        if start == -1 or end == -1 or end < start or existing.count(START_MARKER) > 1 or existing.count(END_MARKER) > 1:
            raise ConnectError(
                "invalid_target",
                "the WhyKit markers in the instructions file are unbalanced or repeated",
                hint=f"keep exactly one {START_MARKER} … {END_MARKER} pair, or remove both",
            )
        merged = existing[:start] + block + existing[end + len(END_MARKER):]
        return merged, "unchanged" if merged == existing else "updated"
    if not existing.strip():
        return block + "\n", "updated"
    # Append after one blank line, keeping the existing text byte for byte.
    separator = "\n" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    if existing.endswith("\n\n"):
        separator = ""
    return existing + separator + block + "\n", "updated"


def _absolute(raw: str | os.PathLike[str]) -> Path:
    # Absolutize without resolving symlinks; the symlink check below needs them.
    return Path(os.path.abspath(Path(raw).expanduser()))


def plan(vault: Path, *, host: str, repo: str | None, max_sensitivity: str) -> dict[str, object]:
    """Everything ``whykit connect`` would do, without writing anything."""
    if host not in HOSTS:
        raise ConnectError("invalid_argument", f"unknown host: {host}", hint="choose one of: " + ", ".join(HOSTS))
    if max_sensitivity not in SENSITIVITY_LEVELS:
        raise ConnectError("invalid_argument", f"unknown sensitivity: {max_sensitivity}")
    vault = vault.resolve()
    repo_path = _absolute(repo) if repo else Path.cwd().absolute()
    if not repo_path.is_dir():
        raise ConnectError("invalid_target", f"repository directory does not exist: {repo_path}")
    repo_path = repo_path.resolve()
    target = repo_path / HOSTS[host]["instructions"]
    if target.is_symlink():
        raise ConnectError("unsafe_path", f"refusing to write through a symlink: {target}")
    if target.exists() and not target.is_file():
        raise ConnectError("invalid_target", f"expected a regular file: {target}")
    server = server_command(vault, max_sensitivity)
    block = instruction_block(_vault_reference(vault, repo_path))
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    merged, action = merge_block(existing, block)
    return {
        "contract_version": 1,
        "root": str(vault),
        "host": host,
        "repo": str(repo_path),
        "instructions_file": str(target),
        "action": action,
        "written": False,
        "mcp": {
            "installed": server.installed,
            "command": server.command,
            "args": list(server.args),
            "config_location": HOSTS[host]["config"],
            "config": host_config(host, server),
        },
        "block": block,
        "_merged": merged,
    }


def apply(report: dict[str, object]) -> dict[str, object]:
    """Write the planned instruction file; a no-op when nothing changes."""
    from .io import atomic_write_text

    merged = str(report.pop("_merged"))
    if report["action"] != "unchanged":
        atomic_write_text(Path(str(report["instructions_file"])), merged)
        report["written"] = True
    return report


def render_text(report: dict[str, object]) -> str:
    """Human output: the config to paste, then what happened to the file."""
    mcp = report["mcp"]
    assert isinstance(mcp, dict)
    host = HOSTS[str(report["host"])]
    lines = [f"1. Give {host['label']} read-only access to the vault ({mcp['config_location']}):", ""]
    lines += ["   " + line for line in str(mcp["config"]).splitlines()]
    if not mcp["installed"]:
        lines += ["", "   whykit-mcp is not installed yet: uv tool install --upgrade 'whykit[mcp]'",
                  "   then run `whykit connect` again to fill in its absolute path."]
    lines += [""]
    target = report["instructions_file"]
    action = report["action"]
    if report["written"]:
        verb = {"created": "created", "updated": "updated"}[str(action)]
        lines.append(f"2. Agent instructions {verb}: {target}")
    elif action == "unchanged":
        lines.append(f"2. Agent instructions already up to date: {target}")
    else:
        would = "create" if action == "created" else "update"
        lines += [f"2. Would {would} the agent instructions in {target}:", ""]
        lines += ["   " + line if line else "" for line in str(report["block"]).splitlines()]
        lines += ["", "   Run again with --write to apply. Only the marked block is touched."]
    return "\n".join(lines)
