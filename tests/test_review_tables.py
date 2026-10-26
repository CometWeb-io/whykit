"""Review writers, lint, history and Explorer agree about visible table headers."""
from __future__ import annotations

from test_content_quality import DAY, _TinyCopy
from whykit.explorer_index import build_explorer_index
from whykit.immutability import _review_log_parts, allowed_review_log_append
from whykit.lint import lint
from whykit.review import record_review
from whykit.tables import review_table_header

HEADER = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
SEPARATOR = "|---|---|---|---|---|---|---|"


class ReviewTableTests(_TinyCopy):
    def test_formatted_header_and_alignment_keep_the_same_events(self) -> None:
        log = self.vault / "00-context/review-log.md"
        before = log.read_text(encoding="utf-8")
        events = build_explorer_index(self.vault, today=DAY)["reviews"]
        after = before.replace(HEADER, "| DATE  | TARGET | REVIEWER | OUTCOME | PREVIOUS REVIEW | NEXT REVIEW | NOTE |")
        after = after.replace(SEPARATOR, "| :--- | ---: | :---: | --- | --- | --- | --- |")
        log.write_text(after, encoding="utf-8")
        self.assertTrue(allowed_review_log_append(before, after))
        self.assertEqual(build_explorer_index(self.vault, today=DAY)["reviews"], events)
        record_review(self.vault, "D-002", reviewer="Ada Example", outcome="confirmed", today=DAY)
        self.assertEqual(len(build_explorer_index(self.vault, today=DAY)["reviews"]), len(events) + 1)

    def test_hidden_example_tables_do_not_become_history(self) -> None:
        log = self.vault / "00-context/review-log.md"
        before = log.read_text(encoding="utf-8")
        rows = _review_log_parts(before)[1]
        events = build_explorer_index(self.vault, today=DAY)["reviews"]
        fake = HEADER + "\n" + SEPARATOR + "\n| 2026-09-17 | [[missing]] | Fake reviewer | approved | — | 2027-01-01 | fake receipt |\n"
        for block in ("<!--\n" + fake + "-->\n", "````markdown\n" + fake + "````\n"):
            with self.subTest(block=block[:10]):
                text = before.replace("# Review log\n", "# Review log\n\n" + block)
                log.write_text(text, encoding="utf-8")
                self.assertEqual(_review_log_parts(text)[1], rows)
                self.assertFalse([f for f in lint(self.vault, today=DAY)[1] if f.code.startswith("review_log.")])
                self.assertEqual(build_explorer_index(self.vault, today=DAY)["reviews"], events)
                record_review(self.vault, "D-002", reviewer="Ada Example", outcome="confirmed", today=DAY)
                self.assertIn(block, log.read_text(encoding="utf-8"))
                self.assertEqual(len(build_explorer_index(self.vault, today=DAY)["reviews"]), len(events) + 1)

    def test_bad_or_missing_separators_fail_before_a_review_write(self) -> None:
        log = self.vault / "00-context/review-log.md"
        original = log.read_text(encoding="utf-8")
        for separator in ("|---|---|---|---|---|---|", "|---|---|bad|---|---|---|---|", ""):
            with self.subTest(separator=separator):
                text = original.replace(SEPARATOR, separator)
                log.write_text(text, encoding="utf-8")
                self.assertIsNone(review_table_header(text.splitlines()))
                self.assertIsNone(_review_log_parts(text))
                self.assertIn("review_log.table", {f.code for f in lint(self.vault, today=DAY)[1]})
                self.assertEqual(build_explorer_index(self.vault, today=DAY)["reviews"], [])
                with self.assertRaisesRegex(ValueError, "table is missing or malformed"):
                    record_review(self.vault, "D-002", reviewer="Ada Example", outcome="confirmed", today=DAY)
                self.assertEqual(log.read_text(encoding="utf-8"), text)

    def test_hidden_event_rows_are_rejected_by_every_consumer(self) -> None:
        log = self.vault / "00-context/review-log.md"
        original = log.read_text(encoding="utf-8")
        event = "| 2026-09-17 | [[06-decisions/d-002-keep-the-ledger-to-evidence-and-decisions]] | Ada Example | confirmed | 2027-03-17 | 2027-06-17 | checked |"
        for text in (
            original.replace(SEPARATOR, SEPARATOR + " <!--\n" + event),
            original + event.replace("checked |", "<!-- |") + "\n" + event + "\n",
            original + event.replace("checked |", "`open |") + "\n" + event.replace("checked |", "close` |") + "\n",
        ):
            with self.subTest(text=text[-180:]):
                log.write_text(text, encoding="utf-8")
                self.assertIsNone(review_table_header(text.splitlines()))
                self.assertIsNone(_review_log_parts(text))
                self.assertIn("review_log.table", {f.code for f in lint(self.vault, today=DAY)[1]})
                self.assertEqual(build_explorer_index(self.vault, today=DAY)["reviews"], [])
                before = {p: p.read_bytes() for p in self.vault.rglob("*.md")}
                with self.assertRaisesRegex(ValueError, "table is missing or malformed"):
                    record_review(self.vault, "D-002", reviewer="Ada Example", outcome="confirmed", today=DAY)
                self.assertEqual({p: p.read_bytes() for p in self.vault.rglob("*.md")}, before)

    def test_writer_refuses_an_event_that_hides_its_own_cells(self) -> None:
        for params in ({"reviewer": "Ada <!--"}, {"note_text": "<!--"}):
            with self.subTest(params=params):
                before = {p: p.read_bytes() for p in self.vault.rglob("*.md")}
                with self.assertRaisesRegex(ValueError, "table is missing or malformed"):
                    record_review(self.vault, "D-002", outcome="confirmed", today=DAY,
                                  **{"reviewer": "Ada Example", **params})
                self.assertEqual({p: p.read_bytes() for p in self.vault.rglob("*.md")}, before)

    def test_visible_inline_code_and_closed_note_comments_remain_valid(self) -> None:
        note = "Checked `config | value` and `<!-- literal -->`. <!-- internal note --> Done."
        record_review(self.vault, "D-002", reviewer="Ada `Example`", outcome="confirmed", note_text=note, today=DAY)
        log = self.vault / "00-context/review-log.md"
        parts = _review_log_parts(log.read_text(encoding="utf-8"))
        self.assertIsNotNone(parts)
        self.assertEqual(parts[1][-1][2], "Ada `Example`")
        self.assertEqual(parts[1][-1][6], note)
        self.assertFalse([f for f in lint(self.vault, today=DAY)[1] if f.code.startswith("review_log.")])
        self.assertEqual(build_explorer_index(self.vault, today=DAY)["reviews"][-1]["note"], note)
