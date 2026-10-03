# Security model

What WhyKit protects, where it draws trust boundaries, what it guarantees at
each one, and what it does not. Read it before you point WhyKit at a vault
that holds confidential material, run it on pull requests from people you do
not fully trust, or expose a vault to an agent through the MCP server.

To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Assets

| Asset | Why it matters |
|---|---|
| Vault content | Evidence, decisions and notes; often internal or confidential business material. |
| Sensitivity labels | Decide what the MCP server and Explorer exports show. A label is metadata, not access control. |
| Decision history | Accepted reasoning is append-only; the history gate is what makes a rewrite visible. |
| The CI gate result | A pull request that passes `whykit check` is trusted more than one that does not. |
| Credentials near the vault | The CI token, an MCP bearer token, secrets that someone pasted into a note by mistake. |
| The machine running WhyKit | CPU, memory and terminal of a developer laptop or CI runner. |

## Who and what is untrusted

WhyKit treats all of these as hostile input:

- **Vault content.** Note text, front matter, file and folder names, links, the
  evidence register, the review log. In a pull request the author controls all
  of it, including names with line breaks, control characters, bidirectional
  overrides and look-alike digits.
- **Git history and revision names.** Paths in a tree, object contents, and the
  `--base` / `--head` values given to `whykit check`, `whykit history` and
  `whykit diff`.
- **Team policy in `whykit.toml`.** Custom rule patterns are written by people,
  and in a pull request by its author.
- **MCP clients.** An agent connected to `whykit-mcp` may be following
  instructions planted in a note. It can call every tool, page with any cursor
  and ask for completions.
- **Web pages** in the browser of someone running the MCP server or Explorer on
  their machine.

The process running WhyKit, the user who started it and the repository
permissions are trusted. WhyKit never asks for elevated rights.

## Guarantees

### Parsing vault content

- **No crash on hostile content.** A note that is not valid UTF-8, front matter
  with arbitrary nesting or broken quoting, a link whose name the operating
  system rejects (too long, embedded NUL) or a malformed table row becomes a
  finding (`frontmatter.invalid`, `wikilink.missing`, …). It never stops the
  command for the rest of the vault.
- **Bounded time.** The front matter, table, wikilink and Markdown link parsers
  are close to linear in the size of the input. A note built from thousands of
  repeated brackets, unclosed wikilink openers or unclosed link destinations
  costs about as much as any other note of that size.
- **No path traversal through links.** A wikilink or Markdown link that leaves
  the vault (`..`, an absolute path, a symlink pointing outside) resolves to
  nothing and is reported (`wikilink.outside`, `markdown_link.outside`). Link
  resolution never opens a file outside the vault root.
- **No mutation through symlinks.** Commands that write refuse to write through
  a symlinked directory or file inside the vault, and every multi-file change is
  staged and applied atomically under one lock.

### Team policy patterns

- A custom rule pattern is rejected before any note is read when it has a
  shape that takes exponential or high polynomial time: backreferences,
  conditional groups, quantifiers nested in a repeated group, a repeated group
  that can match nothing, alternation inside a repeated group, or adjacent
  repeats that can match the same text. The check reads the pattern as the
  engine parses it, so verbose mode and inline comments cannot hide a shape.
- Patterns are matched one line at a time, on at most the first 10,000
  characters of a line, and all pattern checks in one run share a time budget.
  When the budget is spent, the checks that did not run are reported as
  findings of the rule. They are never skipped silently.
- Path globs cannot leave the vault (`..`, absolute paths and drive letters are
  rejected).

### Git history and `whykit diff`

- A revision that starts with `-` is refused before Git sees it, so a
  revision name cannot become a Git option.
- `whykit diff` reads both revisions from Git objects into a fresh temporary
  directory. It skips symlinks and submodules, refuses tree paths with empty,
  `.`, `..` or `.git` components, and never writes outside that directory.
- Git output is read in its NUL-separated form; a listing that does not parse
  fails the command rather than being half-read.

### MCP server

- **Nothing above the ceiling is visible.** Every tool, resource, prompt,
  completion and change notification is computed from a view of the vault
  without the notes above `--max-sensitivity`. A hidden note looks exactly like
  one that does not exist: same error, same shape, no count, no backlink, no
  ambiguity with a visible twin.
- **Unreadable labels fail closed.** A note whose front matter does not parse,
  or that spells the key differently (`Sensitivity:`), is treated as above
  every ceiling. A note with valid front matter and no label is `internal`.
- **Evidence follows the register's label.** Register rows, counts and `E-NNN`
  completions are shown only when `00-context/evidence-register.md` itself is
  within the ceiling.
- **Cursors cannot count hidden rows.** They are bound to the call, the
  ceiling and the visible ordering with a per-process key.
- **No writes.** No tool modifies the vault, runs Git or reaches the network.
- **Streamable HTTP.** A non-loopback `--host` requires a bearer token
  (compared in constant time). Without a token, a request whose `Host` or
  `Origin` header does not name the machine itself is rejected, however the
  loopback address was spelt, which blocks DNS rebinding from a web page.

### Output that people and CI systems read

- Human-readable output of `whykit lint`, `whykit check`, `whykit history`,
  `whykit diff`, the `whykit new decision --from` dry run and parse cache
  warnings prints vault-derived text (paths, titles, finding messages) on
  one line, with line breaks, control characters and bidirectional overrides
  shown as visible escapes. Vault content cannot start a new log line that a CI
  runner would read as a workflow command, and cannot send terminal escape
  sequences.
- GitHub annotations (`--format github`) escape their data and properties per
  GitHub's rules.
- The pull request comment written by the Action escapes HTML, Markdown
  syntax, mentions and autolinks in vault text, so a change under review cannot
  add markup, ping people or plant a clickable link. The hidden marker that
  identifies the comment cannot be closed early.
- The Action passes its GitHub token only to the step that posts the comment,
  never to `whykit diff` while it reads the change under review, and refuses to
  run that step for any event other than `pull_request`.

### Explorer

- Note content is rendered as text, never as HTML. Links are followed only for
  `http:` and `https:` targets or vault records; images in notes are not loaded.
- The built page carries a Content Security Policy without `unsafe-inline` or
  `unsafe-eval`, and loads its index as data, not as script.

## Non-guarantees

- **Sensitivity is a filter, not access control.** Anyone who can read the
  repository can read every note. The MCP ceiling decides what a client is
  shown, not what the server process can read.
- **The secret scan is a safety net.** It catches common credential shapes in
  text files, not encoded or novel secrets, and does not inspect binary files.
  Use your Git host's secret scanning as well.
- **A pull request can change its own policy.** `whykit check` reads
  `whykit.toml` from the checked-out change. A pull request that edits it can
  relax its own gate (switch a profile's history check off, demote a rule)
  in the same change. Security-relevant overrides are printed with every run
  and need a written reason, but they are not refused. Protect `whykit.toml`
  with a CODEOWNERS entry and required review, as you would a CI workflow.
- **Explorer exports include every label.** `npm run index` writes confidential
  and restricted notes into the static build and only warns. Do not publish an
  Explorer build of a vault that is not public. The Explorer server binds to
  loopback unless you pass `--allow-sensitive-network`.
- **Bodies are untrusted data for agents.** The MCP server marks returned
  content as untrusted, but it cannot stop an agent from following
  instructions written in a note.
- **No TLS.** Put a TLS-terminating proxy in front of an MCP server that other
  machines can reach.
- **Timing is not constant.** Response times depend on vault size. The server
  does not try to hide how many notes exist from a client that can measure
  time precisely.
- **Large vaults cost time.** Commands read every note. Bounded parsing keeps
  one hostile note from costing more than a normal note of its size, but a
  vault of many very large files still takes time to read.

## How this is tested

- `tests/fuzz_parsers.py` is a seeded fuzz harness for the front matter,
  table, link, custom-rule and Git-path parsers, plus every read-only command
  on a vault filled with generated notes. Every case runs under a time and
  memory budget, and a scaling check fails any parser whose cost grows
  superlinearly. A short fixed-seed round runs with the test suite; run more
  with `python tests/fuzz_parsers.py --seed N --cases 3000`.
- `tests/test_adversarial_inputs.py` pins each crash, hang and injection the
  harness or review found, and `tests/test_mcp_security.py` the sensitivity
  and transport rules above.
