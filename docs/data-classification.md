# Data classification and Explorer exports

Labels are `public`, `internal`, `confidential`, and `restricted`, in that order.
A missing note label inherits `internal`; an unreadable or invalid label is
withheld by filtered readers, even at the highest ceiling. Labels are editable
assertions, not authentication or a check that the content is safe to disclose.

## Evidence rows

Legacy active and retired tables inherit their register's note label. Both
tables also accept one optional final `Sensitivity` column. Its effective label
is the stricter of the row and register labels. A retired row also inherits any
stricter replacement label, through the replacement chain. Empty or invalid
cells in a labeled table, malformed headers/separators and duplicate IDs fail
closed; `evidence.sensitivity` warns about unreadable row classifications.

`whykit new evidence --sensitivity restricted` extends a legacy active table,
preserving its existing rows' inherited labels. Later additions without the
option inherit the register label; a populated placeholder label is preserved.
Retirement preserves the explicit row label. Writers reject ambiguous tables
before changing bytes. Tables inside code blocks or HTML comments are examples.

MCP returns only rows within its configured ceiling. It withholds the raw
register note whenever per-row classification is present, since returning its
body would expose the other rows. Counts and completions describe visible rows;
hidden rows behave like missing targets. CLI reports remain local, full views.

Filtered readers recognize `.MD` and case variants of the register path. If
several captured notes match that path, they withhold every candidate body and
all evidence rows; the private serve network guard requires explicit opt-in.

The [worked example](../examples/approval/README.md) includes both table formats
and a draft decision explaining this choice.

## Public exports

`whykit explorer-index` and normal Explorer builds default to public exports.
They omit whole non-public notes and notes depending on omitted material:
links, paths, decision/evidence IDs, known titles and aliases, including in code
examples. Evidence rows participate in that dependency closure. Unresolved
wikilinks and unclassified local attachments withhold their containing notes.
Ledgers, lint findings and counts follow the resulting visibility. Nothing
rewrites an approved rationale into a redacted version.

The filter is deliberately conservative: a mention of a known title can omit
an otherwise public note. It cannot detect sensitive facts that were mislabeled
public, unnamed references or paraphrases. Review the resulting artifact before
publishing it. Static Explorer sites have no access control.

Use `whykit explorer-index --private` for a full local index. For a private
Explorer build, explicitly set `WHYKIT_EXPLORER_PRIVATE=1`. `whykit serve` uses
this private mode on loopback; serving a raw per-row register over the network
requires the existing sensitive-network opt-in, including unpopulated tables.
A private build must stay private, including its bodies and findings
chunks and single-file HTML. Imported indexes via `--from` require private mode.

`whykit explorer-index --publication-preview` produces separate **private**
diagnostics with omitted paths, reasons and an input manifest hash. Never
publish that report. Its `publication_ready` means only that lint found no
errors; it is not permission to publish or an assessment of source truth.
