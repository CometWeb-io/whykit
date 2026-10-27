# Recorded snapshots and asserted validity

```bash
uv run whykit knowledge-at --root /path/to/vault --recorded-revision HEAD --valid-on 2026-09-17 --max-sensitivity restricted --json
```

The CLI defaults to public records. The chosen revision supplies the recorded
snapshot; `--valid-on` evaluates separately declared intervals in that snapshot.

`whykit.temporal_graph.knowledge_at` separates two questions: what a particular
Git commit recorded, and which validity interval a note explicitly asserts for
a requested date. Neither axis is independent verification of a claim.

```python
import datetime as dt
from pathlib import Path
from whykit.temporal_graph import knowledge_at

report = knowledge_at(
    Path("my-ledger"), recorded_revision="HEAD",
    valid_on=dt.date(2026, 10, 1), target="D-001",
    allowed_sensitivities={"public"}, max_nodes=1000,
)
```

The report follows [temporal-graph.schema.json](../schemas/temporal-graph.schema.json).
It pins `recorded_revision` to the exact local commit SHA before reading its
objects. Branch/tag names are accepted inputs, but every later read uses that
resolved SHA. Option-like revision names and non-commit objects are refused by
the existing safe Git helpers. Repositories with replacement objects are
refused: Git replacements could otherwise change bytes behind a reported SHA.
There is no checkout, reset, index update, ref
write, fetch or network access. Dirty or staged working-tree content is not
part of the recorded snapshot.

`recorded_at` is Git's committer timestamp (`%cI`), identified by
`recorded_time_source="git_committer_assertion"`. Commit authors can set that
timestamp; it is not a trusted clock, receipt time or signed time attestation.
`valid_on` is a separate date supplied by the caller, never inferred from it.
Two revisions can give different answers for the same `valid_on` when a later
commit records a correction with an earlier effective date.

## Optional validity assertions

Notes may opt into these top-level fields without migrating older vaults:

```yaml
valid_from: 2026-09-01
valid_until: 2026-11-01
```

The interval is half-open: the start is included and the end excluded.
`valid_from` asserts the start; omitting `valid_until` means no asserted end.
An end alone cannot establish earlier active validity. States are:

| State | Meaning at `valid_on` |
|---|---|
| `active` | An explicit start has been reached and an asserted end has not. |
| `not_yet` | The date precedes an explicit start. |
| `expired` | An explicit end has been reached, including when no start is known. |
| `unknown` | There is no explicit start and no end already reached. |
| `invalid` | Front matter/keys/dates are malformed, or end is at/before start. |

Dates must be real `YYYY-MM-DD` strings; empty dates, wrong-case date keys,
duplicate keys and invalid calendar dates fail closed to `invalid`. Existing
vaults remain queryable with `unknown` validity. `created`, `last_updated`,
`review_by`, evidence acquisition dates and Git dates never become a validity
interval. Evidence register rows have `unknown` validity because they do not
carry these per-note assertions. The existing linter's pass is not validation
of these optional temporal fields; this API validates them when queried.

The node's lifecycle `status` and typed `supersedes` edge remain separate from
its `validity.state`. A historically superseded record can still assert an
active interval for a past date. The engine does not invent an end date from
supersession. Both sides of a supersession explicitly active on the requested
date trigger `overlapping_supersession`; `unknown`/`invalid` also produce
review signals. An `active` interval does not prove that a decision was
approved, is binding, remains factually sound or permits an agent to act.
There is no semantic contradiction detector or LLM analysis here.

## Confinement, selection and omissions

The existing `diff.materialize` copies Git objects into a private temporary
directory. Symlinks, submodules and unsafe tree paths are omitted, never
followed; `omitted_tree_entries` reports their count for an unrestricted query.
That raw-tree count is withheld as `null` when a sensitivity filter is set,
because unsafe entries have no readable classification. Case/Unicode path aliases
and file/directory aliases that could overwrite each other fail the query
before materialization. A temporary tree is removed after success or failure.
Large binary attachments are not materialized as full payloads, following the
existing diff reader. This is a note/dependency snapshot, not a byte-for-byte
archival export of every repository object.

Repository objects and refs must remain stable while the query runs. The
reader does not defend against a hostile local process changing replacement
refs or the object database during a query.

The captured snapshot graph uses the same visible-note and evidence-row
filtering as [transitive dependencies](dependency-graph.md). Filtering occurs
before graph statistics and relation extraction. This API does not accept a
current `VaultIndex` as a historical authorization grant: the caller must
authorize the requested repository/revision separately. Label filters do not
authenticate that caller. The local Git reader can materialize the chosen
vault before applying labels; use only repositories the process may read.

With no target, nodes sort by ID and `max_nodes` caps the output. With a target,
the same target rules as deep impact select one visible node. Edges are emitted
only when both endpoints are included. `truncated` means the node cap removed
selected nodes, not that another target's neighbors were promised. Statistics
describe the permitted full snapshot before this output cap. No paths into the
temporary directory or source working directory appear in node IDs or paths.
Reports still contain permitted source metadata; this is not a publication or
enterprise access-control boundary.
