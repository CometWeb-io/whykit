"""Property-style round-trip checks for the hand-written parsers.

WhyKit deliberately parses a small YAML subset and Markdown tables itself, so
the writers (`_yaml_string`, `_table_cell`) and readers (`_parse_front_matter`,
`_split_table_row`) must agree on every value a user can type. A seeded RNG
keeps failures reproducible without adding a test dependency.
"""
from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whykit.evidence import list_evidence  # noqa: E402
from whykit.lint import _parse_front_matter, _split_table_row, load_note  # noqa: E402
from whykit.scaffold import _next_id, _slugify, _table_cell, _yaml_string, create_evidence  # noqa: E402
from _vaults import fresh_vault  # noqa: E402

# Characters that are syntax somewhere in front matter or tables, plus
# whitespace, non-ASCII and an astral-plane code point.
ALPHABET = list("ab Z09|\\[]#:'\"-,{}*&!%@`>\t\néł中\U0001F600")
SEED = 20261002
ROUNDS = 3000


def _random_text(rng: random.Random, max_len: int = 12) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, max_len)))


class FrontMatterRoundTripTests(unittest.TestCase):
    def test_any_string_survives_the_writer_and_the_parser(self) -> None:
        rng = random.Random(SEED)
        for _ in range(ROUNDS):
            value = _random_text(rng)
            with self.subTest(value=value):
                parsed = _parse_front_matter(f"title: {_yaml_string(value)}\nowner: {_yaml_string(value)} # note")
                self.assertEqual(parsed, {"title": value, "owner": value})

    def test_inline_lists_of_quoted_strings_round_trip(self) -> None:
        rng = random.Random(SEED + 1)
        for _ in range(ROUNDS // 3):
            values = [_random_text(rng, 6) for _ in range(rng.randint(0, 4))]
            line = "tags: [" + ", ".join(_yaml_string(value) for value in values) + "]"
            with self.subTest(values=values):
                self.assertEqual(_parse_front_matter(line)["tags"], values)

    def test_a_full_note_keeps_its_front_matter_and_body_offset(self) -> None:
        rng = random.Random(SEED + 2)
        for _ in range(ROUNDS // 10):
            title = _random_text(rng)
            text = f"---\ntitle: {_yaml_string(title)}\nsource_ids: []\n---\n# Body\n"
            for newline in ("\n", "\r\n"):
                with self.subTest(title=title, newline=newline):
                    note = load_note(Path("note.md"), text=text.replace("\n", newline))
                    self.assertIsNone(note.front_error)
                    self.assertEqual(note.front["title"], title)
                    self.assertEqual(note.body_offset, 4)


class TableRoundTripTests(unittest.TestCase):
    def test_cells_round_trip_when_no_wikilink_is_closed(self) -> None:
        # `]]` is excluded: a closed `[[...]]` legitimately spans cells (Obsidian
        # aliases), so only rows without a closer have one correct reading.
        rng = random.Random(SEED + 3)
        alphabet = [char for char in ALPHABET if char != "]"]
        for _ in range(ROUNDS):
            cells = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 10))) for _ in range(rng.randint(1, 7))]
            row = "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"
            with self.subTest(cells=cells):
                self.assertEqual(_split_table_row(row), [" ".join(cell.split()) for cell in cells])

    def test_wikilink_aliases_stay_in_one_cell(self) -> None:
        self.assertEqual(
            _split_table_row("| D-001 | [[06-decisions/d-001-first|First decision]] | accepted |"),
            ["D-001", "[[06-decisions/d-001-first|First decision]]", "accepted"],
        )

    def test_a_stray_wikilink_opener_does_not_swallow_later_cells(self) -> None:
        self.assertEqual(_split_table_row("| E-001 | Notes [[draft | report |"), ["E-001", "Notes [[draft", "report"])

    def test_evidence_with_a_stray_opener_stays_visible_and_ids_keep_allocating(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            day = fresh_vault(vault)
            for source in ("Notes [[draft", "Second"):
                create_evidence(vault, source=source, location="https://example.com/a", kind="report",
                                claims="Claim", today=day)
            listing = list_evidence(vault)
            self.assertEqual([item["id"] for item in listing["evidence"]], ["E-001", "E-002"])
            self.assertEqual(listing["evidence"][0]["source"], "Notes [[draft")


class IdentifierTests(unittest.TestCase):
    def test_slugs_are_ascii_kebab_case_and_never_empty(self) -> None:
        rng = random.Random(SEED + 4)
        for _ in range(ROUNDS):
            slug = _slugify(_random_text(rng, 20))
            with self.subTest(slug=slug):
                self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual(_slugify("Zażółć gęślą jaźń"), "zazoc-gesla-jazn")
        self.assertEqual(_slugify("中文"), "record")

    def test_next_id_follows_the_highest_matching_prefix(self) -> None:
        self.assertEqual(_next_id([], "D"), "D-001")
        self.assertEqual(_next_id(["D-002", "D-010", "E-099", "D-x"], "D"), "D-011")
        self.assertEqual(_next_id(["E-999"], "E"), "E-1000")


if __name__ == "__main__":
    unittest.main()
