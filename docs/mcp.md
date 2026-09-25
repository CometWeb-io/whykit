# MCP server

WhyKit can expose a vault to an MCP-compatible host through a local stdio
server. The server is read-only: it provides `query`, `context`, and `impact`;
it cannot create or edit vault files.

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

The SDK is an optional extra (`mcp>=2,<3`). See the [security policy](../SECURITY.md)
before connecting an MCP host to a real vault.
