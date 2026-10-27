# Synthetic hybrid retrieval development corpus

48 semantic identities: 42 ranked Markdown documents and six evidence-row intermediaries. Home.md is required vault backing and is excluded by the shared benchmark grant. The canonical inventory explicitly lists all 42 ranked paths; register/log backing is captured for scope/governance and removed before every ranking arm. Raw capture inputs and evidence rows are never lexical documents.

Six wholly synthetic topics, four development and two locked, contain actual native capture, claim, approval, confirmed-review and outcome records. All fields and aliases were set before approval; receipts were not fabricated or rewritten. Numeric Outcome values are stipulated local declarations, not independently verified values or causal effects.

`topics.json` contains the content stipulations. `qrels.json` freezes 24 graded intents and eight separate controls, intent roles, rationales and topic partitions. Grades follow intended use, not graph distance. E rows/citations are not hidden extra retrieval hits. This is author-designed regression data, not independent human gold, model evaluation or user relevance evidence. No ranking output was inspected while authoring/freezing the corpus.

`generate.py DESTINATION --manifest MANIFEST` uses only native local APIs and writes an absent synthetic destination. `corpus-manifest.json` binds the exact bytes of the checked-in vault, plus the permitted result universe. Two independently generated destinations matched canonical bytes before any ranking run. `.whykit/mutation.lock` is a writer implementation artifact and is excluded from canonical inventory/copies.

`tests/hybrid_retrieval_benchmark.py --freeze-inputs ABSENT_FILE` validates bytes without ranking. Measurements require explicit `--run --expect-fixture-sha256 HASH --output ABSENT_FILE`; they are a later separately authorized step after runtime freeze. Never tune these qrels, queries, topic split or fixed recipe after inspecting results; report every loss.
