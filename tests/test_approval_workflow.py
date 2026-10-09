"""Approval binds a reviewed snapshot and commits its ledger changes together."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import unittest

from test_content_quality import DAY, FILLED, ROOT, _TinyCopy
from _jsonschema import validate
from whykit.cli import main
from whykit.immutability import approval_record_hash
from whykit.io import vault_mutation_lock
from whykit.lint import lint, load_note
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, create_decision
from unittest.mock import patch


class ApprovalWorkflowTests(_TinyCopy):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.decision("Approve the reviewed choice", status="draft")
        self.fill(self.path, FILLED)
        self.did = load_note(self.path).front["decision_id"]

    def preview(self, **kwargs) -> dict:
        return approve_decision(self.vault, self.did, reviewer="Ada Example", today=DAY,
                                next_review="2027-01-01", **kwargs)

    def documents(self) -> dict:
        return {path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in self.vault.rglob("*") if path.is_file() and ".whykit" not in path.parts}

    def test_preview_is_complete_and_does_not_write(self) -> None:
        before = {path: path.read_bytes() for path in self.vault.rglob("*") if path.is_file()}
        plan = self.preview()
        self.assertFalse(plan["applied"])
        self.assertEqual(plan["snapshot_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(plan["reviewed_record"], self.path.read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in plan["evidence"]], ["E-001"])
        self.assertEqual(len(plan["changes"]), 3)
        self.assertEqual({path: path.read_bytes() for path in self.vault.rglob("*") if path.is_file()}, before)

    def test_apply_updates_record_ledgers_and_preserves_producer_extensions(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        block = 'provenance:\n  producer: example.import/v1\n  upstream:\n    - "artifact-a"\n    - "artifact-b"\n  custom_key: "keep: exactly"\n  human_reviewed: false\n'
        self.path.write_text(text.replace("tags: []\n", "tags: []\n" + block), encoding="utf-8")
        plan = self.preview()
        result = self.preview(write=True, expected_sha256=plan["expected_sha256"])
        self.assertTrue(result["applied"])
        note = load_note(self.path)
        self.assertEqual(note.front["status"], "approved")
        self.assertIs(note.front["provenance"]["human_reviewed"], True)
        self.assertIn('  upstream:\n    - "artifact-a"\n    - "artifact-b"\n  custom_key: "keep: exactly"\n', note.text)
        original_body = load_note(self.path, text=text).body.replace("## Status\n\nProposed", "## Status\n\nAccepted").replace("- Decided on: TBD", f"- Decided on: {DAY.isoformat()}")
        self.assertEqual(note.body, original_body)
        self.assertEqual(approval_record_hash(note.text), result["record_sha256"])
        self.assertIn(f"record-sha256:{result['record_sha256']}", (self.vault / "00-context/review-log.md").read_text(encoding="utf-8"))
        self.assertFalse([item for item in lint(self.vault, today=DAY)[1] if item.level == "error"])
        with self.assertRaisesRegex(ValueError, "draft/in_review"):
            self.preview(write=True, expected_sha256=plan["expected_sha256"])

    def test_a_previous_approval_cannot_be_replayed_after_resetting_the_record(self) -> None:
        plan = self.preview()
        self.preview(write=True, expected_sha256=plan["expected_sha256"])
        self.path.write_text(_frontmatter_replace(self.path.read_text(encoding="utf-8"), "status", "draft"), encoding="utf-8")
        before = self.documents()
        with self.assertRaisesRegex(ValueError, "already has an approval event"):
            self.preview()
        self.assertEqual(self.documents(), before)

    def test_register_change_during_preview_is_refused(self) -> None:
        from whykit import review
        register = self.vault / "00-context/evidence-register.md"
        original = review._parse_evidence_register_text

        def changed(text):
            register.write_text(register.read_text(encoding="utf-8") + "\nChanged during preview.\n", encoding="utf-8")
            return original(text)

        with patch.object(review, "_parse_evidence_register_text", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during preview"):
                self.preview()
        self.assertEqual(load_note(self.path).front["status"], "draft")

    def test_unknown_nested_producer_flags_are_not_rewritten(self) -> None:
        from whykit.review import _reviewed_provenance
        text = self.path.read_text(encoding="utf-8")
        block = 'provenance:\n  producer: example.import/v1\n  original:\n    human_reviewed: false\n  human_reviewed: false\n'
        self.path.write_text(text.replace("tags: []\n", "tags: []\n" + block), encoding="utf-8")
        changed = _reviewed_provenance(self.path.read_text(encoding="utf-8"))
        self.assertIn('  original:\n    human_reviewed: false\n  human_reviewed: true\n', changed)
        # Arbitrary nested mappings are outside WhyKit's supported YAML subset.
        before = self.documents()
        with self.assertRaises(ValueError):
            self.preview()
        self.assertEqual(self.documents(), before)

    def test_partial_approval_transaction_recovers_all_three_files(self) -> None:
        from whykit import io as transaction
        plan = self.preview()
        original = transaction.atomic_write_bytes
        writes = 0

        def interrupted(path, data, **kwargs):
            nonlocal writes
            original(path, data, **kwargs)
            if path in {self.path, self.vault / "06-decisions/decision-log.md", self.vault / "00-context/review-log.md"}:
                writes += 1
                if writes == 1:
                    raise OSError("simulated interrupted approval")

        with patch.object(transaction, "atomic_write_bytes", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted approval"):
                self.preview(write=True, expected_sha256=plan["expected_sha256"])
        with vault_mutation_lock(self.vault):
            pass
        with vault_mutation_lock(self.vault):
            pass
        self.assertEqual(load_note(self.path).front["status"], "approved")
        self.assertIn(f"record-sha256:{plan['record_sha256']}", (self.vault / "00-context/review-log.md").read_text(encoding="utf-8"))
        self.assertIn("| accepted |", (self.vault / "06-decisions/decision-log.md").read_text(encoding="utf-8"))

    def test_stale_record_registry_policy_and_log_are_refused(self) -> None:
        targets = (self.path, self.vault / "00-context/evidence-register.md",
                   self.vault / "whykit.toml", self.vault / "00-context/review-log.md")
        for path in targets:
            with self.subTest(path=path.name):
                plan = self.preview()
                old = path.read_bytes()
                path.write_bytes(old + b"\n<!-- changed after preview -->\n" if path.suffix == ".md" else old + b"\n# changed after preview\n")
                before = self.documents()
                with self.assertRaisesRegex(ValueError, "stale"):
                    self.preview(write=True, expected_sha256=plan["expected_sha256"])
                self.assertEqual(self.documents(), before)
                path.write_bytes(old)

    def test_changed_reviewer_date_or_deadline_is_refused(self) -> None:
        plan = self.preview()
        for change in ({"reviewer": "Pat Example"}, {"today": DAY + dt.timedelta(days=1)}, {"next_review": "2028-01-01"}):
            args = {"reviewer": "Ada Example", "today": DAY, "next_review": "2027-01-01", **change}
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "stale"):
                approve_decision(self.vault, self.did, write=True, expected_sha256=plan["expected_sha256"], **args)

    def test_write_requires_a_preview_hash_and_placeholders_cannot_be_approved(self) -> None:
        before = self.documents()
        for value in (None, "", "0", "G" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "expect-hash"):
                self.preview(write=True, expected_sha256=value)
        placeholder = self.decision("Unfinished choice", status="draft")
        with self.assertRaisesRegex(ValueError, "placeholder|requires content"):
            approve_decision(self.vault, load_note(placeholder).front["decision_id"], reviewer="Ada Example", today=DAY)
        self.assertEqual(self.documents()[self.path.relative_to(self.vault).as_posix()], before[self.path.relative_to(self.vault).as_posix()])

    def test_new_immutable_scaffolds_are_rejected_before_writing(self) -> None:
        before = self.documents()
        for status in ("approved", "superseded", "archived"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "review approve"):
                create_decision(self.vault, "No shortcut", status=status, owner="Ada Example", today=DAY)
        self.assertEqual(self.documents(), before)

    def test_missing_sections_evidence_and_owner_are_refused(self) -> None:
        original = self.path.read_text(encoding="utf-8")
        variants = (original.replace("## Rationale", "## Other rationale"),
                    original.replace("E-001", "E-999"),
                    _frontmatter_replace(original, "owner", "TODO"))
        for text in variants:
            with self.subTest(text=text[:50]):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.preview()
        self.path.write_text(original, encoding="utf-8")

    def test_supersession_waits_for_approval_and_changes_four_files(self) -> None:
        previous = next((self.vault / "06-decisions").glob("d-002-*.md"))
        old = previous.read_bytes()
        replacement = self.decision("Replace the accepted choice", status="draft", supersedes="D-002")
        self.fill(replacement, FILLED)
        self.assertEqual(previous.read_bytes(), old)
        did = load_note(replacement).front["decision_id"]
        plan = approve_decision(self.vault, did, reviewer="Ada Example", today=DAY)
        self.assertEqual(len(plan["changes"]), 4)
        approve_decision(self.vault, did, reviewer="Ada Example", today=DAY, write=True, expected_sha256=plan["expected_sha256"])
        self.assertEqual(load_note(previous).front["status"], "superseded")
        self.assertEqual(load_note(previous).front["superseded_by"], did)
        self.assertEqual(load_note(replacement).front["status"], "approved")

    def test_front_matter_update_does_not_swallow_an_empty_key_or_bom(self) -> None:
        source = "\ufeff---\nlast_updated:\nsource_of_truth: false\n---\nBody\n"
        result = _frontmatter_replace(source, "last_updated", "2026-09-17")
        self.assertEqual(result, "\ufeff---\nlast_updated: 2026-09-17\nsource_of_truth: false\n---\nBody\n")

    def test_cli_preview_and_apply_emit_json_and_leave_no_pending_writes(self) -> None:
        base = ["review", "approve", self.did, "--root", str(self.vault), "--reviewer", "Ada Example", "--today", DAY.isoformat(), "--json"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(base), 0)
        plan = json.loads(out.getvalue())
        schema = json.loads((ROOT / "schemas/decision-approval-result.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(plan, schema), [])
        self.assertTrue(validate({**plan, "expected_sha256": "wrong"}, schema))
        self.assertTrue(validate({key: value for key, value in plan.items() if key != "reviewed_record"}, schema))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([*base, "--write", "--expect-hash", plan["expected_sha256"]]), 0)
        applied = json.loads(out.getvalue())
        self.assertTrue(applied["applied"])
        self.assertEqual(validate(applied, schema), [])
        with vault_mutation_lock(self.vault):
            pass
        record_review(self.vault, self.did, reviewer="Ada Example", outcome="confirmed", today=DAY, next_review="2028-01-01")


if __name__ == "__main__":
    unittest.main()
