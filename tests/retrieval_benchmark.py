"""Reproduce the fixed synthetic lexical/BM25 comparison without external data."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import tempfile
import time

from test_retrieval import CORPUS, load_corpus, metrics
from whykit.query import query_vault
from whykit.retrieval import retrieve_bm25
from whykit.vault_index import VaultIndex


def main() -> None:
    rows = []
    timings: dict[str, list[float]] = {"lexical": [], "bm25": []}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        corpus = load_corpus(root)
        index = VaultIndex.load(root)
        for query in corpus["queries"]:
            outputs = {}
            for iteration in range(31):
                # Alternate execution order; discard the first warm-up pair.
                for algorithm in (("lexical", "bm25") if iteration % 2 else ("bm25", "lexical")):
                    started = time.perf_counter()
                    hits = (
                        query_vault(root, text=query["text"], limit=3, vault=index)["results"]
                        if algorithm == "lexical" else retrieve_bm25(root, query["text"], limit=3, vault=index)
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    if iteration:
                        timings[algorithm].append(elapsed_ms)
                    outputs[algorithm] = {
                        "paths": [hit["path"] for hit in hits],
                        "recall_at_3": None, "ndcg_at_3": None,
                    }
                    if query["relevance"]:
                        recall, ndcg = metrics(outputs[algorithm]["paths"], query["relevance"])
                        outputs[algorithm].update(recall_at_3=recall, ndcg_at_3=ndcg)
            rows.append({"query": query["text"], "relevance": query["relevance"], **outputs})
    summary = {}
    for algorithm, samples in timings.items():
        judged = [row[algorithm] for row in rows if row["relevance"]]
        summary[algorithm] = {
            "mean_recall_at_3": statistics.mean(row["recall_at_3"] for row in judged),
            "mean_ndcg_at_3": statistics.mean(row["ndcg_at_3"] for row in judged),
            "warm_median_ms": statistics.median(samples),
            "warm_p95_ms": sorted(samples)[math.ceil(0.95 * len(samples)) - 1],
            "timing_samples": len(samples),
        }
    print(json.dumps({
        "scope": "Synthetic development fixture, not independent or real-model evidence",
        "python": platform.python_version(), "platform": platform.platform(),
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "documents": len(corpus["documents"]), "queries": len(rows),
        "judged_queries": sum(bool(row["relevance"]) for row in rows),
        "timing_scope": "Warm preloaded VaultIndex; filter/rank included, file read/parse excluded",
        "summary": summary, "results": rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
