from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from _claims import TODAY, approved_claim_vault
from _jsonschema import validate
from whykit.graph import build_graph
from whykit.trace import build_trace
from whykit.impact import analyze_impact
from whykit.context import build_context
from whykit.pack import build_pack
from whykit.status import build_status
from whykit.query import query_vault
from whykit.backlinks import build_backlinks
from whykit.contract import output_schema_name


class ClaimReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.cid, self.cp, self.did, self.dp = approved_claim_vault(self.root)

    def test_conflict_matches_across_readers(self):
        trace = build_trace(self.root, today=TODAY, decision=self.did)
        self.assertEqual(trace["contract_version"], 2)
        self.assertEqual(
            trace["decisions"][0]["claims"][0]["verification_status"], "disputed"
        )
        self.assertFalse(trace["decisions"][0]["claims"][0]["history_reconstructed"])
        self.assertEqual(trace["decisions"][0]["direct_evidence"], [])
        self.assertEqual(trace["decisions"][0]["claim_gaps"], [])
        for target in (self.cid, self.did):
            context = build_context(self.root, target)
            self.assertEqual(context["claims"][0]["verification_status"], "disputed")
        query = query_vault(self.root, doc_type="claim")
        self.assertEqual(
            query["results"][0]["claim"]["verification_status"], "disputed"
        )

    def test_graph_impact_and_backlinks_keep_typed_edges(self):
        graph = build_graph(self.root)
        self.assertEqual(
            {e["type"] for e in graph["edges"]} & {"claim", "supports", "contradicts"},
            {"claim", "supports", "contradicts"},
        )
        report = analyze_impact(self.root, "E-001")
        self.assertTrue(
            any(r.get("claim_id") == self.cid for r in report["references"])
        )
        self.assertTrue(
            any(
                r.get("decision_id") == self.did and r.get("via_claim") == self.cid
                for r in report["references"]
            )
        )
        self.assertTrue(
            any(
                r["type"] == "claim"
                for r in build_backlinks(self.root, self.cid)["backlinks"]
            )
        )

    def test_pack_includes_claim_and_bounded_untrusted_fragments(self):
        pack = build_pack(self.root, targets=[self.did])
        self.assertEqual(pack["format"], "whykit.context-bundle/v2")
        claim = next(c for c in pack["contexts"] if c.get("kind") == "claim")
        self.assertEqual(
            {r["relation"] for r in claim["fragments"]}, {"supports", "contradicts"}
        )
        self.assertTrue(
            all(r["content_trust"] == "untrusted_data" for r in claim["fragments"])
        )
        bounded = build_pack(self.root, targets=[self.did], max_chars=180)
        self.assertLessEqual(
            sum(
                len(c.get("content", ""))
                + sum(len(f["content"]) for f in c.get("fragments", []))
                for c in bounded["contexts"]
            ),
            180,
        )

    def test_changed_receipt_requires_dependent_review(self):
        p = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        p.unlink()
        status = build_status(self.root, today=TODAY)
        ids = {
            r.get("claim_id") or r.get("decision_id")
            for r in status["review_queue"]
            if r["state"] == "requires_review"
        }
        self.assertEqual(ids, {self.cid, self.did})

    def test_v2_schemas_validate_actual_outputs(self):
        cases = {
            "graph": build_graph(self.root),
            "trace": build_trace(self.root, today=TODAY),
            "impact": analyze_impact(self.root, self.cid),
            "context": build_context(self.root, self.cid),
            "pack": build_pack(self.root, targets=[self.did]),
            "status": build_status(self.root, today=TODAY),
            "query": query_vault(self.root, doc_type="claim"),
            "backlinks": build_backlinks(self.root, self.cid),
        }
        for command, result in cases.items():
            with self.subTest(command=command):
                schema = json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "schemas"
                        / output_schema_name(command, contract_version=2)
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(validate(result, schema), [])
