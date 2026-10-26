"""A postponed review must be explained by new events in the same Git diff."""
from __future__ import annotations

import datetime as dt
import unittest

from test_history_gate import APPROVED, REVIEW_LOG, RepoTestCase, _replace_key
from whykit.immutability import allowed_review_log_append
from whykit.review import record_review

PATH = "06-decisions/d-001-use-git.md"
TARGET = "[[06-decisions/d-001-use-git]]"
ROW = f"| 2026-10-01 | {TARGET} | Pat Example | confirmed | 2027-01-05 | 2027-04-01 | Checked E-001 |\n"


class ReviewHistoryIntegrityTests(RepoTestCase):
    def _rescheduled(self) -> None:
        text = _replace_key(APPROVED, "review_by", "2027-04-01")
        self.repo.write(PATH, _replace_key(text, "last_updated", "2026-10-01"))

    def _log(self, row: str = ROW) -> None:
        self.repo.write("00-context/review-log.md", REVIEW_LOG.replace("\n\nTrailing", "\n" + row.rstrip("\n") + "\n\nTrailing"))

    def test_no_event_or_an_old_event_cannot_move_the_deadline(self) -> None:
        for old_event in (False, True):
            with self.subTest(old_event=old_event):
                self.repo.write(PATH, APPROVED)
                self._log(ROW if old_event else "")
                base = self.repo.commit("baseline")
                self._rescheduled()
                self.repo.commit("date only")
                self.assertEqual(self.repo.blocked(base), [("M", PATH)])

    def test_wrong_or_invalid_review_event_is_blocked(self) -> None:
        replacements = [
            (TARGET, "[[06-decisions/d-002-draft]]"),
            ("confirmed", "update-required"),
            ("2027-01-05", "2027-02-05"),
            ("2027-04-01", "2027-05-01"),
            ("2026-10-01", "2025-10-01"),
            ("2026-10-01", "2026-10-02"),
            ("Pat Example", "TODO"),
            ("2026-10-01", "not-a-date"),
        ]
        for before, after in replacements:
            with self.subTest(replacement=(before, after)):
                self.repo.write(PATH, APPROVED)
                self.repo.write("00-context/review-log.md", REVIEW_LOG)
                base = self.repo.commit("baseline")
                self._rescheduled()
                self._log(ROW.replace(before, after))
                self.repo.commit("invalid event")
                self.assertIn(("M", PATH), self.repo.blocked(base))

    def test_matching_new_event_is_allowed_but_duplicates_are_not(self) -> None:
        self._rescheduled()
        self._log()
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])
        self._log(ROW + ROW)
        self.repo.commit("replayed event")
        self.assertIn(("M", PATH), self.repo.blocked(self.base))

    def test_real_review_command_can_append_a_chain_in_one_change(self) -> None:
        for day, deadline in (("2026-10-01", "2027-04-01"), ("2026-10-02", "2027-05-01")):
            record_review(self.repo.vault, "D-001", reviewer="Pat Example", outcome="confirmed", next_review=deadline, today=dt.date.fromisoformat(day))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_new_log_can_attest_a_legacy_record(self) -> None:
        (self.repo.vault / "00-context/review-log.md").unlink()
        base = self.repo.commit("legacy vault without log")
        record_review(self.repo.vault, "D-001", reviewer="Pat Example", outcome="confirmed", next_review="2027-04-01", today=dt.date(2026, 10, 1))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(base), [])

    def test_staged_gate_does_not_use_an_unstaged_event(self) -> None:
        self._rescheduled()
        self.repo.git("add", PATH)
        self._log()
        self.assertEqual(self.repo.blocked(self.base, staged=True), [("M", PATH)])
        self.repo.git("add", "00-context/review-log.md")
        self.assertEqual(self.repo.blocked(self.base, staged=True), [])

    def test_last_updated_only_does_not_require_a_rescheduling_event(self) -> None:
        self.repo.write(PATH, _replace_key(APPROVED, "last_updated", "2026-10-01"))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])

    def test_formatted_table_keeps_events_and_accepts_the_review_writer(self) -> None:
        formatted = "\n".join(
            "| " + " | ".join(cell.strip().ljust(22) for cell in line.strip("|").split("|")) + " |"
            if line.startswith("|") and not line.startswith("|---") else line
            for line in REVIEW_LOG.splitlines()
        ) + "\n"
        self.assertTrue(allowed_review_log_append(REVIEW_LOG, formatted))
        self.repo.write("00-context/review-log.md", formatted)
        record_review(self.repo.vault, "D-001", reviewer="Pat Example", outcome="confirmed", next_review="2027-04-01", today=dt.date(2026, 10, 1))
        self.repo.commit()
        self.assertEqual(self.repo.blocked(self.base), [])


if __name__ == "__main__":
    unittest.main()
