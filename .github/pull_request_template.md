## Problem

What concrete failure mode or workflow gap does this address?

## Change

What changed, and which data-format or governance invariant is affected?

## Verification

- [ ] `uv sync --locked`
- [ ] `uv run python -m unittest discover -s tests -v`
- [ ] `uv run whykit lint examples/northline --strict --today 2026-09-17`
- [ ] `uv run whykit init /tmp/fresh && uv run whykit lint --root /tmp/fresh`
- [ ] Explorer build/check if UI or indexing changed
- [ ] No private vault data, credentials or internal source IDs included

## Compatibility

Does this change front matter, IDs, schemas, parsing or decision/evidence semantics?
