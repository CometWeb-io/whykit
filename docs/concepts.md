# Concepts

WhyKit has three moving parts: **evidence**, **decisions** and the **review
cycle**. Everything else (queries, graphs, context packs, the Explorer) is a view
over those three, computed from the Markdown files in the vault.

```text
evidence register (E-NNN) ──cited by──> decision record (D-NNN) ──re-checked by──> review log
                                                  │
                                                  └──superseded by──> newer decision (D-NNN)
```

This page explains what each part is, which files hold it, and what the linter
does and does not check about it. Commands are shown as `whykit …`; from a
source checkout, prefix them with `uv run` and pass `--root` (see the
[README](../README.md#60-second-quickstart)).

## The vault

A vault is a directory of Markdown files. WhyKit recognizes one by `Home.md` and
`00-context/` at its root, and finds it by walking up from the current directory
unless you pass `--root`. A vault is meant to be its own Git repository, usually
private. A freshly initialized vault
looks like this:

| Path | Holds |
|---|---|
| `Home.md` | The map of content. Every note should be reachable from here. |
| `AGENTS.md` | The contract agents and people work under. Ships with four unanswered questions. |
| `00-context/evidence-register.md` | The evidence register (active and retired sources). |
| `00-context/review-log.md` | The append-only review log. |
| `06-decisions/decision-log.md` | The decision index, one row per decision. |
| `06-decisions/d-NNN-*.md` | One decision record per file. |
| `notes/` | Freeform working notes. |
| `templates/` | Starters for new records (`status: template`). |
| `whykit.toml` | Repository-local policy: defaults and the `local`, `ci`, `release` gates. |

Every governed note starts with YAML front matter (`title`, `type`, `status`,
`owner`, `created`, `last_updated`, `source_of_truth`, `sensitivity`, ...). The
full list is in [Configuration](configuration.md#core-document-metadata).

## Evidence

Evidence is anything a claim rests on: an interview set, an analytics export, a
vendor document, a benchmark, a contract clause. WhyKit does not store the source
itself. It stores a **register row** that says what the source is and where a
person can go to inspect it.

```bash
whykit new evidence \
  --source "Support ticket export, Q3" --type dataset \
  --location "https://example.com/exports/q3-tickets.csv" \
  --claims "Most onboarding tickets mention SSO"
```

That appends one row to `00-context/evidence-register.md`:

| ID | Source | Type | Date | Accessed | Location | Claims it supports |
|---|---|---|---|---|---|---|
| E-001 | Support ticket export, Q3 | dataset | 2026-10-02 | 2026-10-02 | https://example.com/exports/q3-tickets.csv | Most onboarding tickets mention SSO |

Rules worth knowing:

- **IDs are stable and never reused.** `E-001` means the same source forever.
  Other documents cite it in their `source_ids` front matter or inline. An ID
  inside `inline code` or a fenced code block is an example of the syntax, not a
  citation: `graph`, `impact`, `context`, `query` and `trace` all ignore it.
- **A row with no source and no location is a placeholder, not evidence.** The
  linter will not let a document cite it.
- **Evidence is retired, not deleted.** When a source turns out to be wrong or is
  replaced, `whykit evidence retire E-001 --why "..." --replaced-by E-014` moves
  it to the *Retired sources* table. Existing citations keep resolving, and the
  linter warns (`evidence.retired`) wherever a current document still leans on
  it. Superseded and archived records are history and are not flagged.
- **Freshness is opt-in.** If some source types go stale quickly, set an
  access-age window per type in `whykit.toml` (see
  [Configuration](configuration.md#evidence-access-age)). That produces a warning
  when the `Accessed` date is too old; it does not prove the source is still
  right.

Before changing a source, ask what depends on it:

```bash
whykit impact E-001
```

## Decisions

A decision record captures one material choice: what was decided, why, on what
evidence, which alternatives lost, and when someone should look at it again.

```bash
whykit new decision "Ship SSO before audit logs" \
  --owner Platform --status draft --source E-001
```

This creates `06-decisions/d-001-ship-sso-before-audit-logs.md` from the
decision template **and** adds the matching row to the decision log in the same
operation, so the index and the records cannot drift apart. Fill in the
*Context*, *Decision*, *Rationale* and *Alternatives considered* sections while
the record is still `draft` or `in_review`.

### Lifecycle

```text
draft ──> in_review ──> approved ──> superseded
                            └──────> archived
```

- `draft` and `in_review` records are freely editable.
- Once a record is `approved`, `superseded` or `archived`, its reasoning is
  **historical**. `whykit history` and `whykit check --base <ref>` refuse any
  diff that rewrites it.
- An approved decision needs a `review_by` date. If you approve without
  `--next-review`, WhyKit sets one from `defaults.decision_review_days` in
  `whykit.toml` (90 days in a fresh vault).

### Changing your mind: supersede, do not rewrite

When an accepted decision turns out to be wrong or outdated, record a new one:

```bash
whykit new decision "Ship audit logs first" \
  --owner Platform --status draft --source E-001 --supersedes D-001
```

Complete the new draft and [approve it](guide.md#approve-a-decision). Only
then does WhyKit mark `D-001` as `superseded` with
`superseded_by: D-002`, and updates both decision-log rows. The old reasoning
stays exactly as written. That is the point: six months later, the record shows
what was believed at the time *and* what replaced it.

Two edits are allowed on an accepted record. The lifecycle transition changes
`status` to `superseded` or `archived`, with `last_updated` and optionally
`superseded_by`. A confirmed review (`whykit review record`) moves only
`review_by` and `last_updated`. Everything else is rejected by the history check.

## The review cycle

A record that was right when it was written can quietly become wrong. WhyKit
cannot detect that, so it does the next best thing: it makes sure every accepted
decision has a date on which a person will look again, and it reports the ones
that have passed.

```bash
whykit review list --due-days 30          # what is due in the next 30 days
whykit review list --overdue-only
whykit review record D-002 \
  --reviewer Platform --outcome confirmed \
  --note "Ticket share rechecked against the Q4 export"
```

`review record` appends a row to `00-context/review-log.md`. A `confirmed`
review also moves the record's `review_by` forward: to `--next-review` when you
pass it, otherwise by `defaults.decision_review_days`. Other outcomes only log
the event (including any `--next-review` you give) and leave `review_by` alone,
so the record stays in the due queue until someone acts on it. Outcomes:

| Outcome | Use it when |
|---|---|
| `confirmed` | The evidence still holds; the decision stands. Only `approved` records can be confirmed. |
| `update-required` | Something needs revisiting, but the record is not wrong yet. |
| `supersede-required` | The decision should be replaced. Follow up with `new decision --supersedes`. |
| `archived` | The decision no longer applies to anything. |

The review log is append-only, just like accepted reasoning: the history check
rejects edits to or deletions of earlier rows. A review event records that
somebody re-checked a record. It does not prove the underlying claim is true.

Overdue reviews show up as `review_by.overdue` warnings in `whykit lint`, so a
`ci` gate (which treats warnings as failures) turns a missed review into a red
build. Pin `--today` in CI if you want a deterministic result instead.

## The five information types

Decisions and evidence are the structured part. The prose around them should keep
five kinds of statement apart:

- **Verified fact**: supported by reviewed evidence, cited as `E-NNN`.
- **Decision**: an authorized choice made at a known point in time.
- **Hypothesis**: plausible but not demonstrated.
- **Recommendation**: proposed, and not a decision until accepted.
- **Open question**: a known unknown with an owner.

An unmarked claim reads as a verified fact, which is how a guess becomes
folklore. In Obsidian, callouts keep the types visible while reading; see
[Using WhyKit with Obsidian](obsidian.md#callouts-for-information-types). A
`[!fact]` callout without an inline `E-NNN` is a lint warning
(`fact.inline_evidence`).

## What the linter checks, and what it cannot

WhyKit checks **shape and traceability**:

- every cited `E-NNN` exists in the register, and retired sources are flagged;
- every decision record is indexed in the decision log with matching ID and status;
- supersession chains have no forks, cycles or dangling references;
- approved decisions carry a review date, and overdue ones are reported;
- decision records that are drafted, in review or approved no longer hold the
  scaffold's prompts (`decision.placeholder`);
- accepted reasoning and earlier review events were not rewritten (`history`);
- front matter is complete, links resolve, and nothing looks like a credential.

It does **not** check whether a source is honest, whether the reasoning was
sound, or whether a decision was any good. A citation can be present and still
be wrong. The [rule reference](rules.md) lists every check with its severity, and
`whykit rules <code>` explains why a rule exists.
