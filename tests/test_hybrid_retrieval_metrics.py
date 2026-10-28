"""Metric oracles and untimed native preparation-scope regressions."""
from __future__ import annotations

from contextlib import nullcontext
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hybrid_retrieval_benchmark as harness
from hybrid_retrieval_benchmark import metrics
from test_hybrid_retrieval_boundaries import tree
from whykit import lint
from whykit.io import vault_read_lock
from whykit.parse_cache import persistent
from whykit.vault_index import NoteCache, reuse_notes


class HybridRetrievalMetricTests(unittest.TestCase):
    def test_graded_rank_discount_and_recall_at_distinct_k(self):
        qrels = {"direct": 2, "background": 1}
        at_one = metrics(["background", "direct"], qrels, 1)
        self.assertEqual(at_one["recall"], 0.5)
        self.assertAlmostEqual(at_one["ndcg"], 1 / 3)
        at_two = metrics(["background", "direct"], qrels, 2)
        self.assertEqual(at_two["recall"], 1)
        self.assertAlmostEqual(at_two["ndcg"], (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3)))

    def test_duplicate_never_inflates_gain_and_consumes_rank_budget(self):
        result = metrics(["direct", "direct", "background"], {"direct": 2, "background": 1}, 2)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["duplicate_results"], 1)
        self.assertAlmostEqual(result["ndcg"], 3 / (3 + 1 / math.log2(3)))

    def test_zero_gold_not_a_perfect_score_and_partial_results_use_fixed_k(self):
        result = metrics(["unrelated"], {}, 3)
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["ndcg"])
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(metrics(["direct"], {"direct": 2}, 3)["precision"], 1 / 3)
        self.assertEqual(metrics([], {"direct": 2}, 3)["recall"], 0)

    def test_bad_grades_and_k_refuse(self):
        for qrels, k in (({"a": True}, 3), ({"a": 3}, 3), ({"a": -1}, 3), ({"a": 1.0}, 3), ({"a": 1}, True), ({}, 0)):
            with self.subTest(qrels=qrels, k=k), self.assertRaises(ValueError):
                metrics([], qrels, k)


class PreparedScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault"
        self.corpus = harness.copy_corpus(self.root)
        self.case = {"text": "md", "decision_role": "reference"}

    def test_prepared_fresh_cache_nested_restore_and_error_cleanup(self):
        original, seen = harness._prepared, []
        def observe(*args, **kwargs):
            cache = lint._request_cache()
            self.assertIsNotNone(cache)
            seen.append(cache)
            return original(*args, **kwargs)
        with patch.object(harness, "_prepared", side_effect=observe):
            harness.prepared(self.root, self.case)
        self.assertIsNone(lint._request_cache())
        seen.clear()
        with patch.object(harness, "_prepared", side_effect=observe), lint.path_cache():
            outer = lint._request_cache()
            harness.prepared(self.root, self.case)
            harness.prepared(self.root, self.case)
            self.assertIs(lint._request_cache(), outer)
            self.assertTrue(outer.active)
            self.assertIsNot(seen[0], outer)
            self.assertIsNot(seen[1], seen[0])
            self.assertTrue(all(not cache.active for cache in seen))
        self.assertIsNone(lint._request_cache())
        with patch.object(harness, "_prepared", side_effect=RuntimeError("scope probe")):
            with lint.path_cache():
                outer = lint._request_cache()
                with self.assertRaisesRegex(RuntimeError, "scope probe"):
                    harness.prepared(self.root, self.case)
                self.assertIs(lint._request_cache(), outer)
                self.assertTrue(outer.active)
            with self.assertRaisesRegex(RuntimeError, "scope probe"):
                harness.prepared(self.root, self.case)
        self.assertIsNone(lint._request_cache())

    def test_native_all_arm_wires_grants_capture_and_persistent_no_write(self):
        first = self.corpus["topics"]["retry"]
        def outputs(state):
            self.assertIsNone(lint._request_cache())  # Preparation scope ends before ranking.
            return {arm: harness.wire(harness.run_arm(arm, state, self.case, limit=100)) for arm in harness.ARMS}
        before = tree(self.root)
        with persistent(True):
            for options in ({}, {"scope": ["public"]},
                            {"scope": ["public", "restricted"], "omit_evidence": first["evidence_id"]}):
                # The baseline capture is required by the post-edit checks below.
                with self.subTest(options=options) if options else nullcontext():
                    with vault_read_lock(self.root), reuse_notes(NoteCache()):
                        legacy = harness._prepared(self.root, self.case, **options)
                    current = harness.prepared(self.root, self.case, **options)
                    self.assertEqual(outputs(current), outputs(legacy))
                    paths = {current["index"].relative(note.path) for note in current["index"].notes}
                    if not options:
                        self.assertIn(first["paths"]["claim"], paths)
                        self.assertIn(first["paths"]["outcome"], paths)
                        captured, expected = current, outputs(current)
                    else:
                        self.assertNotIn(first["paths"]["claim"], paths)
                        self.assertNotIn(first["paths"]["outcome"], paths)
                    if "omit_evidence" in options:
                        self.assertNotIn(first["evidence_id"], current["rows"][0])
                        self.assertNotIn(first["evidence_id"], current["rows"][1])
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.root / ".whykit/cache").exists())
        target = self.root / first["paths"]["decision"]
        self.assertTrue(captured["metadata"][target]["applicability"]["premise"])
        target.write_bytes(target.read_bytes() + b"\nLater unreviewed disk B.\n")
        after_edit = tree(self.root)
        with persistent(True):
            self.assertEqual(outputs(captured), expected)
            refreshed = harness.prepared(self.root, self.case)
        self.assertFalse(refreshed["metadata"][target]["applicability"]["premise"])
        self.assertNotEqual(refreshed["document_hashes"][target], captured["document_hashes"][target])
        self.assertEqual(tree(self.root), after_edit)
        self.assertFalse((self.root / ".whykit/cache").exists())
