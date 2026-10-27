"""BM25 regressions, including filter isolation and a fixed development corpus."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whykit.query import query_vault  # noqa: E402
from whykit.retrieval import retrieve_bm25  # noqa: E402
from whykit.vault_index import VaultIndex  # noqa: E402

CORPUS = Path(__file__).parent / "fixtures" / "retrieval-corpus.json"


def write_note(root: Path, path: str, body: str, **metadata) -> None:
    front = {
        "title": "Record", "type": "guide", "status": "draft", "owner": "Ada Example",
        "source_of_truth": False, "source_ids": [], "tags": [], **metadata,
    }
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in front.items())
        + "\n---\n\n" + body, encoding="utf-8",
    )


def load_corpus(root: Path) -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    for doc in corpus["documents"]:
        write_note(root, doc["path"], doc["body"], title=doc["title"])
    return corpus


def metrics(paths: list[str], relevance: dict[str, int], k: int = 3) -> tuple[float, float]:
    relevant = {path for path, grade in relevance.items() if grade > 0}
    if not relevant:
        raise ValueError("Recall/nDCG require a query with relevant documents")
    recall = len(set(paths[:k]) & relevant) / len(relevant)
    dcg = sum((2 ** relevance.get(path, 0) - 1) / math.log2(rank + 2) for rank, path in enumerate(paths[:k]))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:k]))
    return recall, dcg / ideal


class BM25Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_formula_and_path_tie_break(self) -> None:
        for path in ("b.md", "a.md"):
            write_note(self.root, path, "needle")
        hits = retrieve_bm25(self.root, "needle")
        self.assertEqual([hit["path"] for hit in hits], ["a.md", "b.md"])
        for hit in hits:
            self.assertAlmostEqual(hit["score"], math.log1p(0.5 / 2.5))

    def test_length_normalization_and_term_saturation(self) -> None:
        write_note(self.root, "short.md", "needle")
        write_note(self.root, "long.md", "needle " + "filler " * 80)
        write_note(self.root, "repeated.md", "needle " * 80)
        hits = retrieve_bm25(self.root, "needle")
        scores = {hit["path"]: hit["score"] for hit in hits}
        self.assertGreater(scores["short.md"], scores["long.md"])
        self.assertGreater(scores["repeated.md"], scores["short.md"])
        self.assertLess(scores["repeated.md"], 2.2 * math.log1p(0.5 / 3.5))

    def test_tokens_unicode_or_matching_and_repeated_terms(self) -> None:
        write_note(self.root, "a.md", "Zażółć SQLite: migration.", title="Łódź")
        write_note(self.root, "b.md", "catalogue")
        self.assertEqual(retrieve_bm25(self.root, "cat"), [])
        self.assertEqual(retrieve_bm25(self.root, "sqlite unknown"), retrieve_bm25(self.root, "sqlite"))
        self.assertEqual(retrieve_bm25(self.root, "SQLITE sqlite"), retrieve_bm25(self.root, "sqlite"))
        self.assertEqual(retrieve_bm25(self.root, "ŁO\u0301DZ\u0301")[0]["path"], "a.md")
        self.assertEqual(retrieve_bm25(self.root, "sqlite migration")[0]["path"], "a.md")
        self.assertEqual(query_vault(self.root, text="sqlite migration")["results"], [])

    def test_empty_missing_and_limits(self) -> None:
        self.assertEqual(retrieve_bm25(self.root, "anything"), [])
        write_note(self.root, "a.md", "needle")
        write_note(self.root, "b.md", "needle")
        for query in ("", " \n", "---!?", "absent"):
            self.assertEqual(retrieve_bm25(self.root, query), [])
        self.assertEqual(retrieve_bm25(self.root, "needle", limit=0), [])
        self.assertEqual(len(retrieve_bm25(self.root, "needle", limit=1)), 1)
        with self.assertRaisesRegex(ValueError, "limit"):
            retrieve_bm25(self.root, "", limit=-1)

    def test_all_filters_share_query_semantics(self) -> None:
        write_note(self.root, "match.md", "needle E-001", type="decision", status="approved",
                   sensitivity="public", source_of_truth=True, tags=["Topic"])
        write_note(self.root, "other.md", "needle", owner="Ben Example", sensitivity="restricted")
        filters = [
            {"doc_type": "decision"}, {"status": "approved"}, {"owner": "ADA"},
            {"sensitivity": "public"}, {"allowed_sensitivities": {"public"}},
            {"source_id": "E-001"}, {"tag": "topic"}, {"canonical_only": True},
        ]
        for options in [*filters, {key: value for item in filters for key, value in item.items()}]:
            with self.subTest(options=options):
                expected = query_vault(self.root, **options)["results"]
                actual = retrieve_bm25(self.root, "needle", **options)
                self.assertEqual([{key: value for key, value in hit.items() if key != "score"} for hit in actual], expected)
                self.assertEqual(actual[0]["path"], "match.md")
        self.assertEqual(retrieve_bm25(self.root, "needle", allowed_sensitivities=set()), [])
        self.assertEqual(retrieve_bm25(self.root, "needle", status="archived"), [])

    def test_template_and_unlabelled_records(self) -> None:
        write_note(self.root, "template.md", "needle", type="decision", status="template")
        write_note(self.root, "draft.md", "needle", type="decision")
        hits = retrieve_bm25(self.root, "needle", doc_type="decision", allowed_sensitivities={"internal"})
        self.assertEqual([hit["path"] for hit in hits], ["draft.md"])
        hits = retrieve_bm25(self.root, "needle", doc_type="decision", status="template")
        self.assertEqual([hit["path"] for hit in hits], ["template.md"])
        self.assertEqual(retrieve_bm25(self.root, "needle", allowed_sensitivities={"public"}), [])

    def test_excluded_notes_do_not_change_scores_or_order(self) -> None:
        write_note(self.root, "public.md", "needle", sensitivity="public")
        write_note(self.root, "background.md", "unrelated", sensitivity="public")
        before = retrieve_bm25(self.root, "needle", allowed_sensitivities={"public"})
        for i in range(12):
            write_note(self.root, f"hidden-{i}.md", "needle " * 50, sensitivity="restricted")
        after = retrieve_bm25(self.root, "needle", allowed_sensitivities={"public"})
        self.assertEqual(before, after)
        self.assertEqual(retrieve_bm25(self.root, "needle", sensitivity="restricted", allowed_sensitivities={"public"}), [])

    def test_unreadable_labels_never_enter_filtered_corpus_or_change_scores(self) -> None:
        for label in ("public", "internal"):
            write_note(self.root, f"{label}.md", "needle", sensitivity=label)
            write_note(self.root, f"{label}-background.md", "unrelated", sensitivity=label)
        malformed = (
            'sensitivity: public\nSensitivity: restricted',
            'Sensitivity: restricted',
            'sensitivity: public\nsensitivity: restricted',
            'sensitivity: [public',
            'sensitivity: unknown',
            'sensitivity: [public]',
        )
        for options in ({"allowed_sensitivities": {"public"}}, {"sensitivity": "public"},
                        {"allowed_sensitivities": {"internal"}}, {"sensitivity": "internal"}):
            before = retrieve_bm25(self.root, "needle", **options)
            self.assertEqual(len(before), 1)
            for front in malformed:
                with self.subTest(options=options, front=front):
                    denied = self.root / "denied.md"
                    denied.write_text(f"---\n{front}\n---\n" + "needle " * 100, encoding="utf-8")
                    try:
                        self.assertEqual(retrieve_bm25(self.root, "needle", **options), before)
                    finally:
                        denied.unlink()

    def test_allowed_labels_use_canonical_classification_within_supplied_grant(self) -> None:
        write_note(self.root, "a.md", "needle", sensitivity="PUBLIC")
        write_note(self.root, "outside-grant.md", "needle " * 50, sensitivity="public")
        write_note(self.root, "denied.md", "needle " * 50, sensitivity="public", Sensitivity="restricted")
        index = VaultIndex.load(self.root).subset(lambda note: note.path.name != "outside-grant.md")
        before = query_vault(self.root, vault=index)
        with mock.patch.object(VaultIndex, "load", side_effect=AssertionError("must retain supplied grant")):
            hits = retrieve_bm25(self.root, "needle", allowed_sensitivities={"public"}, vault=index)
        self.assertEqual([hit["path"] for hit in hits], ["a.md"])
        self.assertEqual(query_vault(self.root, vault=index), before)

    def test_confined_index_is_respected_and_not_mutated(self) -> None:
        write_note(self.root, "a.md", "needle")
        write_note(self.root, "hidden.md", "needle " * 20)
        index = VaultIndex.load(self.root).subset(lambda note: note.path.name == "a.md")
        before = query_vault(self.root, text="needle", vault=index)
        hits = retrieve_bm25(self.root, "needle", vault=index)
        self.assertEqual([hit["path"] for hit in hits], ["a.md"])
        self.assertEqual(query_vault(self.root, text="needle", vault=index), before)
        self.assertNotIn("score", before["results"][0])

    def test_title_path_body_but_not_arbitrary_front_matter_are_searchable(self) -> None:
        write_note(self.root, "pathneedle.md", "bodyneedle", title="titleneedle", owner="ownerneedle")
        for query in ("pathneedle", "bodyneedle", "titleneedle"):
            self.assertEqual(len(retrieve_bm25(self.root, query)), 1)
        self.assertEqual(retrieve_bm25(self.root, "ownerneedle"), [])
        (self.root / "plain.md").write_text("plainneedle", encoding="utf-8")
        self.assertEqual(retrieve_bm25(self.root, "plainneedle")[0]["path"], "plain.md")

    def test_fixed_development_corpus_comparison(self) -> None:
        corpus = load_corpus(self.root)
        index = VaultIndex.load(self.root)
        baseline, bm25 = [], []
        for query in corpus["queries"]:
            lexical = query_vault(self.root, text=query["text"], limit=3, vault=index)["results"]
            ranked = retrieve_bm25(self.root, query["text"], limit=3, vault=index)
            if not query["relevance"]:
                self.assertEqual(lexical, [])
                self.assertEqual(ranked, [])
                continue
            baseline.append(metrics([hit["path"] for hit in lexical], query["relevance"]))
            bm25.append(metrics([hit["path"] for hit in ranked], query["relevance"]))
        self.assertGreater(sum(row[0] for row in bm25), sum(row[0] for row in baseline))
        self.assertGreater(sum(row[1] for row in bm25), sum(row[1] for row in baseline))

    def test_metric_calculation(self) -> None:
        self.assertEqual(metrics(["a", "b"], {"a": 2, "b": 1}), (1.0, 1.0))
        recall, ndcg = metrics(["x", "a"], {"a": 2, "b": 1}, k=2)
        self.assertEqual(recall, 0.5)
        self.assertAlmostEqual(ndcg, (3 / math.log2(3)) / (3 + 1 / math.log2(3)))
        with self.assertRaises(ValueError):
            metrics([], {})


if __name__ == "__main__":
    unittest.main()
