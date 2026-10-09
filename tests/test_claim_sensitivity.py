from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _claims import claim_vault, read_view
from whykit import sensitivity, contract
from whykit.lint import load_note
from whykit.vault_index import VaultIndex
from whykit.explorer_index import build_explorer_index
from _jsonschema import validate
from _vaults import fresh_vault


def public_note(path: Path, body: str, *, claim_ids: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: guide\ntitle: Example guide\nstatus: draft\nowner: Ada Example\n"
        "created: 2026-10-09\nlast_updated: 2026-10-09\nsource_of_truth: false\n"
        "sensitivity: public\n" + claim_ids + "---\n\n" + body,
        encoding="utf-8",
    )


class ClaimSensitivityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.path = claim_vault(self.root)

    def api(self, module, name):
        method = getattr(module, name, None)
        self.assertTrue(callable(method), f"Claim privacy requires {name}")
        return method

    def levels(self):
        return self.api(sensitivity, "claim_dependency_levels")(
            VaultIndex.load(self.root), read_view(self.root)
        )

    def test_hidden_dependency_withholds_whole_document(self):
        dependent = self.root / "notes/public-guide.md"
        public_note(
            dependent,
            "This guide uses the assessment.",
            claim_ids="claim_ids: [C-001]\n",
        )
        view = read_view(self.root)
        view["evidence"]["E-002"]["sensitivity"] = "restricted"
        index, projected = self.api(sensitivity, "visible_claim_view")(
            VaultIndex.load(self.root), view, ceiling="public"
        )
        self.assertIsNone(index.note_for(self.path))
        self.assertIsNone(index.note_for(dependent))
        self.assertNotIn("C-001", projected["records"])
        self.assertNotIn("raw_inputs", projected)
        self.assertNotIn("C-001", json.dumps(projected, default=str))

    def test_shared_snapshot_floor(self):
        private = self.path.with_name("c-002-private.md")
        private.write_text(
            self.path.read_text(encoding="utf-8")
            .replace("C-001", "C-002")
            .replace("sensitivity: public", "sensitivity: restricted"),
            encoding="utf-8",
        )
        view = read_view(self.root)
        levels = self.levels()
        self.assertEqual(levels[self.path], 3)
        for row in view["records"]["C-001"]["relations"]:
            self.assertEqual(levels[self.root / row["snapshot"]], 3)
        _, projected = self.api(sensitivity, "visible_claim_view")(
            VaultIndex.load(self.root), view, ceiling="public"
        )
        serialized = json.dumps(projected, default=str)
        for row in view["records"]["C-001"]["relations"]:
            self.assertNotIn(row["source_snapshot_hash"], serialized)

    def test_unknown_and_cyclic_dependencies_fail_closed(self):
        a, b = self.root / "notes/a.md", self.root / "notes/b.md"
        public_note(a, "[[b]]\n", claim_ids="claim_ids: [C-999]\n")
        public_note(b, "[[a]]\n")
        levels = self.levels()
        self.assertEqual(levels[a], sensitivity.UNREADABLE_LEVEL)
        self.assertEqual(levels[b], sensitivity.UNREADABLE_LEVEL)

    def test_plain_and_code_markers_inherit_private_floor(self):
        self.path.write_text(
            self.path.read_text(encoding="utf-8").replace(
                "sensitivity: public", "sensitivity: confidential"
            ),
            encoding="utf-8",
        )
        dependent = self.root / "notes/plain.md"
        public_note(dependent, "```text\nC-001\n```\n")
        self.assertEqual(self.levels()[dependent], 2)

    def test_public_claims_remain_public(self):
        self.assertEqual(self.levels()[self.path], 0)
        index, projected = self.api(sensitivity, "visible_claim_view")(
            VaultIndex.load(self.root), read_view(self.root), ceiling="public"
        )
        self.assertIsNotNone(index.note_for(self.path))
        self.assertIn("C-001", projected["records"])

    def test_version_specific_schema_selection(self):
        resolve = self.api(contract, "output_schema_name")
        self.assertEqual(resolve("graph"), "graph.schema.json")
        self.assertEqual(resolve("graph", contract_version=2), "graph-v2.schema.json")
        with self.assertRaises(ValueError):
            resolve("graph", contract_version=3)
        with self.assertRaises(ValueError):
            resolve("evidence list", contract_version=2)

    def test_claim_graph_schema_preserves_legacy_edge_contract(self):
        schemas = Path(__file__).resolve().parents[1] / "schemas"
        payload = {
            "contract_version": 2,
            "nodes": [],
            "edges": [
                {
                    "from": "claim",
                    "to": "evidence:E-001",
                    "type": "contradicts",
                    "source_snapshot_hash": "a" * 64,
                    "fragment": "lines:1-1",
                }
            ],
            "unresolved": [],
            "stats": {},
        }
        try:
            new = json.loads(
                (schemas / "graph-v2.schema.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            self.fail("v2 claims graph must have a published output contract")
        self.assertEqual(validate(payload, new), [])
        old = json.loads((schemas / "graph.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(validate({**payload, "contract_version": 1}, old))

    def test_snapshot_marker_without_claim_ids_is_withheld(self):
        record = read_view(self.root)["records"]["C-001"]
        snapshot = record["relations"][0]["snapshot"]
        self.path.write_text(
            self.path.read_text(encoding="utf-8").replace(
                "sensitivity: public", "sensitivity: restricted"
            ),
            encoding="utf-8",
        )
        dependent = self.root / "notes/snapshot-reference.md"
        public_note(dependent, f"Snapshot: {snapshot}\n")
        self.assertEqual(self.levels()[dependent], 3)

    def test_malformed_claim_never_lowers_classification(self):
        self.path.write_text(
            self.path.read_text(encoding="utf-8").replace("scope:", "Scope:"),
            encoding="utf-8",
        )
        self.assertTrue(load_note(self.path).has_front)
        self.assertEqual(self.levels()[self.path], sensitivity.UNREADABLE_LEVEL)

    def test_evidence_location_cannot_expose_shared_private_snapshot(self):
        self.path.write_text(
            self.path.read_text(encoding="utf-8").replace(
                "sensitivity: public", "sensitivity: restricted"
            ),
            encoding="utf-8",
        )
        view = read_view(self.root)
        row = view["records"]["C-001"]["relations"][0]
        view["evidence"]["E-001"]["location"] = row["snapshot"]
        _, projected = self.api(sensitivity, "visible_claim_view")(
            VaultIndex.load(self.root), view, ceiling="public"
        )
        self.assertNotIn("E-001", projected["evidence"])
        self.assertNotIn(
            row["source_snapshot_hash"], json.dumps(projected, default=str)
        )

    def test_public_explorer_withholds_typed_claim_dependency(self):
        dependent = self.root / "notes/public-guide.md"
        public_note(
            dependent,
            "Guide content must be withheld.",
            claim_ids="claim_ids: [C-001]\n",
        )
        register = self.root / "00-context/evidence-register.md"
        lines = register.read_text(encoding="utf-8").splitlines()
        register.write_text(
            "\n".join(
                line.replace("| public |", "| restricted |")
                if line.startswith("| E-002 |")
                else line
                for line in lines
            )
            + "\n",
            encoding="utf-8",
        )
        result = build_explorer_index(self.root)
        self.assertFalse(
            any(doc["id"] == "notes/public-guide" for doc in result["docs"])
        )
        self.assertNotIn("Guide content must be withheld.", json.dumps(result))

    def test_legacy_invalid_config_keeps_export_diagnostics(self):
        root = (Path(self.root.parent) / "legacy").resolve()
        fresh_vault(root, "--minimal")
        (root / "whykit.toml").write_text("format_version = 99\n", encoding="utf-8")
        try:
            report = build_explorer_index(root)
        except ValueError:
            self.fail("Legacy raw exporter must preserve invalid-config diagnostics")
        self.assertEqual(report["policy"]["evidenceAccessAgeDays"], {})
