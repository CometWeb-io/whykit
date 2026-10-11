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
execution_authorized:false, trusted_history_checked:false. Reports include only the declared proposal ID,
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

A malformed vault configuration blocks the preflight instead of silently
omitting staleness rules. Missing or future evidence access dates also block a
positive structural result. This does not authenticate approval events or
check a trusted Git baseline: apply independent history review to an agent's
proposed changes before any execution.

A spoofed document with a D-NNN field is not a valid decision. Only a
well-formed record under 06-decisions with a matching ID and a locally valid
approval receipt can pass this structural preflight. Missing/future timestamps
or duplicate IDs block it. **Local receipts are editable and do not authenticate
a human reviewer**, and no trusted Git baseline is verified. A separate,
independently controlled Git review remains mandatory.

Claims with disputed, unsupported or unknown verification status block a
positive preflight even if a human-approved decision includes a written
explanation. A supported claim must still have a valid local binding. A
non-opted-in claim reference or an absent/incomplete claim projection cannot
be treated as legacy evidence with no claim obligations.
