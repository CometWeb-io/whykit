---
title: "Obsidian conventions"
aliases: ["Vault conventions"]
type: guide
status: approved
owner: "Product Lead"
created: 2026-03-12
last_updated: 2026-03-12
source_of_truth: true
sensitivity: public
source_ids: []
tags: ["governance", "obsidian"]
workstream: "root"
---

# Obsidian conventions

Open the repository root as the vault root.

## Compatibility

- UTF-8 Markdown files.
- No community plugin required for core navigation or reading.
- Readable in both Obsidian and a plain Git host.
- YAML front matter for document properties.
- Obsidian callouts instead of platform-specific block markup.

## Required properties

`title`, `aliases`, `type`, `status`, `owner`, `created`, `last_updated`, `source_of_truth`, `sensitivity`, `source_ids`, `tags`.

`source_of_truth: true` requires `status: approved`. It is a claim, not a formality. It means: if another document disagrees with this one, this one wins. Very few documents should carry it, and every one that does needs an owner who notices when it goes stale.

Sensitivity is one of `public`, `internal`, `confidential`, or `restricted`. The label describes handling; repository permissions still enforce access.

## Links

- Wikilinks for internal notes: `[[path/to/note|Readable label]]`.
- Markdown links for external sources.
- Link to the canonical note instead of duplicating it.
- For files in another repository, follow [[INTEROP]] — those are not wikilinks.

## File names

- Lowercase `kebab-case.md` for normal notes.
- Root navigation notes may use a readable name, such as [[Home]].
- Date-stamped documents end with the date: `name-YYYY-MM-DD.md`. A report called `audit.md` is unusable the second time someone runs an audit.

## Callouts

Callouts are how the five information types stay visible while reading:

```
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

Start from [[Home]]. Every substantive note links back to at least one map, strategy, workstream or source note. A note nothing links to will not be found again, whatever the search index says.
