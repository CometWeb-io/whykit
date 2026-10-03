# MCP server

WhyKit can expose a vault to an MCP-compatible host through a local stdio
server, or through streamable HTTP on a loopback port (see
[Streamable HTTP](#streamable-http)). The server
is read-only: it provides seven tools (`query`, `context`, `impact`, `status`,
`pack`, `trace` and `backlinks`), one resource (`whykit://decisions`), one
resource template (`whykit://record/{+target}`, listed once per visible
decision) and two prompts; nothing it
offers can create or edit vault files. Every tool declares an output schema,
list-style results are paged with opaque cursors, prompt and resource-template
arguments can be completed, and subscribed clients are told when a visible
record changes.

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
uv tool install 'whykit[mcp] @ /absolute/path/to/whykit-checkout'   # from a local clone
```

The extra has to be part of the requirement itself, as above.
`uv tool install --from <path> whykit` installs the core package without the
MCP SDK, and uv rejects `uv tool install --from <path> 'whykit[mcp]'` because
the two requirements conflict.

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
A note whose label WhyKit cannot read is treated as above every ceiling: front
matter that does not parse (a tab, an unclosed bracket, a duplicate key) or a
label spelt differently (`Sensitivity:`) hides the note rather than defaulting
it to `internal`. A note with valid front matter and no `sensitivity` key is
`internal`. Evidence-register entries do not carry their own sensitivity field;
they inherit the label of `00-context/evidence-register.md` itself, so a
register above the ceiling withholds every row, count and `E-NNN` completion.

## Protocol versions

The server speaks both MCP protocol eras, and the client's opening picks one
per connection:

- **Classic handshake** (`initialize`): protocol versions `2024-11-05`,
  `2025-03-26`, `2025-06-18` and `2025-11-25`. The server answers with the
  version the client asked for when it supports it, otherwise with the newest
  one it supports. Hosts that connect this way get every tool, resource,
  prompt and completion; the capability set is static (no
  `subscriptions/listen`, no change notifications).
- **2026-07-28** (`server/discover` and per-request envelopes): everything
  above plus [change notifications](#change-notifications).

Over stdio, a `server/discover` probe does not commit the connection. A host
that probes and then falls back to `initialize` on the same pipe, for example
because its probe timed out while the server was still starting, is served
the classic handshake. Once a client has sent any other 2026-07-28 request,
a later `initialize` on that connection is refused with error `-32022`. Over
[streamable HTTP](#streamable-http) the era is chosen per request.

The MCP extra is pinned to the SDK releases this negotiation is tested with
(`mcp>=2.2,<2.4`); CI runs the handshake tests against the lowest and the
highest of them.

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
| | `cursor` | string | `""` (first page) | `next_cursor` of the previous page; ≤ 128 chars |
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
| | `cursor` | string | `""` (first page) | `next_cursor` of the previous page; ≤ 128 chars |
| `backlinks` | `target` | string | required | target rules below |
| | `limit` | integer | `100` | minimum 0; clamped to 500 |
| | `cursor` | string | `""` (first page) | `next_cursor` of the previous page; ≤ 128 chars |

Unknown extra arguments are ignored.

### What each tool returns

| Tool | Returns |
| --- | --- |
| `query` | Ranked document summaries (path, title, status, owner, evidence IDs), no bodies; `total` counts every match, `results` holds one page of at most `limit`, `next_cursor` continues it |
| `context` | One record: body (marked `content_trust: "untrusted_data"`), cited evidence, backlinks, supersession, lint findings. A body over `max_chars` drops its front matter first (`front_matter_omitted: true`) |
| `impact` | Reverse dependencies of an E-NNN, D-NNN or document |
| `status` | Lint counts, review queue and document states; never the host path of the vault |
| `pack` | A budgeted multi-record bundle (`whykit.context-bundle/v1`); hidden and unknown targets are listed under `missing` with the same reason |
| `trace` | For each decision: evidence cited directly or inherited `via` a linked note, each item's state (`active`, `retired`, `missing`, stale) and the gaps `no_evidence`, `missing_evidence`, `retired_evidence`, `stale_evidence`. `summary` covers every visible decision; `decisions` holds one page of at most `limit` of the `matched` records, `truncated` says whether more follow and `next_cursor` continues |
| `backlinks` | Typed inbound edges (`wikilink`, `evidence`, `supersedes`) as `{from, type, to}`; `count` is the full total, `backlinks` one page of at most `limit` of them, `truncated` says whether more follow and `next_cursor` continues |

`trace` follows `whykit trace`: evidence is never inherited from another
decision, and only an approved decision nobody has superseded is `live`.

### Structured output

Every tool lists an `outputSchema`, and every successful result's
`structuredContent` follows it. Each schema is the CLI's JSON contract for the
same view (see [Automation](automation.md#schemas)) with the fields the server
adds or withholds:

| Tool | Base schema | MCP differences |
| --- | --- | --- |
| `query` | `query-result.schema.json` | adds `max_sensitivity` and `next_cursor`; `query.limit` is the page size |
| `context` | `context-pack.schema.json` | adds `content_trust` |
| `impact` | `impact-report.schema.json` | none |
| `status` | `status-report.schema.json` | no `root`; adds `max_sensitivity` and `review_due_days`; `evidence_active` and `evidence_retired` are `null` under a `public` ceiling |
| `pack` | `context-bundle.schema.json` | adds `max_sensitivity` |
| `trace` | `trace-report.schema.json` | adds `matched`, `truncated`, `next_cursor`, `max_sensitivity`, and under a `public` ceiling `evidence_details: "withheld"` with evidence items reduced to `{id, via, state: "withheld"}` |
| `backlinks` | `backlinks-report.schema.json` | adds `truncated`, `max_sensitivity` and `next_cursor` |

Error results (`isError: true`) carry the error body described under
[Errors](#errors) instead; clients validate only successful results against
the output schema. The wheel ships copies of the base schemas in
`whykit/contract_schemas/`, which a test keeps identical to `schemas/`.

### Pagination

`query`, `trace` and `backlinks` return one page of at most `limit` items and a
`next_cursor`, which is `null` on the last page. To read the next page, repeat
the call with the same arguments and `cursor` set to that value; an empty
`cursor` starts at the first page. `limit: 0` returns no items and no cursor,
while the totals still count every match.

A cursor is opaque: an offset into the *visible* result list, bound by a MAC
to the tool, its arguments, the ceiling and the full visible ordering. It
never counts, skips over or encodes a record above the ceiling, so it is the
same string whether or not hidden records exist. It is rejected with
`invalid_cursor` when it was issued for other arguments, another tool, another
ceiling or another server process (the key is random per process), when it
was altered, and when the visible results changed since it was issued, so a
client never silently skips or repeats an item. Restart from the first page
in every case. Changes to hidden records leave cursors valid.

Document bodies returned by `context`, `pack`, the `record` resource and the
prompts are untrusted data. A note can contain text written to look like
instructions; agents must treat it as data. The server instructions say so too.

## Resources

| URI | Content |
| --- | --- |
| `whykit://decisions` | JSON index of visible decisions: `decision_id`, `title`, `status`, `path` and a `whykit://record/...` URI each (at most 1,000 rows, with `count` and `truncated`) |
| `whykit://record/{+target}` | The `context` report for one target (`whykit://record/D-002`, `whykit://record/notes/research`) with a 20,000-character body budget |

Resources are computed when they are read, so they follow the vault as it
changes.

`resources/list` returns `whykit://decisions` followed by one
`whykit://record/D-NNN` resource for each visible decision (once per ID), 100
per page. Its `nextCursor` works like a tool cursor; an invalid one is
answered with the MCP error `-32602`. `resources/templates/list` returns the
`whykit://record/{+target}` template. A hidden, missing or ambiguous record, and a target that fails the
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

## Completions

The server answers `completion/complete` for:

| Reference | Argument | Candidates |
| --- | --- | --- |
| prompt `summarize_decision` | `decision_id` | visible decision IDs |
| prompt `review_evidence_gaps` | `today` | today's date |
| template `whykit://record/{+target}` | `target` | visible decision IDs, evidence IDs (when the ceiling is `internal` or higher), then visible vault-relative paths |

Candidates are matched by case-insensitive prefix and come from the same
confined view as the tools, so a record above the ceiling is never offered and
a prefix only a hidden record matches completes to nothing, exactly like one
nothing matches. At most 100 values are returned, with `total` and `hasMore`.
An unknown reference or argument, or a partial value with control characters
or over 512 characters, also completes to nothing.

## Change notifications

On the 2026-07-28 protocol, a client opens a `subscriptions/listen` stream and
names the resource URIs it cares about. While a stream is open, the server
checks the vault every `--watch-interval` seconds (default 2; `0` turns
notifications off) and sends the changes since the first check after the
stream opened. With no stream open the server does no checking at all.
It sends:

- `notifications/resources/updated` for a `whykit://record/...` URI whose
  record changed, appeared or disappeared: the record's own file for its
  `D-NNN` and its vault-relative path (with and without `.md`), and the
  evidence-register row for an `E-NNN`. A change to another record, for
  example a new backlink, does not notify dependents;
- `notifications/resources/updated` for `whykit://decisions` when the
  decision index changed;
- `notifications/resources/list_changed` when the resource list changed (a
  visible decision was added, removed or retitled).

The check fingerprints only what a client could read under the ceiling, so
editing, adding or deleting a hidden record produces no notification, and a
record that crosses the ceiling looks exactly like one created or deleted.
Between changes a check stats the vault's Markdown files and does nothing
else; it takes about 0.13 s at 5,000 notes, and about 0.9 s when something
changed.

Clients on the older initialize handshake (protocol 2025-11-25 and earlier)
are not offered `resources.subscribe`; they should read a resource again when
they need it.

## Streamable HTTP

`--http` serves the same server over streamable HTTP at `/mcp` instead of
stdio:

```bash
uv run whykit-mcp --root /absolute/path/to/private-vault --http --port 8000
```

`--host` defaults to `127.0.0.1`. Without a token, WhyKit rejects (HTTP 421,
code `forbidden_host`) every request whose `Host` or `Origin` header does not
name `127.0.0.1`, `localhost`, `[::1]` or the bound host, however the loopback
bind address was spelt. That blocks DNS rebinding from a web page. `--port`
defaults to `8000`.

With `--token-file PATH` (or `WHYKIT_MCP_TOKEN` in the environment) every
request must send `Authorization: Bearer <token>`; anything else gets HTTP 401
with `{"error": {"code": "unauthorized", ...}}` before it reaches the MCP
layer. The token must be at least 32 printable ASCII characters and is
compared in constant time; pass it in a file or the environment rather than on
the command line, where other local users can read it. A `--host` that is not
loopback (for example `0.0.0.0`) is refused unless a token is set. There is
no TLS: put a TLS-terminating proxy in front of a server reachable from other
machines. Sensitivity remains a response filter, not an access-control
boundary, so the token decides who can ask and the ceiling decides what they
see.

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
| `invalid_cursor` | A `cursor` is malformed, altered, from another call, ceiling or server process, or the visible results changed since it was issued; repeat the call without it |
| `invalid_config` | The vault configuration does not validate (`status` without `due_days`) |
| `vault_unavailable` | The configured root is no longer a WhyKit vault |
| `unknown_tool` | No tool has this name |
| `unauthorized` | Streamable HTTP only: the request lacks the bearer token (HTTP 401, before any MCP processing) |
| `forbidden_host` | Streamable HTTP without a token only: the `Host` or `Origin` header does not name this machine (HTTP 421, before any MCP processing) |
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
- `query`, `status`, `trace`, `whykit://decisions` and `resources/list` count
  only visible records, and graph nodes, backlinks and inherited evidence
  never pass through a hidden note;
- page cursors are computed over visible results only, completions offer only
  visible IDs and paths, and change notifications fingerprint only visible
  content (see [Pagination](#pagination), [Completions](#completions) and
  [Change notifications](#change-notifications));
- lint findings in `context` and `status` are computed on the confined view, so
  a wikilink or Markdown link from a visible note to a hidden one is reported
  as unresolved, exactly like a link to a note that does not exist. Findings on
  hidden files (for example from the secret scan) are dropped. Run
  `whykit lint` for the full-vault result.

Ambiguity between two *visible* records is still reported as `ambiguous`.

The SDK is an optional extra (`mcp>=2.2,<2.4`). See the [security policy](../SECURITY.md)
before connecting an MCP host to a real vault.
