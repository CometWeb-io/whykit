# Field evaluation: `whykit affected` on real repositories

Synthetic examples show that a feature works as designed. They cannot show
whether the design fits how teams actually write decisions. This page records
what replaying real history showed, what changed because of it and what is
still unknown. It is the first entry in the [roadmap's validation](../ROADMAP.md#validation-before-more-features),
not a benchmark result.

## Method

Four public repositories that keep ADRs, cloned on 12 November 2026:

| Repository | Commit | ADR folder | ADRs in force |
|---|---|---|---|
| `thomvaill/log4brains` | `17e3202` | `docs/adr` | 9 |
| `adr/madr` | `ba75bb1` | `docs/decisions` | 19 |
| `npryce/adr-tools` | `b3279ba` | `doc/adr` | 9 |
| `backstage/backstage` | `6a70152` | `docs/architecture-decisions` | 14 |

For each, [`scripts/eval_affected.py`](../scripts/README.md#eval_affectedpy)
replayed up to the last 300 non-merge commits through `whykit affected --adr`,
using today's ADRs and working tree. Every match from the term experiment
below was then read by hand. The labels are one reviewer's judgement.

## Finding 1: ADRs rarely name paths, so inferred scopes cover little

| Repository | ADRs with no scope | Commits with an applicable ADR |
|---|---|---|
| log4brains | 9 of 9 | 0 of 146 |
| madr | 19 of 19 | 0 of 286 |
| adr-tools | 8 of 9 | 65 of 127 |
| backstage | 13 of 14 | 195 of 300 |

Where a scope was inferred, it was a whole top-level folder (`src/**`,
`packages/**`) that matched over half of all commits. That is accurate for a
decision about overall structure and useless as a review prompt.

**Changed:** matches made only through patterns covering a whole top-level
folder are marked `broad`, counted separately and listed last. Inference now
reads a mentioned path only when its first segment exists at the repository
root; before, prose such as `dd/mm/yyyy`, `CI/CD`, `and/or`, domain names and
package-relative paths were candidates, and an unanchored path could have been
reported as moved code.

**Conclusion:** zero-migration mode is useful as a first look, not as a scope
source. Explicit scopes remain necessary.

## Finding 2: Git co-change history is mostly noise for ADRs

The commits that changed each ADR file were dominated by imports, formatting
sweeps and mass renames touching 100–460 files. After dropping commits with
more than 25 code files or more than one ADR, most ADRs had no remaining
signal, and what remained was mixed (navigation config next to the right
package).

**Changed:** `whykit scope suggest` ignores commits that change more than
`--max-files` files (default 25) and reports how many it ignored. Suggestions
from commit messages that name a decision ID are unaffected.

## Finding 3: most ADRs are about code patterns, not folders

Of the 14 Backstage ADRs in force, at least six decide a library or a coding
convention (a date library, an HTTP client, a mocking library, default
exports, a React typing pattern). No path scope can express that. This
finding led to `watch_terms`: words whose appearance in changed code lines
makes a decision apply.

Five Backstage ADRs were given terms naming the alternatives they rejected,
and 300 commits were replayed with their diffs:

| | Matches | Real (by hand) |
|---|---|---|
| First version: every changed line | 9 | 1 |
| After the changes below | 1 | 1 |

The eight false matches were an ordinary word in code comments (2), lock-file
churn from dependency bumps (2) and code samples in Markdown documentation
(4). The real one added a default export that `React.lazy` requires, against
the "avoid default exports" ADR: a deliberate exception a reviewer should
accept knowingly.

**Changed:** term matching skips comment-only lines, prose files (`.md`,
`.mdx`, `.rst`, `.txt`, `.adoc`), lock files and changelogs. Write terms as the
code would spell them (`date-fns`, `React.FC`, `export default`) rather than as
common words.

Also found: an ADR marked `[superseded]` only in its title was treated as in
force. Title markers are now read.

## Round 2: migrating the same ADRs into vaults

Each ADR folder was adopted into a fresh vault (`whykit adopt --profile
adr-only --write`) and every staged file promoted with `whykit new decision
--from FILE --write`.

| Repository | Files promoted | Lint errors | Shapes recognised |
|---|---|---|---|
| log4brains | 10 | 0 | MADR |
| madr | 19 | 0 | MADR |
| adr-tools | 9 | 0 | Nygard |
| backstage | 15 | 0 | Nygard |

Every promotion succeeded and the vaults linted without errors. Every record
still needed a person before approval: the placeholder warning fired on all of
them, mostly for a missing rationale section, which these ADR styles do not
have. That is the intended stop: an imported record is a draft.

Found and changed:

- The adoption report `MIGRATION.md` that `adopt` writes next to the staged
  files was accepted by `new decision --from` as if it were an ADR. It is now
  refused.
- MADR's *Pros and Cons of the Options* were dropped, leaving the alternatives
  table without upsides or risks. Good/Bad bullets now fill those columns.
- Status badges (`img.shields.io` images) were offered as evidence candidates.
  Badge services and image links are now skipped.
- An ADR whose status says superseded, deprecated or rejected (in a status
  section, a meta line, front matter or a `[superseded]` title marker) became
  an ordinary draft that could be approved by mistake. Promotion now warns to
  supersede or archive it instead.
- `deciders` / `decision-makers` now become the owner when `--owner` is not
  given. None of the four repositories fills them in (only their templates
  do), so every owner still started as `TODO` here.

## Round 2: scale

On Backstage (77,000 commits; a 200-commit range changing about 1,200 files
and 72,000 lines):

| Command | Before | After |
|---|---|---|
| `affected --adr … --base HEAD~200` | 1.6 s | 1.6 s |
| `affected --root … --base HEAD~200` | 1.5 s | 1.5 s |
| `scope suggest` for 15 decisions | 22 s | 1.2 s |

`scope suggest` ran two `git log` searches per decision and one `git show` per
commit; it now reads all decisions' history in two passes. The text report of
`affected` listed every matched file on one line (over 1,000 for one broad
decision) and now shows five and a count.

## Round 3: keeping scopes honest, and term suggestions

`whykit scope check` was run on Backstage with a vault of five approved
decisions given real and deliberately broken scopes. In 5.2 seconds over
12,657 tracked files (9,844 scanned for terms) it reported the pattern for a
package that no longer exists, a misspelt term (`cross-fech`), a broad
`plugins/**` (5,810 files) and an unscoped decision. It also reported `dayjs`
as unseen, which is correct and expected for a library the decision rejected,
so unseen terms never fail `--strict`.

Term suggestions from rejected alternatives were measured on the four migrated
vaults. The first version proposed 10 terms, about four plausible; the noise
was a date, a document file name, a URL anchor and English compounds. After
skipping URLs, dates and document names: 7 proposals (`Fuse.js`, `adr-tools`
three times, `adr-j`, `less-than`, `greater-than`), five plausible. Two of the
four repositories got none, because their ADRs list no options. Suggestions
are therefore preview-only and never written.

## What this does not show

- **Recall.** Nobody labelled every commit for every ADR, so missed cases are
  unknown. One match in 300 commits says the signal is quiet, not that it is
  complete.
- **Vaults.** These repositories use plain ADRs. Explicit `applies_to`,
  evidence and review checks in a vault were not field-tested here.
- **Usefulness to people.** Whether reviewers act on the report, and whether
  it is worth the setup, needs external users. The roadmap's targets (90%
  precision and 80% recall on labelled pull requests, three teams for 30 days)
  are still open.

To reproduce, clone a repository and run:

```bash
python3 scripts/eval_affected.py /path/to/repo --adr docs/decisions --commits 300 --diff
```
