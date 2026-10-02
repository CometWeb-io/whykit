# Using WhyKit with Obsidian

A WhyKit vault is an ordinary folder of Markdown, so [Obsidian](https://obsidian.md)
can open it as-is. Obsidian is optional: the vault reads the same on a Git host,
in any editor, and through the CLI. Nothing in WhyKit needs a community plugin.

The division of labour is simple. **Obsidian is where you read and write. WhyKit
is what checks the result**, locally and in CI.

## Open the vault

1. Create a vault with `whykit init <dir>` (see the
   [README](../README.md#60-second-quickstart)).
2. In Obsidian, choose **Open folder as vault** and pick that directory, the one
   containing `Home.md` and `whykit.toml`. The repository root is the vault root; do not open a
   subfolder.
3. Start from `Home.md`, the map of content.

The generated `.gitignore` already excludes Obsidian's per-device files
(`.obsidian/workspace.json`, `.obsidian/workspace-mobile.json`) and WhyKit's own
lock and crash-recovery files under `.whykit/`. Whether you commit the rest of
`.obsidian/` (themes, hotkeys, enabled core plugins) is a team choice; WhyKit
ignores it either way.

## Recommended settings

These are Obsidian core settings, not plugins:

| Setting | Value | Why |
|---|---|---|
| Files and links → Use `[[Wikilinks]]` | On | WhyKit resolves wikilinks for links, backlinks and orphan checks. |
| Files and links → New link format | Absolute path in vault | Matches the `[[06-decisions/d-001-...]]` links WhyKit writes. Bare names become ambiguous as the vault grows, which the linter reports as `wikilink.ambiguous`. |
| Files and links → Default location for new attachments | In the folder specified below: `assets` | The conventions keep attachments in `assets/`. |
| Templates (core plugin) → Template folder location | `templates` | Reuse the starters that ship with the vault. |

## Creating notes

You can create notes in Obsidian or with the CLI. The CLI is safer for anything
with an ID:

- **Decisions and evidence: use the CLI.** `whykit new decision` and
  `whykit new evidence` allocate the next `D-NNN` / `E-NNN` and update the index
  in the same operation. Doing that by hand is how two people end up with two
  `D-014`s.
- **Notes: either works.** `whykit new note "Title" --workstream notes --link-from Home.md`
  creates a note with complete front matter and links it from a map so it is not
  an orphan. In Obsidian, insert a starter from `templates/`, then change
  `status: template` to `status: draft`, set `owner`, and set `created` and
  `last_updated` to today (the starters carry the date the vault was created).
  Link the note from a map before you commit.

Every substantive note needs front matter (Obsidian shows it as *Properties*).
The required keys are listed in [Configuration](configuration.md#core-document-metadata).
A note nothing links to is reported as `note.orphan`: link it from `Home.md` or a
folder's map.

## Links

- Internal notes: `[[06-decisions/d-001-ship-sso-before-audit-logs|D-001]]`.
  A path plus a readable label stays unambiguous when titles repeat.
- External sources: ordinary Markdown links, and register them as evidence if a
  claim depends on them.
- Embeds (`![[...]]`) are checked too; a broken one is `embed.missing`.
- Files in other repositories are not wikilinks. `INTEROP.md` in the vault says
  how to point at them.

## Callouts for information types

Obsidian callouts keep the five information types visible while reading:

```markdown
> [!fact] Verified fact
> Most onboarding tickets in Q3 mention SSO (E-001).

> [!hypothesis] Hypothesis
> SSO is the main blocker for larger accounts.

> [!decision] Decision
> Ship SSO before audit logs. See [[06-decisions/d-001-ship-sso-before-audit-logs|D-001]].
```

`fact`, `hypothesis` and `decision` are not built-in Obsidian callout types.
Obsidian renders unknown types with the default callout style, so they display
fine without any plugin or CSS snippet. Add a CSS snippet if you want them
coloured differently.

The linter reads `[!fact]` callouts: one without an inline `E-NNN` citation is a
`fact.inline_evidence` warning.

## Checking from Obsidian

Obsidian does not run WhyKit for you. Keep a terminal open on the vault, or use
the pre-commit hook:

```bash
whykit lint                    # from inside the vault directory
whykit install-hooks           # check before every commit
```

If you sync the vault with a Git plugin that commits automatically, remember the
hook is only as strong as the `whykit` binary on that machine's `PATH`; see
[CI: local hooks](ci.md#local-hooks). CI is the gate that cannot be skipped.

## Graph view and WhyKit's graph

Obsidian's graph view shows wikilinks. WhyKit's graph adds two relations
Obsidian does not know about, evidence citations and decision supersession:

```bash
whykit graph --format obsidian      # JSON with typed nodes and edges
whykit graph --format dot           # Graphviz
whykit backlinks D-001              # what links to a decision, note or E-NNN
```

## Sync and sensitivity

Obsidian Sync, iCloud and similar services copy every file in the vault. WhyKit's
`sensitivity` labels are metadata, not access control: a `restricted` note is
synced like any other. Decide where the vault may live before you put
confidential material in it, and read the [security policy](../SECURITY.md).
