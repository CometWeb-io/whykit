"""`whykit affected`: decision scopes, change mapping and review signals."""
from __future__ import annotations

import contextlib
import io
import datetime
import json
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
from whykit.affected import declared_scope, glob_match, pattern_problem  # noqa: E402
from whykit.immutability import allowed_lifecycle_change, approval_record_hash  # noqa: E402
from whykit.review import approve_decision  # noqa: E402

SCHEMA = json.loads((ROOT / "schemas" / "affected-report.schema.json").read_text(encoding="utf-8"))
# Records written by `whykit new` carry the real date, so "today" must not
# lag behind the clock or every fresh record looks future-dated.
TODAY = datetime.date.today().isoformat()


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class GlobTests(unittest.TestCase):
    def test_matching(self) -> None:
        cases = [
            ("src/payments/**", "src/payments/ledger.py", True),
            ("src/payments/**", "src/payments/deep/x/y.py", True),
            ("src/payments/**", "src/paymentsx/ledger.py", False),
            ("src/payments/", "src/payments/a.py", True),
            ("src/*.py", "src/a.py", True),
            ("src/*.py", "src/sub/a.py", False),
            ("**/*.sql", "db/migrations/001.sql", True),
            ("**/*.sql", "001.sql", True),
            ("Dockerfile", "Dockerfile", True),
            ("Dockerfile", "app/Dockerfile", False),
            ("src/?.py", "src/a.py", True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(glob_match(pattern, path), expected)

    def test_invalid_patterns(self) -> None:
        for pattern in ("", " src/**", "/etc/**", "C:/x", "../up/**", "src/../x", "src\\x", 3):
            with self.subTest(pattern=pattern):
                self.assertIsNotNone(pattern_problem(pattern))
        self.assertIsNone(pattern_problem("src/**"))

    def test_declared_scope_accepts_a_string_or_a_list(self) -> None:
        self.assertEqual(declared_scope({"applies_to": "src/**"}), (["src/**"], []))
        valid, invalid = declared_scope({"applies_to": ["src/**", "../x"]})
        self.assertEqual(valid, ["src/**"])
        self.assertEqual(invalid[0]["pattern"], "../x")
        self.assertEqual(declared_scope({}), ([], []))


class ScopeIsRoutingMetadataTests(unittest.TestCase):
    BASE = "---\ntitle: X\nstatus: approved\nreview_by: 2027-01-01\napplies_to: [\"src/a/**\"]\n---\n# Body\n"

    def test_approved_decision_may_change_its_scope_only(self) -> None:
        moved = self.BASE.replace('["src/a/**"]', '["src/b/**", "lib/**"]')
        self.assertTrue(allowed_lifecycle_change(self.BASE, moved))
        block = self.BASE.replace('applies_to: ["src/a/**"]\n', "applies_to:\n  - src/b/**\n  - lib/**\n")
        self.assertTrue(allowed_lifecycle_change(self.BASE, block))
        self.assertFalse(allowed_lifecycle_change(self.BASE, moved.replace("# Body", "# Changed body")))

    def test_scope_does_not_change_the_approval_hash(self) -> None:
        without = self.BASE.replace('applies_to: ["src/a/**"]\n', "")
        self.assertEqual(approval_record_hash(self.BASE), approval_record_hash(without))

    def test_indented_lines_under_scalar_keys_are_still_compared(self) -> None:
        sneaky = self.BASE.replace("review_by: 2027-01-01\n", "review_by: 2027-01-01\n  hidden: change\n")
        self.assertFalse(allowed_lifecycle_change(self.BASE, sneaky))


class AffectedCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "shop"
        self.repo.mkdir()
        self.vault = self.repo / "decisions"
        fresh_vault(self.vault)
        root = str(self.vault)
        self.assertEqual(call("new", "--root", root, "evidence", "--source", "Audit checklist", "--type", "document",
                              "--location", "https://example.com/audit.pdf", "--claims", "Rounding must be exact")[0], 0)
        self.assertEqual(call("new", "--root", root, "decision", "Store money as integer cents", "--owner", "Payments",
                              "--status", "draft", "--source", "E-001", "--applies-to", "src/payments/**")[0], 0)
        self.assertEqual(call("new", "--root", root, "decision", "Server-render the storefront", "--owner", "Web",
                              "--status", "draft", "--source", "E-001")[0], 0)
        for did in ("D-001", "D-002"):
            fill_decision(next((self.vault / "06-decisions").glob(f"d-{did[2:]}-*.md")))
            plan = approve_decision(self.vault, did, reviewer="Team")
            approve_decision(self.vault, did, reviewer="Team", write=True, expected_sha256=plan["expected_sha256"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def affected(self, *extra: str) -> tuple[int, dict]:
        code, out, err = call("affected", "--root", str(self.vault), "--repo", str(self.repo),
                              "--today", TODAY, "--json", *extra)
        report = json.loads(out)
        if "error" not in report:
            self.assertEqual(validate(report, SCHEMA), [], report)
        return code, report

    def test_new_decision_writes_the_scope(self) -> None:
        text = next((self.vault / "06-decisions").glob("d-001-*.md")).read_text(encoding="utf-8")
        self.assertIn('applies_to: ["src/payments/**"]', text)
        code, out, _ = call("new", "--root", str(self.vault), "decision", "Bad", "--applies-to", "../x", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "operation_rejected"))

    def test_planned_files_map_to_decisions_and_report_gaps(self) -> None:
        code, report = self.affected("--files", "src/payments/ledger.py", "src/web/home.html")
        self.assertEqual(code, 0)
        self.assertEqual([d["decision_id"] for d in report["decisions"]], ["D-001"])
        self.assertEqual(report["decisions"][0]["matched_files"], ["src/payments/ledger.py"])
        self.assertFalse(report["decisions"][0]["review_signal"])
        self.assertEqual(report["uncovered_files"], ["src/web/home.html"])
        self.assertEqual(report["unscoped_decisions"], ["D-002"])
        self.assertFalse(report["semantics_checked"])
        # A configuration gap is a caveat, not a review signal on every decision.
        self.assertIn("evidence_age_unbounded", report["caveats"])

    def test_retired_source_is_a_review_signal_and_strict_fails(self) -> None:
        self.assertEqual(call("evidence", "--root", str(self.vault), "retire", "E-001", "--why", "Replaced")[0], 0)
        code, report = self.affected("--files", "src/payments/ledger.py")
        self.assertEqual(code, 0)
        self.assertEqual(report["decisions"][0]["problems"], ["retired_evidence"])
        self.assertEqual(report["decisions"][0]["reasons"], ["a cited source was retired"])
        self.assertTrue(report["review_required"])
        code, _ = self.affected("--files", "src/payments/ledger.py", "--strict")
        self.assertEqual(code, 1)

    def test_overdue_review_is_a_review_signal(self) -> None:
        _, report = self.affected("--files", "src/payments/a.py", "--today", "2030-01-01")
        self.assertIn("review_overdue", report["decisions"][0]["problems"])

    def test_superseded_decisions_do_not_apply(self) -> None:
        root = str(self.vault)
        self.assertEqual(call("new", "--root", root, "decision", "Use a money library", "--owner", "Payments",
                              "--status", "draft", "--source", "E-001", "--supersedes", "D-001",
                              "--applies-to", "src/payments/**")[0], 0)
        fill_decision(next((self.vault / "06-decisions").glob("d-003-*.md")))
        plan = approve_decision(self.vault, "D-003", reviewer="Team")
        approve_decision(self.vault, "D-003", reviewer="Team", write=True, expected_sha256=plan["expected_sha256"])
        _, report = self.affected("--files", "src/payments/ledger.py")
        self.assertEqual([d["decision_id"] for d in report["decisions"]], ["D-003"])

    def test_sensitivity_ceiling_withholds_decisions(self) -> None:
        _, report = self.affected("--files", "src/payments/ledger.py", "--max-sensitivity", "public")
        self.assertEqual((report["decisions"], report["withheld_decisions"]), ([], 1))
        self.assertEqual(report["summary"]["applicable_decisions"], 1)

    def test_markdown_output_never_claims_the_change_is_safe(self) -> None:
        code, out, _ = call("affected", "--root", str(self.vault), "--repo", str(self.repo), "--today", TODAY,
                            "--files", "src/payments/ledger.py", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("| **D-001** Store money as integer cents | `src/payments/ledger.py` |", out)
        self.assertIn("does not judge whether this change is consistent", out)
        self.assertNotIn("safe", out.casefold())

    def test_bad_input_is_reported(self) -> None:
        code, out, _ = call("affected", "--root", str(self.vault), "--repo", str(self.repo), "--json",
                            "--files", "../outside.py")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "invalid_argument"))
        code, out, _ = call("affected", "--root", str(self.vault), "--repo", str(self.repo), "--json",
                            "--files", "a.py", "--base", "HEAD")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_git_changes_exclude_the_vault_inside_the_repository(self) -> None:
        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                           check=True, capture_output=True)
        (self.repo / "src" / "payments").mkdir(parents=True)
        (self.repo / "src" / "payments" / "ledger.py").write_text("x = 1\n", encoding="utf-8")
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "init")
        (self.repo / "src" / "payments" / "ledger.py").write_text("x = 2\n", encoding="utf-8")
        (self.repo / "src" / "payments" / "refunds.py").write_text("y = 1\n", encoding="utf-8")
        (self.vault / "Home.md").write_text((self.vault / "Home.md").read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, report = self.affected()
        self.assertEqual(code, 0)
        self.assertEqual(report["source"], {"kind": "git", "base": "HEAD"})
        self.assertEqual(report["changed_files"], ["src/payments/ledger.py", "src/payments/refunds.py"])
        self.assertEqual(report["decisions"][0]["matched_files"], ["src/payments/ledger.py", "src/payments/refunds.py"])
        code, out, _ = call("affected", "--root", str(self.vault), "--repo", str(self.repo), "--json", "--base", "no-such-ref")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "git_error"))


class AdrModeTests(unittest.TestCase):
    """Zero-migration: plain ADR files, no vault."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        for path in ("src/payments/ledger.py", "src/web/home.html"):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text("x\n", encoding="utf-8")
        adr = self.repo / "docs" / "decisions"
        adr.mkdir(parents=True)
        (adr / "0001-integer-cents.md").write_text(
            "---\nstatus: accepted\ndeciders: Payments\n---\n# Use integer cents\n\n"
            "## Context and Problem Statement\n\nRounding in `src/payments/` and `src/billing/rounding.py`.\n\n"
            "## Decision Outcome\n\nInteger cents.\n", encoding="utf-8")
        (adr / "0002-server-render.md").write_text(
            "# 2. Server-render the home page\n\n## Status\n\nSuperseded by 0003\n\n## Context\n\n"
            "src/web/home.html must work without scripts.\n\n## Decision\n\nServer rendering.\n", encoding="utf-8")
        (adr / "0003-cache.md").write_text(
            "# 3. Cache the home page\n\n## Status\n\nAccepted\n\n## Context\n\nThe page src/web/home.html is static; "
            "see https://example.com/cdn/docs/caching.\n\n## Decision\n\nCache at the edge.\n", encoding="utf-8")
        (adr / "0004-declared.md").write_text(
            "---\nstatus: accepted\napplies_to: [\"src/web/**\"]\nsensitivity: confidential\n---\n# 4. Web owns the design system\n\n"
            "## Context\n\nx\n\n## Decision\n\ny\n", encoding="utf-8")
        (adr / "README.md").write_text("# Decisions\n\nIndex of src/web/ decisions.\n", encoding="utf-8")
        (adr / "template.md").write_text("# Title\n\n## Status\n\nAccepted\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_adr(self, *extra: str) -> tuple[int, dict]:
        code, out, _ = call("affected", "--adr", "docs/decisions", "--repo", str(self.repo), "--today", TODAY,
                            "--json", *extra)
        report = json.loads(out)
        if "error" not in report:
            self.assertEqual(validate(report, SCHEMA), [], report)
        return code, report

    def test_live_adrs_map_by_declared_or_inferred_scope(self) -> None:
        code, report = self.run_adr("--files", "src/payments/ledger.py", "src/web/home.html", "README.md")
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "adr")
        ids = {d["decision_id"]: d for d in report["decisions"]}
        # The superseded ADR, the index and the template are not decisions in force.
        self.assertEqual(set(ids), {"adr:0001-integer-cents.md", "adr:0003-cache.md", "adr:0004-declared.md"})
        self.assertEqual(ids["adr:0001-integer-cents.md"]["matched_patterns"], ["src/payments/**"])
        self.assertEqual(ids["adr:0001-integer-cents.md"]["scope_source"], "inferred")
        self.assertEqual(ids["adr:0004-declared.md"]["scope_source"], "declared")
        self.assertEqual(report["uncovered_files"], ["README.md"])
        self.assertEqual(report["caveats"], ["adr_mode", "inferred_scope"])
        self.assertEqual(report["adr"], {"files": 4, "live": 3, "status_unknown": 0, "unreadable": 0})

    def test_moved_code_is_a_review_signal_and_urls_are_ignored(self) -> None:
        _, report = self.run_adr("--files", "src/payments/ledger.py", "src/web/home.html")
        ids = {d["decision_id"]: d for d in report["decisions"]}
        cents = ids["adr:0001-integer-cents.md"]
        self.assertEqual((cents["problems"], cents["missing_paths"]), (["missing_referenced_path"], ["src/billing/rounding.py"]))
        self.assertIn("src/billing/rounding.py", cents["reasons"][0])
        self.assertEqual(ids["adr:0003-cache.md"]["problems"], [])

    def test_sensitivity_ceiling_treats_unlabelled_adrs_as_internal(self) -> None:
        _, report = self.run_adr("--files", "src/web/home.html", "--max-sensitivity", "internal")
        self.assertEqual([d["decision_id"] for d in report["decisions"]], ["adr:0003-cache.md"])
        self.assertEqual(report["withheld_decisions"], 1)
        _, report = self.run_adr("--files", "src/web/home.html", "--max-sensitivity", "public")
        self.assertEqual((report["decisions"], report["withheld_decisions"]), ([], 2))

    def test_missing_vault_points_at_the_adr_folder(self) -> None:
        code, out, err = call("affected", "--repo", str(self.repo), "--files", "a.py")
        self.assertEqual(code, 2)
        self.assertIn("whykit affected --adr docs/decisions", err)
        code, out, _ = call("affected", "--adr", "nope", "--repo", str(self.repo), "--json", "--files", "a.py")
        self.assertEqual(json.loads(out)["error"]["code"], "invalid_target")


class McpAffectedToolTests(unittest.TestCase):
    """The MCP `affected` tool follows the server's sensitivity rules."""

    def setUp(self) -> None:
        from whykit.mcp_server import VaultTools

        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        fresh_vault(self.vault)
        root = str(self.vault)
        call("new", "--root", root, "evidence", "--source", "Audit", "--type", "document",
             "--location", "https://example.com/a.pdf", "--claims", "Exact rounding")
        for title, label in (("Public cents rule", "public"), ("Confidential pricing rule", "confidential")):
            self.assertEqual(call("new", "--root", root, "decision", title, "--owner", "Team", "--status", "draft",
                                  "--source", "E-001", "--sensitivity", label, "--applies-to", "src/**")[0], 0)
        for did in ("D-001", "D-002"):
            fill_decision(next((self.vault / "06-decisions").glob(f"d-{did[2:]}-*.md")))
            plan = approve_decision(self.vault, did, reviewer="Team")
            approve_decision(self.vault, did, reviewer="Team", write=True, expected_sha256=plan["expected_sha256"])
        call("evidence", "--root", root, "retire", "E-001", "--why", "Replaced")
        self.tools = lambda level: VaultTools(self.vault, max_sensitivity=level)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ceiling_hides_decisions_and_register_details(self) -> None:
        payload, is_error = self.tools("public").call("affected", {"files": ["src/a.py"], "today": TODAY})
        self.assertFalse(is_error, payload)
        self.assertEqual([d["decision_id"] for d in payload["decisions"]], ["D-001"])
        # Hidden decisions are absent, not counted.
        self.assertEqual((payload["withheld_decisions"], payload["summary"]["applicable_decisions"]), (0, 1))
        # The register is internal: its state is not reported under a public ceiling.
        self.assertEqual(payload["evidence_details"], "withheld")
        self.assertNotIn("retired_evidence", payload["decisions"][0]["problems"])
        text = json.dumps(payload)
        self.assertNotIn(str(self.vault), text)
        self.assertNotIn("Confidential pricing rule", text)
        self.assertNotIn("root", payload)

    def test_higher_ceiling_shows_both_with_signals(self) -> None:
        payload, _ = self.tools("confidential").call("affected", {"files": ["src/a.py"]})
        self.assertEqual([d["decision_id"] for d in payload["decisions"]], ["D-001", "D-002"])
        self.assertTrue(all("retired_evidence" in d["problems"] for d in payload["decisions"]))
        self.assertNotIn("evidence_details", payload)

    def test_invalid_files_are_refused(self) -> None:
        tools = self.tools("internal")
        for files in ([], ["../x"], ["/etc/passwd"], ["src/*.py"], [7], ["a"] * 201, "src/a.py"):
            with self.subTest(files=files):
                payload, is_error = tools.call("affected", {"files": files})
                self.assertTrue(is_error)
                self.assertEqual(payload["error"]["code"], "invalid_argument")


class FieldFindingsTests(unittest.TestCase):
    """Regression tests for what replaying real ADR repositories showed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        for path in ("src/app.ts", "packages/a/index.ts"):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text("x\n", encoding="utf-8")
        self.adr = self.repo / "docs" / "adr"
        self.adr.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def adr_file(self, name: str, text: str) -> None:
        (self.adr / name).write_text(text, encoding="utf-8")

    def report(self, paths: list[str], diff: dict | None = None) -> dict:
        import datetime as dt
        from whykit.affected import build_affected_adr
        report = build_affected_adr(self.adr, self.repo, paths, {"kind": "files", "base": None},
                                    today=dt.date(2026, 11, 12), diff=diff)
        self.assertEqual(validate(report, SCHEMA), [], report)
        return report

    def test_prose_slashes_are_not_paths_and_unanchored_paths_are_not_moved_code(self) -> None:
        from whykit.affected import _inferred_scope, referenced_paths
        mentioned = referenced_paths("Use dd/mm/yyyy, CI/CD and/or backstage.io/v1alpha1; see components/index.ts "
                                     "and src/gone/old.ts and packages/a.")
        patterns, missing = _inferred_scope(self.repo, mentioned)
        self.assertEqual(patterns, ["packages/a/**"])
        self.assertEqual(missing, ["src/gone/old.ts"])

    def test_broad_scopes_are_marked_and_listed_last(self) -> None:
        self.adr_file("0001-structure.md", "---\nstatus: accepted\napplies_to: [\"packages/**\"]\n---\n# 1. Package structure\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        self.adr_file("0002-app.md", "---\nstatus: accepted\napplies_to: [\"packages/a/**\"]\n---\n# 2. App rule\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        report = self.report(["packages/a/index.ts"])
        broad = {d["decision_id"]: d["broad"] for d in report["decisions"]}
        self.assertEqual(broad, {"adr:0001-structure.md": True, "adr:0002-app.md": False})
        from whykit.affected import render_markdown, render_text
        self.assertIn("(1 broad)", render_text(report))
        markdown = render_markdown(report)
        self.assertLess(markdown.index("App rule"), markdown.index("Package structure"))
        self.assertIn("applies broadly", markdown)

    def test_watch_terms_match_code_lines_only(self) -> None:
        self.adr_file("0003-luxon.md", "---\nstatus: accepted\nwatch_terms: [\"moment\", \"export default\"]\n---\n# 3. Use Luxon\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        noise = {
            "src/app.ts": ["  // at the same moment the holder", "# moment", "const momentum = 1"],
            "yarn.lock": ['    moment: "npm:^2"'],
            "docs/guide.md": ["export default app.createRoot();"],
            "CHANGELOG.md": ["moment"],
        }
        report = self.report(list(noise), noise)
        self.assertEqual(report["decisions"], [])
        real = {"src/app.ts": ['import moment from "moment";'], "packages/a/index.ts": ["export default function A() {}"]}
        report = self.report(list(real), real)
        hits = report["decisions"][0]["matched_terms"]
        self.assertEqual(hits, [{"term": "moment", "files": ["src/app.ts"]},
                                {"term": "export default", "files": ["packages/a/index.ts"]}])
        self.assertFalse(report["decisions"][0]["broad"])

    def test_terms_need_a_diff(self) -> None:
        self.adr_file("0003-luxon.md", "---\nstatus: accepted\nwatch_terms: [\"moment\"]\n---\n# 3. Use Luxon\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        report = self.report(["src/app.ts"])
        self.assertIn("terms_not_checked", report["caveats"])
        self.assertEqual(report["unscoped_decisions"], [])

    def test_superseded_marker_in_the_title_is_not_live(self) -> None:
        self.adr_file("0013-fetch.md", "---\napplies_to: [\"src/**\"]\n---\n# ADR013: [superseded] Use node-fetch\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        self.assertEqual(self.report(["src/app.ts"])["decisions"], [])

    def test_diff_file_and_planned_patch_for_agents(self) -> None:
        self.adr_file("0003-luxon.md", "---\nstatus: accepted\nwatch_terms: [\"moment\"]\n---\n# 3. Use Luxon\n\n## Context\n\nx\n\n## Decision\n\ny\n")
        patch = self.repo / "plan.diff"
        patch.write_text("diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1,2 @@\n x\n"
                         "+import moment from 'moment';\ndiff --git a/src/new.ts b/src/new.ts\nnew file mode 100644\n"
                         "--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+export const y = 1;\n", encoding="utf-8")
        code, out, _ = call("affected", "--adr", "docs/adr", "--repo", str(self.repo), "--diff-file", str(patch), "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(report["source"]["kind"], "diff")
        self.assertEqual(report["changed_files"], ["src/app.ts", "src/new.ts"])
        self.assertEqual(report["decisions"][0]["matched_terms"], [{"term": "moment", "files": ["src/app.ts"]}])
        code, out, _ = call("affected", "--adr", "docs/adr", "--repo", str(self.repo), "--diff-file", str(patch),
                            "--files", "a.ts", "--json")
        self.assertEqual(json.loads(out)["error"]["code"], "usage")


class WatchTermsVaultTests(AffectedCommandTests):
    """watch_terms in a vault: CLI option, envelope and the MCP diff argument."""

    def test_new_decision_writes_terms_and_they_are_routing_not_reasoning(self) -> None:
        root = str(self.vault)
        self.assertEqual(call("new", "--root", root, "decision", "Use Luxon", "--owner", "Web", "--status", "draft",
                              "--source", "E-001", "--watch-term", "moment", "--watch-term", "date-fns")[0], 0)
        text = next((self.vault / "06-decisions").glob("d-003-*.md")).read_text(encoding="utf-8")
        self.assertIn('watch_terms: ["moment", "date-fns"]', text)
        base = "---\nstatus: approved\nwatch_terms: [\"moment\"]\n---\n# Body\n"
        self.assertTrue(allowed_lifecycle_change(base, base.replace('["moment"]', '["moment", "dayjs"]')))
        self.assertEqual(approval_record_hash(base), approval_record_hash(base.replace('watch_terms: ["moment"]\n', "")))
        code, out, _ = call("new", "--root", root, "decision", "Bad", "--watch-term", "x", "--json")
        self.assertEqual(json.loads(out)["error"]["code"], "operation_rejected")

    def test_mcp_tool_checks_terms_in_a_planned_diff(self) -> None:
        from whykit.mcp_server import VaultTools
        record = next((self.vault / "06-decisions").glob("d-002-*.md"))
        record.write_text(record.read_text(encoding="utf-8").replace("tags: []\n", 'tags: []\nwatch_terms: ["moment"]\n', 1),
                          encoding="utf-8")
        tools = VaultTools(self.vault, max_sensitivity="internal")
        diff = "diff --git a/src/web/a.ts b/src/web/a.ts\n--- a/src/web/a.ts\n+++ b/src/web/a.ts\n@@ -0,0 +1 @@\n+import moment from 'moment'\n"
        payload, is_error = tools.call("affected", {"files": ["src/web/a.ts"], "diff": diff})
        self.assertFalse(is_error, payload)
        self.assertEqual([d["decision_id"] for d in payload["decisions"]], ["D-002"])
        payload, is_error = tools.call("affected", {"files": ["src/web/a.ts"], "diff": "a\x00b"})
        self.assertTrue(is_error)


class ReadmeDemoTests(unittest.TestCase):
    def test_readme_demo_is_real_output(self) -> None:
        import re
        import shlex
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        block = re.search(r"```console\n\$ (whykit affected .*?)\n(WhyKit affected .*?)```", readme, re.S)
        self.assertIsNotNone(block, "README affected demo is missing")
        command = block.group(1).replace("\\\n", " ")
        argv = shlex.split(command)[1:]
        argv[argv.index("--root") + 1] = str(ROOT / "examples" / "payments")
        code, out, _ = call(*argv)
        self.assertEqual(code, 0)
        for line in block.group(2).strip().splitlines():
            with self.subTest(line=line):
                self.assertIn(line, out)

    def test_payments_example_is_public_and_scoped(self) -> None:
        example = ROOT / "examples" / "payments"
        for note in example.rglob("*.md"):
            text = note.read_text(encoding="utf-8")
            if text.startswith("---"):
                with self.subTest(note=note.relative_to(example).as_posix()):
                    self.assertRegex(text, r"(?m)^sensitivity: public$")


if __name__ == "__main__":
    unittest.main()
