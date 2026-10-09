from __future__ import annotations
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _claims import TODAY, claim_vault, read_view
from test_claim_migration import inventory
from whykit import review
from whykit.claims import evaluate_claims
from whykit.lint import load_note
from whykit.scaffold import create_claim, create_decision, _frontmatter_replace
from test_content_quality import FILLED


class ClaimApprovalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = (Path(tmp.name) / "vault").resolve()
        self.path = claim_vault(self.root)

    def approve(self, target="C-001", **kw):
        method = getattr(review, "approve_record", None)
        self.assertTrue(
            callable(method), "Claims require the real preview/apply workflow"
        )
        return method(self.root, target, reviewer="Ada Example", today=TODAY, **kw)

    def accept(self, target="C-001"):
        plan = self.approve(target)
        return self.approve(target, write=True, expected_sha256=plan["expected_sha256"])

    def decision(self):
        cid, path = create_decision(
            self.root,
            "Evaluate conflicting observations",
            owner="Ada Example",
            claim_ids=["C-001"],
            today=TODAY,
        )
        text = path.read_text(encoding="utf-8")
        for before, after in FILLED.items():
            text = text.replace(before, after)
        from whykit.placeholders import SCAFFOLD_EVIDENCE_TODO

        text = text.replace(SCAFFOLD_EVIDENCE_TODO, "- C-001")
        # Rationale cites a real direct source as well as the typed claim dependency.
        path.write_text(text, encoding="utf-8")
        return cid, path

    def test_approval_binds_every_input(self):
        for relative in (
            "whykit.toml",
            "00-context/evidence-register.md",
            self.path.relative_to(self.root).as_posix(),
            "00-context/review-log.md",
            next((self.root / "00-context/claim-snapshots").glob("*.txt"))
            .relative_to(self.root)
            .as_posix(),
        ):
            plan = self.approve()
            path = self.root / relative
            original = path.read_bytes() if path.exists() else None
            path.write_bytes((original or b"") + b"\nchanged\n")
            before = inventory(self.root)
            with self.assertRaises(ValueError):
                self.approve(write=True, expected_sha256=plan["expected_sha256"])
            self.assertEqual(before, inventory(self.root))
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_approval_and_confirmed_revalidate(self):
        self.accept()
        result = evaluate_claims(read_view(self.root), today=TODAY)["C-001"]
        self.assertEqual(result["verification_status"], "disputed")
        kwargs = dict(
            reviewer="Ada Example",
            outcome="confirmed",
            today=TODAY + dt.timedelta(days=1),
        )
        before = inventory(self.root)
        preview = review.record_review(self.root, "C-001", **kwargs)
        self.assertFalse(preview["applied"])
        self.assertEqual(before, inventory(self.root))
        review.record_review(
            self.root,
            "C-001",
            write=True,
            expected_sha256=preview["expected_sha256"],
            **kwargs,
        )
        self.assertEqual(load_note(self.path).front["last_verified"], "2026-10-10")
        self.assertEqual(len(read_view(self.root)["review_rows"]), 2)
        snapshot = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        snapshot.unlink()
        with self.assertRaises(ValueError):
            review.record_review(self.root, "C-001", **kwargs)

    def test_disputed_decision_requires_explicit_assessment(self):
        self.accept()
        did, path = self.decision()
        with self.assertRaisesRegex(ValueError, "Claim assessment"):
            self.approve(did)
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n## Claim assessment\n\n- C-001: We accept the observed conflict and will verify the two environments.\n",
            encoding="utf-8",
        )
        self.accept(did)
        self.assertIn(
            "decision-claim-receipt/v1:",
            (self.root / "00-context/review-log.md").read_text(encoding="utf-8"),
        )

    def test_unknown_blocks_decision_and_reset_blocks_claim(self):
        did, _ = self.decision()
        with self.assertRaises(ValueError):
            self.approve(did)
        self.accept()
        self.path.write_text(
            _frontmatter_replace(
                self.path.read_text(encoding="utf-8"), "status", "draft"
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.approve()

    def test_supersedes_is_atomic_and_never_rewrites_semantics(self):
        self.accept()
        cid, path = create_claim(
            self.root,
            "Revised observation",
            statement="Different environment.",
            scope="Desktop v2.2",
            valid_from="2026-10-01",
            supersedes="C-001",
            today=TODAY,
            owner="Ada Example",
        )
        table = self.path.read_text(encoding="utf-8").split("## Evidence", 1)[1]
        path.write_text(
            path.read_text(encoding="utf-8").split("## Evidence", 1)[0]
            + "## Evidence"
            + table,
            encoding="utf-8",
        )
        self.assertEqual(load_note(self.path).front["status"], "approved")
        self.accept(cid)
        self.assertEqual(load_note(self.path).front["status"], "superseded")
        self.assertEqual(load_note(self.path).front["superseded_by"], cid)

    def test_crash_recovery_claim_and_log(self):
        from whykit import io as tx

        plan = self.approve()
        original = tx.atomic_write_bytes
        failed = [False]

        def fail(path, data, **kw):
            if Path(path) == self.path and not failed[0]:
                failed[0] = True
                raise OSError("injected write failure")
            return original(path, data, **kw)

        with patch.object(tx, "atomic_write_bytes", side_effect=fail):
            with self.assertRaises(OSError):
                self.approve(write=True, expected_sha256=plan["expected_sha256"])
        with tx.vault_mutation_lock(self.root):
            pass
        self.assertEqual(load_note(self.path).front["status"], "approved")
        self.assertEqual(
            evaluate_claims(read_view(self.root), today=TODAY)["C-001"][
                "verification_status"
            ],
            "disputed",
        )

    def test_accepted_semantic_edits_cannot_be_confirmed(self):
        self.accept()
        self.path.write_text(
            _frontmatter_replace(
                self.path.read_text(encoding="utf-8"),
                "statement",
                "Edited accepted semantics",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            review.record_review(
                self.root,
                "C-001",
                reviewer="Ada Example",
                outcome="confirmed",
                today=TODAY,
            )

    def test_decision_with_only_claim_evidence_can_be_approved(self):
        self.accept()
        did, path = self.decision()
        text = path.read_text(encoding="utf-8").replace("(E-001)", "(C-001)")
        path.write_text(
            text
            + "\n## Claim assessment\n\n- C-001: Conflict is bounded to the environments listed in scope.\n",
            encoding="utf-8",
        )
        self.accept(did)

    def test_approval_payloads_match_v2_schemas(self):
        import json
        from _jsonschema import validate

        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas/decision-approval-result-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(validate(self.approve(), schema), [])

    def test_pending_transaction_prevents_read_acceptance(self):
        from whykit.io import stage_transaction, vault_mutation_lock
        self.accept()
        with vault_mutation_lock(self.root):
            stage_transaction(self.root, {self.path: self.path.read_text(encoding="utf-8")})
        with self.assertRaises(OSError):
            read_view(self.root)
