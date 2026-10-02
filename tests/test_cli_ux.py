"""CLI ergonomics: help, exit codes, actionable errors, --json parity, completion."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
EXAMPLE = ROOT / "examples" / "northline"

sys.path.insert(0, str(ROOT / "src"))

from whykit import __version__  # noqa: E402


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True,
        env={**os.environ, **(env or {})},
    )


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


class TopLevelTests(unittest.TestCase):
    def test_version_has_short_and_long_flags(self) -> None:
        for flag in ("-V", "--version"):
            result = run(flag)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), f"whykit {__version__}")

    def test_bare_command_prints_help_and_exits_2(self) -> None:
        result = run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("commands:", result.stderr)
        self.assertIn("exit codes:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_every_option_has_help_text(self) -> None:
        import argparse

        from whykit.cli import build_parser

        def walk(parser: argparse.ArgumentParser, path: str) -> list[str]:
            missing: list[str] = []
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        missing += walk(child, f"{path} {name}")
                elif not action.help:
                    missing.append(f"{path} {action.option_strings or action.dest}")
            return missing

        self.assertEqual(walk(build_parser(), "whykit"), [])


class NoVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_vault_commands_explain_how_to_recover(self) -> None:
        commands = [
            ["status"], ["query", "x"], ["context", "x"], ["backlinks", "x"], ["impact", "x"],
            ["graph"], ["snapshot"], ["policy"], ["check"], ["evidence", "list"],
            ["review", "list"], ["explorer-index"], ["install-hooks"], ["lint"],
            ["new", "decision", "x"],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                result = run(*argv, cwd=self.base)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no WhyKit vault found at or above", result.stderr)
                self.assertIn("whykit init <dir>", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_explicit_root_that_is_not_a_vault_names_the_path(self) -> None:
        result = run("status", "--root", str(self.base))
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"not a WhyKit vault: {self.base.resolve()}", result.stderr)
        self.assertIn("Home.md and 00-context/", result.stderr)

    def test_doctor_json_always_has_a_root_key(self) -> None:
        result = run("doctor", "--json", cwd=self.base)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["root"])
        self.assertFalse(payload["passed"])


class InputErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.assertEqual(run("init", str(self.vault)).returncode, 0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_os_errors_are_reported_without_a_traceback(self) -> None:
        blocker = self.base / "file"
        blocker.write_text("x", encoding="utf-8")
        result = run("init", str(blocker / "child"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot complete `whykit init`", result.stderr)
        self.assertIn("WHYKIT_DEBUG=1", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_debug_env_restores_the_traceback(self) -> None:
        blocker = self.base / "file"
        blocker.write_text("x", encoding="utf-8")
        result = run("init", str(blocker / "child"), env={"WHYKIT_DEBUG": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Traceback", result.stderr)

    def test_lint_rejects_a_missing_path_instead_of_reporting_clean(self) -> None:
        result = run("lint", "no-such-note.md", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("path not found: no-such-note.md", result.stderr)
        self.assertNotIn("clean", result.stdout)

    def test_lint_rejects_a_non_markdown_file(self) -> None:
        (self.vault / "data.csv").write_text("a,b\n", encoding="utf-8")
        result = run("lint", "data.csv", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a Markdown file", result.stderr)

    def test_lint_still_accepts_existing_paths(self) -> None:
        result = run("lint", "Home.md", cwd=self.vault)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_dates_name_the_flag_and_format(self) -> None:
        cases = [
            ["new", "decision", "x", "--review-by", "tomorrow"],
            ["new", "evidence", "--source", "s", "--location", "https://example.com",
             "--type", "report", "--claims", "c", "--date", "2026-13-01"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                result = run(*argv, "--json", cwd=self.vault)
                self.assertEqual(result.returncode, 2)
                self.assertIn("is not a real ISO date", result.stderr)
                self.assertIn("YYYY-MM-DD", result.stderr)
                self.assertNotIn("isoformat", result.stderr)

    def test_missing_snapshot_suggests_creating_one(self) -> None:
        result = run("verify-snapshot", "missing.json", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("snapshot not found", result.stderr)
        self.assertIn("whykit snapshot --output", result.stderr)

    def test_non_json_snapshot_is_named_as_such(self) -> None:
        result = run("verify-snapshot", "Home.md", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a WhyKit snapshot (invalid JSON", result.stderr)


@unittest.skipUnless(shutil.which("git"), "git is required")
class HistoryErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.assertEqual(run("init", str(self.vault)).returncode, 0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_outside_a_work_tree_is_explained(self) -> None:
        result = run("history", "--base", "HEAD", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not inside a Git work tree", result.stderr)
        self.assertNotIn("usage: git", result.stderr)

    def test_unknown_revision_is_explained_not_dumped(self) -> None:
        git("init", "-q", cwd=self.vault)
        git("add", "-A", cwd=self.vault)
        git("commit", "-qm", "init", cwd=self.vault)
        result = run("history", "--base", "no-such-ref", cwd=self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown Git revision: no-such-ref", result.stderr)
        self.assertIn("git fetch", result.stderr)
        self.assertNotIn("usage: git", result.stderr)

        check = run("check", "--base", "no-such-ref", "--json", cwd=self.vault)
        history = next(c for c in json.loads(check.stdout)["checks"] if c["name"] == "history")
        self.assertFalse(history["passed"])
        self.assertIn("unknown Git revision", history["detail"])

    def test_history_json(self) -> None:
        git("init", "-q", cwd=self.vault)
        git("add", "-A", cwd=self.vault)
        git("commit", "-qm", "init", cwd=self.vault)
        result = run("history", "--base", "HEAD", "--json", cwd=self.vault)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["contract_version"], 1)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["blocked"], [])


class JsonParityTests(unittest.TestCase):
    def test_init_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            result = run("init", "--json", str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["contract_version"], 1)
            self.assertEqual(payload["layout"], "minimal")
            self.assertEqual(Path(payload["root"]), target)

    def test_pack_and_graph_accept_json_flag(self) -> None:
        for argv in (["pack", "--query", "pricing", "--json"], ["graph", "--json"]):
            with self.subTest(argv=argv):
                result = run(*argv, "--root", str(EXAMPLE))
                self.assertIn(result.returncode, (0, 1), result.stderr)
                json.loads(result.stdout)

    def test_json_conflicts_with_another_format(self) -> None:
        result = run("graph", "--json", "--format", "dot", "--root", str(EXAMPLE))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--json conflicts with --format dot", result.stderr)

    def test_graph_default_format_is_unchanged(self) -> None:
        result = run("graph", "--root", str(EXAMPLE))
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)


class BrokenPipeTests(unittest.TestCase):
    def test_closed_stdout_does_not_print_a_traceback(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, str(CLI), "explorer-index", "--root", str(EXAMPLE), "--today", "2026-09-17"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        proc.stdout.read(1)
        proc.stdout.close()
        stderr = proc.stderr.read().decode()
        proc.stderr.close()
        proc.wait(timeout=60)
        self.assertNotIn("Traceback", stderr)


class CompletionTests(unittest.TestCase):
    def test_completion_scripts_render(self) -> None:
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell):
                result = run("completion", shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("whykit", result.stdout)
                self.assertIn("verify-snapshot", result.stdout)

    def test_unknown_shell_is_a_usage_error(self) -> None:
        result = run("completion", "powershell")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    @unittest.skipUnless(shutil.which("bash"), "bash is required")
    def test_bash_completion_completes_commands_options_and_choices(self) -> None:
        script = run("completion", "bash").stdout
        probe = script + r'''
t() { COMP_WORDS=("$@"); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 )); _whykit; echo "${COMPREPLY[*]}"; }
t whykit li
t whykit new ""
t whykit new decision --status ""
t whykit graph --format d
t whykit completion ""
t whykit lint --today 2026 ""
'''
        result = subprocess.run(["bash", "-c", probe], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "lint")
        self.assertEqual(lines[1].split(), ["decision", "evidence", "note"])
        self.assertIn("in_review", lines[2].split())
        self.assertEqual(lines[3], "dot")
        self.assertEqual(lines[4].split(), ["bash", "zsh", "fish"])
        self.assertEqual(lines[5], "")

    @unittest.skipUnless(shutil.which("zsh"), "zsh is required")
    def test_zsh_completion_registers(self) -> None:
        script = run("completion", "zsh").stdout
        result = subprocess.run(
            ["zsh", "-f", "-c", script + "\nprint -r -- ${_comps[whykit]}"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("_whykit", result.stdout)

    @unittest.skipUnless(shutil.which("fish"), "fish is required")
    def test_fish_completion_parses(self) -> None:
        script = run("completion", "fish").stdout
        result = subprocess.run(["fish", "--no-execute", "-c", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
