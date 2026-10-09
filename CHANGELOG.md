# Changelog

All notable changes to WhyKit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
semantic versioning, where the **data contract** is the public API alongside the
CLI and the lint rule codes.

## [Unreleased]

The draft for **0.3.0**, the first public package release. It covers every
change since the internal 0.2.0 milestone, including the `0.3.0.dev0` source
preview that source checkouts report until the release. Upgrading from a
checkout of 0.2.0 or the preview: read the
[0.3 migration guide](docs/migration-0.3.md) first.

Copyright is held by Maciej Zmitrukiewicz. CometWeb is the product name and
hello@cometweb.io the contact; CometWeb is not the copyright holder.

### Highlights

- **Every decision traced to its evidence.** `whykit trace` follows each
  decision to the sources it cites, through supersession, and flags missing,
  retired and stale ones; `--gaps-only --strict` makes it a CI gate.
- **A machine contract you can build on.** Every command that writes JSON
  emits exactly one versioned document with stable error codes and exit codes,
  described by JSON Schemas; lint also speaks SARIF and GitHub annotations.
- **Records, reviews and evidence from the CLI.** `new`, `review`, `evidence`,
  `query`, `context`, `pack`, `impact`, `graph`, `status`, `snapshot` and a
  versioned `whykit.toml` policy with `local`, `ci` and `release` profiles.
- **Fast on large vaults.** On 5,000 notes `lint` takes about 1.5 s instead of
  108 s, and `pack` about 1 s instead of 675 s, with identical output.
- **Team rules without a fork.** `[[rules.custom]]` in `whykit.toml` adds a
  team's own conventions as rule codes, and `[rules.overrides]` retunes
  built-in rules per path, while security-relevant rules cannot be switched
  off without a written reason that every run reports.
- **What a pull request does to the reasoning.** `whykit diff` lists new,
  superseded and archived decisions, review-date moves, changed evidence and
  the decisions citing it, and the Action can keep it as one pull request
  comment.
- **A read-only MCP server** with seven tools, resources and prompts, behind a
  sensitivity ceiling that hidden records cannot leak through: typed
  structured results, signed page cursors, completions and change
  notifications, over stdio or token-protected streamable HTTP.
- **Safe, portable writes.** Journaled multi-file transactions under an OS
  lock, read-only files refused up front, and identical results on Linux,
  macOS and Windows, including legacy code-page consoles. Python 3.11–3.14.
- **A vendor-neutral starter vault** by default, and `whykit adopt` to bring
  existing Markdown and ADRs in.
- **Reproducible, attested packages** with an SBOM, published only from a
  version tag through PyPI trusted publishing.

### Breaking changes

#### Controlled decision approval

- New decisions start as draft/in-review. `review approve` previews the complete
  record, evidence and diff, then applies only its `--expect-hash` snapshot in a
  recoverable transaction. Supersession occurs at approval, not draft creation.
- Review logs accept `approved` events with record/snapshot SHA-256 receipts.
  History requires a new matching event for new accepted records, including
  staged first commits. Existing baseline history is preserved. Reviewer labels
  remain assertions; they do not authenticate identity or validate source truth.
  See [migration](docs/migration-0.3.md#review-history-and-interrupted-writes).
  A [worked approval record](examples/approval/README.md) demonstrates the event
  format without modifying the existing historical examples.


Each entry says what an existing vault, script or pipeline has to change. The
[migration guide](docs/migration-0.3.md) has the details and examples.

- `whykit snapshot` writes format `whykit.snapshot/v2` by default. v2 hashes
  text with one leading UTF-8 byte-order mark removed and CRLF turned into LF,
  and records that in a new `normalization` key, so a baseline taken on Linux
  verifies on a Windows checkout. `verify-snapshot` reads the format from the
  baseline, so existing v1 baselines keep verifying byte for byte.
  *Migration:* nothing for existing baselines; pass `whykit snapshot --format
  v1` if a consumer parses v1 only.
- The per-module entry points (`python -m whykit.lint`, `python -m
  whykit.graph` and 16 others) are removed. *Migration:* use the `whykit`
  command; `whykit-mcp` and `python -m whykit.mcp_server` are unchanged.
- An unexpected exception exits 70 with error code `internal_error` (stdout is
  the usual error object under `--json`), so a WhyKit bug can no longer be
  mistaken for a vault problem (1) or a usage error (2). *Migration:* treat
  70 as "report a bug", not as a failed gate.
- `whykit rules --json` prints an object,
  `{"contract_version": 1, "count": N, "rules": [...]}`, instead of a bare
  array; `rules <code> --json` adds `contract_version`. *Migration:* read
  `.rules` instead of the top-level array.
- `evidence retire` and `review record` exit 1 instead of 2 when the evidence
  ID or record they name does not exist (error code `not_found`), matching the
  read commands.
- `new` exits 1 instead of 2 when the vault has no evidence register or
  decision log to append to (error code `vault_invalid`, with a hint to restore
  the file). `explorer-index` on a vault with lint errors still exits 1 and now
  prints the `vault_invalid` error object.
- `whykit lint <path>` exits 2 for a path that does not exist, escapes the
  vault, or is not Markdown, instead of linting zero files and reporting
  "clean". *Migration:* fix the path in the calling script.
- `whykit init` writes the vendor-neutral layout by default. The former
  GTM-oriented workstream starter is opt-in with `whykit init --full`;
  `--minimal` remains accepted as an alias of the default, so existing setup
  scripts keep working.
- The optional MCP extra targets the MCP SDK 2.x (`mcp>=2.2,<2.4`).
  *Migration:* reinstall with the `mcp` extra to upgrade the SDK.
- `whykit-mcp` defaults to `--max-sensitivity internal`. *Migration:* pass a
  higher ceiling explicitly if a host must see confidential records.
- Duplicate front-matter keys are rejected, and unknown `[defaults]` or
  top-level `whykit.toml` keys fail validation instead of being ignored.
- `whykit new` names a record whose title has no Latin letters or digits
  (`会议记录`, `Протокол`) `record-<8 hex digits>.md`, derived from the title,
  instead of `record.md`, so a second such title no longer collides with the
  first. Letters that do not decompose are folded instead of dropped
  (`Łódź` gives `lodz`, not `odz`). Existing files are not renamed.
  *Migration:* scripts that predicted the file name should read `path` from
  `new --json`.
- In the Explorer graph, Space selects the focused note instead of opening it;
  Enter (or a double click) opens it. The graph is now a single Tab stop:
  arrow keys move between notes instead of Tab.
- New lint rules can turn a passing `--strict` run red: the warnings
  `decision.placeholder`, `path.case_collision`, `frontmatter.empty`,
  `source_of_truth.invalid`, `fact.evidence_missing` and
  `markdown_link.missing` (now also for missing local images), and the errors
  `secret.scan_non_utf8`, `secret.scan_unreadable`,
  `secret.scan_skipped_large_file` and `embed.missing`. The rule catalog is
  in [Lint rules](docs/rules.md).

### Added

- Document the private-vault dogfood record: tool/vault revisions, dirty-state
  hashes, command/date, counts and PASS/FAIL, with raw operating notes kept out
  of public artifacts.

- `adopt --compare DIR` checks same-path Markdown after migration without
  writing either tree. Typed per-file hashes and issues cover lost wikilinks,
  E-/D-IDs, native declarations and canonical review-log rows; code examples
  are excluded. Unknown/unreadable or missing files fail closed. No inferred
  path mapping, generic history conversion or Git history preservation claim.

- `whykit workspace ROOT…` reports independent vault lint and review state,
  with each vault's policy and review window, isolated failures, alias
  deduplication and typed JSON. Overlapping roots are rejected; no vault notes
  or parse caches are written. This is a local owner report, not a public export.

#### Commands

- `whykit trace` follows every decision to the evidence it cites, including
  evidence inherited through supersession, and flags missing, retired and
  stale sources. `--gaps-only --strict` turns it into a CI gate; the staleness
  window reuses the `[evidence_access_age_days]` policy behind
  `evidence.access_stale`.
- `whykit new decision|evidence|note` scaffolds records, allocates stable IDs
  and keeps registry rows synchronized; approved supersession updates the
  predecessor's lifecycle metadata and the decision index transactionally.
  Mutating create commands expose `--json`, and `new note --link-from
  <map.md>` atomically keeps strict vaults free of accidental orphan notes.
- `whykit status` reports deterministic vault metrics and a review queue, in
  human or JSON form.
- A repository-local, versioned `whykit.toml` policy with `local`, `ci` and
  fail-closed `release` profiles, plus `whykit policy` and `whykit check`.
- `whykit query` provides ranked, metadata-filtered discovery with a
  versioned JSON contract.
- `whykit context` emits bounded context packs around one evidence ID,
  decision or document, including supporting evidence, relationships,
  supersession lineage and scoped findings.
- `whykit pack` combines explicit targets with ranked query results under a
  total body-size budget, deduplicates supporting evidence and renders JSON or
  Markdown handoff bundles. `pack --for generic|cursor|claude|codex` adds a
  stable preamble for that host without changing the vault format.
- `whykit review list|record` adds an operational review queue and an
  append-only review-event ledger.
- `whykit evidence list|retire` manages active and retired evidence without
  deleting or reusing stable IDs, with validated replacement chains.
- `whykit snapshot` and `verify-snapshot` fingerprint governed content and
  distinguish byte-level drift from time-based health drift.
- `whykit graph` exports typed `wikilink`, `evidence` and `supersedes`
  relations as JSON, Graphviz DOT, a Mermaid flowchart with escaped labels
  (`--format mermaid`), or a portable Obsidian Graph-Analysis JSON
  (`--format obsidian`, with `--output`).
- `whykit backlinks <target>`, the CLI mirror of the Explorer's inbound links.
- `whykit diff --base <ref> [--head <ref>]` reports what a change does to the
  decisions: new, superseded and archived decisions with their supersession
  chain, status and review-date moves, evidence added, retired or re-sourced,
  decisions citing changed evidence, and lint findings introduced or fixed.
  It reads both revisions from Git without a checkout, writes `text`, `json`,
  `markdown` (a pull request comment) or `github`, and is a report, not a
  gate: it exits 0 whenever the comparison ran.
- `whykit impact` traces reverse dependencies of evidence IDs, decisions and
  documents before they are retired, superseded or renamed.
- `whykit rules` is a machine-readable catalog of every lint rule code;
  `--markdown` renders the documentation table from the same registry, so the
  rule docs cannot silently drift. `rules --root <vault>` lists that vault's
  custom rules and overrides after the built-in catalog.
- `whykit explorer-index` exports the Explorer `vault.json` payload from the
  canonical Python parser.
- `whykit adopt --profile generic|adr-only|obsidian-loose`, `--json`
  readiness scoring (estimated minutes to first green lint) and a
  `MIGRATION.md` on `--write`. `adopt` reports decision IDs claimed twice,
  within the import or by the vault already (`score.id_conflicts` and the
  migration report).
- `whykit completion bash|zsh|fish` prints a shell completion script generated
  from the live CLI, so new commands and options complete without a release.
- `whykit history --staged` checks the staged changes instead of a head
  commit.
- `whykit init --minimal`, a vendor-neutral layout without GTM workstream
  folders, and the companion `examples/tiny/` (two evidence rows, one
  decision) for a fast, CI-checked first contact.
- `--json` on `init`, `history`, `graph` and `pack` (`graph` and `pack` keep
  `--format`; combining `--json` with another format is a usage error).
- `--root` is accepted after a nested action as well as before it
  (`whykit new decision "Title" --root vault`).

- `whykit lsp`, a read-only language server over stdio for editors: lint
  diagnostics as you type (including for an unsaved evidence register),
  wikilink and `E-NNN`/`D-NNN` completion, hover, go-to-definition and
  document links. It never writes a file. Setup for VS Code, Neovim, Helix
  and Zed is in [Editors](docs/editors.md).
- `whykit new decision --from FILE` promotes an existing ADR (MADR, Nygard,
  Y-statement or Polish headings), for example one staged by `adopt`, into a
  decision record. It prints the section mapping, unmapped headings and
  evidence candidates as a dry run, and writes the record and its
  decision-log row only with `--write`; the original text is kept under
  "Original record" and its SHA-256 in provenance, so the same file is not
  promoted twice.
- `--no-cache` (or `WHYKIT_NO_CACHE=1`) turns off the new parse cache; see
  Performance.

#### Machine contract and CI formats

- A machine contract for every command that writes JSON: stdout holds exactly
  one document, success and failure alike carry `contract_version`, and a
  failure is an error object with a stable `code`, a `message` and a `hint`.
  Error codes are listed in `whykit.contract.ERROR_CODES`, and every command's
  output has a JSON Schema in `schemas/` (19 new schemas, checked by the test
  suite), alongside the versioned schemas for query results, context packs,
  snapshots, typed graphs, policy-gate reports and review events. See
  [Automation](docs/automation.md) for exit codes and the stability policy.
- `whykit lint --format sarif` writes SARIF 2.1.0 for code scanning (schema
  `schemas/lint-sarif.schema.json`, every rule linked to its anchor in
  `docs/rules.md`), and `lint --format github` / `check --format github` print
  GitHub workflow annotations.
- `schemas/diff-report.schema.json` describes `whykit diff --json`.
- `lint --json` and `check --json` carry an `overrides` array when a policy
  override is in effect or the secret scan was switched off, and `rules
  --json` marks each rule `security` and, for team rules, `custom`.

#### Lint rules

- `decision.placeholder` (warning): a draft, in-review or approved decision
  record still holds the prompts `whykit new decision` writes, or an empty
  context, decision, rationale, alternatives or consequences section. The
  prompts come from one shared module, so the rule follows the scaffold. A
  vault fresh from `init` plus one `new decision` shows five warnings.
- `path.case_collision` (warning): two notes whose paths differ only by letter
  case or Unicode normalization, which a macOS or Windows checkout cannot keep
  apart.
- `frontmatter.empty` (a required key with no value), `source_of_truth.invalid`
  (a non-boolean `source_of_truth`) and `fact.evidence_missing` (a fact callout
  citing an unregistered `E-NNN`), all warnings. Missing local Markdown images
  are reported under `markdown_link.missing`.
- An optional `[evidence_access_age_days]` policy warns
  (`evidence.access_missing`, `evidence.access_stale`,
  `evidence.access_future`) when active sources of configured types lack a
  recent `Accessed` date. It is off by default and does not certify that a
  claim is true.
- Optional hub-map lint via `require_hub_links` in `whykit.toml` profiles or
  defaults (`hub.unlinked_workstream`); unresolved Obsidian embeds `![[…]]`
  report as `embed.missing`.
- Team rules in `whykit.toml`. `[[rules.custom]]` defines rules with a
  `custom.` code that check required front-matter keys and values, required
  sections, required and forbidden patterns, a minimum number of cited
  sources and a maximum age, scoped by type, status, path and workstream.
  `[rules.overrides."<code>"]` raises, lowers or switches off any rule, for
  the whole vault or for path globs. Patterns that can take exponential time
  are rejected, and an invalid policy names the offending key
  (`rules.custom[0].forbidden_patterns[0] …`): `check`, `policy`, `rules` and
  `status` stop with `invalid_config`, `lint` reports it as `config.invalid`.
  See [Configuration](docs/configuration.md#team-rules).

#### MCP server

- An optional read-only MCP server, installed with the `mcp` extra and run as
  `whykit-mcp`, with `query`, `context`, `impact`, `status`, `pack`, `trace`
  and `backlinks` tools, read-only record resources, two prompts (summarize a
  decision, list decisions with evidence gaps), and the server version in its
  handshake.
- Every tool honours the configured sensitivity ceiling: hidden records are
  indistinguishable from missing ones and do not consume the body budget,
  bodies are marked `content_trust: "untrusted_data"`, and every failure,
  including an argument the SDK rejects against the input schema, returns the
  same structured JSON error body without leaking internal messages. See
  [MCP server](docs/mcp.md).
- Every tool declares an `outputSchema` derived from the CLI's JSON contract,
  and every successful result carries matching `structuredContent`; the
  wheel ships the base schemas in `whykit/contract_schemas/`.
- `query`, `trace`, `backlinks` and `resources/list` are paged: pass a
  result's `next_cursor` back as `cursor`. A stale, altered or foreign cursor
  is refused with `invalid_cursor`.
- `completion/complete` offers visible decision IDs, evidence IDs and vault
  paths for the prompt arguments and the `whykit://record/{+target}`
  template.
- On the 2026-07-28 protocol, `subscriptions/listen` streams
  `notifications/resources/updated` and `list_changed` when a visible record
  changes; `--watch-interval` sets the polling interval (`0` turns it off).
- `whykit-mcp --http` serves streamable HTTP at `/mcp`, on `127.0.0.1:8000` by
  default, with an optional bearer token from `--token-file` or
  `WHYKIT_MCP_TOKEN`.

#### GitHub Action and hooks

- The composite Action accepts a `today` input and exposes the installed
  WhyKit version as the `version` output; `annotations` and `sarif` inputs
  and a `sarif-file` output connect it to code scanning. It installs into a
  private virtual environment, so it never changes the Python used by later
  steps.
- The Action's `comment` input keeps the decision diff as one pull request
  comment, updated on every push and appended to the job summary;
  `github-token` chooses the token that writes it. CI runs it on this
  repository's own pull requests.
- Pre-commit hooks (`.pre-commit-hooks.yaml`): `whykit-lint`, and
  `whykit-history` backed by `whykit history --staged`.
- Vaults created by `init` ignore `.whykit/mutation.lock` and
  `.whykit/transactions/`.

#### Explorer

- A decision timeline, an evidence freshness view, and a supersession chain on
  decision pages that walks back to the first decision and forward to the
  current one.
- Evidence backlinks, active and retired evidence state, the review queue and
  history, citations and the vault policy in the Explorer index.
- Ranked and multi-term search, URL deep links for every view and filter that
  survive reloads, paged long lists, a graph layout without overlapping nodes,
  graph labels measured instead of truncated, and hover highlighting of a
  note's direct links.
- The graph is keyboard-operable as one listbox: arrow keys, Home and End,
  type-ahead to a title, Space to select and Enter to open, at 5,000 notes
  too.
- When the vault holds `confidential` or `restricted` notes, `npm run index`
  warns and every page of the build shows a banner with their count.
- A README, ESLint, a unit test suite (`npm test`, included in `npm run
  check`) and a Playwright end-to-end suite (desktop and mobile, with axe
  accessibility checks) run against static builds.

- `npm run build:single` writes the Explorer as one self-contained HTML file
  that opens straight from disk (`file://`), with its own Content Security
  Policy (hash-pinned inline script and style, still no `unsafe-inline`).
- A print stylesheet: a decision or note prints as a clean document without
  navigation.

#### Documentation, packaging and tooling

- Documentation: a docs index; concepts, CI integration, Obsidian,
  troubleshooting and FAQ pages; a command reference (`docs/cli.md`) checked
  against the argument parser; `docs/rules.md` and `docs/configuration.md`;
  two tutorials the test suite runs as written; and a 60-second quickstart in
  the README. A test checks every documented command against the real CLI.
- Packaging: PEP 639 licence metadata, an exactly pinned build backend,
  reproducible archives under `SOURCE_DATE_EPOCH`, `scripts/check_dist.py`
  (archive contents, metadata, byte-identical rebuilds, SBOM contents), and a
  CycloneDX SBOM of a clean install. See [Releasing](docs/releasing.md).
- `scripts/bench.py` (with `--mcp` and `--memory` for MCP latency and peak
  memory), a deterministic synthetic vault generator and
  [Performance](docs/performance.md), with timing budgets and output digests
  in the test suite.
- CI runs the suite and the example vaults on Windows, including a legacy
  `cp1252` console, and the Explorer end-to-end suite as an optional job.
- `scripts/release_rehearsal.py` rehearses a release locally against one
  clean commit, offline: version from the changelog, two reproducible builds,
  `check_dist.py`, `twine check`, a wheel smoke test (`init`, `lint`, `trace`,
  MCP tool listing) on every supported Python installed, release notes, and
  the tag commands for the maintainer. It never tags, pushes or uploads.
- [Security model](docs/security-model.md): what WhyKit protects, its trust
  boundaries, what it guarantees and what it does not. `SECURITY.md` links it
  and states what is in scope for a report.
- `tests/fuzz_parsers.py`, a seeded fuzz harness for the front matter, table,
  link, custom-rule and Git-path parsers and every read-only command, with a
  time and memory budget per case and a check that fails superlinear parser
  cost. A fixed-seed round runs with the test suite.
- [Editors](docs/editors.md) for `whykit lsp`, and the Explorer README's
  security headers for GitHub Pages, Netlify, Cloudflare Pages and nginx.
- `scripts/bench.py --cache` (cold and warm parse cache) and `--runs N`.
- [Migrating to 0.3](docs/migration-0.3.md), for vaults, scripts and CI
  coming from 0.2.0 or the source preview.

### Changed

#### Command behaviour

- `query --type decision` (or any `--type`) no longer lists the template for
  that type; ask for `--status template` to find templates.
- `status` counts only real decision records; the decision log and the
  decision template are no longer counted as decisions.
- `evidence retire` says how many references are on live records and how many
  are on superseded or archived ones.
- `check` lists the findings that fail the gate.
- `history` warns on stderr when it found no decision records under `--root`,
  and when `--head HEAD` leaves uncommitted Markdown edits unchecked.
- The decision history check protects immutable reasoning instead of freezing
  the whole file: an approved record may only refresh review metadata or make
  a metadata-only transition to `superseded` or `archived`; rationale and body
  edits still fail. Quoted statuses and YAML comments are recognized.
- Decision lineage rejects supersession cycles and multiple approved
  replacements of the same predecessor instead of leaving the current truth
  ambiguous in a manually edited vault.
- `pack` packs a record once even when it is named several ways (an ID, a
  path, a stem, a query hit).
- `review list` ends with the window it searched (for example "0 review(s)
  overdue or due by 2026-11-02") instead of a bare count.
- Evidence IDs inside inline code or fenced code blocks are examples, not
  citations: `graph`, `impact`, `trace`, `context`, `query` and the Explorer
  index all use the same rule.
- The front matter parser accepts column-zero, four-space and compact nested
  block lists, a UTF-8 byte-order mark, and a closing fence at end of file
  without a newline. Block scalars are rejected with a specific message.
- `init` stamps generated front matter with the actual creation date, prints
  the bare `whykit lint` command as its next step when WhyKit is installed as
  a tool and `uv run whykit` only from a source checkout. `whykit init --full`
  describes itself as the starter with optional workstreams; template wording
  is vendor-neutral.
- `whykit-mcp` without the MCP extra prints how to install it and exits 2.
- `install-hooks` works for a vault in a subdirectory of a repository and in
  a linked worktree (Git decides where the hook goes, so `core.hooksPath` is
  respected), and the hook checks the vault, not the repository root.
  `doctor` finds the hook there too.
- `doctor` counts the same findings as `lint`, orphan-note warnings included.
- `new note` with a title that already exists names the existing note and
  suggests a more specific title.
- Installed and checkout pre-commit hooks run `check --profile local`, so local
  validation uses the same versioned policy model as CI and release.

- `whykit -h` lists the commands grouped by job (author, check, explore,
  integrate, maintain), and `--root DIR` may come before the command
  (`whykit --root vault lint`) as well as after it. `adopt` accepts `--root`
  as a spelling of `--into`, `serve` accepts `--root` as well as a positional
  vault, `doctor` accepts `--today` and `snapshot` accepts `--json`.
- An unknown option names the command whose `-h` lists the accepted ones.
- A `.md` suffix in any letter case marks a Markdown file for every command,
  so `notes/Old.MD` is no longer skipped on Linux and macOS while being read
  on Windows.
- The Explorer loads lint findings only when Health opens, so the bundled
  index no longer carries them.
- `whykit serve` from an installed copy without the Explorer says where the
  copy was installed from and how to run the Explorer from that checkout or a
  clone of the same commit.
- `whykit context` budgets report `front_matter_omitted` when front matter was
  dropped to fit.

#### Messages and output

- JSON, DOT and Mermaid output is always UTF-8, whatever the console encoding.
  Human output on an ASCII or legacy code-page console falls back to ASCII
  spellings and escapes instead of failing with `UnicodeEncodeError`.
- Commands outside a vault explain how to recover and name the path they
  checked. OS errors and a closed stdout no longer print a traceback;
  `WHYKIT_DEBUG=1` restores it for bug reports. `history` explains an unknown
  revision or a directory outside a Git work tree instead of dumping Git
  usage.
- Every CLI option has help text; a bare `whykit` prints help and exits 2.
- `lint` text output gives each finding as `error    line 17    [code]`, so
  the rule code starts in the same column for errors and warnings and a line
  number is never glued to the level.
- `adopt` caps its file column at 60 cells and measures it in terminal cells,
  so wide CJK file names keep the assessment column aligned.
- Invalid dates name the flag and the expected `YYYY-MM-DD` format; `check
  --today` rejects the same non-calendar ISO forms as `lint --today`.
- The Explorer names a vault after its README title or first heading, never
  "README"; vaults created by `init --minimal` get the title "Company knowledge
  vault".

#### Internals

- `VaultIndex` provides a single parse pass shared by lint, query, context,
  graph, impact, backlinks, status, pack and the Explorer export. `graph`,
  `backlinks`, `impact`, `status`, `snapshot` and `lint` resolve each distinct
  link target once per run, and `snapshot` parses the vault once.
- The Explorer index build (`apps/explorer/scripts/build-vault-index.mjs`)
  delegates to `whykit explorer-index`: one Python parser for the UI contract.

#### Project, CI and documentation

- CI tests the full supported range, Python 3.11 through 3.14, and the package
  classifiers advertise 3.14 (verified locally with Python 3.14.6). CI also runs the full suite, including the
  Windows mutation-lock implementation, on Windows.
- The MCP CI job exercises the read-only tools and sensitivity denials
  through the SDK client, and the README links to a dedicated stdio setup and
  sensitivity-boundary guide.
- Fresh-vault CI asserts the default, `--full` and legacy `--minimal` layouts
  and checks that the expected policy finding, not an arbitrary command
  failure, is what rejects an unconfigured vault.
- Local development and GitHub Actions use [uv](https://docs.astral.sh/uv/)
  (`uv.lock`, `uv sync --locked`, `uv run`, `uv build`). The runtime remains
  dependency-free, and the composite Action still installs via `pip`, so
  consumers do not need uv on the runner.
- Snapshot CI artifacts are written under `.whykit/` (ignored by Git),
  matching the documented local snapshot path.
- The release workflow builds and checks the archives in one job and
  publishes, still only from a version tag, exactly the artifacts that job
  checked. That build job (`release-build.yml`) also runs on every pull
  request as a dry run that cannot publish.
- The README leads with the Git-native evidence-and-decision ledger, gives a
  runnable checkout-based quick start, and states the verification and privacy
  boundaries before the command catalogue. It distinguishes local, CI and
  clean-commit release gates; contributor smoke instructions use a fresh
  temporary vault and do not suggest stashing work as release verification;
  source-checkout instructions avoid public PyPI installs until release;
  adoption examples name their target vault; the diagram uses a
  repository-relative path; and the docs link to the Code of Conduct.
- `docs/rules.md` is generated by `whykit rules --markdown` and has one anchor
  per rule; the guide is shorter and links to the reference pages. Public
  documentation no longer advertises removed or nonexistent baseline, SARIF or
  legacy configuration flags.
- Examples: the tiny example records `D-002`, which supersedes `D-001`.
  Northline's competitor walkthrough uses reserved `.example` sources and
  fictional Vendor A / Vendor B profiles instead of attributing synthetic
  findings to real companies, and Northline records the partner-channel
  evidence for its direct motion in D-011, which supersedes D-010 instead of
  rewriting it, so `whykit trace --strict` passes on the example.
- Maintainer positioning no longer implies verified dogfooding; the
  publication checklist calls out the historical versions of the examples
  before repository visibility changes. The Explorer wordmark no longer
  carries its former mark.

### Fixed

- Subcommands use the regular argument parser for their own help. The main
  parser keeps grouped command help; `lint --help` and other leaf commands no
  longer crash while attempting to render an empty top-level command list.

- Query cursors now bind the content of visible matches, so a metadata/body edit
  cannot mix two versions of the result set while retaining the same path order.
  Hidden changes still leave cursors valid.


- Explorer export defaults to public with fail-closed labels and transitive
  reference withholding; private local viewing is an explicit opt-in. Product
  Explorer build and E2E/a11y jobs now block CI and release gates.
- Explorer lockfile uses patched source-map-js 1.2.2 for GHSA-68fv-2mgg-jv7q.

- `check --base` evaluates the current vault under both the pinned base policy
  and the current policy. The report adds `history_checked`; optional or disabled
  history is printed as SKIP rather than an executed OK.
- ADR promotion refuses approved/superseded/archived output; the source status
  remains in provenance. `decision.unreviewed` warns when approval contradicts
  `human_reviewed`; strict profiles fail it. The decision schema requires
  `review_by` for approved metadata and rejects an explicitly denied review.
- The MCP stdio negotiator uses a bounded queue and bounded discovery reply
  bookkeeping while preserving classic fallback after a burst of probes.
- `init --profile gtm` names the optional workstream scaffold explicitly;
  `--full` remains a compatibility alias and default init stays neutral.

- Multi-file commits keep their staged bytes until COMMITTED, recover legacy
  journals from hash-matching targets, and retry interrupted cleanup without
  overwriting a later edit. Real directory-sync I/O failures are reported.
  `new note --link-from` uses the same transaction as decision/review writes.
- The history gate rejects review-date changes without matching new confirmed
  review events in the same diff. It validates the complete deadline chain,
  reviewer, dates and target, including staged changes.
- Review tables share header/separator parsing across lint, history, review
  writes and Explorer; column padding cannot hide events. Explorer's Markdown
  renderer handles escaped pipes and unclosed wikilinks like the Python parser.

#### Decision history and lineage

- `whykit history` checked the wrong thing in several cases: a record with a
  non-ASCII file name escaped the check (Git C-quoted the name; paths are now
  read NUL-separated), a `status:` line in the body could stand in for the
  front matter, `Approved` in another case was not recognised, a vault in a
  subdirectory was missed when run from inside it, and a non-UTF-8 locale
  could crash it. A shallow clone with no merge base gets a hint to fetch full
  history.
- `whykit history --root <subdir>` resolves Git paths when the vault is not
  the work-tree root (the layout CI uses for `examples/northline`).
- A superseded decision whose successor was itself superseded later
  (D-001 → D-002 → D-003) no longer raises `decision.superseded_by_missing`.
- Supersession works when the vault path is a symlink.

#### Links and parsing

- A wikilink spelled with a different case or Unicode normalization than the
  note's path resolves to the real note on every operating system, instead of
  resolving on macOS and Windows and failing on Linux.
- Wikilinks to attachments (`![[diagram.png]]`, `[[brief.pdf]]`) resolve by
  file name or vault path; escaped alias pipes inside tables keep the link
  target; backslash paths resolve; NFC link text matches NFD file names.
- Markdown table parsing preserves escaped pipes and wikilink aliases inside
  cells, and a stray `[[` in a register or log table no longer swallows the
  following cells or hides an evidence row from ID allocation.
- Relative Markdown links are linted for missing targets and vault-boundary
  escapes, not only wikilinks; lint-scoped paths and wikilinks can no longer
  traverse outside the selected vault.
- Canonical-only graphs never contain edges to excluded nodes; an ambiguous
  bare stem or duplicate decision ID is reported instead of silently picking
  one.
- The review log table header is accepted when padded by a Markdown
  formatter.
- `whykit.toml` with a UTF-8 byte-order mark is read instead of rejected.
- Invalid list-valued lifecycle metadata produces lint findings instead of a
  `TypeError` traceback.
- Numbered `AGENTS.md` TODO items are counted, so the starter vault surfaces
  all four unanswered agent-contract questions.
- The linter no longer exempts the retired root-level `NAMING.md` file from
  front-matter and orphan checks.

- Notes that used to stop a command now produce a finding: a link target
  longer than the file system allows or containing a NUL byte, a target
  naming an unknown home directory (`~user/…`), a file that is not valid
  UTF-8 (read with the bad bytes replaced and reported as
  `frontmatter.invalid`), and deeply nested inline lists in front matter.
- Decision and evidence IDs use ASCII digits only, so `E-００１` in fullwidth
  digits is no longer a second ID that looks like `E-001`.

#### Lint findings

- `evidence.retired` is no longer reported on superseded or archived records,
  which must not be rewritten; and the `—` that `evidence retire` writes when
  nothing replaced a source no longer fails lint.

#### Writing to a vault

- `review record --today` with a past date can no longer move `last_updated`
  backwards (which produced a lint error), and a review dated before the
  record was created is refused without writing anything.
- Rewriting a CRLF file keeps it CRLF.
- `new` prints vault-relative paths with forward slashes on Windows too.
- Text given on the command line in a C locale is written back unchanged.
- Concurrent `new decision`, `new evidence`, `review`, `evidence retire` and
  `adopt --write` runs serialize under a vault-wide lock, so parallel writers
  cannot allocate duplicate D-NNN or E-NNN IDs.
- Concurrent first writes no longer fail when several writers create the
  vault's `.whykit` metadata directory at once; the path is revalidated after
  the collision to keep the symlink and vault-boundary checks.
- Relative vault paths resolve both sides before comparison, so macOS
  `/var` ↔ `/private/var` aliasing no longer breaks the path fields of
  `status`, `graph`, `snapshot`, `doctor` or policy reports.

#### init

- `init` refuses a symlink on the path you named, and refuses the WhyKit
  package source tree even with `--force`.
- `init` reports a non-directory target as a usage error instead of raising a
  traceback, including with `--force`.
- `init --force` preserves existing vault files instead of replacing colliding
  template paths; it keeps custom `.gitignore` rules and appends WhyKit's
  protective patterns when they are missing. `init --minimal --force` no
  longer deletes pre-existing files: the minimal layout is applied in an
  isolated staging directory, then merged.
- `init` and `init --minimal` no longer leave `AGENTS.md` telling people to
  write an ingestion record in `07-research/sources/` after that directory has
  been removed; the layout step rejects any remaining reference to a deleted
  workstream.

#### adopt

- `adopt` stages short but structured ADRs instead of discarding them as
  stubs, skips binary files named `.md`, tolerates a byte-order mark, parses
  CRLF front matter the same way as normal linting (including on Windows),
  and stages each batch atomically, so an interrupted run never leaves a half
  batch that a later run picks up.
- `adopt --write` stages only useful files; `adr-only` no longer copies
  hundreds of files classified as unsupported, nor heading-only stubs marked
  for discard.
- Repeated same-day `adopt --write` runs keep separate staging batches and
  ingestion receipts; a source `MIGRATION.md` is no longer replaced by the
  generated migration report.
- `adopt` checks front matter through the linter's own parser and rules,
  explains that its score is not a full vault lint, flags known incompatible
  register and log tables, and reports non-Markdown or unreadable files left
  out of its scan.
- The committed ingestion record names the source directory only, not its
  absolute path, which could publish a local home directory.
- Permanent ingestion records keep complete SHA-256 digests instead of
  16-character prefixes, and readiness assessment and hashing use the same
  bytes.
- `adopt --json` prints forward-slash paths on Windows.
- `adopt` reports a Markdown symlink as the duplicate of the file it points
  to, never the other way round.

#### Output and portability

- DOT output keeps each statement on one line whatever the title contains,
  and Mermaid labels escape `&` and control characters.
- `whykit-mcp` degrades on an ASCII or legacy code-page console instead of
  failing on help or log output.
- Output to a pipe that closes early (`whykit lint --json | head -1`) exits 1
  without a traceback or `Exception ignored` message, whatever the format.
- Python 3.11: `whykit review` no longer raises `SyntaxError` from an
  f-string backslash in the review-log template.

#### MCP server

- Hosts that connect with the classic `initialize` handshake are served again
  when a `server/discover` probe came first on the same stdio pipe (a probe
  that timed out while the server started, then a fallback); they got error
  `-32022`. The `mcp` extra is now `mcp>=2.2,<2.4`, the SDK releases CI tests.
- MCP tools answer from a view of the vault in which records above the
  sensitivity ceiling do not exist, so a hidden record can no longer show up
  as an ambiguous link, a backlink, a duplicate decision ID or a lint finding.
- `context-pack.schema.json` accepts the single register row that `context`
  returns as `evidence` for an `E-NNN` target.

#### Explorer

- Accessibility violations reported by axe.
- Index bodies no longer include part of the YAML front matter, and the
  generated lint summary includes the Markdown file count the TypeScript
  `VaultIndex` contract requires.
- Markdown rendering always advances on unsupported syntax instead of hanging
  on malformed or unsupported block markers.
- Link and backlink resolution is pre-indexed instead of reparsing the whole
  vault repeatedly.

### Security

- Evidence tables support an optional final Sensitivity column and
  `new evidence --sensitivity`. Writers preserve labels during retirement;
  filtered readers honor register/replacement floors, withhold invalid labels
  and duplicate IDs, and share captured contents and classification. MCP hides
  raw labeled tables and source row offsets; public Explorer exports keep safe
  rows. `evidence.sensitivity` starts as a warning. Legacy tables keep inherited
  classification; see the migration note and example D-002.

- MCP results have a separate 1 MiB compact UTF-8 JSON budget, including both
  tool representations, metadata and resource/prompt results. Oversized results
  return `response_too_large` rather than incomplete success data. The result
  budget excludes the enclosing JSON-RPC/transport framing and report-building
  memory; document `max_chars` budgets retain their character-based meaning.
- Streamable HTTP enforces a 1 MiB body budget, 30-second body receive deadline,
  eight active authorized requests and a shared 60-request burst refilling at
  two requests/second, after authentication/Host checks. Connection/task cap 32
  in Uvicorn. Rate/busy/body failures have documented HTTP statuses and codes.
  Limits are per process; remote TLS/proxy controls remain operator-owned.
- MCP transport guards cap stdio input lines at 1 MiB, JSON request IDs at
  1024 UTF-8 bytes and JSON-RPC envelopes at 2 MiB. Stdio checks the actual
  output including its newline and closes on oversized input without draining
  it. HTTP preflight and stdio validation return fixed errors without echoing
  peer data. Buffered diagnostic stdout is flushed before descriptor restoration.


#### Vault writes and files

- A change that would rewrite a file marked read-only is refused before any
  file is written (error code `io_error`), instead of silently replacing it on
  POSIX or failing halfway on Windows.
- The vault mutation lock uses OS advisory `flock` (Windows file locking) on
  `.whykit/mutation.lock` instead of a directory lock, so a crash or SIGKILL
  cannot leave the vault permanently unwritable.
- Multi-file ledger writes go through journaled transactions
  (`.whykit/transactions/<id>/`, READY then COMMITTED); pending READY journals
  are recovered under the mutation lock.
- Mutating writes go through `safe_vault_target` / `safe_vault_dir`, which
  refuse parent-directory symlinks that would escape the vault root.
- `init --force` refuses a symlinked destination directory or `.gitignore`,
  and `graph` / `snapshot --output` refuse symlinked targets instead of
  resolving them to another file inside or outside the vault.
- `adopt` refuses overlapping source and vault trees, skips Markdown symlinks
  that resolve outside the import root, refuses a symlink escape of
  `.import-staging`, and verifies source hashes again before staging.

#### Secret scan

- The built-in secret scan fails closed on non-UTF-8 and unreadable candidate
  files (`secret.scan_non_utf8`, `secret.scan_unreadable`) and on files larger
  than 5 MiB (`secret.scan_skipped_large_file`) instead of silently skipping
  them.
- Security-relevant rules (the secret scan, `sensitivity.invalid`,
  `wikilink.outside`, `markdown_link.outside`) can be lowered or switched off
  by a team policy only with a written `reason`. Every such override is
  printed with each `lint` and `check` run, listed in the JSON `overrides`
  array with the findings it suppressed, kept in SARIF as a suppressed result
  and annotated as a GitHub `::notice`.
- Switching the whole secret scan off, with `lint --no-secrets` or a profile's
  `secrets = false`, is no longer silent: it is reported the same way, as an
  `overrides` entry for `secret.*` with `skipped_by`.

#### Agents, the MCP server and the Explorer server

- `whykit pack` marks document bodies as untrusted data and prepends a
  prompt-injection boundary to every agent preamble (`content_trust`).
- MCP targets are validated before touching the filesystem: absolute paths,
  URLs, `~`, `.` and `..` segments and control characters are rejected, and
  symlinks out of the vault are not followed.
- MCP defaults to `--max-sensitivity internal`, rejects a negative
  `max_chars` and clamps result budgets.
- MCP `query` filtered a non-existent `hits` field while leaving the
  unfiltered `results` intact; sensitivity is now applied in `query_vault()`
  through `allowed_sensitivities` before ranking and the limit, and `context`
  and `impact` recursively filter graph expansions (incoming, outgoing,
  references, evidence).
- MCP sensitivity denials return the same empty shape as a missing target, so
  callers cannot confirm filtered records or their sensitivity labels; a real
  stdio-client test calls `query`, `context` and `impact` against that
  ceiling.
- Public-only MCP context omits evidence-register details, because evidence
  rows have no per-entry sensitivity label and the register itself defaults
  to internal.
- MCP over HTTP: a non-loopback `--host` is refused without a bearer token;
  the token (at least 32 characters, compared in constant time) is checked
  before any MCP processing, with `unauthorized` and HTTP 401 otherwise; on
  loopback, non-local `Host` and `Origin` headers are rejected against DNS
  rebinding.
- MCP page cursors are bound by a per-process MAC to the tool, arguments,
  ceiling and visible ordering, and never count or encode a hidden record;
  completions and change notifications see only visible records.
- The Explorer build carries a Content Security Policy that allows only its
  own origin, with no inline script or style.
- `whykit serve` refuses a non-loopback host when any non-public document
  would be bundled into the Explorer, unless the operator passes
  `--allow-sensitive-network`.

- The MCP server fails closed on labels: a note whose front matter does not
  parse, or that spells the key differently (`Sensitivity:`), is treated as
  above every ceiling, and evidence register rows, counts and completions are
  shown only when the register itself is within the ceiling.
- MCP over HTTP without a token rejects a foreign `Host` or `Origin` however
  the loopback bind address was spelt, not only for the exact default host.

#### Parsing and output

- Wikilink, Markdown link and table parsing is linear in the size of the
  note; a note of repeated `[` or `|` characters could take quadratic time.
- Custom rule patterns in `whykit.toml` are parsed and checked for
  catastrophic backtracking, including shapes that hide from a token scan,
  under one time budget shared by all patterns; a check that runs out of
  budget says so instead of passing silently.
- Human-readable output of `lint`, `check`, `history`, `diff` and the
  `new decision --from` dry run prints vault text on one line, with line
  breaks, control characters and bidirectional overrides shown as visible
  escapes, so a note cannot forge a CI workflow command or send terminal
  escape sequences.

#### GitHub Action, CI and releases

- The composite Action installs the exact checked-out action revision instead
  of an unrelated or latest PyPI package, passes untrusted inputs through
  environment variables rather than shell interpolation, and fails closed
  when decision or review history verification is requested from a shallow
  clone. It can run repository-local `ci` and `release` policy profiles
  directly, accepts an explicit `base` ref for non-PR history gates, and keeps
  the legacy lint-mode inputs for compatibility.
- The release workflow checks out full history, requires the tag to be an
  ancestor of `origin/main`, and fails closed when a real history baseline
  cannot be resolved (no more `HEAD...HEAD` pass). Its publish job downloads
  only the distributions the build job checked; the build backend and its
  plugins are pinned exactly, and the SBOM tool is pinned in the release
  tooling group.
- Repository automation: every third-party action is pinned to a full commit
  SHA, workflows declare least-privilege permissions and do not persist the
  checkout token, and workflows are audited with zizmor and OpenSSF
  Scorecard. Dependabot covers every ecosystem with a cooldown.
- gitleaks is upgraded to 8.30.1 with SHA-256 verification of the downloaded
  archive, and the retained CI secret-scan output omits gitleaks' verbose
  findings, which can include commit author and email metadata; maintainers
  can use verbose mode locally when investigating a finding.
- Contributor tooling (ruff, mypy, zizmor, twine) is pinned in `uv.lock`; CI
  checks that the lockfile matches `pyproject.toml` and validates the
  distribution metadata.
- The decision diff comment escapes text taken from the vault (no HTML,
  links or mentions), edits only a bot-written comment carrying its marker,
  never posts for a fork's pull request, and turns an API refusal into a
  warning.
- `SECURITY.md` matches a public repository.

- The Action passes its GitHub token only to the step that posts the
  comment; `whykit diff` reads the change under review without it.
- The pull request comment no longer autolinks URLs from vault text, and the
  hidden marker that identifies it cannot be closed early by a note.

### Performance

- Query filters/ranking retain lightweight note references; CLI renders only its
  result limit and MCP renders only its verified page. Full visible ordering and
  exact total remain unchanged; a one-result page no longer builds all summaries.


- Large vaults are much faster: on a 5,000-note vault `lint`, `status`,
  `check` and `explorer-index` take about 1.5 s, `context` and `pack` about
  1 s (from 108 s, 65 s, 125 s, 78 s, 163 s and 675 s). Output is unchanged.
- The secret scan reuses one read per file and skips files that cannot match;
  path ordering, relative paths and note bodies are computed once and share
  strings. At 20,000 notes `lint` went from 7.9 s to 3.7 s and the secret scan
  from 1.45 s to 0.31 s; output is unchanged.
- The MCP server keeps parsed notes between calls and reuses a file's parsed
  note only while its modification time, change time, size and inode are
  unchanged; a file written in the last two seconds is never cached. Repeated tool calls
  on a large vault no longer re-parse it.
- The Explorer bundles everything except note bodies and loads the bodies
  after the first paint, so time to interactive on the 5,000-note synthetic
  vault fell from 760–970 ms to 320–360 ms; its budget dropped from 4 s to
  1.5 s, with a new 2.5 s budget for a deep link to a note's text.

- Read commands keep a parse cache in `.whykit/cache/`: each note's front
  matter and derived views (links, citations, secret-scan hits), reused only
  while the file's `stat` signature is exactly unchanged, verified by
  SHA-256 when it was written within two seconds, and discarded whenever the
  WhyKit version, its source, the Python version or `whykit.toml` changes.
  Output is identical with the cache on, off, cold or warm. On 5,000 notes a
  warm cache takes `lint` from 0.81 s to 0.43 s, `status` from 0.90 s to
  0.49 s and `trace` from 0.74 s to 0.40 s; a cold run, which writes the
  cache, costs about 10% more than running without it.

## [0.2.0] — 2026-09-17

Internal milestone. It was never tagged and never published. Source checkouts
after it report `0.3.0.dev0`; their changes are listed under [Unreleased].

### Added

- **Buildable package.** Wheel and source distribution include the CLI and vault
  template, so adoption will not require forking the repository. Version 0.2.0
  was not published to PyPI; until a package release exists, install from a
  source checkout.
- **`whykit adopt`.** Inventories an existing directory of Markdown, hashes
  every file, flags duplicates and empty stubs, and detects existing ADRs by
  filename or by a Context+Decision heading pair. Stages into `.import-staging/`
  and writes an ingestion record; never writes into a workstream.
- **`CometWeb-io/whykit` GitHub Action.** Lints a vault and enforces decision
  immutability on pull requests in three lines of workflow.
- **`whykit install-hooks`.** Installs the pre-commit vault check into a vault
  created by `init`, which previously had no way to get one.
- **`whykit history`.** The decision-immutability check, promoted from a loose
  script to a first-class subcommand with a `--root`.
- **`provenance` front-matter block** and `schemas/provenance.schema.json`. A
  neutral extension namespace so any producing system can record what wrote a
  note without the format growing vendor-specific fields.
- **`lint --today`.** Evaluates review dates as of a fixed date, making runs
  reproducible and the overdue rule testable.
- **Lint rule `decision.review_missing`** (warning): an approved decision with no
  `review_by` is how a vault goes quietly stale.
- **Lint rule `agents.unconfigured`** (warning): `AGENTS.md` ships with questions
  only the adopting company can answer, and an unanswered contract is worse than
  an absent one.
- **Lint rule `agents.absent`** (warning) for a vault with no agent contract.
- `docs/integrations.md`, `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, a release
  workflow with PyPI trusted publishing, and a "why not ADR" comparison.

### Fixed

- **First-run failure.** `lint <a-vault-directory>` used the working directory as
  the vault root, so linting another vault reported every wikilink and evidence
  ID as broken — around 120 phantom errors on the bundled example. A positional
  path that is itself a vault root is now used as the root, and the linter also
  walks up from the working directory, so `whykit lint` works from any
  subdirectory of a vault.
- A vault created by `init` no longer ships broken wikilinks to files that only
  existed in the source repository.
- The pre-commit hook no longer assumes a `scripts/` directory that a generated
  vault does not have.
- The secret scan runs the gitleaks binary rather than the official Action, which
  requires a paid licence key for organisation-owned repositories and would have
  broken CI the day the project moved under an org.

### Changed

- **The repository is no longer itself a vault.** Tooling lives in
  `src/whykit/`, the vault template in `src/whykit/template/`. Being both a
  template and a tool was the root cause of most of the above.
- The Explorer's CI job is non-blocking. It is a viewer, not part of the data
  contract, and it must not be able to block a fix to the linter.
- CI runs on Python 3.11, 3.12 and 3.13, and verifies that a built wheel installs
  into a clean environment and can create and lint a vault with no checkout
  present.
- Every approved decision in the example vault now carries a `review_by` date, so
  the example demonstrates the rule instead of tripping it.
- CometWeb's authorship is stated in `README.md` and `NOTICE` rather than
  appearing only in a copyright line.

## [0.1.0] — 2026-09-17

Initial implementation. No package was published under this version.

- Portable Markdown/Obsidian vault structure.
- Evidence register with stable `E-NNN` identifiers.
- Append-only decision records with stable `D-NNN` identifiers.
- Deterministic linting for structure, links, evidence, decision IDs, lifecycle
  rules and common secret leaks.
- JSON Schemas, a worked example vault, and an optional local Explorer.
