---
title: Obsidian conventions
aliases:
  - Vault conventions
type: guide
status: approved
owner: WhyKit maintainers
created: 2026-10-09
last_updated: 2026-10-09
source_of_truth: true
sensitivity: public
tags:
  - governance
  - obsidian
---

# Obsidian conventions

Open the repository root as the vault root.

## Compatibility

- UTF-8 Markdown files.
- No community plugin required for core navigation or reading.
- Readable in both Obsidian and a plain Git host.
- YAML front matter for document properties.
- Obsidian callouts instead of platform-specific block markup.

## Required properties for new working notes

```yaml
title: Human-readable title
aliases: []
type: strategy | research | framework | specification | decision | map-of-content | guide
status: draft | in_review | approved | superseded | archived
owner: Owner name
created: YYYY-MM-DD
last_updated: YYYY-MM-DD
source_of_truth: false
sensitivity: internal
source_ids: []
tags: []
```

Templates use `status: template`. `sensitivity` is one of `public`, `internal`, `confidential`, or `restricted`.

`source_of_truth: true` is a claim, not a formality. It means: if another
document disagrees with this one, this one wins. Very few documents should carry
it, and every one that does needs an owner who notices when it goes stale.

## Links

- Wikilinks for internal notes: `[[path/to/note|Readable label]]`.
- Markdown links for external sources.
- Link to the canonical note instead of duplicating it.
- Prefer unique note names; add a path where a title could be ambiguous.
- For files in another repository, follow `INTEROP.md` — those are not wikilinks.

## File names and structure

- Lowercase `kebab-case.md` for normal notes.
- Root navigation notes may use a readable name, such as `Home.md`.
- Attachments in `assets/`, linked relatively.
- No empty heading-only exports, no duplicate stubs.
- No exporter-generated identifiers in normalized file names.
- Date-stamped documents end with the date: `name-YYYY-MM-DD.md`. Anything
  reporting a point in time needs this; a report called `audit.md` is unusable
  the second time someone runs an audit.

## Obsidian callouts

Callouts are how the five information types stay visible while reading:

```markdown
> [!fact] Verified fact
> Supported by a reviewed source.

> [!hypothesis] Hypothesis
> Requires validation.

> [!warning] Needs verification
> Do not publish or treat as canonical.

> [!decision] Decision
> Link to the decision-log entry.
```

## Navigation

Start from `Home.md`. Every substantive note links back to at least one map,
strategy, workstream or source note. A note nothing links to will not be found
again, whatever the search index says.
