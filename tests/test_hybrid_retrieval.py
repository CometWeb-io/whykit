"""Scoring and deterministic bounded traversal; no relevance-quality claims."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_retrieval import write_note
from whykit import hybrid_retrieval as hybrid
from whykit.context_applicability import context_metadata
from whykit.retrieval import retrieve_bm25
from whykit.vault_index import VaultIndex

DAY = dt.date(2026, 9, 17)


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.note("seed.md", "quartzsignal [[answer]]")
        self.note("answer.md", "Procedure without matching vocabulary")

    def note(self, path, body, **fields):
        write_note(self.root, path, body, sensitivity="public", **fields)

    def rank(self, query="quartzsignal", **options):
        index = VaultIndex.load(self.root)
        rows = ({}, {}, [])
        hashes = {n.path: hashlib.sha256(n.text.encode()).hexdigest() for n in index.notes}
        metadata = context_metadata(index, [], False, set(), rows, {}, {}, DAY, hashes)
        return hybrid.rank_prepared(index, query, rows=rows, metadata=metadata, document_hashes=hashes,
                                    valid_on=DAY, **options)

    def test_exact_forward_reverse_and_two_step_weights(self):
        report = self.rank()
        seed, answer = report["results"]
        self.assertEqual(seed["path"], "seed.md")
        self.assertEqual(seed["score"], .6)
        self.assertEqual(answer["graph_support"], .25)
        self.assertEqual(answer["score"], .1)
        self.assertEqual(answer["explanation"]["edges"][0]["walk_direction"], "forward")
        self.note("seed.md", "quartzsignal")
        self.note("answer.md", "Procedure [[seed]]")
        answer = next(r for r in self.rank()["results"] if r["path"] == "answer.md")
        self.assertEqual(answer["graph_support"], .125)
        self.note("answer.md", "Procedure [[third]]")
        self.note("seed.md", "quartzsignal [[answer]]")
        self.note("third.md", "Final procedure")
        third = next(r for r in self.rank()["results"] if r["path"] == "third.md")
        self.assertEqual(third["graph_support"], .0625)
        self.assertEqual(third["explanation"]["depth"], 2)

    def test_cycles_and_duplicate_edges_never_boost_support(self):
        baseline = self.rank()
        self.note("seed.md", "quartzsignal [[answer]] [[answer]] [[seed]]")
        self.note("answer.md", "Procedure [[seed]]")
        current = self.rank()
        self.assertEqual(current["results"][0]["graph_support"], 0)
        self.assertEqual(current["results"][1]["graph_support"], baseline["results"][1]["graph_support"])
        self.assertEqual(current, self.rank())

    def test_lexical_ablation_preserves_bm25_scores_and_order(self):
        self.note("second.md", "quartzsignal quartzsignal")
        old = retrieve_bm25(self.root, "quartzsignal")
        new = self.rank(max_depth=0)
        self.assertEqual([(r["path"], r["lexical_score"]) for r in new["results"]],
                         [(r["path"], r["score"]) for r in old])
        self.assertTrue(all(r["explanation"] is None for r in new["results"]))

    def test_results_limit_does_not_change_candidate_count(self):
        full = self.rank()
        limited = self.rank(limit=1)
        self.assertEqual(limited["stats"]["matched_candidates"], len(full["results"]))
        self.assertTrue(limited["truncation"]["results"])
        self.assertEqual(limited["results"], full["results"][:1])
        empty = self.rank(limit=0)
        self.assertEqual(empty["results"], [])
        self.assertEqual(empty["citations"], [])

    def test_unhashable_invalid_enums_are_fixed_invalid_inputs(self):
        for field in ("validity", "decision_role"):
            with self.subTest(field=field), self.assertRaises(hybrid.HybridRetrievalError) as raised:
                self.rank(**{field: []})
            self.assertEqual(raised.exception.code, "invalid_inputs")

    def test_streaming_token_budget_stops_before_materializing_all_matches(self):
        self.note("large.md", "word " * 100)
        original = hybrid._iter_tokens
        admissions = 0
        def count(text):
            nonlocal admissions
            for token in original(text):
                admissions += 1
                yield token
        with patch.object(hybrid, "MAX_TOKENS", 5), patch.object(hybrid, "_iter_tokens", side_effect=count):
            self.assertEqual(self.rank()["status"], "refused")
        self.assertEqual(admissions, 6)

    def test_evidence_bridge_weights_and_citation_use_captured_row(self):
        from whykit.compiled_context import _hash

        self.note("seed.md", "quartzsignal", source_ids=["E-001"])
        self.note("answer.md", "Procedure", source_ids=["E-001"])
        index = VaultIndex.load(self.root)
        row = {"sensitivity": "public", "source": "Captured row", "line": "19"}
        rows = ({"E-001": row}, {}, [])
        hashes = {n.path: hashlib.sha256(n.text.encode()).hexdigest() for n in index.notes}
        metadata = context_metadata(index, [], False, set(), rows, {}, {}, DAY, hashes)
        report = hybrid.rank_prepared(index, "quartzsignal", rows=rows, metadata=metadata,
                                      document_hashes=hashes, valid_on=DAY)
        answer = next(r for r in report["results"] if r["path"] == "answer.md")
        self.assertEqual(answer["graph_support"], .125)
        self.assertEqual(answer["explanation"]["nodes"],
                         ["document:seed.md", "evidence:E-001", "document:answer.md"])
        citation = next(c for c in report["citations"] if c["kind"] == "evidence")
        self.assertEqual(citation["sha256"], _hash({k: v for k, v in row.items() if k != "line"}))
        with patch.object(hybrid, "MAX_CONSTRUCTION_NODES", 2):
            refused = hybrid.rank_prepared(index, "quartzsignal", rows=rows, metadata=metadata,
                                           document_hashes=hashes, valid_on=DAY)
        self.assertEqual(refused["reason"], "scope_too_large")

    def test_invalid_source_id_refuses_before_discovery(self):
        with patch.object(VaultIndex, "load", side_effect=AssertionError("must validate first")):
            with self.assertRaises(hybrid.HybridRetrievalError) as raised:
                hybrid.retrieve_hybrid(self.root, "needle", valid_on=DAY, source_id="not-an-E-ID")
        self.assertEqual(raised.exception.code, "invalid_inputs")

    def test_diamond_uses_one_lexicographically_selected_path(self):
        self.note("seed.md", "quartzsignal [[b]] [[a]]")
        self.note("a.md", "Branch [[answer]]")
        self.note("b.md", "Branch [[answer]]")
        answer = next(r for r in self.rank()["results"] if r["path"] == "answer.md")
        self.assertEqual(answer["graph_support"], .0625)
        self.assertEqual(answer["explanation"]["nodes"],
                         ["document:seed.md", "document:a.md", "document:answer.md"])

    def test_matched_path_collector_includes_candidates_after_result_limit(self):
        matched = set()
        result = self.rank(limit=1, _matched_paths=matched)
        self.assertEqual(matched, {"seed.md", "answer.md"})
        self.assertEqual(result["stats"]["matched_candidates"], len(matched))
        self.assertEqual(len(result["results"]), 1)
        self.assertNotIn("_matched_paths", result)
        self.assertEqual(result, self.rank(limit=1))

    def test_hybrid_summary_source_ids_are_canonical_without_changing_legacy_summary(self):
        from whykit.query import _summary
        from _jsonschema import validate
        import json

        self.note("seed.md", "quartzsignal", source_ids=["E-001", "not-an-E-ID", "E-12", "e-003", "E-004\n"])
        report = hybrid.retrieve_hybrid(self.root, "quartzsignal", valid_on=DAY)
        seed = next(r for r in report["results"] if r["path"] == "seed.md")
        self.assertEqual(seed["source_ids"], ["E-001", "E-004"])
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/hybrid-retrieval.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        index = VaultIndex.load(self.root)
        legacy = _summary(index, index.note_for(self.root / "seed.md"))
        self.assertIn("not-an-E-ID", legacy["source_ids"])

    def test_prepared_ranking_scopes_operation_cache_without_persistent_writes_or_wire_changes(self):
        import json
        from whykit import dependency_graph, lint
        from whykit.parse_cache import persistent

        index = VaultIndex.load(self.root)
        rows = ({}, {}, [])
        hashes = {n.path: hashlib.sha256(n.text.encode()).hexdigest() for n in index.notes}
        metadata = context_metadata(index, [], False, set(), rows, {}, {}, DAY, hashes)
        options = dict(rows=rows, metadata=metadata, document_hashes=hashes, valid_on=DAY)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        original = dependency_graph._build_visible_graph
        caches = []
        def graph(*args, **kwargs):
            caches.append(lint._request_cache())
            self.assertIsNotNone(caches[-1])
            return original(*args, **kwargs)
        paths = set()
        with persistent(True), patch.object(dependency_graph, "_build_visible_graph", side_effect=graph):
            decorated = hybrid.rank_prepared(index, "quartzsignal", **options, _matched_paths=paths)
        self.assertIsNone(lint._request_cache())
        raw_paths = set()
        raw = hybrid.rank_prepared.__wrapped__(index, "quartzsignal", **options, _matched_paths=raw_paths)
        self.assertEqual(json.dumps(decorated, sort_keys=True), json.dumps(raw, sort_keys=True))
        self.assertEqual(paths, raw_paths)
        with lint.path_cache():
            outer = lint._request_cache()
            with patch.object(dependency_graph, "_build_visible_graph", side_effect=graph):
                self.assertEqual(hybrid.rank_prepared(index, "quartzsignal", **options), decorated)
            self.assertIs(caches[-1], outer)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.root / ".whykit").exists())

    def test_both_prepared_branches_use_closed_captured_builder_without_readmission(self):
        import json
        from whykit import dependency_graph
        from whykit.knowledge_visibility import visible_knowledge_index
        from whykit.lint import Note
        from whykit.sensitivity import note_sensitivity_level

        ordinary = VaultIndex.load(self.root)
        card = Note(path=self.root / "00-context/implementation/specs/card.md", text="quartzsignal",
            has_front=True, front={"title": "Card", "type": "specification", "status": "draft",
            "owner": "Ada Example", "created": DAY.isoformat(), "last_updated": DAY.isoformat(),
            "source_of_truth": False, "sensitivity": "restricted", "provenance": {"whykit_impact": json.dumps({
            "format": "whykit.impact-dependencies/v1", "artifact_kind": "spec", "dependencies": []})}})
        original = dependency_graph._build_visible_graph
        for extras in ([], [card]):
            with self.subTest(impact=bool(extras)):
                notes = [*ordinary.notes, *extras]
                index = VaultIndex(self.root, notes, {}, {}).subset(lambda _note: True)
                rows = ({}, {}, [])
                index.derived["evidence_register"] = rows
                hashes = {n.path: hashlib.sha256(n.text.encode("utf-8")).hexdigest() for n in notes}
                options = dict(rows=rows, metadata={}, document_hashes=hashes, valid_on=DAY,
                               allowed_sensitivities={"public", "restricted"})
                calls = []
                closed = []

                def close(view, evidence, closed=closed):
                    result = visible_knowledge_index(view, evidence)
                    closed.append(result)
                    return result

                def build(root, view, active, retired, *, calls=calls, rows=rows, closed=closed, **limits):
                    calls.append(view)
                    self.assertTrue(closed)
                    self.assertIs(view, closed[-1])
                    self.assertEqual(root, self.root)
                    self.assertTrue(view.confined)
                    self.assertEqual((active, retired), ({}, {}))
                    self.assertEqual(view.derived["evidence_register"], rows)
                    self.assertEqual(visible_knowledge_index(view, set(active)).notes, view.notes)
                    self.assertTrue(all(note_sensitivity_level(n) in {0, 3} for n in view.notes))
                    return original(root, view, active, retired, **limits)

                with patch.object(dependency_graph, "_view_graph", side_effect=AssertionError("no repeated admission")), \
                        patch("whykit.knowledge_visibility.visible_knowledge_index", side_effect=close), \
                        patch.object(dependency_graph, "_build_visible_graph", side_effect=build):
                    report = hybrid.rank_prepared(index, "quartzsignal", **options)
                    self.assertEqual(report["status"], "ranked")
                    self.assertTrue(calls)
                    calls.clear()
                    hybrid.rank_prepared(index, "quartzsignal", **options, max_depth=0)
                    hybrid.rank_prepared(index, "unmatchablealphabetic", **options)
                    self.assertFalse(calls)

    def test_canonical_decision_identity_checks_type_and_invalid_metadata_before_path_work(self):
        from types import SimpleNamespace
        from whykit.knowledge_visibility import canonical_decision_id
        from whykit.lint import load_note

        class NoPathWork:
            def relative_to(self, _root):
                raise AssertionError("nondecision or invalid metadata must not resolve a path")

        for fields in ({"type": "guide"}, {"type": "reference"}, {"type": "outcome"}):
            self.assertIsNone(canonical_decision_id(SimpleNamespace(front=fields, front_error=None, path=NoPathWork()), self.root))
        self.assertIsNone(canonical_decision_id(SimpleNamespace(front={"type": "decision"}, front_error="invalid", path=NoPathWork()), self.root))
        self.note("06-decisions/d-001-choice.md", "Reviewed choice", type="decision", decision_id="D-001")
        self.note("06-decisions/d-002-mismatch.md", "Wrong identity", type="decision", decision_id="D-001")
        self.assertEqual(canonical_decision_id(load_note(self.root / "06-decisions/d-001-choice.md"), self.root), "D-001")
        self.assertIsNone(canonical_decision_id(load_note(self.root / "06-decisions/d-002-mismatch.md"), self.root))
        self.assertIsNone(canonical_decision_id(load_note(self.root / "06-decisions/d-001-choice.md"), self.root / "other-vault"))

    def test_each_note_summary_is_computed_once_across_native_outcome_admission_passes(self):
        import shutil
        from collections import Counter
        from whykit.sensitivity import classified_evidence

        example = Path(__file__).resolve().parents[1] / "examples/outcomes"
        shutil.copytree(example, self.root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".whykit"))
        index = VaultIndex.load(self.root)
        active, retired, occurrences, _ = classified_evidence(index)
        rows = active, retired, occurrences
        hashes = {n.path: hashlib.sha256(n.text.encode()).hexdigest() for n in index.notes}
        metadata = context_metadata(index, [], False, set(), rows, {}, {"evidence_access_age_days": {"fixture": 90}}, DAY, hashes)
        with patch.object(hybrid, "_summary", wraps=hybrid._summary) as summaries:
            report = hybrid.rank_prepared(index, "fixture", rows=rows, metadata=metadata,
                                          document_hashes=hashes, valid_on=DAY, decision_role="reference",
                                          allowed_sensitivities={"public", "internal", "confidential", "restricted"}, limit=100)
        counts = Counter(call.args[1].path for call in summaries.call_args_list)
        self.assertTrue(counts)
        self.assertEqual(set(counts.values()), {1})
        self.assertEqual(counts[self.root / "00-context/outcomes/o-001.md"], 1)
        self.assertIn("00-context/outcomes/o-001.md", {r["path"] for r in report["results"]})
