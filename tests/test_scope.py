"""`whykit scope suggest` and source drift in `whykit affected`."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import fill_decision, fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.review import approve_decision  # noqa: E402
from whykit.scope import patterns_for  # noqa: E402

SCOPE_SCHEMA = json.loads((ROOT / "schemas" / "scope-suggestions.schema.json").read_text(encoding="utf-8"))
AFFECTED_SCHEMA = json.loads((ROOT / "schemas" / "affected-report.schema.json").read_text(encoding="utf-8"))


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class PatternTests(unittest.TestCase):
    def test_grouping(self) -> None:
        files = {"src/payments/a.py": {"c1", "c2"}, "src/payments/b.py": {"c2"}, "Dockerfile": {"c3"},
                 "docs/x/y/z.md": {"c1"}}
        suggestions = patterns_for(files)
        self.assertEqual([s["pattern"] for s in suggestions], ["src/payments/**", "Dockerfile", "docs/x/y/z.md"])
        self.assertEqual(suggestions[0]["commits"], 2)


class TermCandidateTests(unittest.TestCase):
    def test_only_code_looking_names_are_proposed(self) -> None:
        from whykit.scope import rejected_alternatives, term_candidates
        record = (
            "## Alternatives considered\n\n| Alternative | Upside | Risk | Why rejected |\n|---|---|---|---|\n"
            "| Option 1: Fuse.js | | | |\n| Use `moment` | | | |\n| date-fns | | | |\n"
            "| Monolith | | | |\n| MIT | | | |\n| Include in adr, 72 stars as of 2018-06-14 | | | |\n"
            "| See `0001-use-markdown.md` and <https://example.com/a#file-name-conventions> | | | |\n"
            "\n## Consequences\n\n| Not | an | alternative | row |\n"
        )
        names = rejected_alternatives(record)
        self.assertEqual(len(names), 7)
        self.assertEqual([c["term"] for c in term_candidates(names)], ["Fuse.js", "moment", "date-fns"])


@unittest.skipIf(shutil.which("git") is None, "git is required")
class GitScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "shop"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        self.git("init", "-q", "-b", "main")
        self.write("src/web/home.html", "a\n")
        self.commit("init")
        self.vault = self.repo / "decisions"
        fresh_vault(self.vault)
        root = str(self.vault)
        call("new", "--root", root, "evidence", "--source", "Load test", "--type", "dataset",
             "--location", "bench/load.csv", "--claims", "Fast enough")
        call("new", "--root", root, "decision", "Store money as cents", "--owner", "Payments", "--status", "draft",
             "--source", "E-001")
        fill_decision(next((self.vault / "06-decisions").glob("d-001-*.md")))
        plan = approve_decision(self.vault, "D-001", reviewer="Payments")
        approve_decision(self.vault, "D-001", reviewer="Payments", write=True, expected_sha256=plan["expected_sha256"])
        self.write("src/payments/ledger.py", "x\n")
        self.write("src/payments/refunds.py", "y\n")
        self.write("bench/load.csv", "1\n")
        self.commit("Payments ledger in cents (D-001)")
        self.write("src/payments/ledger.py", "x\nz\n")
        self.write("uv.lock", "lock\n")
        self.commit("Fix rounding, see D-001")
        self.write("src/web/home.html", "a\nb\n")
        self.commit("Home tweak, unrelated to D-0010 or XD-001")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, env=self.env)

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def suggest(self, *extra: str) -> tuple[int, dict]:
        code, out, _ = call("scope", "suggest", "--root", str(self.vault), "--repo", str(self.repo), "--json", *extra)
        report = json.loads(out)
        if "error" not in report:
            self.assertEqual(validate(report, SCOPE_SCHEMA), [], report)
        return code, report

    def test_suggests_from_commits_that_name_or_change_the_decision(self) -> None:
        code, report = self.suggest()
        self.assertEqual(code, 0)
        item = report["decisions"][0]
        self.assertEqual(item["decision_id"], "D-001")
        patterns = [s["pattern"] for s in item["suggestions"]]
        # Lock files are noise; the unrelated commit (D-0010, XD-001) is not matched.
        self.assertEqual(patterns, ["src/payments/**", "bench/load.csv"])
        self.assertEqual(item["suggestions"][0]["commits"], 2)
        self.assertNotIn("src/web/home.html", json.dumps(report))
        self.assertEqual(report["written"], [])

    def test_write_fills_only_unscoped_decisions_and_keeps_history_valid(self) -> None:
        code, report = self.suggest("--write", "--min-commits", "2")
        self.assertEqual((code, report["written"]), (0, ["D-001"]))
        record = next((self.vault / "06-decisions").glob("d-001-*.md")).read_text(encoding="utf-8")
        self.assertIn('applies_to: ["src/payments/**"]\n', record)
        # Writing a scope on an approved decision passes the history gate.
        self.commit("Scope D-001")
        code, out, err = call("history", "--root", str(self.vault), "--base", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        # A second run does not touch a decision that now has a scope.
        _, again = self.suggest("--write")
        self.assertEqual(again["written"], [])
        self.assertEqual(again["decisions"], [])

    def test_wide_commits_are_ignored(self) -> None:
        for index in range(30):
            self.write(f"generated/file{index}.ts", "x\n")
        self.commit("Reformat everything (mentions D-001)")
        _, report = self.suggest()
        item = report["decisions"][0]
        self.assertEqual(item["skipped_wide_commits"], 1)
        self.assertNotIn("generated/**", [s["pattern"] for s in item["suggestions"]])
        _, wide = self.suggest("--max-files", "100")
        self.assertIn("generated/**", [s["pattern"] for s in wide["decisions"][0]["suggestions"]])

    def test_unknown_decision_is_not_found(self) -> None:
        code, report = self.suggest("D-999")
        self.assertEqual((code, report["error"]["code"]), (1, "not_found"))

    def test_a_changed_repository_source_flags_the_decision_citing_it(self) -> None:
        # D-001 has no scope, but it cites E-001, whose location is bench/load.csv.
        self.write("bench/load.csv", "1\n2\n")
        code, out, _ = call("affected", "--root", str(self.vault), "--repo", str(self.repo), "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(validate(report, AFFECTED_SCHEMA), [], report)
        decision = report["decisions"][0]
        self.assertEqual(decision["decision_id"], "D-001")
        self.assertEqual(decision["matched_sources"], [{"evidence_id": "E-001", "files": ["bench/load.csv"]}])
        self.assertIn("cited_source_changed", decision["problems"])
        self.assertIn("E-001 (bench/load.csv)", decision["reasons"][0])
        self.assertEqual(report["uncovered_files"], [])

    def test_mcp_matches_sources_only_when_the_register_is_visible(self) -> None:
        from whykit.mcp_server import VaultTools

        payload, _ = VaultTools(self.vault, max_sensitivity="internal").call("affected", {"files": ["bench/load.csv"]})
        self.assertEqual([d["decision_id"] for d in payload["decisions"]], ["D-001"])
        payload, _ = VaultTools(self.vault, max_sensitivity="public").call("affected", {"files": ["bench/load.csv"]})
        self.assertEqual(payload["decisions"], [])
        self.assertEqual(payload["uncovered_files"], ["bench/load.csv"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(shutil.which("git") is None, "git is required")
class ScopeCheckTests(GitScenarioTests):
    CHECK_SCHEMA = json.loads((ROOT / "schemas" / "scope-check.schema.json").read_text(encoding="utf-8"))

    def scope_record(self, line: str) -> None:
        record = next((self.vault / "06-decisions").glob("d-001-*.md"))
        record.write_text(record.read_text(encoding="utf-8").replace("tags: []\n", f"tags: []\n{line}\n", 1), encoding="utf-8")

    def check(self, *extra: str) -> tuple[int, dict]:
        code, out, _ = call("scope", "check", "--root", str(self.vault), "--repo", str(self.repo), "--json", *extra)
        report = json.loads(out)
        self.assertEqual(validate(report, self.CHECK_SCHEMA), [], report)
        return code, report

    def test_unscoped_decision_is_reported(self) -> None:
        code, report = self.check()
        self.assertEqual(code, 0)
        self.assertEqual([f["kind"] for f in report["decisions"][0]["findings"]], ["unscoped"])

    def test_dead_broad_and_unseen(self) -> None:
        self.scope_record('applies_to: ["src/payments/**", "src/billing/**", "src/**"]\nwatch_terms: ["ledger", "cross-fech"]')
        self.write("src/payments/ledger.py", "def ledger():\n    pass\n")
        self.commit("touch")
        code, report = self.check()
        findings = {(f["kind"], f["value"]) for f in report["decisions"][0]["findings"]}
        self.assertEqual(findings, {("dead_pattern", "src/billing/**"), ("broad_pattern", "src/**"),
                                    ("unseen_term", "cross-fech")})
        self.assertEqual(report["problems"], 1)
        self.assertEqual(code, 0)
        code, _ = self.check("--strict")
        self.assertEqual(code, 1)
