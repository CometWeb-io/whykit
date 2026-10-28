# Knowledge v2 and exact-byte migration

The v2 interchange model describes `Claim`, `EvidenceArtifact`, `Decision`,
`ReviewEvent` and `Outcome`. It separates their source formats from the typed
view; existing Markdown and captured JSON remain readable by their original
readers. A review event's process outcome is distinct from an Outcome metric.
The schemas are [knowledge-v2](../schemas/knowledge-v2.schema.json) and
[knowledge-migration](../schemas/knowledge-migration.schema.json).

```bash
uv run whykit knowledge view --root /path/to/vault --json
uv run whykit knowledge view --root /path/to/vault --max-sensitivity restricted --json
```

The default view is public. A supplied API `VaultIndex` limits candidate paths
and evidence row IDs; projection never expands that grant. Native claims and
outcomes require their permitted dependencies. Mixed-label registers and review
logs do not expose raw bodies or full-file hashes through a filtered view.
Scoped review events use visible-row identities and row-local digests. Reviewer
names remain attribution; projection does not authenticate them.

## Convert without rewriting source history

A full migration bundle contains the typed v2 manifest **and exact original raw
files**, hashes, lengths and directory inventory. It preserves unknown source
fields, BOM, CRLF, missing final newlines and binary attachments. Validation
re-derives the typed manifest from those raw files; merely recomputing a forged
manifest's hashes cannot make inconsistent typed fields valid. Self-consistent
hashes still do not establish source authenticity or a trusted signer.

Full raw data cannot be a filtered public export. Bundle creation requires the
explicit `--private` flag; a narrowed caller index or classification set is
refused. The output must be a new file outside the source vault. Choose a private
directory outside the checkout as well if its Git status must remain unchanged.
Bundle data is base64-encoded, **not encrypted**; do not publish classified data.

```bash
uv run whykit knowledge bundle --root /path/to/vault --private --output /private/export/vault-v2.json --json
```

Preview returns metadata and an input-bound `expected_sha256`, without creating
the output. Review it, then repeat with `--write --expect-hash <reviewed-hash>`.
Publication creates a private 0600 file exclusively; it never replaces another
file. Source changes require a fresh preview. Source Markdown, approval receipts,
Git refs and index are not rewritten. Creating the requested output within the
same checkout can still add an ordinary untracked file.

## Restore into a new destination

```bash
uv run whykit knowledge restore /private/export/vault-v2.json --into /private/restored-vault --json
```

The destination must not exist. Preview validates the bundle and returns a bound
hash; repeat with `--write --expect-hash <reviewed-hash>` to restore. The process
creates the destination exclusively and uses bounded binary writes, preserving
raw bytes and empty directories. It adds a derived `.whykit/knowledge-v2.json`
manifest. `exact_bytes_preserved` is false during preview and becomes true only
after the complete restored inventory has been verified. Unknown/conflicting
paths, namespace aliases, symlinks and inconsistent payloads are refused.

Restore is **not globally atomic**. On failure cleanup removes only its own
unchanged entries; it leaves unexpected data from another writer intact and
returns failure. Keep the source vault and bundle until the restored vault has
been checked. Both operations refuse Git metadata destinations.

Git internals, known caches, locks and transaction journals are excluded, with
explicit exclusions recorded in the bundle. Unknown machine-state files refuse
export rather than silently disappearing. A pending recovery must be resolved
before migration. A pre-existing derived v2 manifest is excluded only after it
matches the current source-derived view.

**Git history is not transferred.** The original repository remains intact; the
restored tree is a data-equivalent vault, not the same trusted repository.
Local receipt and anchor equality is byte evidence. It does not recreate signed
identity, a trusted Git base, historical commits or permission to act. Result
flags include `git_history_transferred:false` and `execution_authorized:false`.
Run the normal lint, preflight and independently trusted history checks relevant
to the restored environment; migration is not a governance approval.

## Limits and platform support

The portable typed view works through the normal Python reader. Safe native
bundle export and restore currently require POSIX descriptor-relative no-follow
filesystem operations. Unsupported hosts, including Windows, receive a refusal
before publication or restore writes; native Windows migration remains open.
The bundle format alone is not proof of platform implementation support.

Limits are 25,000 source files, 100,000 typed objects, 16 MiB per file, 64 MiB of
raw files and 96 MiB of serialized bundle input. Depth and node counts are also
bounded. An exceeded limit refuses the operation instead of truncating data.
This is exact-byte data conversion, not semantic verification, a general disk
backup or proof of cross-host operational equivalence.

Filtered review events require their original target text to be a canonical
relative record path. Short aliases are not resolved after narrowing the view: an
alias that is ambiguous with a hidden record must not become a public event.
Such noncanonical rows are omitted before validation, counts and hashing,
without consulting a wider index. Full private strict migration retains its
legacy target resolution and raw-byte preservation.
