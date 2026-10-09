"""New acceptance needs a fresh receipt for the content in the checked tree."""
from __future__ import annotations

import datetime as dt
import contextlib
import io
import json
import shutil
import unittest

from test_content_quality import DAY, FILLED, ROOT
from test_history_gate import Repo
from whykit.immutability import _initial_approvals
from whykit.review import approve_decision, record_review
from whykit.scaffold import _frontmatter_replace, _update_decision_log_status_text, create_decision


class ApprovalHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo("kb")
        self.addCleanup(self.repo.cleanup)
        shutil.copytree(ROOT / "examples/tiny", self.repo.vault, dirs_exist_ok=True)
        self.did, self.path = create_decision(self.repo.vault, "Review before accepting", owner="Ada Example", source_ids=["E-001"], today=DAY)
        text = self.path.read_text(encoding="utf-8")
        for before, after in FILLED.items():
            self.assertIn(before, text)
            text = text.replace(before, after)
        self.path.write_text(text, encoding="utf-8")
        self.base = self.repo.commit("Draft ready for review")
        self.relative = self.path.relative_to(self.repo.vault).as_posix()

    def approve(self) -> dict:
        plan = approve_decision(self.repo.vault, self.did, reviewer="Ada Example", today=DAY)
        return approve_decision(self.repo.vault, self.did, reviewer="Ada Example", today=DAY,
                                write=True, expected_sha256=plan["expected_sha256"])

    def manually_accept(self) -> None:
        text = _frontmatter_replace(self.path.read_text(encoding="utf-8"), "status", "approved")
        self.path.write_text(_frontmatter_replace(text, "review_by", "2027-01-01"), encoding="utf-8")
        log = self.repo.vault / "06-decisions/decision-log.md"
        log.write_text(_update_decision_log_status_text(log.read_text(encoding="utf-8"), self.did, "accepted", DAY), encoding="utf-8")

    def test_metadata_only_acceptance_is_blocked(self) -> None:
        self.manually_accept()
        self.repo.commit("Unreviewed acceptance")
        self.assertIn(("M", self.relative), self.repo.blocked(self.base))

    def test_missing_approval_and_review_have_typed_reports_and_annotations(self) -> None:
        from whykit.check import gate_annotations, run_check
        from whykit.immutability import main
        from _jsonschema import validate

        self.manually_accept()
        self.repo.commit("Missing approval event")
        report = run_check(self.repo.vault, profile_name="ci", base=self.base, today=DAY)
        blocked = next(item for item in report["checks"] if item["name"] == "history")["blocked"]
        self.assertEqual(blocked[0]["reason"], "approval_without_event")
        self.assertIn("approval_without_event", "\n".join(gate_annotations(report, self.repo.vault)))
        schema = json.loads((ROOT / "schemas/check-report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--base", self.base, "--root", str(self.repo.vault), "--json"]), 1)
        history = json.loads(out.getvalue())
        self.assertEqual(history["blocked"][0]["reason"], "approval_without_event")
        schema = json.loads((ROOT / "schemas/history-report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(history, schema), [])

        # Existing acceptance is grandfathered; extending its review needs a new event.
        base = self.repo.commit("Existing accepted baseline")
        self.path.write_text(_frontmatter_replace(self.path.read_text(encoding="utf-8"), "review_by", "2030-01-01"), encoding="utf-8")
        self.repo.commit("Missing review event")
        report = run_check(self.repo.vault, profile_name="ci", base=base, today=DAY)
        blocked = next(item for item in report["checks"] if item["name"] == "history")["blocked"]
        self.assertEqual(blocked[0]["reason"], "review_without_event")
        self.assertIn("review_without_event", "\n".join(gate_annotations(report, self.repo.vault)))

    def test_real_approval_and_followup_review_pass(self) -> None:
        self.approve()
        record_review(self.repo.vault, self.did, reviewer="Ada Example", outcome="confirmed",
                      today=DAY + dt.timedelta(days=1), next_review="2027-01-01")
        self.repo.commit("Approved then reviewed")
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_reasoning_or_deadline_changed_after_approval_is_blocked(self) -> None:
        self.approve()
        original = self.path.read_text(encoding="utf-8")
        for changed in (original + "\nNew unreviewed reasoning.\n", _frontmatter_replace(original, "review_by", "2030-01-01")):
            with self.subTest(change=changed[-40:]):
                self.path.write_text(changed, encoding="utf-8")
                self.repo.commit("Changed after approval")
                self.assertIn(("M", self.relative), self.repo.blocked(self.base))
                self.path.write_text(original, encoding="utf-8")

    def test_wrong_target_hash_date_reviewer_and_replayed_row_are_blocked(self) -> None:
        result = self.approve()
        log = self.repo.vault / "00-context/review-log.md"
        original = log.read_text(encoding="utf-8")
        rows = original.splitlines()
        event = next(line for line in rows if f"record-sha256:{result['record_sha256']}" in line)
        changes = (event.replace(self.relative.removesuffix(".md"), "06-decisions/not-this-record"),
                   event.replace(result["record_sha256"], "0" * 64),
                   event.replace(DAY.isoformat(), "2026-01-01", 1),
                   event.replace("Ada Example", "TODO"),
                   event + "\n" + event)
        for changed in changes:
            with self.subTest(row=changed[:80]):
                log.write_text(original.replace(event, changed), encoding="utf-8")
                self.repo.commit("Invalid receipt")
                self.assertIn(("M", self.relative), self.repo.blocked(self.base))
                log.write_text(original, encoding="utf-8")

    def test_old_approval_event_cannot_authorize_a_new_acceptance(self) -> None:
        self.approve()
        # Model a pre-existing forged event attached to an unaccepted record.
        text = _frontmatter_replace(self.path.read_text(encoding="utf-8"), "status", "draft")
        self.path.write_text(text, encoding="utf-8")
        base = self.repo.commit("Old event attached to draft")
        self.manually_accept()
        self.repo.commit("Replay old event")
        self.assertIn(("M", self.relative), self.repo.blocked(base))
        log = self.repo.vault / "00-context/review-log.md"
        text = log.read_text(encoding="utf-8")
        old_event = next(line for line in text.splitlines() if "record-sha256:" in line)
        log.write_text(text.replace(old_event, old_event + "\n" + old_event), encoding="utf-8")
        self.repo.commit("Append a copy of the old receipt")
        self.assertIn(("M", self.relative), self.repo.blocked(base))

    def test_worked_example_has_a_matching_receipt_and_can_start_a_repository(self) -> None:
        repo = Repo("kb")
        self.addCleanup(repo.cleanup)
        shutil.copytree(ROOT / "examples/approval", repo.vault, dirs_exist_ok=True)
        repo.git("add", "-A")
        self.assertEqual(_initial_approvals(str(repo.vault)), [])

    def test_added_accepted_record_needs_a_receipt(self) -> None:
        self.repo.git("rm", "-q", "--", str(self.path.relative_to(self.repo.top)))
        base = self.repo.commit("No new decision yet")
        self.did, self.path = create_decision(self.repo.vault, "Fresh acceptance", owner="Ada Example", source_ids=["E-001"], today=DAY)
        self.relative = self.path.relative_to(self.repo.vault).as_posix()
        self.manually_accept()
        self.repo.commit("Added unreviewed accepted record")
        self.assertIn(("A", self.relative), self.repo.blocked(base))

    def test_staged_gate_uses_only_the_staged_receipt(self) -> None:
        self.approve()
        self.repo.git("add", "--", str(self.path.relative_to(self.repo.top)), "kb/06-decisions/decision-log.md")
        from whykit.immutability import changed_records

        self.assertIn(("M", self.relative), changed_records(self.base, "HEAD", str(self.repo.vault), staged=True))
        self.repo.git("add", "--", "kb/00-context/review-log.md")
        self.assertEqual(changed_records(self.base, "HEAD", str(self.repo.vault), staged=True), [])

    def test_first_commit_cannot_bootstrap_unreviewed_acceptance(self) -> None:
        repo = Repo("kb")
        self.addCleanup(repo.cleanup)
        record = self.path.read_text(encoding="utf-8")
        repo.write(self.relative, _frontmatter_replace(record, "status", "approved"))
        repo.git("add", "-A")
        self.assertIn(("A", self.relative), _initial_approvals(str(repo.vault)))


if __name__ == "__main__":
    unittest.main()
