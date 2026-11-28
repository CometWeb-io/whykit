"""Promotion of real-world ADR shapes, from migrating four public ADR folders."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _vaults import fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.promote import PromotionError, build_promotion  # noqa: E402

MADR = """---
status: accepted
decision-makers: Ada Example, Lin Example
---
# Add status field

## Context and Problem Statement

ADRs have a status. See <https://example.com/issue/2>.

![status](https://img.shields.io/badge/status-accepted-green.svg)

## Considered Options

* Use YAML front matter
* Use badge
* Use text line

## Decision Outcome

Chosen option: "Use YAML front matter", because it is supported by most parsers.

## Pros and Cons of the Options

### Use YAML front matter

* Good, because most Markdown parsers support it

### Use badge

* Good, because it is visual
* Bad, because it needs a badge service

### Use text line

* Good, because plain Markdown
* Bad, because it must be maintained
"""


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class PromoteFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.vault = self.base / "vault"
        fresh_vault(self.vault)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def source(self, name: str, text: str) -> Path:
        path = self.base / "adr" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_madr_pros_cons_deciders_and_badges(self) -> None:
        promotion = build_promotion(self.vault, self.source("0008-add-status-field.md", MADR))
        self.assertEqual(promotion.source_owner, "Ada Example, Lin Example")
        self.assertEqual(promotion.alternatives, ["Use badge", "Use text line"])
        self.assertEqual(promotion.option_details["use badge"],
                         (["It is visual"], ["It needs a badge service"]))
        self.assertEqual(promotion.evidence_candidates, ["https://example.com/issue/2"])
        code, out, err = call("new", "--root", str(self.vault), "decision", "--from",
                              str(self.base / "adr" / "0008-add-status-field.md"), "--write")
        self.assertEqual(code, 0, out + err)
        record = next((self.vault / "06-decisions").glob("d-001-*.md")).read_text(encoding="utf-8")
        self.assertIn('owner: "Ada Example, Lin Example"', record)
        self.assertIn("| Use badge | It is visual | It needs a badge service | Not chosen; see the original record |", record)
        self.assertNotIn("candidate source from the original record: <https://img.shields.io", record)

    def test_explicit_owner_wins(self) -> None:
        path = self.source("0008-add-status-field.md", MADR)
        code, out, err = call("new", "--root", str(self.vault), "decision", "--from", str(path), "--owner", "Platform", "--write")
        self.assertEqual(code, 0, out + err)
        record = next((self.vault / "06-decisions").glob("d-001-*.md")).read_text(encoding="utf-8")
        self.assertIn('owner: "Platform"', record)

    def test_records_not_in_force_are_flagged(self) -> None:
        superseded = self.source("0002-old.md", "# 2. Old way\n\n## Status\n\nSuperseded by 0003\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        marked = self.source("adr013-fetch.md", "# ADR013: [superseded] Use node-fetch\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        for path in (superseded, marked):
            with self.subTest(path=path.name):
                warnings = build_promotion(self.vault, path).warnings
                self.assertTrue(any("supersede or archive it instead of approving it" in w for w in warnings), warnings)

    def test_the_adopt_report_is_not_a_decision(self) -> None:
        report = self.vault / ".import-staging" / "2026-11-12" / "MIGRATION.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Migration from adr\n\n## Decision\n\nx\n", encoding="utf-8")
        with self.assertRaises(PromotionError):
            build_promotion(self.vault, report)
        code, out, _ = call("new", "--root", str(self.vault), "decision", "--from", str(report), "--json")
        self.assertEqual(json.loads(out)["error"]["code"], "invalid_target")
        # A file of the same name in an adopted folder is still an ordinary input.
        nested = self.vault / ".import-staging" / "2026-11-12" / "adr" / "MIGRATION.md"
        nested.parent.mkdir()
        nested.write_text("# 9. Migrate to Postgres\n\n## Context\n\nx\n\n## Decision\n\ny\n", encoding="utf-8")
        self.assertEqual(build_promotion(self.vault, nested).title, "Migrate to Postgres")


if __name__ == "__main__":
    unittest.main()
