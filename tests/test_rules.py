from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))

from whykit.rules import RULE_BY_CODE  # noqa: E402


class RuleCatalogTests(unittest.TestCase):
    def test_every_lint_rule_code_has_catalog_documentation(self) -> None:
        source = (ROOT / "src/whykit/lint.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'"([a-z][a-z0-9_]+\.[a-z0-9_]+)"', source))
        missing = emitted - set(RULE_BY_CODE)
        self.assertEqual(missing, set(), missing)

    def test_docs_rule_table_covers_the_public_catalog(self) -> None:
        docs = (ROOT / "docs/rules.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"\| `([a-z][a-z0-9_]+\.[a-z0-9_]+)` \|", docs))
        self.assertEqual(set(RULE_BY_CODE) - documented, set())

    def test_markdown_catalog_is_generated_from_the_same_rule_registry(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CLI), "rules", "--markdown"],
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for code in RULE_BY_CODE:
            self.assertIn(f"`{code}`", proc.stdout)

    def test_rules_command_explains_a_known_rule(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CLI), "rules", "evidence.missing"],
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Why:", proc.stdout)
        self.assertIn("Fix:", proc.stdout)

    def test_unknown_rule_is_usage_error(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CLI), "rules", "does.not_exist"],
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 2)
