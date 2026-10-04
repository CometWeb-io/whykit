"""One mental model across commands: grouped help, --root anywhere, --today and --json where they belong."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
EXAMPLE = ROOT / "examples" / "northline"
TODAY = "2026-09-17"

sys.path.insert(0, str(ROOT / "src"))

from whykit.cli import COMMAND_GROUPS, build_parser  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace",
        capture_output=True, env={**os.environ, "COLUMNS": "100"},
    )


def _top_level_commands() -> list[str]:
    import argparse

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    raise AssertionError("no subcommands")


class GroupedHelpTests(unittest.TestCase):
    def test_groups_cover_every_command_exactly_once(self) -> None:
        grouped = [name for _job, _blurb, names in COMMAND_GROUPS for name in names]
        self.assertEqual(sorted(grouped), sorted(_top_level_commands()))
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual([job for job, _blurb, _names in COMMAND_GROUPS],
                         ["author", "check", "explore", "integrate", "maintain"])

    def test_help_lists_commands_under_their_job(self) -> None:
        result = run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("commands:", text)
        positions = []
        for job, _blurb, names in COMMAND_GROUPS:
            heading = re.search(rf"(?m)^  {job}\b", text)
            self.assertIsNotNone(heading, job)
            assert heading is not None
            positions.append(heading.start())
            for name in names:
                line = re.search(rf"(?m)^    {re.escape(name)}\s+\S", text)
                self.assertIsNotNone(line, name)
                assert line is not None
                self.assertGreater(line.start(), heading.start(), name)
        self.assertEqual(positions, sorted(positions))
        # The flat argparse list is gone, so no command is listed twice.
        for name in _top_level_commands():
            self.assertEqual(len(re.findall(rf"(?m)^\s+{re.escape(name)}\s{{2,}}", text)), 1, name)

    def test_help_epilog_lists_every_documented_exit_code(self) -> None:
        text = run("--help").stdout
        epilog = text.split("exit codes:", 1)[1]
        for code in ("0", "1", "2", "70", "130"):
            self.assertRegex(epilog, rf"(?m)^  {code}\s")

    def test_bare_command_still_prints_grouped_help_to_stderr(self) -> None:
        result = run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("commands:", result.stderr)
        self.assertIn("  author", result.stderr)

    def test_completion_still_offers_every_command(self) -> None:
        script = run("completion", "bash").stdout
        for name in _top_level_commands():
            self.assertIn(name, script)


class RootPlacementTests(unittest.TestCase):
    def test_root_before_the_command_is_accepted(self) -> None:
        before = run("--root", str(EXAMPLE), "status", "--today", TODAY, "--json")
        after = run("status", "--root", str(EXAMPLE), "--today", TODAY, "--json")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(json.loads(before.stdout), json.loads(after.stdout))

    def test_root_equals_form_before_the_command(self) -> None:
        result = run(f"--root={EXAMPLE}", "lint", "--today", TODAY, "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_root_before_a_nested_command(self) -> None:
        result = run("--root", str(EXAMPLE), "evidence", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("E-001", result.stdout)

    def test_root_before_a_command_that_takes_no_vault_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run("--root", str(EXAMPLE), "init", str(Path(tmp) / "v"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("invalid choice", result.stderr)
        self.assertIn("--root", result.stderr)

    def test_adopt_accepts_root_as_the_vault(self) -> None:
        args = build_parser().parse_args(["adopt", "notes", "--root", "vault"])
        self.assertEqual(args.into, "vault")
        args = build_parser().parse_args(["adopt", "notes", "--into", "vault"])
        self.assertEqual(args.into, "vault")

    def test_serve_accepts_root_as_the_vault(self) -> None:
        from whykit.cli import _serve_target

        self.assertEqual(_serve_target(build_parser().parse_args(["serve", "--root", "v"])), "v")
        self.assertEqual(_serve_target(build_parser().parse_args(["serve", "v"])), "v")
        self.assertEqual(_serve_target(build_parser().parse_args(["serve"])), ".")
        with self.assertRaises(ValueError):
            _serve_target(build_parser().parse_args(["serve", "a", "--root", "b"]))


class UsageHintTests(unittest.TestCase):
    def test_unrecognized_option_points_at_the_subcommand_help(self) -> None:
        result = run("doctor", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("hint: `whykit doctor -h` lists the options of `whykit doctor`", result.stderr)

    def test_unrecognized_option_on_a_nested_command(self) -> None:
        result = run("review", "list", "--bogus", cwd=EXAMPLE)
        self.assertEqual(result.returncode, 2)
        self.assertIn("`whykit review list -h`", result.stderr)


class TodayTests(unittest.TestCase):
    def _doctor(self, today: str) -> dict[str, object]:
        result = run("doctor", "--root", str(EXAMPLE), "--today", today, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        return {c["name"]: c for c in payload["checks"]}

    def test_doctor_evaluates_review_hygiene_as_of_today_flag(self) -> None:
        self.assertTrue(self._doctor(TODAY)["review_hygiene"]["passed"])
        self.assertFalse(self._doctor("2031-01-01")["review_hygiene"]["passed"])

    def test_doctor_rejects_an_invalid_today(self) -> None:
        result = run("doctor", "--root", str(EXAMPLE), "--today", "2026-13-01", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid_argument")


class JsonSymmetryTests(unittest.TestCase):
    def test_snapshot_accepts_json_for_symmetry(self) -> None:
        plain = run("snapshot", "--root", str(EXAMPLE), "--today", TODAY)
        flagged = run("snapshot", "--root", str(EXAMPLE), "--today", TODAY, "--json")
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        self.assertEqual(plain.stdout, flagged.stdout)


if __name__ == "__main__":
    unittest.main()
