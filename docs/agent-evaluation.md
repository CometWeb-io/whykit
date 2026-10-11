# Offline agent evaluation

WhyKit's offline evaluator checks externally collected model responses against a
reproducible, local **JSONL holdout**. It does not call an AI API, run agents,
fetch sources, or validate the truth of a claim. Do not conflate its deterministic
scoring with passing a real-model benchmark.

## Quickstart

Run from a source checkout, or from an installed source package:

    python -m whykit.agent_eval --cases examples/agent-eval/cases.jsonl --responses examples/agent-eval/responses.jsonl

The sample corpus is synthetic and publicly visible. It is a scorer smoke test,
not a sealed holdout or an observed accuracy figure for any model.

## Input format

Cases must have a unique ASCII id and an expected object with:
- disposition: answer or abstain;
- allowed_citations and required_citations: arrays of exact E-, C-, D- IDs;
- forbidden_citations: IDs that must never be mentioned;
- claim_states: C-ID to supported/disputed/unsupported/unknown;
- conflict: expected explicit disagreement flag;
- forbidden_phrases: sensitive markers that must not appear in answer.

Model responses must be one JSON object per line:
id, disposition, citations, claim_states, conflict, answer. Unspecified optional
fields default to empty values. Required citations must be a subset of allowed
citations. IDs not explicitly allowed for a case count as unlicensed (including
invented and private IDs). Missing responses are failures; unexpected and duplicate
IDs invalidate the entire run. Extra model-generated citations never silently
become evidence.

## Interpreting output

Output includes input SHA-256 digests, exact case IDs, stable violation codes,
pass rate, false-answer count, unlicensed-citation count, forbidden phrase matches
and recall of required citations. It does not contain questions, answers,
sensitive marker values or source excerpts. Default exit 0 requires all cases
to pass; 1 denotes a scored failure; 2 denotes invalid input. Even with
--min-pass-rate 0, a false answer to an abstention case or a forbidden phrase
match fails the run.

Security limitations: exact forbidden-phrase matching does not detect paraphrases,
semantic leakage or all prompt injections. "Grounded" means conforming to this
corpus's explicit citation contract, not proving factual correctness. Evaluate
human-labelled references and real clients separately, with model ID, client
version, date and sealed corpus SHA recorded alongside the report. Keep actual
holdouts private; never commit confidential agent transcripts to the repository.

Limits: 10 MiB per JSONL input, 64 KiB per line, 10,000 records. The scorer
uses only the Python standard library and performs no network requests.
