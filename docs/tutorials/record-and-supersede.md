# Tutorial: record, check and supersede a decision

In about ten minutes you will create a vault, record a pricing decision with the
evidence behind it, change your mind when new evidence arrives, and watch the
history check refuse a quiet rewrite. Every command on this page runs in the
test suite, so the output you see is what the current version prints.

You need Git and a `whykit` command on your `PATH`. From a WhyKit checkout,
`uv tool install --from /path/to/whykit whykit` installs one. If you prefer not
to install anything, run each command as `uv run --project /path/to/whykit
whykit …` instead. The example company, sources and URLs are fictional.

## 1. Create a vault

Start in an empty directory. The vault becomes its own Git repository.

```bash
whykit init pricing-ledger
cd pricing-ledger
git init -q -b main
```

```text
WhyKit vault created: <path>
```

## 2. Register the evidence, then the decision

A decision should rest on something a person can inspect later. Register the
source first, so it gets a stable ID:

```bash
whykit new evidence \
  --source "Churn survey, September" --type survey \
  --location "https://example.com/research/churn-survey.csv" \
  --claims "Price is the top reason small teams cancel"
```

```text
created E-001: 00-context/evidence-register.md
```

Now record the decision and cite that source. `--status approved` makes it an
accepted record, and WhyKit gives it a review date 90 days out.

```bash
whykit new decision "Offer a free plan for teams of up to three" \
  --owner Product --status approved --source E-001
```

```text
created D-001: 06-decisions/d-001-offer-a-free-plan-for-teams-of-up-to-three.md
```

In a real vault you would now open that file and write the context, the
rationale and the alternatives you rejected. Check the vault and commit:

```bash
whykit lint --quiet
git add .
git commit -q -m "Record D-001"
```

```text
24 files — 0 error(s), 5 warning(s)
```

Four warnings are the questions in `AGENTS.md` that only your team can answer.
The fifth, `decision.placeholder`, is the record you just created: it still
holds the prompts `whykit new decision` writes, and lint keeps saying so until
the context, decision and rationale are real. None of them block local work; the
[pull request tutorial](gate-pull-requests.md) shows how to clear them.

## 3. Change your mind: supersede, do not rewrite

A quarter later, new evidence says the free plan did not work. Register it:

```bash
whykit new evidence \
  --source "Free plan cohort, first quarter" --type analytics \
  --location "https://example.com/analytics/free-cohort.csv" \
  --claims "Free teams rarely upgrade and doubled the support load"
```

```text
created E-002: 00-context/evidence-register.md
```

Do not edit `D-001`. Record a new decision that supersedes it:

```bash
whykit new decision "Replace the free plan with a 30-day trial" \
  --owner Product --status approved --source E-002 --supersedes D-001
git add .
git commit -q -m "Supersede D-001 with D-002"
```

```text
created D-002: 06-decisions/d-002-replace-the-free-plan-with-a-30-day-trial.md
```

WhyKit marked `D-001` as `superseded` with `superseded_by: D-002` and updated
both rows of the decision log. The reasoning in `D-001` is untouched, so the
ledger still shows what you believed in September and what replaced it.
`whykit trace` shows which decision is live and what each one rests on:

```bash
whykit trace
```

```text
Decision traceability — <date>
  D-001  06-decisions/d-001-offer-a-free-plan-for-teams-of-up-to-three.md  (superseded by D-002)
      E-001 active
  D-002  06-decisions/d-002-replace-the-free-plan-with-a-30-day-trial.md  (live)
      E-002 active
2 decision(s), 1 live, 0 live with gaps
```

The history check compares two commits and confirms that the only change to the
accepted record was that supersession:

```bash
whykit history --base HEAD~1
```

```text
history: immutable reasoning unchanged; review log append-only
```

## 4. Try to rewrite history

Suppose someone "tidies up" the old decision so it agrees with the new one:

<!-- tutorial: exit=1 -->
```bash
echo "The free plan was always a mistake." >> 06-decisions/d-001-offer-a-free-plan-for-teams-of-up-to-three.md
git commit -q -a -m "Tidy D-001"
whykit history --base HEAD~1
```

```text
Historical decision reasoning is append-only. Supersede; do not rewrite:
  M	06-decisions/d-001-offer-a-free-plan-for-teams-of-up-to-three.md
```

The command exits with status 1, which fails a CI job. Drop the commit; if
`D-001` really needs a correction, that correction is a new decision.

```bash
git reset -q --hard HEAD~1
whykit history --base HEAD~1
```

```text
history: immutable reasoning unchanged; review log append-only
```

## 5. Record the next review

When the review date comes round, record that someone looked again. A
`confirmed` review appends a row to the review log and moves `review_by`
forward:

```bash
whykit review record D-002 --reviewer Product --outcome confirmed \
  --note "Trial conversion rechecked against the second-quarter cohort"
```

```text
review recorded: 06-decisions/d-002-replace-the-free-plan-with-a-30-day-trial.md -> confirmed
next review: <date>
```

## Where next

- [Gate pull requests in GitHub Actions](gate-pull-requests.md) runs the same
  checks on every pull request.
- [Concepts](../concepts.md) explains the lifecycle, the review outcomes and
  what the linter does not check.
- [How-to guides](../guide.md) cover evidence retirement, agent context and
  adopting existing Markdown.
