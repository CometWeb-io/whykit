"""Adversarial grants for namespaced native impact declarations; local fixtures only."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whykit.compiled_context import compile_context
from whykit.dependency_graph import deep_impact
from whykit.hybrid_retrieval import retrieve_hybrid
from whykit.knowledge_v2 import project_knowledge_v2
from whykit.knowledge_visibility import visible_knowledge_index
from whykit.lint import load_note
from whykit.mcp_server import VaultTools
from whykit.pack import build_pack
from whykit.query import query_vault
from whykit.retrieval import retrieve_bm25
from whykit.sensitivity import note_sensitivity_level
from whykit.vault_index import VaultIndex

FIXTURE = Path(__file__).parent / "fixtures/impact"
spec = importlib.util.spec_from_file_location("impact_visibility_fixture", FIXTURE / "generate.py")
assert spec and spec.loader
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
DAY = dt.date(2026, 10, 27)
ALL = {"public", "internal", "confidential", "restricted"}
CANARY = "hiddenimplementationcanary"


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


class ImpactVisibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "vault"
        self.root.mkdir()
        (self.root / "00-context").mkdir()
        native.put(self.root / "Home.md", native.rendered_note("Public map", "ordinary visibleword", label="public"))

    def card(self, name="00-context/implementation/tasks/a.md", targets=(), *, label="restricted", body=CANARY):
        deps = [{"relation": "requires", "target": "document:" + target, "pin": None} for target in targets]
        path = self.root / name
        native.put(path, native.rendered_note("Private implementation", body, label=label,
                   producer={"whykit_impact": native.impact_declaration("task", deps)}))
        return path

    def view(self):
        index = VaultIndex.load(self.root)
        view = index.subset(lambda note: True)
        view.derived["evidence_register"] = ({}, {}, [])
        return view

    def test_reserved_and_namespace_marker_floors_do_not_follow_raw_public_label(self):
        for name in ("00-context/implementation/tasks/a.md", "00-context/Implementation/tasks/a.MD"):
            with self.subTest(path=name):
                path = self.card(name, label="public")
                self.assertEqual(note_sensitivity_level(load_note(path)), 3)
        note = load_note(self.root / "Home.md")
        note.front["provenance"] = {"WhyKit-Impact": None}
        self.assertEqual(note_sensitivity_level(note), 3)
        note.front["provenance"] = {"foreign": {"whykit_impact": "unrelated"}, "artifact_kind": "foreign"}
        self.assertEqual(note_sensitivity_level(note), 0)

    def test_missing_dependency_propagates_across_chain_and_closed_cycles_survive(self):
        a = self.card(targets=["00-context/implementation/tasks/b.md"])
        b = self.card("00-context/implementation/tasks/b.md", ["00-context/implementation/tasks/a.md"])
        full = self.view()
        with patch.object(VaultIndex, "load", side_effect=AssertionError("grant widened")):
            closed = visible_knowledge_index(full, set())
            self.assertIsNotNone(closed.note_for(a))
            self.assertIsNotNone(closed.note_for(b))
            missing = full.subset(lambda note: note.path != b)
            denied = visible_knowledge_index(missing, set())
            self.assertIsNone(denied.note_for(a))
        native.put(b, native.rendered_note("B", CANARY, producer={"whykit_impact": native.impact_declaration(
            "task", [{"relation": "requires", "target": "document:missing.md", "pin": None}])}))
        pruned = visible_knowledge_index(self.view(), set())
        self.assertIsNone(pruned.note_for(a))
        self.assertIsNone(pruned.note_for(b))

    def test_malformed_and_dual_native_holders_are_not_ordinary_documents(self):
        path = self.card()
        for mutate in (lambda n: n.front.update(claim_id="C-001"),
                       lambda n: n.front.update(outcome_id="O-001"),
                       lambda n: n.front["provenance"].update(whykit_impact="not JSON")):
            full = self.view()
            mutate(full.note_for(path))
            self.assertIsNone(visible_knowledge_index(full, set()).note_for(path))

    def reports(self, allowed=None):
        allowed = {"public"} if allowed is None else allowed
        tools = VaultTools(self.root, max_sensitivity="public" if allowed == {"public"} else "restricted")
        return {
            "query": query_vault(self.root, text="visibleword", allowed_sensitivities=allowed),
            "bm25": retrieve_bm25(self.root, "visibleword", allowed_sensitivities=allowed),
            "compiler": compile_context(self.root, query="visibleword", today=DAY, allowed_sensitivities=allowed),
            "hybrid": retrieve_hybrid(self.root, "visibleword", valid_on=DAY, allowed_sensitivities=allowed),
            "knowledge": project_knowledge_v2(self.root, allowed_sensitivities=allowed),
            "pack": build_pack(self.root, targets=["Home"], allowed_sensitivities=allowed),
            "deep": deep_impact(self.root, "Home", allowed_sensitivities=allowed),
            "mcp": tools.query("visibleword"),
        }

    def test_denied_high_frequency_hubs_do_not_change_public_scores_hashes_counts_or_budgets(self):
        before = self.reports()
        self.card(targets=["Home.md"], label="public", body=("visibleword " + CANARY + " ") * 1000)
        after = self.reports()
        self.assertEqual(after, before)
        self.assertNotIn(CANARY, json.dumps(after))

    def test_unscoped_pack_cannot_export_a_marked_invalid_holder(self):
        self.card(targets=["missing.md"])
        report = build_pack(self.root, targets=["00-context/implementation/tasks/a"])
        self.assertEqual(report["resolved"], 0)
        self.assertNotIn(CANARY, json.dumps(report))

    def test_effective_restricted_D_label_filters_without_rewriting_stored_public_bytes(self):
        path = self.root / "06-decisions/d-001-choice.md"
        text = native.rendered_note("Governed reference", "historical declaration", kind="decision", label="public",
                                   producer={"whykit_impact": native.impact_declaration(None, [])})
        from whykit.scaffold import _frontmatter_replace
        text = _frontmatter_replace(text, "decision_id", "D-001")
        native.put(path, text)
        before = path.read_bytes()
        results = query_vault(self.root, allowed_sensitivities={"restricted"})["results"]
        self.assertEqual([row["path"] for row in results], [path.relative_to(self.root).as_posix()])
        self.assertEqual(results[0]["sensitivity"], "restricted")
        self.assertEqual(build_pack(self.root, targets=["D-001"], allowed_sensitivities={"restricted"})["resolved"], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_marked_structured_evidence_and_claim_references_require_the_grant(self):
        path = self.card()
        full = self.view()
        full.note_for(path).front["source_ids"] = ["E-001"]
        self.assertIsNone(visible_knowledge_index(full, set()).note_for(path))
        self.assertIsNotNone(visible_knowledge_index(full, {"E-001"}).note_for(path))
        for ids in ("E-001", [True], ["not-evidence"]):
            full.note_for(path).front["source_ids"] = ids
            self.assertIsNone(visible_knowledge_index(full, {"E-001"}).note_for(path))

    def test_native_C_D_I_and_historical_Outcome_cascade_only_when_grants_are_missing(self):
        root = self.root / "native"
        oracle = json.loads((FIXTURE / "oracle-v1.json").read_text(encoding="utf-8"))
        native.make_base(root, oracle, "A", native.NativeWriter())
        from whykit.sensitivity import classified_evidence
        full = VaultIndex.load(root)
        active, retired, occurrences, _ = classified_evidence(full)
        granted = full.subset(lambda _note: True)
        granted.derived["evidence_register"] = (active, retired, occurrences)
        all_visible = visible_knowledge_index(granted, set(active))
        historical = root / "00-context/outcomes/o-003.md"
        self.assertIsNotNone(all_visible.note_for(historical))
        denied = visible_knowledge_index(granted, {"E-002"})
        for path in ("00-context/claims/c-001.md", "00-context/outcomes/o-003.md",
                     "06-decisions/d-001-synthetic-retry-primary-choice.md",
                     "00-context/implementation/tasks/retry-primary.md"):
            self.assertIsNone(denied.note_for(root / path))
        self.assertIsNotNone(denied.note_for(root / "00-context/outcomes/o-006.md"))
        self.assertEqual(set(denied.derived["evidence_register"][0]), {"E-002"})

    def test_hybrid_denied_holder_does_not_consume_construction_or_term_budget(self):
        import whykit.hybrid_retrieval as hybrid
        self.card(targets=["missing.md"], body="visibleword " * 2000)
        index = self.view()
        hashes = {note.path: "0" * 64 for note in index.notes}
        with patch.object(hybrid, "MAX_CONSTRUCTION_NODES", 1), patch.object(hybrid, "MAX_TOKENS", 10):
            result = hybrid.rank_prepared(index, "visibleword", rows=({}, {}, []), metadata={},
                                          document_hashes=hashes, valid_on=DAY, allowed_sensitivities=ALL,
                                          decision_role="reference")
        self.assertEqual(result["status"], "ranked")
        self.assertEqual([row["path"] for row in result["results"]], ["Home.md"])

    def test_scoped_knowledge_denied_holder_does_not_consume_capture_limit(self):
        import whykit.knowledge_v2 as knowledge
        with patch.object(knowledge, "MAX_FILES", 1):
            before = project_knowledge_v2(self.root, allowed_sensitivities=ALL)
            self.card(targets=["missing.md"], body=CANARY * 2000)
            after = project_knowledge_v2(self.root, allowed_sensitivities=ALL)
        self.assertEqual(after, before)

    def test_scoped_lint_keeps_malformed_candidate_and_warns_only_about_owned_syntax(self):
        from whykit.lint import lint
        path = self.card()
        for scalar, expected in ((native.impact_declaration("task", []), False), ("not JSON", True)):
            native.put(path, native.rendered_note("Owned card", CANARY,
                       producer={"whykit_impact": scalar}))
            view = self.view()
            _, findings = lint(self.root, [path.relative_to(self.root).as_posix()], vault=view,
                                orphans=False, secrets=False, today=DAY)
            warnings = [row for row in findings if row.code == "impact.invalid"]
            self.assertEqual(bool(warnings), expected)
            if expected:
                self.assertEqual(warnings[0].level, "warning")
        path.unlink()
        ordinary = self.root / "Home.md"
        view = self.view()
        view.note_for(ordinary).front["provenance"] = {"artifact_kind": "foreign", "foreign": {"whykit_impact": None}}
        _, findings = lint(self.root, ["Home.md"], vault=view, orphans=False, secrets=False, today=DAY)
        self.assertFalse(any(row.code == "impact.invalid" for row in findings))

    def test_pruning_a_genuine_canonical_duplicate_cannot_restore_a_decision_identity(self):
        from whykit.scaffold import _frontmatter_replace
        for suffix in ("good", "other"):
            path = self.root / f"06-decisions/d-001-{suffix}.md"
            deps = [] if suffix == "good" else [{"relation": "requires", "target": "document:missing.md", "pin": None}]
            text = native.rendered_note("Conflicting decision", "local declaration", kind="decision",
                                       producer={"whykit_impact": native.impact_declaration(None, deps)})
            native.put(path, _frontmatter_replace(text, "decision_id", "D-001"))
        dependent = self.card()
        view = self.view()
        view.note_for(dependent).front["provenance"]["whykit_impact"] = native.impact_declaration("task", [
            {"relation": "requires", "target": "decision:D-001", "pin": None}])
        result = visible_knowledge_index(view, set())
        self.assertIsNone(result.note_for(self.root / "06-decisions/d-001-good.md"))
        self.assertIsNone(result.note_for(self.root / "06-decisions/d-001-other.md"))
        self.assertIsNone(result.note_for(dependent))

    def test_invalid_D_path_cannot_poison_a_canonical_visible_identity(self):
        from whykit.scaffold import _frontmatter_replace
        for number in (1, 2):
            path = self.root / f"06-decisions/d-{number:03d}-choice.md"
            text = native.rendered_note("Canonical reference", "visibleword", kind="decision",
                                       producer={"whykit_impact": native.impact_declaration(None, [])})
            if number == 1:
                native.put(path, _frontmatter_replace(text, "decision_id", "D-001"))
                before = query_vault(self.root, allowed_sensitivities=ALL)
            else:
                native.put(path, _frontmatter_replace(text, "decision_id", "D-001"))
        self.assertEqual(query_vault(self.root, allowed_sensitivities=ALL), before)

    def test_invalid_C_path_cannot_poison_the_native_transitive_chain(self):
        root = self.root / "native"
        oracle = json.loads((FIXTURE / "oracle-v1.json").read_text(encoding="utf-8"))
        native.make_base(root, oracle, "A", native.NativeWriter())
        from whykit.sensitivity import classified_evidence
        def visible():
            full = VaultIndex.load(root)
            active, retired, occurrences, _ = classified_evidence(full)
            view = full.subset(lambda _note: True)
            view.derived["evidence_register"] = (active, retired, occurrences)
            return visible_knowledge_index(view, set(active))
        before = {note.path.relative_to(root).as_posix() for note in visible().notes}
        original = root / "00-context/claims/c-001.md"
        native.put(root / "00-context/claims/c-999.md", original.read_bytes())
        after = {note.path.relative_to(root).as_posix() for note in visible().notes}
        self.assertEqual(after, before)

    def test_missing_dependency_is_checked_before_full_card_reads_and_capture_limits(self):
        import whykit.compiled_context as compiler
        import whykit.knowledge_v2 as knowledge
        denied = self.card(targets=["missing.md"], body=CANARY * 2000)
        grant = self.view()
        baseline = tree(self.root)
        for module, run in ((compiler, lambda: compile_context(self.root, query="visibleword", today=DAY,
                                                              allowed_sensitivities=ALL, vault=grant)),
                            (knowledge, lambda: project_knowledge_v2(self.root, allowed_sensitivities=ALL, vault=grant))):
            original = module._read
            def guarded(path, *args, original=original, **kwargs):
                self.assertNotEqual(path, denied, "denied card body was read")
                return original(path, *args, **kwargs)
            with self.subTest(module=module.__name__), patch.object(module, "_read", side_effect=guarded):
                self.assertNotIn(CANARY, json.dumps(run()))
        self.assertEqual(tree(self.root), baseline)

    def test_prepared_capture_A_is_stable_after_disk_B_with_no_reopen_or_persistent_cache(self):
        import whykit.hybrid_retrieval as hybrid
        from whykit.parse_cache import persistent
        path = self.card(targets=["Home.md"], body="visibleword capturedA")
        grant = self.view()
        hashes = {note.path: "0" * 64 for note in grant.notes}
        def rank():
            return hybrid.rank_prepared(grant, "visibleword", rows=({}, {}, []), metadata={}, document_hashes=hashes,
                                        valid_on=DAY, allowed_sensitivities=ALL, decision_role="reference")
        before = rank()
        native.put(path, native.rendered_note("Changed B", "diskB privatepayload", producer={
            "whykit_impact": native.impact_declaration("task", [{"relation": "requires", "target": "document:missing.md", "pin": None}])}))
        on_disk = tree(self.root)
        with persistent(True), patch.object(VaultIndex, "load", side_effect=AssertionError("recapture")), \
                patch.object(Path, "open", side_effect=AssertionError("source reopen")):
            self.assertEqual(rank(), before)
        self.assertEqual(tree(self.root), on_disk)
        refreshed = compile_context(self.root, query="visibleword", today=DAY, allowed_sensitivities=ALL, vault=grant)
        self.assertNotIn("capturedA", json.dumps(refreshed))

    def test_missing_grant_hub_does_not_change_restricted_reports_either(self):
        before = self.reports(ALL)
        self.card(targets=["missing.md"], body=("visibleword [[Home]] " + CANARY + " ") * 1000)
        self.assertEqual(self.reports(ALL), before)

    def test_hybrid_policy_eligibility_precedes_typed_admission_caps(self):
        import whykit.hybrid_retrieval as hybrid
        from whykit.scaffold import _frontmatter_replace
        path = self.root / "06-decisions/d-001-future.md"
        text = native.rendered_note("Future reference", "visibleword", kind="decision", producer={
            "whykit_impact": native.impact_declaration(None, [])})
        text = _frontmatter_replace(text, "decision_id", "D-001")
        native.put(path, _frontmatter_replace(text, "valid_from", "2099-01-01"))
        holder = self.card()
        grant = self.view()
        grant.note_for(holder).front["provenance"]["whykit_impact"] = native.impact_declaration("task", [
            {"relation": "requires", "target": "decision:D-001", "pin": None}])
        # Valid time is not itself permission: the pure reference view retains both.
        self.assertIsNotNone(visible_knowledge_index(grant, set()).note_for(holder))
        with patch.object(hybrid, "MAX_CONSTRUCTION_NODES", 1):
            result = hybrid.rank_prepared(grant, "visibleword", rows=({}, {}, []), metadata={},
                                          document_hashes={note.path: "0" * 64 for note in grant.notes},
                                          valid_on=DAY, decision_role="reference", allowed_sensitivities=ALL)
        self.assertEqual(result["status"], "ranked")
        self.assertEqual([row["path"] for row in result["results"]], ["Home.md"])
