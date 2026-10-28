# Transitive possible dependencies

```bash
uv run whykit deep-impact E-001 --root /path/to/vault --max-depth 8 --max-nodes 1000 --max-sensitivity restricted --json
```

The CLI defaults to a public ceiling. A higher ceiling is an explicit local
filter choice; it does not grant repository access or prove causal impact.

`whykit.dependency_graph.deep_impact` follows reverse dependencies through the
existing note links, evidence citations and supersession declarations. It
explains which visible records can be reached from an input. A wikilink is a
possible dependency, not proof of causal impact or a request to change a record.
This Python API does not replace the existing `graph` or `impact` reports.

```python
from pathlib import Path
from whykit.dependency_graph import deep_impact

report = deep_impact(
    Path("examples/northline"), "E-001",
    max_depth=8, max_nodes=1000, allowed_sensitivities={"public"},
)
```

The report follows [deep-impact.schema.json](../schemas/deep-impact.schema.json).
Targets are an `E-NNN`, `D-NNN`, vault-relative Markdown path, stem, alias or
returned node ID. Malformed IDs, control characters, absolute paths and `..`
components raise `ValueError`. An unknown or hidden target has `exists=false`,
no results and no target node; duplicate visible decisions or aliases are
ambiguous. File symlinks do not become graph nodes or permit traversal outside
the captured note view.

## Paths, limits and ordering

Document IDs include the complete relative filename (`document:spec.md`);
evidence IDs use `evidence:E-001`. This separates namespaces and preserves the
identity of distinct `.md`/`.MD` paths. Existing graph report IDs are unchanged.

Every edge retains the original dependent → input direction, with type
`wikilink`, `evidence`, `supersedes`, `outcome_decision` or `measurement_claim`.
Starting at the target, breadth-first
traversal follows these edges in reverse. For example, evidence cited by a
decision, a spec linking that decision and another decision linking the spec
produce distances 1, 2 and 3.

Results rank by distance, then node ID. Each result has one deterministic
shortest explanation: `path_nodes` starts at the target and ends at the result;
`path_edges` follows that outward order while retaining each original edge's
direction. Duplicate links are deduplicated. Alternative routes remain in the
induced `edges` list; enumerating every possible path would grow exponentially.

`max_nodes` includes the target, and `max_depth=0` visits only the target.
Negative depth, non-integer limits and fewer than one node are refused.
`truncation.max_depth` and `truncation.max_nodes` identify limits that excluded
an unvisited reverse dependency. A complete result exactly at a limit is not
marked truncated. After a limit is reached the report does not claim to know
the total number of reachable nodes. `stats.view_nodes/view_edges` describe the
entire permitted graph, while `visited_nodes` describes the bounded traversal.

Cycles are reported as sorted strongly connected components in the visited
induced graph, including self loops. They are not an enumeration of all cycle
paths, and an incomplete traversal cannot rule out cycles outside its result.
Graph extraction happens once. Breadth-first traversal and cycle detection are
linear in nodes and edges; deterministic sorting and full explanation output
add their own cost. No persistent graph index or cache is written.

## Visibility and deterministic review signals

Pass `vault=VaultIndex` to retain a caller's captured scope. The index must
belong to the requested root and be a trusted snapshot. The helper confines
link resolution to those notes and never replaces the supplied view with a
fresh full index. Evidence rows come only from the captured register; a missing
register is not reopened from disk.

`allowed_sensitivities` narrows that view with the shared fail-closed classifier
before graph construction, traversal or statistics. Malformed front matter,
unreadable or differently cased label keys and denied notes/rows are excluded.
An unlabelled readable note is internal. A visible citation to an unavailable
row does not create a guessed evidence node or expose a hidden source title.
Hidden and missing rows have the same behavior. An omitted register therefore
leaves its citations unavailable, even if the register still exists on disk.
Duplicate or unreadably classified evidence rows are unavailable even without
a label filter; the engine does not choose one conflicting row as the source.

`review_signals` names structural reasons for review in the visited graph:
retired evidence, unavailable evidence/dependencies, malformed metadata,
unresolved supersession and dependency cycles. These are deterministic flags.
They do not establish semantic contradiction, source truth or authorization.
No model or network calls occur. Sensitivity labels are not access control;
without a supplied grant or label filter, all locally readable notes are in
scope. Reports contain permitted titles and paths and must not be treated as
public exports merely because a sensitivity filter was used.

## Owned dependency declarations

Optional implementation notes live below `00-context/implementation/` with
lowercase ASCII slug paths ending in `.md`. A spec uses `type: specification`;
pull-request and task cards use `type: reference`. Their stored sensitivity must
be `restricted`. Add a JSON scalar in the open provenance block, for example:

```yaml
provenance:
  whykit_impact: '{"format":"whykit.impact-dependencies/v1","artifact_kind":"task","dependencies":[{"relation":"implements","target":"decision:D-001","pin":null}]}'
```

Replace `pin: null` with a `{namespace, sha256}` object when the dependency's
reviewed version is known. Supported namespaces are `evidence_raw_sha256`,
`claim_markdown_sha256`, `decision_record_sha256` and `document_raw_sha256`.
The decoded object follows [impact-dependencies.schema.json](../schemas/impact-dependencies.schema.json).
Relations are `requires`, `implements` or `measures`; each declaration has at
most 32 dependencies and 16 KiB of JSON. A decision may carry the same optional
scalar with `artifact_kind: null`; adding it changes its approval record and
therefore requires a fresh review. Existing approvals are never rewritten.
Unrelated producer fields in provenance retain their existing meaning.

These declarations participate in native visibility closure. Ordinary
`deep-impact` does not turn `requires`, `implements` or `measures` declarations
into traversal edges; its paths follow the link and Outcome relations above.

Visibility closes over required native dependencies before ranking, hashing
or construction budgets. A missing or denied prerequisite withholds its
dependent; omission does not establish that the record is unaffected. Implementation notes
and marked decisions have an effective restricted floor even if their stored
label is lower. Malformed owned syntax produces the `impact.invalid` warning.
