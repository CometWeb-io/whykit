# Changelog

All notable changes to WhyKit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
semantic versioning, where the **data contract** is the public API alongside the
CLI and the lint rule codes.

## [Unreleased]

### Added

- Optional `[evidence_access_age_days]` policy warns (`evidence.access_missing`,
  `evidence.access_stale`, `evidence.access_future`) when active sources of
  configured types lack a recent `Accessed` date. The policy is off by default
  and does not certify claim truth.

### Fixed

- `adopt` now checks front matter through the linter's actual parser/rules,
  explains that its score is not a full vault lint, flags known incompatible
  register/log tables, and reports non-Markdown or unreadable files omitted from
  its scan.
- Permanent ingestion records retain complete SHA-256 digests instead of only
  16-character prefixes; readiness assessment uses the same bytes as hashing.
- Invalid list-valued lifecycle metadata produces lint findings instead of a
  `TypeError` traceback.
- Numbered `AGENTS.md` TODO items are now counted, so the starter vault surfaces
  all four unanswered agent-contract questions.

### Security

- Vault mutation lock uses OS advisory `flock` / Windows file locking on
  `.whykit/mutation.lock` instead of a directory lock, so crash/SIGKILL cannot
  leave the vault permanently unwritable.
- Multi-file ledger writes go through journaled transactions
  (`.whykit/transactions/<id>/` with READY → COMMITTED); pending READY journals
  are recovered under the mutation lock.
- Secret scan fails closed on non-UTF-8 and unreadable candidate files
  (`secret.scan_non_utf8`, `secret.scan_unreadable`).
- `whykit pack` marks document bodies as untrusted data and prepends a
  prompt-injection boundary to every agent preamble (`content_trust`).
- MCP `query` filtered a non-existent `hits` field while leaving unfiltered
  `results` intact; sensitivity is now applied in `query_vault()` via
  `allowed_sensitivities` before ranking/limit, and `context`/`impact` recursively
  filter graph expansions (incoming/outgoing/references/evidence).
- CI/release Actions pinned to full commit SHAs; gitleaks upgraded to 8.30.1
  with SHA-256 verification of the downloaded archive.
- The retained CI secret-scan output omits Gitleaks verbose findings, which can
  include commit author and email metadata; local maintainers can use verbose
  mode when investigating a finding.
- MCP defaults to `--max-sensitivity internal`, rejects negative `max_chars`,
  and clamps result budgets; `whykit serve` refuses non-loopback hosts when
  any non-public documents would be bundled.
- MCP sensitivity denials return the same empty shape as a missing target, so
  callers cannot confirm filtered records or their sensitivity labels. A real
  stdio-client test invokes `query`, `context`, and `impact` against that ceiling.
- Public-only MCP context omits evidence-register details because evidence rows
  lack per-entry sensitivity labels and the register itself defaults to internal.
- `whykit serve` refuses non-loopback exposure when confidential/restricted notes
  are present unless the operator explicitly passes `--allow-sensitive-network`.
- The composite GitHub Action installs the exact checked-out action revision
  instead of an unrelated/latest PyPI package, passes untrusted inputs through
  environment variables rather than shell interpolation, and fails closed when
  decision/review-history verification is requested from a shallow clone. It can
  now run repository-local `ci`/`release` policy profiles directly, accepts an
  explicit `base` ref for non-PR history gates, and retains legacy lint-mode
  inputs for compatibility.

### Changed

- CI tests the full supported range through Python 3.14; package classifiers now
  advertise 3.14, verified locally with Python 3.14.6.
- The optional MCP server now targets SDK 2.x explicitly; CI exercises all three
  read-only tools and sensitivity denials through the SDK client, and the README
  links to a dedicated stdio setup and sensitivity-boundary guide.
- Fresh-vault CI now asserts the default, `--full`, and legacy `--minimal`
  layouts, and verifies that the expected policy finding — not an arbitrary
  command failure — is what rejects an unconfigured vault.
- New vaults now default to the vendor-neutral layout; the former GTM-oriented
  workstream starter is opt-in with `whykit init --full`. `--minimal` remains
  accepted as an alias so existing setup scripts keep working.
- CI now exercises the platform-specific Windows mutation lock and the full unit
  suite on Windows, in addition to the Python-version matrix on Linux.
- README now leads with the Git-native evidence-and-decision ledger, gives a
  runnable checkout-based quick start, and states the product's verification and
  privacy boundaries before the command catalogue. It distinguishes local, CI,
  and clean-commit release gates; contributor smoke instructions use a fresh
  temporary vault and do not suggest stashing work as release verification. The
  docs now link directly to the Code of Conduct. Source-checkout instructions
  avoid public PyPI installs until release, adoption examples name their target
  vault, local docs links are tested, and the README diagram uses a repository-
  relative asset path.
- Northline's competitor walkthrough uses reserved `.example` sources and
  fictional Vendor A / Vendor B profiles instead of attributing synthetic
  findings to real companies. Maintainer positioning no longer implies verified
  CometWeb dogfooding; the publication checklist now calls out the historical
  versions of those examples before repository visibility changes.
- Explorer index build (`apps/explorer/scripts/build-vault-index.mjs`) delegates
  exclusively to `whykit explorer-index` — one Python parser for the UI contract.
- `VaultIndex` provides a single parse pass shared by lint, query, context,
  graph, impact, backlinks, status, pack and Explorer export.
- Local development and GitHub Actions use [uv](https://docs.astral.sh/uv/)
  (`uv.lock`, `uv sync --locked`, `uv run`, `uv build`). Runtime remains
  dependency-free; the composite GitHub Action still installs via `pip` so
  consumers do not need uv on the runner.
- Snapshot CI artifacts write under `.whykit/` (gitignored), matching the
  documented local snapshot path.

### Fixed

- Concurrent first writes no longer fail if multiple writers create the vault's
  `.whykit` metadata directory at the same time; the path is revalidated after
  the collision to preserve symlink and vault-boundary checks.
- Explorer's generated lint summary now includes the Markdown file count
  required by the TypeScript `VaultIndex` contract.
- The linter no longer exempts the retired root-level `NAMING.md` file from
  front-matter and orphan checks.
- `whykit init --minimal --force` no longer deletes pre-existing adopter files:
  minimal layout is applied only in an isolated staging directory, then merged.
- Mutating writes go through `safe_vault_target` / `safe_vault_dir`, which refuse
  parent-directory symlinks that would escape the vault root.
- Release workflow checks out full history (`fetch-depth: 0`), requires the tag
  to be an ancestor of `origin/main`, and fails closed when a real history
  baseline cannot be resolved (no more `HEAD...HEAD` pass).
- Relative vault paths now resolve both sides before comparison, so macOS
  `/var` ↔ `/private/var` aliasing no longer breaks status, graph, snapshot,
  doctor, or policy-report path fields.
- Lint-scoped paths and wikilinks can no longer traverse outside the selected vault boundary.
- Decision history now protects immutable reasoning rather than freezing the entire file: approved records may only refresh review metadata or make a metadata-only transition to `superseded`/`archived`; rationale/body edits still fail the history gate. Quoted statuses and YAML comments are recognized.
- Decision lineage now rejects supersession cycles and multiple approved replacements of the same predecessor instead of leaving ambiguous current truth in a manually edited vault.
- Markdown table parsing now preserves escaped pipes and wikilink aliases inside cells.
- Relative Markdown links are linted for missing targets and vault-boundary escapes, not only wikilinks.
- `whykit adopt` refuses overlapping source/vault trees, skips Markdown symlinks that resolve outside the import root, and verifies source hashes again before staging.
- Explorer Markdown rendering always advances on unsupported syntax instead of hanging on malformed/non-supported block markers.
- Explorer link/backlink resolution is pre-indexed rather than repeatedly reparsing the whole vault.
- Explorer wordmark no longer carries its former mark.
- `whykit init` stamps generated front matter with the actual creation date instead of preserving template build dates.
- Public documentation no longer advertises removed/nonexistent baseline, SARIF or legacy configuration flags.
- Installed and checkout pre-commit hooks now execute `check --profile local` so local validation uses the same versioned repository policy model as CI/release.

### Fixed (prior)

- Python 3.11 compatibility: `whykit review` no longer raises
  `SyntaxError` from an f-string backslash in the review-log template.
- Nested-vault history: `whykit history --root <subdir>` correctly resolves
  Git paths when the vault is not the work-tree root (the layout CI uses for
  `examples/northline`).
- Concurrent `new decision` / `new evidence` / `review` / `evidence retire` /
  `adopt --write` mutations serialize under a vault-wide lock so parallel
  agents cannot allocate duplicate D-NNN / E-NNN IDs.
- Built-in secret scan fails closed on files larger than 5 MiB instead of
  silently skipping them (`secret.scan_skipped_large_file`).
- Duplicate front-matter keys are rejected; unknown `[defaults]` / top-level
  `whykit.toml` keys fail validation; `adopt` hashes content from the same
  bytes it assesses and refuses symlink escape of `.import-staging`.

### Added

- `whykit explorer-index` exports the Explorer `vault.json` payload from the
  canonical Python parser.
- `whykit init --minimal` — vendor-neutral vault layout without GTM workstream
  folders; companion `examples/tiny/` (two evidence rows, one decision) for a
  fast CI-checked first contact.
- `whykit graph --format obsidian` (and `--output`) for a portable Graph-Analysis
  friendly JSON export; `whykit backlinks <target>` for the CLI mirror of Explorer
  inbound links.
- Optional hub-map lint via `require_hub_links` in `whykit.toml` profiles/defaults
  (`hub.unlinked_workstream`); Obsidian embeds `![[…]]` report as `embed.missing`
  when unresolved.
- `whykit adopt --profile generic|adr-only|obsidian-loose`, `--json` readiness
  scoring (estimated minutes to first green lint), and `MIGRATION.md` on `--write`.
- `whykit pack --for generic|cursor|claude|codex` stable agent preambles without
  changing the vault format; optional read-only MCP server via `whykit[mcp]` /
  `whykit-mcp`.
- `whykit new decision|evidence|note` scaffolds records, allocates stable IDs and keeps registry rows synchronized; approved supersession updates predecessor lifecycle metadata and the decision index transactionally. Mutating create commands now expose `--json`, and `new note --link-from <map.md>` can atomically keep strict vaults free of accidental orphan notes.
- `whykit status` exposes deterministic vault metrics and a review queue in human or JSON form.
- Repository-local, versioned `whykit.toml` policy with `local`, `ci` and fail-closed `release` profiles, plus `whykit policy` and `whykit check`.
- `whykit query` provides ranked, metadata-filtered discovery with a versioned JSON contract.
- `whykit context` emits bounded context packs around one evidence ID, decision or document, including supporting evidence, relationships, supersession lineage and scoped findings.
- `whykit pack` combines explicit targets with ranked query results under a total body-size budget, deduplicates supporting evidence and can render JSON or Markdown handoff bundles.
- `whykit review list|record` adds an operational review queue and append-only review-event ledger.
- `whykit evidence list|retire` manages active/retired evidence without deleting or reusing stable IDs, with validated replacement chains.
- `whykit snapshot` and `verify-snapshot` fingerprint governed content and distinguish byte-level drift from time-based health drift.
- `whykit graph` exports typed `wikilink`, `evidence` and `supersedes` relations as JSON or Graphviz DOT for agents and external tooling.
- `whykit impact` traces reverse dependencies for evidence IDs, decisions and documents before they are retired, superseded or renamed.
- `whykit rules` now exists as documented, with a machine-readable catalog for every lint rule code; `--markdown` renders the canonical documentation table from the same registry so rule docs cannot silently drift.
- Versioned JSON Schemas for query results, context packs, snapshots, typed graphs, policy-gate reports and review events.
- Explorer evidence backlinks, active/retired evidence state, review queue/history and ranked search.
- `docs/rules.md` and `docs/configuration.md`, which were previously linked but missing.

## [0.2.0] — 2026-09-17

Candidate for the first public preview. The package has not been published to
PyPI; install it from a source checkout as described in the README.

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
