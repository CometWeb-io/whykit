"""Transitive dependencies preserve the caller's visible, captured graph."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from _jsonschema import validate  # noqa: E402
from whykit.dependency_graph import _build_visible_graph, _view_graph, deep_impact  # noqa: E402
from whykit.lint import load_note  # noqa: E402
from whykit.vault_index import VaultIndex  # noqa: E402


def write_note(root: Path, path: str, body: str = "", **metadata) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    front = {"title": path, "sensitivity": "public", **metadata}
    target.write_text("---\n" + "\n".join(f"{key}: {json.dumps(value)}" for key, value in front.items())
                      + "\n---\n" + body, encoding="utf-8")
    return target


def write_register(root: Path, *, sensitivity: str = "public") -> Path:
    return write_note(root, "00-context/evidence-register.md", """
| ID | Source | Type | Date | Accessed | Location | Claims it supports | Sensitivity |
|---|---|---|---|---|---|---|---|
| E-001 | Example source | report | 2026-10-01 | 2026-10-01 | https://example.com/report | Example claim | public |
| E-002 | private-source-canary | report | 2026-10-01 | 2026-10-01 | https://example.com/private | private-claim-canary | restricted |
""", sensitivity=sensitivity)


class DependencyGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write_register(self.root)
        write_note(self.root, "decisions/first.md", "E-001", decision_id="D-001")
        write_note(self.root, "spec.md", "[[decisions/first]] [[decisions/first]]")
        write_note(self.root, "decisions/second.md", "[[spec]]", decision_id="D-002")

    def report(self, target: str = "E-001", **options) -> dict:
        return deep_impact(self.root, target, **options)

    def test_full_typed_explanations_rank_by_distance_and_are_deduplicated(self) -> None:
        report = self.report()
        self.assertTrue(report["exists"])
        self.assertEqual([(r["node"]["path"], r["distance"]) for r in report["results"]], [
            ("decisions/first.md", 1), ("spec.md", 2), ("decisions/second.md", 3),
        ])
        last = report["results"][-1]
        self.assertEqual(last["path_nodes"], ["evidence:E-001", "document:decisions/first.md",
                                             "document:spec.md", "document:decisions/second.md"])
        self.assertEqual([edge["type"] for edge in last["path_edges"]], ["evidence", "wikilink", "wikilink"])
        self.assertFalse(report["truncated"])
        self.assertEqual(report, self.report())
        schema = json.loads((ROOT / "schemas/deep-impact.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])

    def test_cycles_and_diamond_keep_one_shortest_path_and_all_induced_edges(self) -> None:
        write_note(self.root, "a.md", "[[decisions/first]] [[b]]")
        write_note(self.root, "b.md", "[[decisions/first]] [[a]]")
        write_note(self.root, "top.md", "[[a]] [[b]]")
        report = self.report()
        self.assertEqual(report["cycle_components"], [["document:a.md", "document:b.md"]])
        top = next(r for r in report["results"] if r["node"]["path"] == "top.md")
        self.assertEqual(top["distance"], 3)
        self.assertIn("document:a.md", top["path_nodes"])
        self.assertEqual(sum(e["from"] == "document:top.md" for e in report["edges"]), 2)
        self.assertIn({"node": "document:a.md", "reason": "dependency_cycle"}, report["review_signals"])

    def test_depth_and_node_limits_are_explicit_and_include_target(self) -> None:
        report = self.report(max_depth=1)
        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["truncation"], {"max_depth": True, "max_nodes": False})
        report = self.report(max_nodes=2)
        self.assertEqual(len(report["results"]), 1)
        self.assertTrue(report["truncation"]["max_nodes"])
        report = self.report(max_depth=0)
        self.assertEqual(report["results"], [])
        self.assertTrue(report["truncated"])
        for options in ({"max_depth": -1}, {"max_depth": True}, {"max_nodes": 0}, {"max_nodes": 1.5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.report(**options)

    def test_missing_and_malformed_targets_and_ambiguous_decisions(self) -> None:
        for target in ("E-999", "D-999", "absent"):
            with self.subTest(target=target):
                report = self.report(target)
                self.assertFalse(report["exists"])
                self.assertEqual(report["results"], [])
        for target in ("", "../spec", "/spec", "E-nope", "D-1", "a\n.md", "C:\\outside"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.report(target)
        write_note(self.root, "duplicate.md", decision_id="D-001")
        self.assertTrue(self.report("D-001")["ambiguous"])

    def test_filtered_hidden_notes_rows_and_aliascase_cannot_change_graph_statistics(self) -> None:
        before = self.report(allowed_sensitivities={"public"})
        self.assertTrue(before["exists"])
        self.assertEqual(len(before["results"]), 3)
        write_note(self.root, "hidden-title-canary.md", "[[decisions/first]] E-002", sensitivity="restricted")
        write_note(self.root, "ambiguous-canary.md", "[[spec]]", sensitivity="public", Sensitivity="restricted")
        after = self.report(allowed_sensitivities={"public"})
        self.assertEqual(after, before)
        self.assertNotIn("canary", json.dumps(after))
        self.assertFalse(self.report("E-002", allowed_sensitivities={"public"})["exists"])

    def test_supplied_grant_never_reloads_or_reads_full_evidence_register(self) -> None:
        index = VaultIndex.load(self.root).subset(lambda n: n.path.name != "evidence-register.md")
        before = list(index.notes)
        with mock.patch.object(VaultIndex, "load", side_effect=AssertionError("grant must not broaden")), \
                mock.patch("whykit.lint.evidence_register", side_effect=AssertionError("no full register read")):
            report = self.report("D-001", vault=index)
            missing = self.report("E-001", vault=index)
        self.assertFalse(missing["exists"])
        self.assertNotIn("Example source", json.dumps(report))
        self.assertEqual(index.notes, before)
        self.assertIn({"node": "document:decisions/first.md", "reason": "unavailable_evidence"}, report["review_signals"])

    def test_register_free_sparse_grant_avoids_redundant_projection(self) -> None:
        index = VaultIndex.load(self.root).subset(lambda n: n.path.name != "evidence-register.md")
        original_subset = VaultIndex.subset
        for active in ({}, {"E-001": {"sensitivity": "public", "source": "Captured"}}):
            with self.subTest(active=active):
                index.derived["evidence_register"] = (active, {}, [])
                calls = []

                def subset(view, keep, calls=calls):
                    calls.append(len(view.notes))
                    return original_subset(view, keep)

                with mock.patch.object(VaultIndex, "subset", subset), \
                        mock.patch.object(VaultIndex, "load", side_effect=AssertionError("no reload")):
                    view, nodes, _, _ = _view_graph(self.root, index, {"public"})
                self.assertLessEqual(len(calls), 2)
                self.assertEqual(view.derived["evidence_register"], (active, {}, []))
                self.assertEqual("evidence:E-001" in nodes, bool(active))
                self.assertEqual(len(view.notes), len(index.notes))

    def test_captured_builder_matches_wrapper_and_preserves_construction_limits(self) -> None:
        from whykit.dependency_graph import _build_visible_graph, GraphConstructionLimit

        full = VaultIndex.load(self.root)
        for allowed, rows in ((None, None), ({"public"}, None), ({"restricted"}, None),
                              ({"public"}, ({}, {}, []))):
            with self.subTest(allowed=allowed, rows=rows):
                grant = full.subset(lambda _note: True)
                if rows is not None:
                    grant.derived["evidence_register"] = rows
                expected = _view_graph(self.root, grant, allowed)
                view, nodes, edges, _ = expected
                active, retired, _ = view.derived["evidence_register"]
                self.assertEqual(_build_visible_graph(self.root, view, active, retired), expected)
                self.assertEqual(_build_visible_graph(self.root, view, active, retired,
                    construction_nodes=len(nodes), construction_edges=len(edges)), expected)
                if nodes:
                    with self.assertRaises(GraphConstructionLimit):
                        _build_visible_graph(self.root, view, active, retired, construction_nodes=len(nodes) - 1)
                if edges:
                    with self.assertRaises(GraphConstructionLimit):
                        _build_visible_graph(self.root, view, active, retired, construction_edges=len(edges) - 1)

    def test_restricted_register_does_not_leak_rows_through_visible_citations(self) -> None:
        write_register(self.root, sensitivity="restricted")
        report = self.report("D-001", allowed_sensitivities={"public"})
        encoded = json.dumps(report)
        self.assertNotIn("Example source", encoded)
        self.assertNotIn("evidence:E-001", encoded)
        self.assertFalse(self.report(allowed_sensitivities={"public"})["exists"])

    def test_retired_evidence_is_a_review_signal_not_semantic_contradiction(self) -> None:
        register = self.root / "00-context/evidence-register.md"
        text = register.read_text(encoding="utf-8")
        text = "\n".join(line for line in text.splitlines() if not line.startswith("| E-001 |"))
        register.write_text(text + "\n## Retired sources\n\n"
                            "| ID | Source | Retired on | Why | Replaced by |\n"
                            "|---|---|---|---|---|\n"
                            "| E-001 | Example source | 2026-10-27 | New sample required | |\n", encoding="utf-8")
        report = self.report(allowed_sensitivities={"public"})
        self.assertEqual(report["target_node"]["status"], "retired")
        self.assertIn({"node": "evidence:E-001", "reason": "retired_evidence"}, report["review_signals"])
        self.assertNotIn("contradiction", json.dumps(report))

    def test_duplicate_evidence_rows_are_unavailable_even_without_a_label_filter(self) -> None:
        register = self.root / "00-context/evidence-register.md"
        register.write_text(register.read_text(encoding="utf-8") +
                            "\n| E-001 | Ambiguous source | report | 2026-10-01 | 2026-10-01 | https://example.com/other | Claim | public |\n", encoding="utf-8")
        self.assertFalse(self.report()["exists"])
        report = self.report("D-001")
        self.assertIn({"node": "document:decisions/first.md", "reason": "unavailable_evidence"}, report["review_signals"])
        self.assertNotIn("Ambiguous source", json.dumps(report))

    def test_self_cycle_exact_limits_and_captured_register_survive_disk_changes(self) -> None:
        write_note(self.root, "self.md", "[[self]] E-001")
        report = self.report(max_depth=3, max_nodes=5)
        self.assertFalse(report["truncated"])
        self.assertEqual(report["cycle_components"], [["document:self.md"]])
        index = VaultIndex.load(self.root)
        before = self.report(vault=index)
        (self.root / "00-context/evidence-register.md").write_text("private-disk-canary", encoding="utf-8")
        self.assertEqual(self.report(vault=index), before)

    def test_supersession_is_typed_and_symlinked_targets_do_not_escape(self) -> None:
        write_note(self.root, "new.md", decision_id="D-003", supersedes="D-001")
        report = self.report("D-001")
        self.assertIn({"from": "document:new.md", "to": "document:decisions/first.md", "type": "supersedes"}, report["edges"])
        with tempfile.TemporaryDirectory() as outside:
            secret = write_note(Path(outside), "outside.md", "outside-body-canary")
            try:
                (self.root / "redirect.md").symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            write_note(self.root, "pointer.md", "[[redirect]]")
            self.assertFalse(self.report("redirect")["exists"])
            self.assertNotIn("outside-body-canary", json.dumps(self.report("D-001")))

    def test_full_filename_node_ids_preserve_case_extension_collisions(self) -> None:
        # Captured lexical identities must stay distinct on every host, even
        # where native Path keys cannot represent a case-only pair.
        root = PurePosixPath('/captured/vault')
        notes = [load_note(root / name, text='E-001') for name in ('same.md', 'same.MD')]
        captured = VaultIndex(root, notes, {note.path: note for note in notes}, {},
                              _relative={note.path: note.path.relative_to(root).as_posix() for note in notes})
        _, nodes, edges, _ = _build_visible_graph(root, captured, {'E-001': {'sensitivity': 'public'}}, {})
        self.assertEqual(set(nodes), {'document:same.md', 'document:same.MD', 'evidence:E-001'})
        self.assertEqual({(edge['from'], edge['to'], edge['type']) for edge in edges}, {
            ('document:same.md', 'evidence:E-001', 'evidence'),
            ('document:same.MD', 'evidence:E-001', 'evidence'),
        })
        # Retain the complete native pipeline where its Path flavor can hold
        # both keys; the lexical node/edge assertions above always execute.
        if (self.root / 'same.md') == (self.root / 'same.MD'):
            return
        index = VaultIndex.load(self.root)
        notes = [*index.notes, *(load_note(self.root / name, text="E-001") for name in ("same.md", "same.MD"))]
        captured = VaultIndex(self.root, notes, {note.path: note for note in notes}, {})
        report = self.report(vault=captured)
        ids = {item["node"]["id"] for item in report["results"]}
        self.assertTrue({"document:same.md", "document:same.MD"}.issubset(ids))

    def test_schema_covers_missing_filtered_and_truncated_reports(self) -> None:
        schema = json.loads((ROOT / "schemas/deep-impact.schema.json").read_text(encoding="utf-8"))
        for target, options in (("missing", {}), ("E-001", {"allowed_sensitivities": set()}),
                                ("E-001", {"max_depth": 0}), ("document:spec.md", {})):
            with self.subTest(target=target, options=options):
                self.assertEqual(validate(self.report(target, **options), schema), [])


if __name__ == "__main__":
    unittest.main()
