from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from _claims import TODAY, approved_claim_vault
from _jsonschema import validate
from whykit.explorer_index import build_explorer_index
from whykit.scaffold import _frontmatter_replace


class ClaimExplorerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.cid, self.cp, self.did, self.dp = approved_claim_vault(self.root)

    def test_export_has_explicit_computed_state_and_both_relations(self):
        payload = build_explorer_index(self.root, today=TODAY)
        self.assertEqual(payload["contract_version"], 2)
        claim = next(d for d in payload["docs"] if d.get("claimId") == self.cid)
        self.assertEqual(claim["verificationStatus"], "disputed")
        self.assertEqual(
            {r["relation"] for r in claim["claimRelations"]},
            {"supports", "contradicts"},
        )
        self.assertFalse(claim["historyReconstructed"])
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas/explorer-index-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(validate(payload, schema), [])
        private = json.dumps(build_explorer_index(self.root, today=TODAY, private=True))
        self.assertNotIn("claim-receipt/v1:", private)
        self.assertNotIn("raw_inputs", private)

    def test_public_export_has_no_hidden_claim_artifacts(self):
        hashes = [
            p.stem for p in (self.root / "00-context/claim-snapshots").glob("*.txt")
        ]
        self.cp.write_text(
            _frontmatter_replace(
                self.cp.read_text(encoding="utf-8"), "sensitivity", "restricted"
            ),
            encoding="utf-8",
        )
        payload = json.dumps(build_explorer_index(self.root, today=TODAY))
        for sentinel in [
            self.cp.name,
            self.dp.name,
            "Cached decisions are readable offline.",
            *hashes,
            "claim-receipt/v1:",
        ]:
            self.assertNotIn(sentinel, payload)
