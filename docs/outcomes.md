# Declared decision outcomes

An Outcome records a metric, its definition and unit, a baseline, a target and
an observed value. These are separate fields: a target is a plan, `null` is
unknown or absent, and `"0"` is a declared zero. A changed value does not prove
that a decision caused it. Outcome verification always leaves
`values_checked`, `causality_checked` and `execution_authorized` false.

The optional format uses `00-context/outcomes/o-NNN.md`, required note metadata,
`outcome_id`, `decision_ref` and one fenced `whykit-outcome` JSON payload.
`decision_ref` references a decision; it does not create a second D-ID identity.
The [payload schema](../schemas/outcome-record.schema.json) describes the record.
All Outcome notes are restricted, including metadata downgrades and files in
the reserved directory. The record remains draft and `source_of_truth:false`.

## Record a declaration

Prepare a local JSON file with these fields:

```json
{
  "decision_id": "D-001",
  "metric": {
    "name": "Reviewed items",
    "unit": "items",
    "definition": "Declared completed reviews in the same defined fixture cohort."
  },
  "baseline": {"value": "1.000", "as_of": "2026-09-10", "claim": null},
  "target": {"value": "3.000", "by": "2026-10-01"},
  "observed": {"value": "2.000", "as_of": "2026-09-17", "claim": null},
  "implementation_refs": ["https://example.com/pull/1"]
}
```

This example is deliberately unlinked; it is not verified measurement evidence.
Values are bounded decimal **strings**, preserving precision and trailing zeros.
Floats, exponents, nonfinite values, leading plus signs and more than 32 total
digits or 12 fractional digits are rejected. No arithmetic or target-attainment
verdict is inferred. Real sample dates cannot follow the record's creation day;
baseline and observed dates can precede it for historical measurements.

```bash
uv run whykit outcome create --root /path/to/vault --input /path/to/outcome.json --owner "Ada Example" --today 2026-09-17 --json
```

Review the complete declaration and returned `expected_sha256`, then repeat the
same command with `--write --expect-hash <reviewed-hash>`. A different input,
decision, claim pin or allocated ID requires a new preview. Applying creates a
new O-ID through the existing transaction; it does not rewrite a prior outcome,
decision, approval receipt or claim. Implementation references are opaque data:
no URL, script or service is contacted.

## Bind a historical sample to evidence

Replace a sample's `claim:null` with an exact raw Markdown claim pin:

```json
{"claim_id": "C-001", "sha256": "<the claim record's 64 lowercase hex digits>"}
```

This pin has to identify a real unchanged claim when creating the Outcome.
It binds the declaration to existing captured byte anchors; it does **not**
prove that the quoted text expresses the declared numeric value, comparable
populations, a business result or a causal effect. Assess those independently.

```bash
uv run whykit outcome list --root /path/to/vault --max-sensitivity restricted --json
uv run whykit outcome show O-001 --root /path/to/vault --max-sensitivity restricted --json
uv run whykit outcome verify O-001 --root /path/to/vault --max-sensitivity restricted --today 2026-09-17 --json
```

Verification reports baseline and observed source checks separately. CLI exit 0
requires an observed sample with unchanged claim pin and matching captured
anchors, plus an available decision. A target alone, baseline alone or unlinked
zero cannot produce that result. `observed_source_verified:true` means byte
source binding only; all three semantic/authority flags remain false.

Source observation states from [M07](claims.md#record-source-observations) are
informational for historical samples. New source bytes do not automatically
invalidate a past measurement whose pinned old capture still matches. Changed
claim pins or corrupted original anchors do invalidate its source binding.

Scoped MCP/context/graph readers withhold an Outcome unless its canonical
decision and every referenced claim are in the already granted view. Shared
classification remains a filter, not authentication or an ACL. Graph edges
`outcome_decision` and `measurement_claim` explain declared dependencies, not
causality. Compiled contexts warn that outcome values remain unverified.

The [worked synthetic vault](../examples/outcomes/README.md) supplies actual
captures, pins, a declaration and a separate format decision. It is not evidence
of real adoption or product effectiveness.
