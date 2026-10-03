"""Regressions for friction a new user hits following the README literally."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _vaults import fresh_vault

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"

sys.path.insert(0, str(ROOT / "src"))

from whykit import cli  # noqa: E402
from whykit.cli import build_parser  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True,
    )


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


class VaultTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.vault = Path(self._td.name) / "ledger"
        fresh_vault(self.vault)

    def ok(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = run(*args, "--root", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def codes(self) -> list[str]:
        report = json.loads(run("lint", "--root", str(self.vault), "--json").stdout)
        return [item["code"] for item in report["findings"]]

    def add_evidence(self, name: str) -> None:
        self.ok(
            "new", "evidence", "--source", name, "--type", "dataset",
            "--location", "https://example.com/export.csv", "--claims", "a claim",
        )


class RootAfterActionTests(unittest.TestCase):
    LEAVES = (
        ["new", "decision", "Title"],
        ["new", "evidence", "--source", "s", "--type", "t", "--location", "l", "--claims", "c"],
        ["new", "note", "Title", "--workstream", "notes"],
        ["review", "list"],
        ["review", "record", "D-001", "--reviewer", "R"],
        ["evidence", "list"],
        ["evidence", "retire", "E-001", "--why", "w"],
    )

    def test_root_is_accepted_after_the_action(self) -> None:
        for argv in self.LEAVES:
            with self.subTest(argv=argv):
                args = build_parser().parse_args([*argv, "--root", "vault"])
                self.assertEqual(args.root, "vault")

    def test_root_before_the_action_still_works_and_is_not_clobbered(self) -> None:
        for argv in self.LEAVES:
            with self.subTest(argv=argv):
                args = build_parser().parse_args([argv[0], "--root", "vault", *argv[1:]])
                self.assertEqual(args.root, "vault")
                args = build_parser().parse_args(argv)
                self.assertIsNone(args.root)

    def test_quickstart_with_root_last_creates_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "ledger"
            fresh_vault(vault)
            result = run(
                "new", "evidence", "--source", "Export", "--type", "dataset",
                "--location", "https://example.com/x.csv", "--claims", "c", "--root", str(vault),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("review", "list", "--root", str(vault))
            self.assertEqual(result.returncode, 0, result.stderr)


class RetiredEvidenceOnHistoryTests(VaultTestCase):
    def supersede_and_retire(self) -> subprocess.CompletedProcess[str]:
        self.add_evidence("Q3 export")
        self.add_evidence("Q4 export")
        self.ok("new", "decision", "First", "--owner", "Ops", "--status", "approved", "--source", "E-001")
        self.ok(
            "new", "decision", "Second", "--owner", "Ops", "--status", "approved",
            "--source", "E-002", "--supersedes", "D-001",
        )
        return self.ok("evidence", "retire", "E-001", "--why", "replaced", "--replaced-by", "E-002")

    def test_supersede_then_retire_leaves_no_unfixable_warning(self) -> None:
        # The superseded record may not be edited, so a warning on it could
        # never be cleared and would keep a strict gate red forever.
        self.supersede_and_retire()
        self.assertNotIn("evidence.retired", self.codes())

    def test_retire_reports_current_and_historical_references(self) -> None:
        result = self.supersede_and_retire()
        self.assertIn("0 current and 1 historical reference(s)", result.stdout)
        self.assertNotIn("whykit impact", result.stdout)

    def test_live_record_citing_retired_evidence_is_still_flagged(self) -> None:
        self.add_evidence("Q3 export")
        self.add_evidence("Q4 export")
        self.ok("new", "decision", "Live", "--owner", "Ops", "--status", "approved", "--source", "E-001")
        result = self.ok("evidence", "retire", "E-001", "--why", "replaced", "--replaced-by", "E-002")
        self.assertIn("1 current and 0 historical reference(s)", result.stdout)
        self.assertIn("whykit impact E-001", result.stdout)
        self.assertIn("evidence.retired", self.codes())

    def test_archived_record_is_history_too(self) -> None:
        self.add_evidence("Q3 export")
        self.ok("new", "decision", "Old", "--owner", "Ops", "--status", "archived", "--source", "E-001")
        self.ok("evidence", "retire", "E-001", "--why", "gone")
        self.assertNotIn("evidence.retired", self.codes())


class PackDeduplicationTests(VaultTestCase):
    def test_one_record_named_two_ways_is_packed_once(self) -> None:
        self.add_evidence("Export")
        self.ok("new", "decision", "Ship audit logs", "--owner", "Ops", "--source", "E-001")
        path = "06-decisions/d-001-ship-audit-logs.md"
        report = json.loads(self.ok("pack", "D-001", path, "--query", "audit logs").stdout)
        paths = [item["record"]["path"] for item in report["contexts"] if item.get("record")]
        self.assertEqual(paths.count(path), 1, paths)
        self.assertEqual(report["resolved"], len(report["contexts"]))
        self.assertEqual(report["selected"][0], "D-001")
        self.assertNotIn(path, report["selected"])
        self.assertEqual(
            report["budget"]["used_chars"],
            sum(len(item.get("content") or "") for item in report["contexts"]),
        )


class StatusDecisionCountTests(VaultTestCase):
    def test_template_and_log_are_not_counted_as_decisions(self) -> None:
        self.add_evidence("Export")
        self.ok("new", "decision", "Only one", "--owner", "Ops", "--status", "approved", "--source", "E-001")
        report = json.loads(self.ok("status", "--json").stdout)
        self.assertEqual(report["decisions"], 1)
        self.assertEqual(report["decision_states"], {"approved": 1})
        trace = json.loads(self.ok("trace", "--json").stdout)
        self.assertEqual(report["decisions"], len(trace["decisions"]))


class QueryTypeExcludesTemplatesTests(VaultTestCase):
    def test_type_filter_lists_records_not_their_template(self) -> None:
        self.add_evidence("Export")
        self.ok("new", "decision", "Only one", "--owner", "Ops", "--status", "approved", "--source", "E-001")
        report = json.loads(self.ok("query", "--type", "decision", "--json").stdout)
        paths = [item["path"] for item in report["results"]]
        self.assertTrue(any(path.startswith("06-decisions/d-001-") for path in paths), paths)
        self.assertFalse(any(path.startswith("templates/") for path in paths), paths)

    def test_status_template_still_finds_the_template(self) -> None:
        report = json.loads(self.ok("query", "--type", "decision", "--status", "template", "--json").stdout)
        paths = [item["path"] for item in report["results"]]
        self.assertEqual(paths, ["templates/decision-record-template.md"])


class ExplorerVaultNameTests(unittest.TestCase):
    def name_of(self, *flags: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "ledger"
            fresh_vault(vault, *flags)
            result = run("explorer-index", "--root", str(vault))
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["vaultName"]

    def test_fresh_vault_is_not_named_readme(self) -> None:
        for flags in ((), ("--full",), ("--minimal",)):
            with self.subTest(flags=flags):
                name = self.name_of(*flags)
                self.assertNotEqual(name.casefold(), "readme")
                self.assertEqual(name, "Company knowledge vault")

    def test_placeholder_title_falls_back_to_the_heading(self) -> None:
        from whykit.explorer_index import _vault_name
        from whykit.vault_index import VaultIndex

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "acme-ledger"
            fresh_vault(vault)
            readme = vault / "README.md"
            text = readme.read_text(encoding="utf-8")
            readme.write_text(text.replace("title: Company knowledge vault", "title: README"), encoding="utf-8")
            self.assertEqual(_vault_name(vault.resolve(), VaultIndex.load(vault)), "Company knowledge vault")
            readme.write_text(text.replace("# Company knowledge vault", "# README").replace("title: Company knowledge vault", "title: README"), encoding="utf-8")
            self.assertEqual(_vault_name(vault.resolve(), VaultIndex.load(vault)), "acme-ledger")


class CheckOutputTests(VaultTestCase):
    def test_failing_gate_names_the_findings(self) -> None:
        result = run("check", "--root", str(self.vault), "--profile", "ci")
        self.assertEqual(result.returncode, 1)
        self.assertIn("findings that fail this gate:", result.stdout)
        self.assertIn("[agents.unconfigured]", result.stdout)
        self.assertIn("AGENTS.md:", result.stdout)
        self.assertIn("warnings fail it", result.stdout)

    def test_passing_lint_prints_no_findings_block(self) -> None:
        result = run("check", "--root", str(self.vault), "--profile", "local")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("findings that fail", result.stdout)

    def test_blocked_history_lists_the_rewritten_record(self) -> None:
        self.add_evidence("Export")
        self.ok("new", "decision", "Kept", "--owner", "Ops", "--status", "approved", "--source", "E-001")
        git("init", "-q", cwd=self.vault)
        git("add", "-A", cwd=self.vault)
        git("commit", "-qm", "base", cwd=self.vault)
        record = self.vault / "06-decisions" / "d-001-kept.md"
        record.write_text(
            record.read_text(encoding="utf-8").replace("State the choice in one sentence.", "Rewritten."),
            encoding="utf-8",
        )
        git("commit", "-qam", "rewrite", cwd=self.vault)
        result = run("check", "--root", str(self.vault), "--profile", "local", "--base", "HEAD~1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("06-decisions/d-001-kept.md", result.stdout)


class HistoryUncommittedTests(VaultTestCase):
    def setUp(self) -> None:
        super().setUp()
        git("init", "-q", cwd=self.vault)
        git("add", "-A", cwd=self.vault)
        git("commit", "-qm", "base", cwd=self.vault)

    def test_uncommitted_edit_is_called_out(self) -> None:
        (self.vault / "Home.md").write_text("changed\n", encoding="utf-8")
        result = run("history", "--root", str(self.vault), "--base", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uncommitted Markdown change(s) were not checked", result.stderr)
        self.assertIn("Home.md", result.stderr)

    def test_clean_tree_has_no_note(self) -> None:
        result = run("history", "--root", str(self.vault), "--base", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("uncommitted", result.stderr)

    def test_explicit_head_has_no_note(self) -> None:
        (self.vault / "Home.md").write_text("changed\n", encoding="utf-8")
        result = run("history", "--root", str(self.vault), "--base", "HEAD", "--head", "HEAD")
        # Same ref spelled explicitly is still the default; a different ref is
        # a deliberate commit-to-commit comparison and stays quiet.
        self.assertIn("uncommitted", result.stderr)
        git("commit", "-qam", "next", cwd=self.vault)
        (self.vault / "Home.md").write_text("again\n", encoding="utf-8")
        result = run("history", "--root", str(self.vault), "--base", "HEAD~1", "--head", "HEAD~0")
        self.assertNotIn("uncommitted", result.stderr)


class InitNextStepTests(unittest.TestCase):
    def _next_step(self, source_root: Path) -> str:
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            with mock.patch.object(cli, "SOURCE_ROOT", source_root), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["init", os.path.join(td, "v")]), 0)
            return next(line for line in out.getvalue().splitlines() if line.strip().startswith("3."))

    def test_installed_tool_is_told_the_bare_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            line = self._next_step(Path(td))
        self.assertIn("whykit lint --root", line)
        self.assertNotIn("uv run", line)

    def test_checkout_is_told_uv_run(self) -> None:
        self.assertIn("uv run whykit lint --root", self._next_step(ROOT))


if __name__ == "__main__":
    unittest.main()
