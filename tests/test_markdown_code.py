"""Regression cases for misleading citations and cross-surface Markdown code."""
from __future__ import annotations

import unittest
from pathlib import Path

from whykit.lint import Note, _mask_code

BT = chr(96)


class MarkdownCodeTests(unittest.TestCase):
    def assert_only_real_evidence(self, content: str, expected: tuple[str, ...] = ("E-002",)) -> None:
        note = Note(Path("06-decisions/d-001-test.md"), content)
        self.assertEqual(note.cited_evidence, expected)
        masked = _mask_code(content)
        self.assertEqual(len(masked), len(content))
        for before, after in zip(content, masked, strict=True):
            if before in "\r\n":
                self.assertEqual(after, before)

    def test_variable_length_inline_code(self) -> None:
        self.assert_only_real_evidence("Preview " + BT * 2 + "[[E-001]]" + BT * 2 + " [[E-002]]")
        self.assert_only_real_evidence(BT * 3 + "code " + BT + "E-001" + BT * 3 + " [[E-002]]")
        self.assert_only_real_evidence(BT * 2 + "first\nE-001\nthird" + BT * 2 + " [[E-002]]")

    def test_cr_only_and_mixed_line_endings_preserve_evidence_extraction(self) -> None:
        for fence in (BT * 3, "~~~"):
            with self.subTest(fence=fence):
                self.assert_only_real_evidence(
                    fence + "markdown\r[[E-001]]\r" + fence + "\r[[E-002]]"
                )
        self.assert_only_real_evidence("~~~\r[[E-001]]\r\n~~~\n[[E-002]]")

    def test_unclosed_inline_delimiter_is_literal(self) -> None:
        self.assert_only_real_evidence(BT + "[[E-001]]", ("E-001",))

    def test_unclosed_fenced_code_masks_to_eof(self) -> None:
        self.assert_only_real_evidence(BT * 3 + "md\n[[E-001]]\n", ())

    def test_fence_can_have_three_spaces_of_indentation(self) -> None:
        self.assert_only_real_evidence("   " + BT * 3 + "md\n[[E-001]]\n   " + BT * 3 + "\n[[E-002]]")

    def test_fence_closer_must_match_kind_and_minimum_length(self) -> None:
        for opener, premature, closer in (
            (BT * 4, BT * 3, BT * 4),
            ("~" * 4, "~" * 3, "~" * 4),
            ("~" * 3, BT * 3, "~" * 3),
        ):
            with self.subTest(opener=opener, premature=premature):
                self.assert_only_real_evidence(opener + "txt\n" + premature + "\n[[E-001]]\n" + closer + "\n[[E-002]]")

    def test_fence_info_string_backticks_invalid_for_backtick_fence(self) -> None:
        # An invalid opener is prose, not a fenced code block.
        self.assert_only_real_evidence(BT * 3 + "bad" + BT + "\n[[E-001]]", ("E-001",))

    def test_link_and_evidence_extractors_share_the_mask(self) -> None:
        body = "~" * 3 + "\n[[private]] E-001 [link](private.md)\n" + "~" * 3 + "\n[[public]] E-002"
        note = Note(Path("Home.md"), body)
        self.assertEqual(note.cited_evidence, ("E-002",))
        self.assertEqual([item[0] for item in note.wikilink_hits], ["public"])
        self.assertFalse(any("private.md" in item for item in note.markdown_link_hits))

    def test_plain_document_reuses_input_without_allocating_a_second_string(self) -> None:
        source = "See E-001 and [[real-note]] without any code fences"
        self.assertIs(_mask_code(source), source)

    def test_comment_fixture_parity(self) -> None:
        import json

        fixture = Path(__file__).resolve().parent / "fixtures" / "markdown-code-comments.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(id=case["id"]):
                note = Note(Path("Home.md"), case["markdown"])
                self.assertEqual(list(note.cited_evidence), case["evidence"])
                self.assertEqual([entry[0] for entry in note.wikilink_hits], case["wikilinks"])
                masked = _mask_code(case["markdown"])
                self.assertEqual(len(masked), len(case["markdown"]))
                for index, original in enumerate(case["markdown"]):
                    if original in "\r\n":
                        self.assertEqual(masked[index], original)

    def test_comment_precedence_with_many_delimiters_stays_bounded(self) -> None:
        # A comment opener must not be erased by a code span created later,
        # and many unmatched runs must not trigger repeated rescans.
        source = ("<!-- " + BT + "body --> " + BT + " ") * 2500 + "[[E-002]]"
        masked = _mask_code(source)
        self.assertEqual(len(masked), len(source))
        self.assertIn("[[E-002]]", masked)

    def test_only_commonmark_cr_lf_crlf_split_physical_lines(self) -> None:
        # Unicode separators are content, not physical Markdown line endings.
        # A phantom fence created by str.splitlines() would hide E-001.
        # The later standalone tilde run is a real opener and hides E-002.
        for separator in ("\u0085", "\u2028", "\u2029", "\v", "\f"):
            with self.subTest(separator=ascii(separator)):
                content = "prefix" + separator + "~~~\n[[E-001]]\n~~~\n[[E-002]]"
                self.assert_only_real_evidence(content, ("E-001",))

    def test_many_unmatched_runs_do_not_quadratically_scan(self) -> None:
        content = (BT + " text " + BT * 2 + " x ") * 5000 + " [[E-002]]"
        self.assert_only_real_evidence(content)


if __name__ == "__main__":
    unittest.main()
