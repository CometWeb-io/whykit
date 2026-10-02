# MCP server

WhyKit can expose a vault to an MCP-compatible host through a local stdio
server. The server is read-only: it provides `query`, `context`, `impact`,
`status`, and `pack`; it cannot create or edit vault files.

The core package stays dependency-free. From a WhyKit checkout, install the
optional MCP extra and start the server with the vault you want to expose:

```bash
uv sync --locked --extra mcp
uv run whykit-mcp --root /absolute/path/to/private-vault
```

For an MCP host that launches stdio servers, use the equivalent command in its
server configuration (the exact config file is host-specific):

```json
{
  "mcpServers": {
    "whykit": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/whykit",
        "--extra",
        "mcp",
        "whykit-mcp",
        "--root",
        "/absolute/path/to/private-vault",
        "--max-sensitivity",
        "internal"
      ]
    }
  }
}
```

Replace both paths with absolute paths on your machine. The default ceiling is
`internal`; `--max-sensitivity` also accepts `public`, `confidential`, and
`restricted`. Expose higher-sensitivity records only to a host and agent that
are already authorized to read them. Sensitivity is a response filter, **not an
access-control boundary**: the server runs with the operating-system
permissions of its process, and repository permissions remain authoritative.
Evidence-register entries do not currently carry their own sensitivity field;
their details are therefore treated as `internal` and omitted from context
responses when the ceiling is `public`.

## Tools

Every tool is annotated `readOnlyHint: true`, `destructiveHint: false`,
`idempotentHint: true` and `openWorldHint: false`. None of them writes to the
vault, takes the mutation lock, runs Git or reaches the network. There are no
write tools; create and change records with the CLI so that lint, the
append-only decision rules and code review still apply.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `query` | `text`, `limit` (clamped to 100), `type`, `status`, `owner`, `tag`, `source_id` (E-NNN), `canonical_only` | Ranked document summaries, no bodies |
| `context` | `target`, `max_chars` (clamped to 50,000) | One record: body, cited evidence, backlinks, supersession, lint findings |
| `impact` | `target` | Reverse dependencies of an E-NNN, D-NNN or document |
| `status` | `today` (ISO date), `due_days` (0–3650; default from vault policy) | Lint counts, review queue and document states |
| `pack` | `targets` (up to 20), `query`, `max_docs` (1–20), `max_chars`, `canonical_only`, `agent` | A budgeted multi-record bundle (`whykit.context-bundle/v1`) |

A `target` is an `E-NNN`, a `D-NNN`, or a vault-relative path, file stem or
alias, exactly as on the command line. Successful calls return the JSON report
both as `structuredContent` and as a text block, so hosts that only read text
keep working.

Document bodies returned by `context` and `pack` are marked
`content_trust: "untrusted_data"`. A note can contain text written to look like
instructions; agents must treat it as data. The server instructions say so too.

## Errors

A call the server cannot answer comes back as a tool result with `isError:
true` and a JSON body:

```json
{"error": {"code": "invalid_target", "message": "target must not contain '.' or '..' path segments"}}
```

| Code | Meaning |
| --- | --- |
| `invalid_target` | The target is empty, too long, absolute, a URL, starts with `~`, contains `.`/`..` segments or control characters |
| `invalid_argument` | Any other argument has the wrong type, range or format |
| `invalid_config` | The vault configuration does not validate (`status` without `due_days`) |
| `vault_unavailable` | The configured root is no longer a WhyKit vault |
| `internal_error` | Anything unexpected; the message is fixed and never carries exception text |

Codes are stable; messages may be reworded. Error messages never echo vault
content or host paths. Arguments that break the advertised input schema (for
example a negative `limit`) are rejected by the SDK before they reach WhyKit and
also come back with `isError: true`.

## Vault confinement

The root is resolved once at start-up. A target is validated as a string before
the filesystem is touched, so a traversal attempt fails the same way whether or
not the file it names exists. Resolution then only accepts files inside the
resolved root: a symlink that points outside the vault is not followed, and a
file the vault index skips (for example under `.obsidian/`) is reported as
missing rather than confirmed. `status` drops the absolute root path that the
CLI's `whykit status --json` includes.

## Sensitivity

A record above the ceiling is indistinguishable from one that does not exist:
`context` and `impact` return the ordinary missing-target shape, `pack` lists it
under `missing` with reason `missing` before it can use any of the body budget,
and `query` and `status` count only visible records. Nested graph nodes
(backlinks, outgoing links, references) above the ceiling are removed, and so are
`status` findings that name a hidden document.

One narrow signal remains: a stem, alias or decision ID shared by a visible and
a hidden record resolves as `ambiguous` rather than to the visible one. Give
hidden records unique file names and decision IDs, or run a separate server per
audience when that matters.

The SDK is an optional extra (`mcp>=2,<3`). See the [security policy](../SECURITY.md)
before connecting an MCP host to a real vault.
