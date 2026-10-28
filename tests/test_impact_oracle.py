"""Native synthetic functional truth and scope, before any impact engine exists."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whykit import artifact_store
from whykit.claim_ledger import read_claim
from whykit.claim_provenance import verify_anchor
from whykit.compiled_context import _capture, compile_context
from whykit.config import load_config
from whykit.context_applicability import context_metadata
from whykit.immutability import _approval_event_matches, approval_record_hash
from whykit.knowledge_v2 import project_knowledge_v2
from whykit.lint import load_note
from whykit.mcp_server import VaultTools
from whykit.outcome_ledger import read_outcome
from whykit.parse_cache import persistent
from whykit.sensitivity import classified_evidence
from whykit.vault_index import NoteCache, VaultIndex, reuse_notes

FIXTURE = Path(__file__).parent / "fixtures/impact"
spec = importlib.util.spec_from_file_location("impact_native_fixture", FIXTURE / "generate.py")
assert spec and spec.loader
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def metadata(root: Path, grant=None):
    view, rows, _, hashes, states, reviews = _capture(root, native.LABELS, grant, native.AS_OF)
    values = context_metadata(view, reviews[0], reviews[1], reviews[2], rows, states,
                              load_config(root)[0], native.AS_OF, hashes)
    return view, values


class NativeImpactOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="whykit-impact-oracle-")
        cls.addClassCleanup(temporary.cleanup)
        cls.parent = Path(temporary.name).resolve()
        cls.root = cls.parent / "first"
        cls.manifest = native.generate(cls.root)
        cls.bindings = json.loads((cls.root / "native-bindings.json").read_text(encoding="utf-8"))
        cls.oracle = json.loads((FIXTURE / "oracle-v1.json").read_text(encoding="utf-8"))
        cls.validator = native.validator()
        cls.verification = native.verify_corpus(cls.root, cls.manifest)

    def case_root(self, name):
        return self.root / self.manifest["cases"][name]["vault"]

    def test_native_bytes_receipts_and_functional_denominators(self):
        self.assertEqual(hashlib.sha256((FIXTURE / "oracle-v1.json").read_bytes()).hexdigest(),
                         "3de1166a328aa76c8812256802f58a90b5f3da37a8098717e7fa2199de222a53")
        expected = json.loads((FIXTURE / "corpus-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(self.manifest, expected)
        self.assertEqual(self.bindings, json.loads((FIXTURE / "native-bindings.json").read_text(encoding="utf-8")))
        checks = self.verification["native_checks"]
        self.assertEqual(len(checks), 18)
        self.assertEqual(sum(row["matched_decision_receipts"] for row in checks), 76)
        result = self.verification["functional_oracle"]
        self.assertEqual((result["core_judgments"], result["core_affected"], result["core_known_unaffected"]), (72, 8, 64))
        self.assertEqual((result["supplemental_judgments"], result["supplemental_affected"], result["supplemental_known_unaffected"]), (36, 8, 28))
        self.assertTrue(result["native_artifact_bytes_checked"])
        self.assertFalse(result["ranker_evaluated"])
        for row in result["rows"]:
            if row["affected"]:
                self.assertNotEqual(row["after_output_before_repair"], row["required_after_output"])
                self.assertEqual(row["minimal_repair"]["output"], row["required_after_output"])

    def test_second_absolute_root_is_byte_exact_and_has_no_writer_artifacts(self):
        other = self.parent / "second"
        second = native.generate(other)
        self.assertEqual(second, self.manifest)
        self.assertEqual(tree(other), tree(self.root))
        self.assertFalse(any(p.name == ".whykit" for p in self.root.rglob("*")))
        self.assertEqual(sum(p.is_file() for p in self.root.rglob("*")), self.manifest["physical_file_count"])

    def test_changed_source_and_Z_keep_approved_reference_chain_after_premise_loss(self):
        for name in ("retry.E.functional", "retry.E.other-version"):
            root = self.case_root(name)
            view, values = metadata(root)
            path = next(p for p in (root / "06-decisions").glob("d-001-*.md"))
            self.assertIsNotNone(view.note_for(path))
            self.assertEqual(load_note(path).front["status"], "approved")
            self.assertFalse(values[path]["applicability"]["premise"])
            self.assertIn("claim_source_changed", values[path]["applicability"]["reasons"])
            self.assertEqual(sum(item["decision_id"] is not None for item in values.values()), 4)
        rows = self.verification["functional_oracle"]["rows"]
        self.assertFalse(any(row["affected"] for row in rows if row["event"] == "retry.E.other-version"))
        self.assertEqual(sum(row["affected"] for row in rows if row["event"] == "retry.E.functional"), 4)

    def test_whole_hash_inert_field_and_relocation_do_not_define_truth(self):
        for workflow in self.oracle["workflows"]:
            a = workflow["sources"]["A"]["utf8"].encode()
            inert = workflow["sources"]["A2"]["utf8"].encode()
            relocated = workflow["sources"]["B_relocated"]["utf8"].encode()
            self.assertNotEqual(hashlib.sha256(a).digest(), hashlib.sha256(inert).digest())
            for fragment in workflow["fragments"]:
                start, end = fragment["start_byte"], fragment["end_byte"]
                self.assertEqual(a[start:end], inert[start:end])
            root = self.root / self.manifest["base_snapshots"]["A"]["vault"]
            claim = read_claim(root, workflow["claim_ids"][0], allowed_sensitivities=native.LABELS)
            link = claim["payload"]["links"][0]
            self.assertEqual(verify_anchor(link, a), "matched")
            self.assertEqual(verify_anchor(link, relocated), "version_mismatch")
            self.assertEqual(self.manifest["uncertainty_controls"][workflow["id"] + "-relocated"]["expected_selector_state"], "unknown")
            scalar = self.manifest["uncertainty_controls"][workflow["id"] + "-value-only"]
            control = self.root / scalar["vault"]
            ambiguous = read_claim(control, scalar["selector_claim_id"], allowed_sensitivities=native.LABELS)
            self.assertNotIn(":", ambiguous["payload"]["links"][0]["location"]["quote"])
            self.assertFalse(scalar["binary_denominator"])

    def test_real_D_successor_and_lifecycle_namespaces_and_historical_O(self):
        for event in self.oracle["supplemental_events"]:
            transition = self.bindings["decision_transitions"][event["id"]]
            before = (self.root / transition["before"]["path"]).read_bytes()
            after = (self.root / transition["after"]["path"]).read_bytes()
            self.assertNotEqual(hashlib.sha256(before).digest(), hashlib.sha256(after).digest())
            if event["id"].endswith("lifecycle-only"):
                self.assertEqual(approval_record_hash(before.decode()), approval_record_hash(after.decode()))
            else:
                self.assertNotEqual(approval_record_hash(before.decode()), approval_record_hash(after.decode()))
                old_after = (self.root / transition["old_after"]["path"]).read_text(encoding="utf-8")
                self.assertEqual(approval_record_hash(before.decode()), approval_record_hash(old_after))
            for consumer_id, item in self.bindings["consumers"][event["id"]].items():
                if consumer_id.endswith("historical-outcome"):
                    self.assertEqual((self.root / item["note"]["path"]).read_bytes(),
                                     (self.root / item["note_before"]["path"]).read_bytes())
        root = self.case_root("retry.D.approved-supersession")
        historical = read_outcome(root, "O-003", allowed_sensitivities=native.LABELS)
        self.assertIsNone(historical["payload"]["observed"]["claim"])
        planned = read_outcome(root, "O-001", allowed_sensitivities=native.LABELS)
        self.assertIsNotNone(planned["payload"]["baseline"]["claim"])
        self.assertNotIn("impact_dependencies", planned["record"])
        self.assertNotIn("provenance", planned["record"])

    def test_native_config_and_approval_tamper_fail_without_oracle_relabel(self):
        bindings = copy.deepcopy(self.bindings)
        entry = bindings["consumers"]["retry.E.functional"]["retry.specification.primary"]["functional_config"]
        path = self.root / entry["path"]
        before = path.read_bytes()
        try:
            value = json.loads(before)
            value["value"] = 4
            path.write_bytes(native.json_text(value).encode())
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.validator.evaluate(self.oracle, bindings, self.root)
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "invalid fixture baseline"):
                self.validator.evaluate(self.oracle, bindings, self.root)
        finally:
            path.write_bytes(before)
        root = self.case_root("retry.E.functional")
        path = next((root / "06-decisions").glob("d-001-*.md"))
        log = (root / "00-context/review-log.md").read_text(encoding="utf-8")
        self.assertTrue(_approval_event_matches(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix(), None, log))
        self.assertFalse(_approval_event_matches(path.read_text(encoding="utf-8") + "\nUnreviewed later reasoning.\n", path.relative_to(root).as_posix(), None, log))

    def test_public_actual_labels_and_sparse_native_grants_are_read_only(self):
        root = self.root / self.manifest["base_snapshots"]["A"]["vault"]
        before = tree(root)
        with persistent(True):
            self.assertIsNone(read_claim(root, "C-001", allowed_sensitivities={"public"}))
            self.assertIsNone(read_outcome(root, "O-001", allowed_sensitivities={"public"}))
            public = project_knowledge_v2(root, allowed_sensitivities={"public"})
            public_context = compile_context(root, query="selected bounded parameter", today=native.AS_OF,
                                             allowed_sensitivities={"public"})
            public_query = VaultTools(root, max_sensitivity="public").query("selected bounded parameter")
        wire = json.dumps([public, public_context, public_query], sort_keys=True)
        self.assertNotIn("attempts", wire)
        self.assertNotIn("Synthetic retry", wire)
        self.assertNotIn(self.oracle["workflows"][0]["sources"]["A"]["source_version"], wire)
        with reuse_notes(NoteCache()):
            full = VaultIndex.load(root)
        active, retired, occurrences, _ = classified_evidence(full)
        active.pop("E-001")
        grant = full.subset(lambda note: True)
        grant.derived["evidence_register"] = (active, retired, occurrences)
        original = artifact_store.read_source_observation
        def scoped_observation(vault, evidence_id, version, **kwargs):
            self.assertNotEqual(evidence_id, "E-001")
            return original(vault, evidence_id, version, **kwargs)
        with patch("whykit.compiled_context.read_source_observation", side_effect=scoped_observation):
            view, _ = metadata(root, grant)
        self.assertIsNone(view.note_for(root / "00-context/claims/c-001.md"))
        self.assertIsNone(view.note_for(root / "00-context/outcomes/o-001.md"))
        self.assertIsNone(view.note_for(root / "00-context/outcomes/o-003.md"))  # Its required D also lost the C/E dependency grant.
        self.assertEqual(tree(root), before)
