# Editor integration

`whykit lsp` is a small language server. Any editor that can start a language
server over stdio gets lint results and vault navigation from it, without a
WhyKit plugin for that editor. It ships with the core package and needs nothing
beyond Python.

The server is read-only: it never writes, renames or formats a file, and it
offers no code actions. Everything it reports comes from the same parser and
linter as `whykit lint`.

## What it does

| Feature | Behaviour |
|---|---|
| Diagnostics | Lint findings, with the rule code and a range on the offending link, ID or line. The whole vault is checked when the editor connects, on save and when files change on disk; while you type, the edited files are re-checked from the unsaved buffer after a pause |
| Completion | Inside `[[`: note paths, aliases and decision IDs (`[[D-0` offers `D-002` with its title and status). Elsewhere: `E-NNN` and `D-NNN` identifiers, with retired evidence marked deprecated |
| Hover | On an `E-NNN`: source, type, dates, location and what it supports, or when and why it was retired. On a `D-NNN` or a wikilink: title, type, status, owner, review date and cited evidence |
| Go to definition | A wikilink opens its note (at the heading, for `[[note#Heading]]`); an `E-NNN` jumps to its row in the evidence register; a `D-NNN` opens the decision record |
| Document links | Wikilinks and local Markdown links that resolve become clickable |

IDs and links inside inline code or fenced code blocks are examples of the
syntax, so the server ignores them, exactly as the linter does.

## Start the server

```bash
whykit lsp
```

The editor starts this command for you; you do not run it in a terminal. With
no options, each open file is served by the vault around it (the nearest
directory with `Home.md` and `00-context/`), so one server handles several
vaults. `--root DIR` pins it to a single vault.

| Option | Use it when |
|---|---|
| `--root DIR` | The editor's workspace is not the vault, or you want one vault only |
| `--debounce MS` | You want a longer pause after typing before the edited file is re-checked (default 300) |
| `--today YYYY-MM-DD` | You want review dates evaluated as of a fixed day |
| `--stdio` | Your client always passes it; it is accepted and ignored |

## Configuration examples

The snippets below are examples, not maintained integrations: editor
configuration formats change, so check them against your editor's current
documentation. Each one starts `whykit lsp` for Markdown files. If `whykit` is
not on the editor's `PATH`, use the absolute path of the executable (find it
with `command -v whykit`).

### VS Code

VS Code has no built-in way to start an arbitrary language server. A generic
client extension, such as *Generic LSP Client (v2)*, provides one. With it
installed, add to `.vscode/settings.json` in the vault:

```json
{
  "glspc.server.command": "whykit",
  "glspc.server.commandArguments": ["lsp"],
  "glspc.server.languageId": ["markdown"]
}
```

### Neovim

Neovim 0.11 or newer, in `init.lua`:

```lua
vim.lsp.config("whykit", {
  cmd = { "whykit", "lsp" },
  filetypes = { "markdown" },
  root_markers = { "Home.md" },
})
vim.lsp.enable("whykit")
```

On older versions, start it from a `FileType markdown` autocommand with
`vim.lsp.start({ name = "whykit", cmd = { "whykit", "lsp" }, root_dir = vim.fs.root(0, { "Home.md" }) })`.

### Helix

In `languages.toml`, define the server and add it to Markdown next to any
server you already use:

```toml
[language-server.whykit]
command = "whykit"
args = ["lsp"]

[[language]]
name = "markdown"
language-servers = ["marksman", "whykit"]
```

### Zed

Zed starts only the language servers its extensions declare, so settings
alone cannot add a new one. They can point an existing Markdown server at a
different binary. This example replaces the Marksman server with WhyKit's for
Markdown files (you lose Marksman's features in exchange):

```json
{
  "lsp": {
    "marksman": {
      "binary": { "path": "whykit", "arguments": ["lsp"] }
    }
  }
}
```

To run both, the alternative is a small Zed extension that declares a
`whykit` language server; that is outside what this repository ships.

## Limits

- The evidence register, the decision log and `whykit.toml` are read from disk
  by the linter, so findings that depend on them follow the saved file. Hover
  and completion read an open, unsaved evidence register from the buffer.
- Findings that compare files (orphans, duplicate decision IDs, the secret
  scan, the review log, path collisions, `AGENTS.md`) come from the last
  whole-vault check while you type, and are refreshed on save.
- On a very large vault the whole-vault check takes as long as `whykit lint`
  (see [Performance](performance.md)); it runs in the background, and typing
  only re-checks the files you edit.
- The editor must tell the server about files changed outside it (a `git
  checkout`, for example). Clients that support watched files do this
  automatically; otherwise saving any note triggers a whole-vault check.
