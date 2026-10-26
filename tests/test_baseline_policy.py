"""Head policy cannot waive the checks imposed by the pinned base policy."""
from __future__ import annotations

import datetime as dt
import shutil
import unittest
from pathlib import Path

from test_history_gate import Repo
from whykit.check import run_check

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 9, 17)


class BaselinePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)
        shutil.copytree(ROOT / "examples/tiny", self.repo.vault, dirs_exist_ok=True)
        self.config = self.repo.vault / "whykit.toml"
        self.base = self.repo.commit("synthetic policy baseline")

    def check(self, **kwargs) -> dict:
        return run_check(self.repo.vault, profile_name="ci", today=DAY, **kwargs)

    def test_optional_history_is_explicitly_unchecked_without_a_base(self) -> None:
        report = self.check()
        self.assertTrue(report["passed"])
        self.assertFalse(report["history_checked"])
        history = next(item for item in report["checks"] if item["name"] == "history")
        self.assertFalse(history["checked"])

    def test_unchanged_base_and_head_policies_pass(self) -> None:
        report = self.check(base=self.base)
        self.assertTrue(report["passed"])
        self.assertTrue(report["history_checked"])
        self.assertTrue(report["baseline"]["passed"])

    def test_head_cannot_turn_off_history_for_a_reasoning_rewrite(self) -> None:
        text = self.config.read_text(encoding="utf-8").replace('history = "optional"', 'history = "off"')
        self.config.write_text(text, encoding="utf-8")
        record = next((self.repo.vault / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8") + "\nChanged accepted reasoning.\n", encoding="utf-8")
        self.repo.commit()
        report = self.check(base=self.base)
        self.assertFalse(report["passed"])
        self.assertFalse(next(item for item in report["checks"] if item["name"] == "history")["checked"])
        self.assertTrue(report["baseline"]["history_checked"])
        self.assertFalse(next(item for item in report["baseline"]["checks"] if item["name"] == "history")["passed"])

    def test_removed_custom_rule_still_checks_head_content(self) -> None:
        rule = '''\n[[rules.custom]]
id = "custom.keep_example_marker_out"
level = "error"
summary = "A forbidden marker is absent."
forbidden_patterns = ["SYNTHETIC_POLICY_PROBE"]
'''
        original = self.config.read_text(encoding="utf-8")
        self.config.write_text(original + rule, encoding="utf-8")
        base = self.repo.commit("pin a team rule")
        self.config.write_text(original, encoding="utf-8")
        home = self.repo.vault / "Home.md"
        home.write_text(home.read_text(encoding="utf-8") + "\nSYNTHETIC_POLICY_PROBE\n", encoding="utf-8")
        self.repo.commit()
        self.assertTrue(self.check()["passed"])
        report = self.check(base=base)
        self.assertFalse(report["passed"])
        self.assertIn("custom.keep_example_marker_out", {f["code"] for f in report["baseline"]["lint"]["findings"]})

    def test_new_rule_override_cannot_hide_the_baseline_finding(self) -> None:
        home = self.repo.vault / "Home.md"
        text = home.read_text(encoding="utf-8")
        self.assertIn("[[notes/README|Freeform notes]]", text)
        home.write_text(text.replace("[[notes/README|Freeform notes]]", "Freeform notes"), encoding="utf-8")
        self.config.write_text(self.config.read_text(encoding="utf-8") + '\n[rules.overrides."hub.unlinked_workstream"]\nlevel = "off"\n', encoding="utf-8")
        self.repo.commit()
        report = self.check(base=self.base)
        self.assertFalse(report["passed"])
        self.assertIn("hub.unlinked_workstream", {f["code"] for f in report["baseline"]["lint"]["findings"]})

    def test_bad_or_unavailable_baseline_fails_closed(self) -> None:
        for base in ("unknown-ref", "--help"):
            with self.subTest(base=base):
                report = self.check(base=base)
                self.assertFalse(report["passed"])
                self.assertFalse(next(item for item in report["checks"] if item["name"] == "baseline_policy")["passed"])

    def test_nested_vault_reads_its_own_baseline_policy(self) -> None:
        nested = self.repo.vault / "knowledge"
        shutil.copytree(ROOT / "examples/tiny", nested)
        base = self.repo.commit("nested vault")
        report = run_check(nested, profile_name="ci", base=base, today=DAY)
        self.assertTrue(report["passed"])
        self.assertTrue(report["baseline"]["passed"])

    def test_missing_base_config_uses_defaults_instead_of_head_policy(self) -> None:
        original = self.config.read_text(encoding="utf-8")
        self.config.unlink()
        base = self.repo.commit("baseline with built-in defaults")
        self.config.write_text(original.replace('history = "optional"', 'history = "off"'), encoding="utf-8")
        record = next((self.repo.vault / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8") + "\nUnreviewed rewrite.\n", encoding="utf-8")
        self.repo.commit("weakened head policy")
        report = self.check(base=base)
        self.assertFalse(report["passed"])
        self.assertTrue(report["baseline"]["history_checked"])
        self.assertIn("built-in defaults", report["baseline"]["config_source"])

    def test_invalid_base_config_fails_closed_after_head_repairs_it(self) -> None:
        original = self.config.read_text(encoding="utf-8")
        self.config.write_text("format_version = [\n", encoding="utf-8")
        base = self.repo.commit("malformed baseline policy")
        self.config.write_text(original, encoding="utf-8")
        self.repo.commit("valid head policy")
        report = self.check(base=base)
        self.assertFalse(report["passed"])
        self.assertIn("cannot validate policy", next(item for item in report["checks"] if item["name"] == "baseline_policy")["detail"])

    def test_utf8_bom_in_base_config_uses_the_same_parser_as_disk(self) -> None:
        self.config.write_bytes(b"\xef\xbb\xbf" + self.config.read_bytes())
        base = self.repo.commit("BOM baseline")
        report = self.check(base=base)
        self.assertTrue(report["passed"])
        self.assertTrue(report["baseline"]["passed"])

    def test_base_policy_must_be_a_regular_git_blob(self) -> None:
        before = self.config.read_bytes()
        blob = self.repo.git("hash-object", "-w", "whykit.toml").strip()
        self.repo.git("update-index", "--cacheinfo", f"120000,{blob},whykit.toml")
        self.repo.git("commit", "-qm", "non-regular baseline policy")
        base = self.repo.git("rev-parse", "HEAD").strip()
        report = self.check(base=base)
        self.assertFalse(report["passed"])
        failed = next(item for item in report["checks"] if item["name"] == "baseline_policy")
        self.assertIn("must be a regular file", failed["detail"])
        self.assertEqual(self.config.read_bytes(), before)

    def test_head_cannot_remove_base_evidence_freshness_policy(self) -> None:
        from whykit.lint import _split_table_row

        original = self.config.read_text(encoding="utf-8")
        register = self.repo.vault / "00-context/evidence-register.md"
        lines = register.read_text(encoding="utf-8").splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith("| E-001 |"))
        cells = _split_table_row(lines[at])
        kind = cells[2]
        cells[4] = "2026-01-01"
        lines[at] = "| " + " | ".join(cells) + " |"
        register.write_text("\n".join(lines) + "\n", encoding="utf-8")
        header = "[evidence_access_age_days]"
        setting = f'\n"{kind}" = 1\n'
        policy = original.replace(header, header + setting) if header in original else original + "\n" + header + setting
        self.config.write_text(policy, encoding="utf-8")
        base = self.repo.commit("pin evidence freshness")
        self.config.write_text(original, encoding="utf-8")
        self.repo.commit("remove freshness at head")
        self.assertTrue(self.check()["passed"])
        report = self.check(base=base)
        self.assertFalse(report["passed"])
        self.assertIn("evidence.access_stale", {item["code"] for item in report["baseline"]["lint"]["findings"]})


if __name__ == "__main__":
    unittest.main()
