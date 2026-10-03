"""Explorer index fields that power the timeline and evidence freshness views."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _jsonschema import unsupported_keywords, validate  # noqa: E402
from whykit.explorer_index import build_explorer_index  # noqa: E402

NORTHLINE = ROOT / "examples" / "northline"
SCHEMA = ROOT / "schemas" / "explorer-index.schema.json"


class ExplorerIndexPolicyTests(unittest.TestCase):
    def _copy(self, tmp: str) -> Path:
        vault = Path(tmp) / "vault"
        shutil.copytree(NORTHLINE, vault)
        return vault

    def test_policy_carries_access_age_thresholds_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._copy(tmp)
            config = vault / "whykit.toml"
            config.write_text(
                config.read_text(encoding="utf-8") + '\n[evidence_access_age_days]\nanalytics = 30\n"vendor doc" = 180\n',
                encoding="utf-8",
            )
            policy = build_explorer_index(vault)["policy"]
        self.assertEqual(policy["evidenceAccessAgeDays"], {"analytics": 30, "vendor doc": 180})
        self.assertEqual(policy["decisionReviewDays"], 90)
        self.assertEqual(policy["statusDueDays"], 30)

    def test_policy_defaults_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._copy(tmp)
            (vault / "whykit.toml").unlink()
            policy = build_explorer_index(vault)["policy"]
        self.assertEqual(policy, {"evidenceAccessAgeDays": {}, "decisionReviewDays": 90, "statusDueDays": 30})

    def test_malformed_config_yields_no_thresholds_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._copy(tmp)
            (vault / "whykit.toml").write_text("format_version = 1\n[evidence_access_age_days]\nanalytics = -3\n", encoding="utf-8")
            policy = build_explorer_index(vault)["policy"]
        self.assertEqual(policy["evidenceAccessAgeDays"], {})
        self.assertIsNone(policy["decisionReviewDays"])

    def test_citations_cover_front_matter_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._copy(tmp)
            note = vault / "07-research" / "citation-probe.md"
            note.write_text(
                "---\ntitle: Citation probe\ntype: research\nstatus: draft\nowner: Research\n"
                "created: 2026-09-01\nlast_updated: 2026-09-01\nsensitivity: internal\n"
                "source_ids: [E-001]\n---\n\n# Citation probe\n\nThe body cites [E-003] and E-003 again, "
                "and E-001 once more. [[Home]]\n",
                encoding="utf-8",
            )
            docs = {d["id"]: d for d in build_explorer_index(vault)["docs"]}
        self.assertEqual(docs["07-research/citation-probe"]["citations"], ["E-001", "E-003"])
        self.assertEqual(docs["07-research/citation-probe"]["sourceIds"], ["E-001"])
        # The register lists IDs; it does not cite them.
        self.assertEqual(docs["00-context/evidence-register"]["citations"], [])

    def test_citations_follow_the_graph_rule_and_skip_code(self) -> None:
        from whykit.graph import build_graph

        with tempfile.TemporaryDirectory() as tmp:
            vault = self._copy(tmp)
            note = vault / "07-research" / "citation-probe.md"
            note.write_text(
                "---\ntitle: Citation probe\ntype: research\nstatus: draft\nowner: Research\n"
                "created: 2026-09-01\nlast_updated: 2026-09-01\nsensitivity: internal\n"
                "source_ids: []\n---\n\n# Citation probe\n\nCites E-001. Syntax example: `E-003`.\n\n"
                "```\nsource_ids: [E-002]\n```\n\n[[Home]]\n",
                encoding="utf-8",
            )
            docs = {d["id"]: d for d in build_explorer_index(vault)["docs"]}
            graph = build_graph(vault)
        self.assertEqual(docs["07-research/citation-probe"]["citations"], ["E-001"])
        for doc_id, doc in docs.items():
            graph_cited = sorted(
                edge["to"].split(":", 1)[1] for edge in graph["edges"]
                if edge["type"] == "evidence" and edge["from"] == doc_id
            )
            with self.subTest(doc=doc_id):
                self.assertEqual(doc["citations"], graph_cited)

    def test_index_still_matches_its_schema(self) -> None:
        import json

        payload = build_explorer_index(NORTHLINE)
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(unsupported_keywords(schema), [])
        self.assertEqual(validate(payload, schema), [])
        self.assertIn("policy", payload)
        self.assertTrue(all(isinstance(d["citations"], list) for d in payload["docs"]))


if __name__ == "__main__":
    unittest.main()
