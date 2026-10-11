# Governed agent preflight (read-only)

Before acting, an AI agent supplies a list of decisions it believes govern a
proposed change. The preflight checks those **explicit** references against
current WhyKit evidence, claim bindings and review deadlines. It does not
contact a model, mutate the vault, run Git or authorize an operation.

    uv run python scripts/agent_preflight.py --root /path/to/vault --proposal /path/to/proposal.json

Example proposal: an object with proposal_id, a nonempty decision_ids array of
up to 64 unique D-NNN identifiers, and optional summary. The summary is
never printed and never affects the verdict. Inputs are capped at 64 KiB,
strictly checked for duplicate JSON keys and hashed with SHA-256.

## Results

- blocked (exit 1): any named decision is missing, ambiguous, not currently
  binding, superseded, missing a review date, overdue, or dependent on
  unresolved evidence/claims.
- review_required (exit 0): explicitly named decisions passed those structural
  checks. A human must still evaluate relevance, authorization, safety and
  policy conformance.
- error (exit 2): malformed proposal, unreadable vault or filesystem error.

Both normal outcomes have review_required:true, semantics_checked:false,
execution_authorized:false. Reports include only the declared proposal ID,
input SHA, named decision IDs, stable problem codes and current date. There are
no document bodies, paths, titles, reviewer identities or secret excerpts.

## Material limitations

The tool cannot discover which decisions are relevant to a code change;
an agent can omit important IDs. Human reviewers must independently evaluate
decision coverage. It does not prove that an action complies with any decision,
or that the cited evidence is true. A dated evaluation uses *current vault
bytes*, not a reconstructed historical Git state. For private context review,
use WhyKit trace, impact, context and pack. Combine this with the
offline agent evaluator to test how real models select references.

This feature deliberately requires human action outside the CLI before any
file edits, merge, publication or deployment.

Machine-readable schema: [agent-preflight-result.schema.json](../schemas/agent-preflight-result.schema.json).
Input schema: [agent-preflight-proposal.schema.json](../schemas/agent-preflight-proposal.schema.json).
