from __future__ import annotations

import json
import unittest
from pathlib import Path

from whykit.tables import review_table_header, split_table_row

HEADER = "| Date | Target | Reviewer | Outcome | Previous review | Next review | Note |"
SEPARATOR = "|---|---|---|---|---|---|---|"


class TableParserTests(unittest.TestCase):
    def test_python_and_explorer_share_row_contract_fixtures(self) -> None:
        path = Path(__file__).parent / "fixtures/table-rows.json"
        for fixture in json.loads(path.read_text(encoding="utf-8")):
            with self.subTest(row=fixture["row"]):
                self.assertEqual(split_table_row(fixture["row"]), fixture["cells"])

    def test_review_table_requires_real_separator_and_ignores_code(self) -> None:
        self.assertEqual(review_table_header([HEADER, SEPARATOR]), 0)
        self.assertEqual(review_table_header(["```", HEADER, SEPARATOR, "```", HEADER, SEPARATOR]), 4)
        for lines in ([HEADER], [HEADER, "text"], [HEADER, "|---|---|"], ["```", HEADER, SEPARATOR, "```"]):
            with self.subTest(lines=lines):
                self.assertIsNone(review_table_header(lines))


if __name__ == "__main__":
    unittest.main()
