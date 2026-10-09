# WhyKit claims example

Synthetic data only. This is deterministic mechanism proof, not product,
model, host, reviewer-identity or factual-truth proof. Evaluate at the pinned
2026-10-09 date; this date evaluates current captured data, not Git history.

C-001 has one supporting and one contradicting local observation. Both were
reviewed with the normal preview/apply workflow, so its state is `disputed`.
D-001 explicitly accepts that bounded conflict. D-002 records the format
choice: separate optional C records preserve the meaning of existing E IDs.

```sh
whykit lint --root examples/claims --strict --today 2026-10-09
whykit check --root examples/claims --profile ci --today 2026-10-09
whykit trace --root examples/claims --decision D-001 --today 2026-10-09 --json
whykit pack --root examples/claims D-001 --max-chars 4000
```

Start with `claims enable` preview, apply its `expected_sha256`, then use
`new claim` to create a draft. Capture local UTF-8 material, normalize/hash
it and fill the Evidence relations explicitly. Preview and apply C approval
before `new decision --claim C-NNN`; explain each disputed or unsupported
claim in the decision's Claim assessment. Preview and apply D approval, then
trace the decision. Full instructions are in `docs/claims.md` in the source repository.

The hash-named snapshot files contain invented reserved-domain observations.
Receipts are local attribution and integrity checks; trusted Git history is
the independent boundary. Do not reset accepted records to draft.
