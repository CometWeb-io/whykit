# Performance

WhyKit reads the whole vault on every command, with no daemon and no index
that could disagree with the files. That keeps results deterministic and safe
to run in CI. The cost is that every command has to stay fast on a large vault.
Two caches cut the repeated work without changing any output: read commands
keep a [parse cache](#parse-cache) in `.whykit/cache/`, reused only while a
note's file is provably unchanged, and the MCP server, a long-lived process,
keeps parsed notes between calls (see [MCP server](#mcp-server)). This page
gives measured times and explains how to reproduce them.

## Measured times

Each command runs in a fresh Python process, so the times include interpreter
start-up and imports. Each figure is the best of three runs, measured on an
Apple-silicon laptop, CPython 3.12, warm file cache. Vaults come from
`tests/synthetic_vault.py` (see [Method](#method)).

| Command | 1,000 notes | 5,000 notes | 20,000 notes |
|---|---:|---:|---:|
| `lint --json` | 0.2 s | 0.9 s | 3.7 s |
| `status --json` | 0.2 s | 0.9 s | 3.7 s |
| `snapshot` | 0.3 s | 1.3 s | 4.9 s |
| `check --profile ci` | 0.2 s | 0.9 s | 3.8 s |
| `query pipeline` | 0.1 s | 0.4 s | 1.6 s |
| `context D-010` | 0.2 s | 0.6 s | 2.5 s |
| `pack D-010 --query pipeline` | 0.2 s | 0.8 s | 3.3 s |
| `trace` | 0.2 s | 0.7 s | 3.1 s |
| `explorer-index` | 0.2 s | 0.9 s | 3.9 s |
| `graph --json` | 0.2 s | 0.8 s | 3.2 s |
| `impact E-010` | 0.1 s | 0.4 s | 1.5 s |
| `backlinks D-010` | 0.2 s | 0.7 s | 2.9 s |
| `review list` | 0.1 s | 0.4 s | 1.4 s |

Cost grows linearly with vault size. Most of the remaining time goes to
reading and parsing every note and resolving its links.

### Before the secret-scan and memory work

The same commands at 20,000 notes, best of three runs each, with the two
implementations measured alternately on the same machine:

| Command | Before | After |
|---|---:|---:|
| `lint` | 7.9 s | 3.7 s |
| `status` | 6.1 s | 3.7 s |
| `snapshot` | 7.1 s | 4.9 s |
| `check` | 6.2 s | 3.8 s |
| `explorer-index` | 6.1 s | 3.9 s |
| `pack` | 4.8 s | 3.3 s |
| `context` | 3.8 s | 2.5 s |
| `graph` | 4.3 s | 3.2 s |
| `query` | 2.2 s | 1.6 s |

At 5,000 notes `lint` went from 1.9 s to 0.9 s. Within that, the secret scan
itself went from 1.45 s to 0.31 s at 20,000 notes (in-process, best of five).

### Before the request-scoped caches

Here are the same commands on the same 5,000-note vault before this work, with
one run each. The machine was busier during these runs, so treat the absolute
figures as rough. What they show reliably is how the cost grew:

| Command | Before | After |
|---|---:|---:|
| `lint` | 108 s | 1.4 s |
| `status` | 65 s | 1.4 s |
| `snapshot` | 80 s | 1.7 s |
| `check` | 125 s | 1.3 s |
| `context` | 163 s | 0.8 s |
| `pack` | 675 s | 1.0 s |
| `explorer-index` | 78 s | 1.4 s |
| `review list` | 48 s | 0.4 s |
| `trace` | 11 s | 0.9 s |

The output did not change: at 5,000 notes every command's stdout and exit code
is byte-identical to the earlier implementation (see
[Proving output did not change](#proving-output-did-not-change)).

## What was slow

- **`Path.resolve()` on every link occurrence.** Each call `lstat`s every
  component of an absolute path. Containment checks, relative paths and link
  targets triggered over 100,000 of these calls at 1,000 notes, and around
  680,000 for one `pack`. Within a single command, each distinct path is now
  resolved once. On POSIX a file reuses its directory's resolved path, so it
  costs one `lstat` instead of one per path component.
- **A linear scan per decision-log row.** `check_decision_log` found each
  row's record by resolving every note's path, once per row. It now does a
  dictionary lookup.
- **Repeated whole-vault work in multi-record commands.** `pack` built the
  wikilink graph and re-parsed the evidence register once per record. Within
  one command, the graph's adjacency is now built once per vault parse, and the
  register is parsed once.
- **`review list` ran a full lint**, secret scan included, only to read the
  review queue. The queue does not depend on lint findings, so it is now
  computed directly.
- **Smaller costs:** code masking repeated by several checks (now computed once
  per note), line numbers found by re-counting from the start of the file for
  every match (now counted incrementally), and `relative_to()` on paths that
  `rglob` already returns relative to the root.

## The secret scan

The secret scan covers every text file in the vault, not only the notes. It
used to read each note a second time and run all six credential patterns over
every file. The patterns begin with `\b`, so Python's regex engine tries them
at every character offset, which made the scan the largest single cost of
`lint`. Two changes leave its findings unchanged:

- **The note text is reused.** `lint` hands the scanner the text the vault
  index already read, so only files outside the index (configuration, JSON,
  `.env` files, `examples/` and so on) are read again. The index strips a
  leading byte-order mark; no pattern can match it or depends on it, so line
  numbers are the same.
- **A prefilter skips patterns that cannot match.** Each pattern has a
  necessary condition: a literal such as `AKIA` or `gh[pousr]_` that every
  match contains, found with a fast substring search. The case-insensitive
  credential pattern is skipped only for ASCII text that contains none of
  `key`, `token`, `secret` and `password` in any case. For non-ASCII text the
  full pattern always runs, because `re.IGNORECASE` also matches, for
  example, the Kelvin sign as `k`.

`tests/test_scale.py` compares the scanner with the original
read-everything implementation on `examples/northline`, `examples/tiny`, a
synthetic vault, and a synthetic vault with planted fake credentials of every
kind (including a byte-order mark, CRLF line endings, Unicode case variants,
code fences, a non-UTF-8 file and files in skipped folders). It also checks on
20,000 generated strings that every pattern match passes its prefilter. The
planted values are assembled at run time from fragments and use reserved
`example.com` / `.invalid` names, so the repository never contains a
credential-shaped string.

## Peak memory

Peak memory traced by `tracemalloc` during one command, including the output
it builds (`scripts/bench.py --memory`):

| Command | 5,000 notes before | after | 20,000 notes before | after |
|---|---:|---:|---:|---:|
| `lint --json` | 40.5 MB | 28.8 MB | 160.2 MB | 115.3 MB |
| `status --json` | 40.8 MB | 28.9 MB | 161.2 MB | 115.3 MB |
| `pack D-010 --query pipeline` | 62.6 MB | 34.3 MB | 248.4 MB | 134.5 MB |
| `explorer-index` | 51.7 MB | 40.1 MB | 205.0 MB | 160.1 MB |

The 20,000-note vault holds 31 MB of text, which every command keeps in
memory. What went:

- **The whole graph inside `pack` and `context`.** Finding one record's
  neighbours built the full graph export, with a dictionary per node and per
  edge, and then kept a dictionary for every node. The wikilink adjacency is
  now built directly, and a node's dictionary is made only when it is returned.
- **A second copy of every path's parts.** Sorting `Path` objects, and
  `Path.relative_to`, cache a list of path components on every path they
  touch, and vault paths live as long as the index. Containment and relative
  paths are now string prefix tests on the already-resolved paths (POSIX; other
  platforms keep `relative_to`), sorting uses an equivalent key, and the
  resolve cache keeps the caller's path object instead of an equal copy.
- **Front-matter strings repeated per note.** Keys such as `status` and
  values such as `approved` were separate strings in every note. Short keys and
  values now share one string per spelling, through a pool capped at 8,192
  entries.
- **Split lines for the note body.** `Note.body` split the whole text into
  lines and joined them again; it now slices after the front matter.

`tests/test_scale.py` fails if one request's peak at 1,000 notes rises from
about 5 MB (`lint`) and 6 MB (`pack`) back towards the 7-12 MB it used to take.

## MCP server

The MCP server is a long-lived process, so it can keep work between calls.
It keeps the parsed notes and reuses a note only while its file's
modification time, change time, size and inode all match. An edit, an atomic
save (which replaces the inode), a rename or a deletion is therefore seen by
the next call. A file whose timestamps are within two seconds of the clock is
never kept, because a second write within the same timestamp tick and with the
same size would otherwise be indistinguishable from the first. Everything
derived from the notes (link resolution, findings, the graph) is still
computed for every call, because it also depends on attachments, symlinks and
configuration.

Latency per tool call at 5,000 notes, through the SDK-free `VaultTools.call`
the server uses (`scripts/bench.py --mcp`, best of three runs; "repeated" is
the median of the later calls on the same server):

| Tool | Cold before | Cold after | Repeated before | Repeated after |
|---|---:|---:|---:|---:|
| `query` | 0.60 s | 0.40 s | 0.52 s | 0.14 s |
| `context` | 0.80 s | 0.55 s | 0.71 s | 0.29 s |
| `impact` | 0.42 s | 0.38 s | 0.42 s | 0.13 s |
| `status` | 1.24 s | 0.86 s | 1.27 s | 0.58 s |
| `pack` | 0.86 s | 0.73 s | 0.85 s | 0.43 s |
| `trace` | 0.68 s | 0.62 s | 0.73 s | 0.35 s |
| `backlinks` | 0.70 s | 0.57 s | 0.69 s | 0.34 s |

The cost of a repeated call is the stat of every note plus the per-call work.
The cache holds the notes of one vault, about the size of its text.
`tests/test_scale.py` edits a note between calls in each of those ways (new
size, same size with the old modification time restored, deletion and
re-creation) and checks that the next call sees the change.

## Parse cache

Most of a command's cost on an unchanged vault used to be reading and parsing
notes that had not changed since the last run. Read commands (`lint`,
`check`, `status`, `snapshot`, `verify-snapshot`, `query`, `context`, `pack`,
`graph`, `backlinks`, `impact`, `trace`, `explorer-index`, `doctor` and
`review list`) now keep what each note's text alone determines in
`.whykit/cache/`:

- `notes.v1`: per note, the `stat` signature (modification and change time in
  nanoseconds, size, inode), the SHA-256 of its bytes and its parsed front
  matter;
- one file per derived view (`wikilink_hits.v1`, `markdown_link_hits.v1`,
  `fact_callouts.v1`, `cited_evidence.v1`, `section_decision_id.v1`,
  `secret_hits.v1`, `decision_placeholders.v1`), each row tagged with the
  digest of the content it was computed from. A view file is read only when a
  command first needs that view, so `query --type decision` never loads the
  link targets.

Everything that depends on more than one file (link resolution, findings,
the graph, the review queue) is computed on every run, as before. Note bodies
are not stored; a command that needs the text (full-text `query`, `context`,
`pack`, `snapshot`, `explorer-index`) reads it and checks its SHA-256 first.
The cache is not a source of truth: deleting `.whykit/cache/` is always safe,
and `--no-cache` before the command (`whykit --no-cache lint`) or
`WHYKIT_NO_CACHE=1` turns it off.
Commands that write the vault, `diff` (which reads commits into a temporary
directory) and the MCP server never read or write it.

### When an entry is reused

Correctness comes first; any doubt means the note is parsed again.

- **Exact signature.** An entry is used only when the file's signature is
  exactly the recorded one. An edit, an atomic save (new inode), a rename, a
  deletion or a same-size edit with the old modification time restored
  (`os.utime`) all change it, because the change time cannot be set back.
  This also means a cache file copied in from another checkout never
  matches.
- **Racy entries are checked by content.** An entry whose file was modified
  within two seconds of being parsed, or whose timestamps are in the future
  (a clock that moved), is reused only after the file's SHA-256 matches. That
  covers file systems with coarse timestamps, as Git does for its index.
- **Whole-cache invalidation.** The cache records a fingerprint of the WhyKit
  version, the source files of the installed package (a development checkout
  changes behaviour without changing the version), the Python version, the
  Unicode database and the bytes of `whykit.toml`. Any change discards it.
- **Damage is ignored, not trusted.** A file that does not parse, or rows of
  the wrong shape, produce `warning: ignoring unreadable parse cache …` (or a
  per-note warning) on stderr, the same result as without the cache, and a
  rebuilt cache. A cache file owned by another user, or reached through a
  symlink, is not read.
- **Concurrent edits.** A note that changes while it is being read is not
  recorded. If a note's text, read later in the same command, no longer has
  the recorded digest, the note is parsed afresh from what is on disk and
  left out of the cache.

The secret scan reuses `secret_hits` only for notes, under the same
content-tagged rule: a changed note, including a change of its `sensitivity`,
is scanned again. Files that are not notes (configuration, JSON, `.env` and
so on) are scanned on every run, and `--no-secrets` or a profile with
`secrets = false` skips the scan as before.

Writes go to a temporary file in `.whykit/cache/` and replace the old one with
`os.replace`, so a reader sees either the old or the new file. A writer takes
`.whykit/cache/write.lock` without waiting and skips the write if another run
holds it, or if a WhyKit command holds the vault's mutation lock. The
directory carries its own `.gitignore`, and `whykit init` adds
`.whykit/cache/` to the vault's `.gitignore`, so the cache never shows up in
`git status` or trips the `release` profile's clean-tree check. The cache file
names have no extension the secret scan or the Markdown collector looks at.

### Cold and warm runs

CPU time (user + system) of each command's process, best of two runs at
5,000 notes and one run at 50,000, on an Apple-silicon laptop with CPython
3.12. The machine was shared with other work while the 50,000-note runs ran,
so their wall-clock times were 1.5 to 2 times the CPU time; CPU time is the
steadier measure. *Before* is the implementation without the cache; *off* is
this one with `WHYKIT_NO_CACHE=1`; *cold* deletes the cache before each
command (so the command parses everything and writes a new cache); *warm*
reuses the cache an earlier command wrote.

| Command | 5k before | 5k cold | 5k warm | 50k before | 50k off | 50k cold | 50k warm |
|---|---:|---:|---:|---:|---:|---:|---:|
| `lint --json` | 0.78 s | 0.94 s | 0.42 s | 9.9 s | 9.5 s | 11.2 s | 5.3 s |
| `status --json` | 0.82 s | 1.00 s | 0.47 s | 9.5 s | 9.5 s | 10.6 s | 5.5 s |
| `check --profile ci` | 0.98 s | 0.96 s | 0.46 s | 10.0 s | 9.3 s | 10.4 s | 5.5 s |
| `snapshot` | 1.13 s | 1.10 s | 0.69 s | 12.9 s | 12.7 s | 13.7 s | 10.9 s |
| `query pipeline` | 0.56 s | 0.49 s | 0.29 s | 5.2 s | 5.1 s | 5.6 s | 3.9 s |
| `query --source E-010` | 0.45 s | 0.49 s | 0.17 s | 5.0 s | 4.9 s | 5.2 s | 1.8 s |
| `context D-010` | 0.64 s | 0.65 s | 0.34 s | 6.7 s | 7.4 s | 7.8 s | 4.2 s |
| `pack D-010 --query pipeline` | 0.87 s | 0.80 s | 0.56 s | 8.4 s | 9.5 s | 9.9 s | 9.0 s |
| `trace` | 0.77 s | 0.72 s | 0.36 s | 7.4 s | 8.1 s | 8.3 s | 4.2 s |
| `explorer-index` | 0.95 s | 0.94 s | 0.58 s | 9.8 s | 10.8 s | 10.9 s | 7.8 s |
| `graph --json` | 0.93 s | 0.78 s | 0.44 s | 10.0 s | 8.9 s | 9.0 s | 5.1 s |
| `impact E-010` | 0.45 s | 0.47 s | 0.15 s | 4.9 s | 5.0 s | 5.1 s | 1.5 s |
| `backlinks D-010` | 0.66 s | 0.69 s | 0.33 s | 7.4 s | 8.0 s | 7.8 s | 4.0 s |
| `review list` | 0.35 s | 0.40 s | 0.14 s | 4.0 s | 4.2 s | 4.5 s | 1.4 s |

A warm `lint` or `status` takes about half the CPU time of a run without the
cache, at both sizes; commands that need only front matter (`impact`,
`review list`, `query --source`) take a third. The wall-clock gain at 50,000
notes was larger (`lint` 14.4 s before, 5.3 s warm) because a warm run reads
almost nothing from disk. Commands that need every note's text gain least:
`pack` still reads and hashes the text of all 50,000 notes. A cold run costs
up to about 15 % more than no cache, for hashing and writing the cache; a
single command on a fresh checkout (a CI job) pays that once, and every later
command in the same job is warm. The remaining warm cost is walking the tree,
stat-ing each file, link resolution and the per-link checks, which depend on
the whole vault and are not cached.

Peak traced memory (`scripts/bench.py --memory`):

| Command | 5k off | 5k cold | 5k warm | 50k off | 50k warm |
|---|---:|---:|---:|---:|---:|
| `lint --json` | 35.0 MB | 36.2 MB | 30.1 MB | 344 MB | 303 MB |
| `status --json` | 35.0 MB | 36.4 MB | 30.1 MB | 346 MB | 303 MB |
| `check --profile ci` | 35.0 MB | 36.3 MB | 30.2 MB | 344 MB | 303 MB |
| `snapshot` | 63.7 MB | 65.1 MB | 66.6 MB | 626 MB | 660 MB |
| `query pipeline` | 27.9 MB | 28.9 MB | 25.8 MB | 282 MB | 263 MB |
| `query --source E-010` | 24.5 MB | 25.6 MB | 17.9 MB | 248 MB | 174 MB |
| `context D-010` | 40.5 MB | 41.6 MB | 36.4 MB | 411 MB | 373 MB |
| `pack D-010 --query pipeline` | 42.3 MB | 43.5 MB | 46.8 MB | 422 MB | 472 MB |
| `trace` | 58.7 MB | 59.9 MB | 52.1 MB | 580 MB | 517 MB |
| `explorer-index` | 47.1 MB | 48.5 MB | 49.7 MB | 466 MB | 501 MB |
| `graph --json` | 77.0 MB | 81.7 MB | 78.2 MB | 755 MB | 769 MB |
| `impact E-010` | 24.3 MB | 25.4 MB | 17.9 MB | 244 MB | 174 MB |
| `backlinks D-010` | 58.6 MB | 59.7 MB | 51.9 MB | 579 MB | 516 MB |
| `review list` | 20.5 MB | 24.6 MB | 18.0 MB | 205 MB | 174 MB |

*Off* matches the implementation without the cache to the tenth of a
megabyte at both sizes. A warm run holds less for most commands, because notes
whose text it never reads cost only their front matter. The four commands that
read every note's text (`snapshot`, `pack`, `explorer-index`, `graph`) peak 2
to 12 % higher warm, for the decoded cache rows they hold alongside the text.
The 50,000-note vault holds 77 MB of Markdown; its cache is about 55 MB on
disk.

Every run in these tables, cold and warm, printed byte-identical output to the
implementation without the cache (`--compare` against a recording of the
*before* code), at 5,000 and 50,000 notes. `tests/test_parse_cache.py` checks
the pinned golden digests with the cache off, cold, unverified and warm, and
covers each invalidation case above (edit, same-size edit with the
modification time restored, rename, delete, `whykit.toml` change, version and
source change, racy entries, a clock behind the files, future timestamps,
an edit between `stat` and read), damaged and wrong-shaped cache files,
symlinks, held locks, concurrent runs, a planted credential added after
caching and a `sensitivity` change.

## Cache scope

The caches are request-scoped. `whykit.lint.path_cache()` wraps each read
entry point (`lint`, `build_status`, `build_graph`, `build_context`,
`build_pack` and the rest). It is re-entrant, and it is discarded when the
outermost call returns. A long-running process such as the MCP server
therefore sees changes on disk at its next request. Commands that write to the
vault never run inside a scope, and each MCP tool call opens its own scope.
The cached evidence register is keyed by
modification time and size, and callers get copies, never the cached objects.
Two caches outlive a request: the MCP server's parsed notes, described
[above](#mcp-server), and the on-disk [parse cache](#parse-cache), which
holds only what a note's own bytes determine and is checked against the file
before each use.

## Method

`tests/synthetic_vault.py` writes a deterministic vault: the same `--notes`
value produces the same bytes on every machine. The vault has workstream
folders, an evidence register (one row per ten notes, a few retired), a
decision log with supersession chains, a review log and dated reports. Each
note links about six times on average, by path, stem, alias or anchor, inside
tables, with labels. A small fixed share of links is broken, ambiguous or
points at an attachment. Some notes are orphans, some fact callouts cite
nothing, and some code fences contain link-like text. Every expensive lint
path does real work.

```bash
python3 tests/synthetic_vault.py /tmp/vault-5k --notes 5000
python3 scripts/bench.py --vault /tmp/vault-5k
python3 scripts/bench.py --vault /tmp/vault-5k --profile /tmp/prof   # cProfile per command
python3 scripts/bench.py --vault /tmp/vault-5k --memory               # peak memory per command
python3 scripts/bench.py --vault /tmp/vault-5k --mcp                  # MCP tool latency
python3 scripts/bench.py --vault /tmp/vault-5k --cache warm --runs 2  # parse cache primed, best of 2
```

`--cache off` (the default) runs every command with `WHYKIT_NO_CACHE=1`, so
timings stay comparable with earlier measurements; `cold` deletes
`.whykit/cache/` before each command; `warm` primes it once with `lint`.
Each row reports wall-clock and CPU time (user + system) of the command's
process.

## Proving output did not change

`scripts/bench.py --record DIR` saves each command's exit code and stdout.
`--compare DIR` fails on any byte difference. The Explorer's `generatedAt`
wall-clock stamp is the only value it ignores. To compare two implementations,
record with one and compare with the other; `--src` points at another checkout's
`src/`:

```bash
python3 scripts/bench.py --vault /tmp/vault-5k --src /path/to/old/src --record /tmp/before
python3 scripts/bench.py --vault /tmp/vault-5k --compare /tmp/before
```

For the request-scoped caches, outputs matched for `examples/northline`,
`examples/tiny` and the 1,000- and 5,000-note synthetic vaults. For the
secret-scan and memory work they matched for `examples/northline`,
`examples/tiny`, the 1,000-, 5,000- and 20,000-note synthetic vaults, and the
5,000-note vault with the planted secrets from `tests/test_scale.py` added
(21 `secret.detected` findings and one `secret.scan_non_utf8`).

The test suite pins the same property. `tests/test_performance.py` checks
SHA-256 digests of twelve commands' output on a 400-note synthetic vault
against digests recorded from the earlier implementation. Those original
digests stay in the test. A command whose output later changed on purpose is
listed in `INTENTIONAL_CHANGES` with its new digest and the behaviour change
that explains it (for example, `evidence.retired` no longer firing on archived
records, or `explorer-index` writing a JSON error object); every other command
must still match the original. To add an entry, regenerate the digests with
`python3 tests/synthetic_vault.py DIR --notes 400 --digests`, review the diff
of the output itself, and record the reason next to the digest.

## Regression budget

`tests/test_performance.py` runs every pinned command in-process on a
1,000-note vault and fails if any of them takes 8 seconds or more. Each one
currently takes well under a second. Before this work, `lint` took 13 seconds
and `pack` 49 seconds. The test also counts `Path.resolve()` calls during a
full lint, which catches a return to per-occurrence resolution even on fast
hardware. On a runner too slow for these budgets, set `WHYKIT_SKIP_PERF=1`.
