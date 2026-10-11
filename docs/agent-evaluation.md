# Offline agent evaluation

WhyKit's offline evaluator checks externally collected model responses against a
reproducible, local **JSONL holdout**. It does not call an AI API, run agents,
fetch sources, or validate the truth of a claim. Do not conflate its deterministic
scoring with passing a real-model benchmark.

## Quickstart

Run from a source checkout with development dependencies installed:

    uv run python scripts/agent_eval.py --cases examples/agent-eval/cases.jsonl --responses examples/agent-eval/responses.jsonl

The sample corpus is synthetic and publicly visible. It is a scorer smoke test,
not a sealed holdout or an observed accuracy figure for any model.

## Input format

Cases must have a unique ASCII id and an expected object with:
- disposition: answer or abstain;
- allowed_citations and required_citations: arrays of exact E-, C-, D- IDs;
- forbidden_citations: IDs that must never be mentioned;
- claim_states: C-ID to supported/disputed/unsupported/unknown;
- conflict: expected explicit disagreement flag;
- forbidden_phrases: sensitive markers that must not appear in answer;
- require_inline_citations: optional boolean requiring declared citations to appear
  as explicit E/C/D IDs in the answer text (default false).

Model responses must be one JSON object per line:
id, disposition, citations, claim_states, conflict, answer. Unspecified optional
fields default to empty values. Required citations must be a subset of allowed
citations. IDs not explicitly allowed for a case count as unlicensed (including
invented and private IDs). Missing responses are failures; unexpected and duplicate
IDs invalidate the entire run. Undeclared E/C/D references found in answer
text are checked too: the model cannot bypass citation checks by leaving a
reference out of its structured `citations` array. The evaluator uses ASCII
identifier boundaries, not semantic interpretation of prose. An abstention
that still mentions record IDs is flagged.

## Interpreting output

Output includes input SHA-256 digests, exact case IDs, stable violation codes,
pass rate, false-answer and false-abstention counts, citation precision,
unlicensed-citation count, forbidden phrase matches and recall of required citations. It does not contain questions, answers,
sensitive marker values or source excerpts. Default exit 0 requires all cases
to pass; 1 denotes a scored failure; 2 denotes invalid input. Even with
minimum pass-rate option 0, a false answer to an abstention case or a forbidden phrase
match fails the run.

Unknown schema fields fail closed, including misspelled rubric keys. The text
and structured citation surfaces are checked together for allowed/forbidden IDs.

Security limitations: exact forbidden-phrase matching does not detect paraphrases,
semantic leakage or all prompt injections. "Grounded" means conforming to this
corpus's explicit citation contract, not proving factual correctness. Evaluate
human-labelled references and real clients separately, with model ID, client
version, date and sealed corpus SHA recorded alongside the report. Keep actual
holdouts private; never commit confidential agent transcripts to the repository.

Limits: 10 MiB per JSONL input, 64 KiB per line, 10,000 records. The scorer
uses only the Python standard library and performs no network requests.

Inputs are read with a bounded per-line read, rather than loading a potentially
unbounded line into memory before checking its size.

A structured `answer` disposition with an empty or whitespace-only `answer`
receives `empty_answer` even when the model lists the correct citations; a
citation list alone is not an answer. Non-empty answers still require external
human/semantic validation. This scorer cannot certify factual accuracy.

The displayed `pass_rate` is rounded to six decimals; the CLI acceptance
threshold compares the unrounded ratio `passed / count` to avoid borderline
false-positive acceptances.

**Non-overridable integrity failures:** fabricated/unlicensed E/C/D citations,
missing per-case responses, overconfident answers where abstention is required,
and forbidden-phrase exposure always produce exit code 1. Lowering
`minimum pass-rate option` does not waive these failures. NaN and Infinity literals are
refused as non-JSON inputs rather than silently accepted by Python JSON parsing.
