from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _claims import TODAY, claim_vault, read_view
from whykit import scaffold
from whykit.lint import load_note
from whykit.claims import evaluate_claims


class ClaimScaffoldTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        claim_vault(self.root)

    def create(self, **kwargs):
        method = getattr(scaffold, "create_claim", None)
        self.assertTrue(
            callable(method), "Claim authoring must create a real draft record"
        )
        return method(
            self.root,
            "Another observation",
            statement="Records can be read.",
            scope="Desktop v2.1",
            valid_from="2026-10-01",
            today=TODAY,
            owner="Ada Example",
            **kwargs,
        )

    def test_new_claim_is_unreviewed_draft(self):
        cid, path = self.create()
        self.assertEqual(cid, "C-002")
        front = load_note(path).front
        self.assertEqual(front["status"], "draft")
        self.assertNotIn("verification_status", front)
        self.assertNotIn("last_verified", front)
        self.assertEqual(
            evaluate_claims(read_view(self.root), today=TODAY)[cid][
                "verification_status"
            ],
            "unknown",
        )

    def test_allocation_never_reuses_historical_ids(self):
        cid, path = self.create()
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'status: "draft"', 'status: "archived"'
            ),
            encoding="utf-8",
        )
        next_id, _ = self.create()
        self.assertEqual(cid, "C-002")
        self.assertEqual(next_id, "C-003")

    def test_creation_requires_opt_in(self):
        config = self.root / "whykit.toml"
        config.write_text(
            config.read_text(encoding="utf-8").split("\n[claims]")[0], encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            self.create()

    def test_decision_can_reference_claim_without_changing_sources(self):
        try:
            did, path = scaffold.create_decision(
                self.root,
                "Consider the observed behavior",
                owner="Ada Example",
                claim_ids=["C-001"],
                today=TODAY,
            )
        except TypeError:
            self.fail("Decision scaffold must accept optional claim_ids")
        front = load_note(path).front
        self.assertEqual(front["claim_ids"], ["C-001"])
        self.assertEqual(front["source_ids"], [])
        self.assertEqual(front["decision_id"], did)
