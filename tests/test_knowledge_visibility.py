"""Native records cannot expand their caller's granted dependency closure."""
from __future__ import annotations

import shutil
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from _vaults import ROOT
from whykit.dependency_graph import deep_impact
from whykit.knowledge_visibility import canonical_decision_id, visible_knowledge_index
from whykit.outcome_ledger import create_outcome
from whykit.compiled_context import compile_context
from whykit.mcp_server import VaultTools
from whykit.vault_index import VaultIndex
from whykit.lint import load_note


class KnowledgeVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault"
        shutil.copytree(ROOT / "examples/provenance", self.root,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))

    @unittest.skipIf(os.name == "nt", "POSIX anchor semantics")
    def test_path_admission_matches_pathlib_including_double_slash_anchors(self):
        for root in (Path("/"), Path("//"), Path("/vault"), Path("//vault")):
            paths = [root, root / "guide.md", Path("/vault-sibling/guide.md"),
                     Path("//vault/guide.md"), Path("/vault/guide.md")]
            notes = [load_note(path, text="---\ntype: reference\n---\n") for path in paths]
            index = VaultIndex(root, notes, {}, {})
            index.derived["evidence_register"] = ({}, {}, [])
            expected = []
            for note in notes:
                try:
                    note.path.relative_to(root)
                except ValueError:
                    continue
                expected.append(note)
            with self.subTest(root=root):
                self.assertEqual(visible_knowledge_index(index, set()).notes, expected)

    def test_graph_withholds_a_claim_without_its_evidence_grant(self):
        grant = VaultIndex.load(self.root).subset(lambda note: note.front.get("claim_id") == "C-001")
        grant.derived["evidence_register"] = ({}, {}, [])
        report = deep_impact(self.root, "C-001", allowed_sensitivities={"restricted"}, vault=grant)
        self.assertFalse(report["exists"])

    def test_graph_keeps_explicit_empty_row_grant_even_with_raw_register(self):
        grant = VaultIndex.load(self.root)
        grant.derived["evidence_register"] = ({}, {}, [])
        report = deep_impact(self.root, "C-001", allowed_sensitivities={"public", "internal", "confidential", "restricted"}, vault=grant)
        self.assertFalse(report["exists"])

    def test_native_filter_preserves_row_grant_for_downstream_consumers(self):
        grant = VaultIndex.load(self.root)
        grant.derived["evidence_register"] = ({}, {}, [])
        filtered = visible_knowledge_index(grant, set())
        report = deep_impact(self.root, "E-004", allowed_sensitivities={"public", "internal", "confidential", "restricted"}, vault=filtered)
        self.assertFalse(report["exists"])

    def outcome(self):
        import datetime as dt
        claim = self.root / "00-context/claims/c-001.md"
        data = {"decision_id": "D-003", "metric": {"name": "Synthetic count", "unit": "items",
                "definition": "Declared count for the synthetic fixture; semantic support is unchecked."},
                "baseline": None, "target": None,
                "observed": {"value": "12345.6789", "as_of": "2026-09-17", "claim": {
                    "claim_id": "C-001", "sha256": hashlib.sha256(claim.read_bytes()).hexdigest()}},
                "implementation_refs": ["https://example.com/pull/1"]}
        args = {"owner": "Ada Example", "today": dt.date(2026, 9, 17)}
        preview = create_outcome(self.root, data, **args)
        return create_outcome(self.root, data, **args, write=True, expected_sha256=preview["expected_sha256"])

    def test_outcome_requires_canonical_decision_and_claim_grants(self):
        self.outcome()
        index = VaultIndex.load(self.root)
        full = visible_knowledge_index(index, {"E-004"})
        self.assertIn("O-001", {n.front.get("outcome_id") for n in full.notes})
        for grant in (index.subset(lambda n: n.front.get("type") != "decision"),
                      index.subset(lambda n: n.front.get("claim_id") != "C-001")):
            limited = visible_knowledge_index(grant, {"E-004"})
            self.assertNotIn("O-001", {n.front.get("outcome_id") for n in limited.notes})
        self.assertNotIn("O-001", {n.front.get("outcome_id") for n in visible_knowledge_index(index, set()).notes})

    def test_typed_paths_link_outcome_to_decision_and_measurement_claim(self):
        outcome = self.outcome()
        report = deep_impact(self.root, "D-003", allowed_sensitivities={"public", "internal", "confidential", "restricted"})
        node = "document:" + outcome["path"]
        self.assertIn(node, {item["node"]["id"] for item in report["results"]})
        self.assertTrue(any(e["from"] == node and e["type"] == "outcome_decision" for e in report["edges"]))
        report = deep_impact(self.root, "C-001", allowed_sensitivities={"public", "internal", "confidential", "restricted"})
        self.assertTrue(any(e["from"] == node and e["type"] == "measurement_claim" for e in report["edges"]))

    def test_outcome_has_one_decision_reference_without_duplicate_identity(self):
        self.outcome()
        index = VaultIndex.load(self.root)
        self.assertEqual([n.front.get("outcome_id") for n in index.notes if n.front.get("decision_id") == "D-003"], [None])
        record = next(n for n in index.notes if n.front.get("decision_id") == "D-003")
        self.assertEqual(canonical_decision_id(record, self.root), "D-003")

    def test_public_mcp_and_compiler_withhold_outcome_metric(self):
        outcome = self.outcome()
        tools = VaultTools(self.root, max_sensitivity="public")
        self.assertFalse(tools.context(outcome["path"])["exists"])
        public = compile_context(self.root, targets=["O-001"], allowed_sensitivities={"public"})
        self.assertNotIn("12345.6789", json.dumps(public))
        private = compile_context(self.root, targets=["O-001"],
                                  allowed_sensitivities={"public", "internal", "confidential", "restricted"})
        self.assertIn("12345.6789", json.dumps(private))
        self.assertIn("compiled.outcome_values_unverified", {w["code"] for w in private["warnings"]})
