# Performance

WhyKit re-reads the whole vault on every command, with no daemon, cache file or
index on disk. That keeps results deterministic and safe to run in CI. The cost
is that every command has to stay fast on a large vault. This page gives
measured times and explains how to reproduce them.

## Measured times

Each command runs in a fresh Python process, so the times include interpreter
start-up and imports. Each figure is the best of three runs, measured on an
Apple-silicon laptop, CPython 3.12, warm file cache. Vaults come from
`tests/synthetic_vault.py` (see [Method](#method)).

| Command | 1,000 notes | 5,000 notes | 20,000 notes |
|---|---:|---:|---:|
| `lint --json` | 0.4 s | 1.4 s | 5.5 s |
| `status --json` | 0.3 s | 1.4 s | 6.4 s |
| `snapshot` | 0.3 s | 1.7 s | 7.0 s |
| `check --profile ci` | 0.3 s | 1.3 s | 5.6 s |
| `query pipeline` | 0.1 s | 0.6 s | 2.2 s |
| `context D-010` | 0.2 s | 0.8 s | 3.3 s |
| `pack D-010 --query pipeline` | 0.2 s | 1.0 s | 4.9 s |
| `trace` | 0.2 s | 0.9 s | 3.9 s |
| `explorer-index` | 0.3 s | 1.4 s | 6.1 s |
| `graph --json` | 0.2 s | 0.9 s | 3.8 s |
| `impact E-010` | 0.1 s | 0.5 s | 1.8 s |
| `backlinks D-010` | 0.2 s | 0.8 s | 3.3 s |
| `review list` | 0.1 s | 0.4 s | 1.7 s |

Cost now grows linearly with vault size. Most of the remaining time goes to
reading and parsing every note, plus the secret scan, which reads every text
file a second time.

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

## Cache scope

The caches are request-scoped. `whykit.lint.path_cache()` wraps each read
entry point (`lint`, `build_status`, `build_graph`, `build_context`,
`build_pack` and the rest). It is re-entrant, and it is discarded when the
outermost call returns. A long-running process such as the MCP server
therefore sees changes on disk at its next request. Commands that write to the
vault never run inside a scope, and each MCP tool call opens its own scope.
The cached evidence register is keyed by
modification time and size, and callers get copies, never the cached objects.

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
```

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

For this change, outputs matched for `examples/northline`, `examples/tiny`
and the 1,000- and 5,000-note synthetic vaults.

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
