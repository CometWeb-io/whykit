# MCP server

WhyKit can expose a vault to an MCP-compatible host through a local stdio
server. The server is read-only: it provides seven tools (`query`, `context`,
`impact`, `status`, `pack`, `trace` and `backlinks`), two resources and two
prompts; nothing it offers can create or edit vault files.

The core package stays dependency-free. WhyKit is not on PyPI yet, so install
the optional MCP extra from a checkout and start the server with the vault you
want to expose:

```bash
uv sync --locked --extra mcp
uv run whykit-mcp --root /absolute/path/to/private-vault
```

or install both commands as a tool straight from the repository:

```bash
uv tool install 'whykit[mcp] @ git+https://github.com/CometWeb-io/whykit'
```

Started without the extra, `whykit-mcp` prints these two options and exits
with status 2. The server identifies itself as `whykit` with the package
version in its MCP server info.

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

A `target` is an `E-NNN`, a `D-NNN`, or a vault-relative path, file stem or
alias, exactly as on the command line, at most 512 characters. Successful calls
return the JSON report both as `structuredContent` and as a text block, so
hosts that only read text keep working.

### Input schemas

Defaults apply when an argument is omitted. "Clamped" means a larger value is
accepted and reduced to the cap; "minimum" values below the bound are rejected.

| Tool | Argument | Type | Default | Constraints |
| --- | --- | --- | --- | --- |
| `query` | `text` | string | `""` | ≤ 1,000 chars; empty lists every visible document |
| | `limit` | integer | `20` | minimum 0; clamped to 100 |
| | `type`, `status`, `tag` | string | `""` | ≤ 200 chars; exact match (tag case-insensitive) |
| | `owner` | string | `""` | ≤ 200 chars; case-insensitive substring |
| | `source_id` | string | `""` | must be `E-NNN` when set |
| | `canonical_only` | boolean | `false` | |
| `context` | `target` | string | required | target rules below |
| | `max_chars` | integer | `4000` | minimum 0; clamped to 50,000 |
| `impact` | `target` | string | required | target rules below |
| `status` | `today` | string | `""` (today) | ISO date `YYYY-MM-DD`, ≤ 32 chars |
| | `due_days` | integer or null | vault policy | minimum 0; clamped to 3,650 |
| `pack` | `targets` | array of string | `[]` | ≤ 20 items, each a valid target |
| | `query` | string | `""` | ≤ 1,000 chars; a target or a query is required |
| | `max_docs` | integer | `8` | minimum 1; clamped to 20 |
| | `max_chars` | integer | `20000` | minimum 0; clamped to 50,000 |
| | `canonical_only` | boolean | `false` | |
| | `agent` | string | `"generic"` | `generic`, `cursor`, `claude` or `codex` |
| `trace` | `decision` | string | `""` (all) | must be `D-NNN` when set |
| | `today` | string | `""` (today) | ISO date `YYYY-MM-DD` |
| | `gaps_only` | boolean | `false` | only live decisions with a gap |
| | `limit` | integer | `50` | minimum 0; clamped to 100 |
| `backlinks` | `target` | string | required | target rules below |
| | `limit` | integer | `100` | minimum 0; clamped to 500 |

Unknown extra arguments are ignored.

### What each tool returns

| Tool | Returns |
| --- | --- |
| `query` | Ranked document summaries (path, title, status, owner, evidence IDs), no bodies |
| `context` | One record: body (marked `content_trust: "untrusted_data"`), cited evidence, backlinks, supersession, lint findings |
| `impact` | Reverse dependencies of an E-NNN, D-NNN or document |
| `status` | Lint counts, review queue and document states; never the host path of the vault |
| `pack` | A budgeted multi-record bundle (`whykit.context-bundle/v1`); hidden and unknown targets are listed under `missing` with the same reason |
| `trace` | For each decision: evidence cited directly or inherited `via` a linked note, each item's state (`active`, `retired`, `missing`, stale) and the gaps `no_evidence`, `missing_evidence`, `retired_evidence`, `stale_evidence`. `summary` covers every visible decision; `decisions` holds at most `limit` of the `matched` records and `truncated` says whether more exist |
| `backlinks` | Typed inbound edges (`wikilink`, `evidence`, `supersedes`) as `{from, type, to}`; `count` is the full total, `backlinks` at most `limit` of them, `truncated` says whether more exist |

`trace` follows `whykit trace`: evidence is never inherited from another
decision, and only an approved decision nobody has superseded is `live`.

Document bodies returned by `context`, `pack`, the `record` resource and the
prompts are untrusted data. A note can contain text written to look like
instructions; agents must treat it as data. The server instructions say so too.

## Resources

| URI | Content |
| --- | --- |
| `whykit://decisions` | JSON index of visible decisions: `decision_id`, `title`, `status`, `path` and a `whykit://record/...` URI each (at most 1,000 rows, with `count` and `truncated`) |
| `whykit://record/{+target}` | The `context` report for one target (`whykit://record/D-002`, `whykit://record/notes/research`) with a 20,000-character body budget |

Resources are computed when they are read, so they follow the vault as it
changes. A hidden, missing or ambiguous record, and a target that fails the
target rules, are all answered with the same MCP "resource not found" error
(`-32602`). Traversal and absolute paths are also rejected by the SDK before
WhyKit sees them.

## Prompts

| Prompt | Arguments | Produces |
| --- | --- | --- |
| `summarize_decision` | `decision_id` (required, `D-NNN`) | A request to summarise the decision, its status and the evidence it rests on, with the decision's `context` (body budget 12,000 characters) and its `trace` record embedded |
| `review_evidence_gaps` | `today` (optional ISO date) | A request to propose the smallest fix for each live decision with a gap, with up to 50 traced decisions embedded |

Embedded data is filtered by the same ceiling as the tools and is wrapped in a
`<whykit-data content_trust="untrusted_data">` block. A hidden decision and one
that does not exist produce the same `-32602` error.

## Errors

A tool call the server cannot answer comes back as a tool result with
`isError: true` and a JSON body, both as `structuredContent` and as text:

```json
{"error": {"code": "invalid_target", "message": "target must not contain '.' or '..' path segments"}}
```

| Code | Meaning |
| --- | --- |
| `invalid_target` | A `target` (or an item of `targets`) is missing, not a string, empty, too long, absolute, a URL, starts with `~`, contains `.`/`..` segments or control characters |
| `invalid_argument` | Any other argument has the wrong type, range or format |
| `invalid_config` | The vault configuration does not validate (`status` without `due_days`) |
| `vault_unavailable` | The configured root is no longer a WhyKit vault |
| `unknown_tool` | No tool has this name |
| `internal_error` | Anything unexpected; the message is fixed and never carries exception text |

The body is the same whether the SDK rejects arguments against the advertised
input schema (for example a negative `limit` or a 600-character target) or
WhyKit's own validation does. Schema messages name the offending fields but
never echo their values. Codes are stable; messages may be reworded. Error
messages never echo vault content or host paths.

Where a code also exists in the CLI's machine contract (`invalid_target`,
`invalid_argument`, `invalid_config`) it means the same thing there; see
[Automation](automation.md) for the CLI's error object and exit codes.

## Vault confinement

The root is resolved once at start-up. A target is validated as a string before
the filesystem is touched, so a traversal attempt fails the same way whether or
not the file it names exists. Resolution then only accepts files inside the
resolved root: a symlink that points outside the vault is not followed, and a
file the vault index skips (for example under `.obsidian/`) is reported as
missing rather than confirmed. `status` drops the absolute root path that the
CLI's `whykit status --json` includes.

## Freshness

Every call reads the vault as it is on disk at that moment. The server keeps
the parsed notes between calls and reuses a note only while its file's
modification time, change time, size and inode are all unchanged, so an edit,
an atomic save, a rename or a deletion is seen by the next call. A file written
in the last two seconds is always read again. Link resolution, findings and
every other answer are computed fresh for each call. See
[Performance](performance.md#mcp-server) for the measured effect.

## Sensitivity

Every tool, resource and prompt works on a confined view of the vault that
contains only records at or below the ceiling. A record above it is
indistinguishable from one that does not exist:

- it is never a resolution candidate, so a stem, alias or decision ID it shares
  with a visible record resolves to the visible one rather than to
  `ambiguous`, and a path to it falls back to stem lookup exactly as a path to
  a missing file would;
- `context`, `impact` and `backlinks` return the ordinary missing-target shape,
  `trace` returns no record for its decision ID, and `pack` lists it under
  `missing` with reason `missing` before it can use any of the body budget;
- `query`, `status`, `trace` and `whykit://decisions` count only visible
  records, and graph nodes, backlinks and inherited evidence never pass
  through a hidden note;
- lint findings in `context` and `status` are computed on the confined view, so
  a wikilink or Markdown link from a visible note to a hidden one is reported
  as unresolved, exactly like a link to a note that does not exist. Findings on
  hidden files (for example from the secret scan) are dropped. Run
  `whykit lint` for the full-vault result.

Ambiguity between two *visible* records is still reported as `ambiguous`.

The SDK is an optional extra (`mcp>=2,<3`). See the [security policy](../SECURITY.md)
before connecting an MCP host to a real vault.
