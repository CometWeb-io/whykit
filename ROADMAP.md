# Roadmap

Where WhyKit is going, and, as importantly, where it is not. Items move only
when the validation below says the current ones are working.

## The one job

**Show which accepted decisions a change touches, and which of them need
another look because something they rest on has changed.** In the pull request,
before merge, and for an agent before it edits.

Remembering decisions is becoming a platform feature. Keeping an auditable,
tool-independent link between a decision, the exact sources it rested on, the
code it governs and its review history is not, and that is where WhyKit puts its
effort.

## Shipped in 0.5

- `applies_to` scopes on decisions, editable without rewriting the reasoning.
- `whykit affected`: changed or planned files → live decisions → review
  signals (retired, missing, stale or changed sources; overdue or failed
  reviews), with uncovered files and unscoped decisions reported.
- **No migration needed:** `whykit affected --adr DIR` reads plain ADRs
  (MADR, Nygard), infers scopes from the paths they mention and flags ADRs
  that name code which no longer exists.
- **For agents:** a read-only, sensitivity-filtered MCP `affected` tool, and
  `whykit connect` to configure hosts and tell agents to call it before they
  edit.
- **In pull requests:** the Action's `affected` input adds the report to the
  job summary and the decision diff comment.
- **Scopes from Git history:** `whykit scope suggest` proposes `applies_to`
  from the commits that changed a decision or name it; `--write` fills in only
  unscoped decisions, for a person to review in the pull request.
- **Sources in the repository:** when a change modifies a file that a
  decision cites as evidence, `affected` flags the decision
  (`cited_source_changed`), even outside its scope.
- **Decisions about code patterns:** `watch_terms` match words in changed code
  lines (or in an agent's planned patch), tuned on real history.
- A fully public example vault (`examples/payments`) and a live Explorer demo.

## Next, in order

0. **Done once:** a [field evaluation](docs/field-evaluation.md) on four public
   ADR repositories. It removed noise from scopes, history and term matching,
   and showed that most real decisions need explicit `applies_to` or
   `watch_terms`, which is now the documented path.
1. **Measure before widening.** Precision and recall of `affected`, of scope
   suggestions and of ADR path inference on labelled real pull requests (see
   below). Tune the grouping rules only from that data.
2. **External sources on request.** An opt-in command that re-captures a URL
   source the user names, so a changed document surfaces as
   `claim_source_changed`. WhyKit stays offline unless asked.
3. **Scope suggestions for ADR folders.** Use commit history in `--adr` mode
   the same way, so plain ADRs get explicit scopes too.

## Validation before more features

These targets decide whether the direction is working. They are goals, not
current results.

| Measure | Target |
|---|---|
| First useful `affected` result on an existing repository | under 10 minutes |
| Precision of decisions reported as applicable | at least 90% on labelled pull requests |
| Recall of known relevant decisions | at least 80% |
| Uncertainty | uncovered files and unscoped decisions always reported, never hidden |
| Corpus | 3–5 external repositories, 60+ labelled pull requests, including negatives |
| Adoption | 3 independent teams keep it in CI for 30 days |

If WhyKit does not beat plain ADRs plus search on these cases, the plan
changes; more features will not fix a missing advantage.

## Not planned

- A policy engine. Enforce machine-checkable rules with a policy engine such
  as Open Policy Agent; WhyKit can point to the decision behind a rule.
- Agent identity, authorization or autonomous approval. Approvals stay with
  people and with your repository's permissions.
- A general AI memory, embeddings by default, a task manager or a hosted
  service. The core stays deterministic, offline and dependency-free.

## How to help

The most valuable contribution right now is evidence: run `whykit affected` on a
real pull request and [open an issue](https://github.com/CometWeb-io/whykit/issues)
with what it reported, what it missed and what was noise. Labelled cases decide
the roadmap.
