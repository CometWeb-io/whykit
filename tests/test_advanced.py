from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whykit.graph import build_graph  # noqa: E402
from whykit.check import run_check  # noqa: E402
from whykit.context import build_context  # noqa: E402
from whykit.pack import build_pack  # noqa: E402
from whykit.query import query_vault  # noqa: E402
from whykit.snapshot import build_snapshot, compare_snapshot  # noqa: E402
from _vaults import fresh_vault, historical_decision  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30)


class AdvancedWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.init_day = fresh_vault(self.vault)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_evidence_and_decision(self) -> Path:
        evidence = run(
            "new", "--root", str(self.vault), "evidence",
            "--source", "Interview", "--type", "interview",
            "--location", "07-research/raw", "--claims", "Supports the decision",
        )
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        # Reader/re-review tests cover accepted records created before approval receipts.
        _, path = historical_decision(self.vault, "Use append-only review history",
                                      owner="Product", source_ids=["E-001"], today=self.init_day,
                                      review_by=(self.init_day + dt.timedelta(days=90)).isoformat())
        return path

    def test_init_includes_versioned_policy_and_review_log(self) -> None:
        self.assertTrue((self.vault / "whykit.toml").exists())
        self.assertTrue((self.vault / "00-context/review-log.md").exists())
        lint = run("lint", "--root", str(self.vault), "--json")
        self.assertEqual(lint.returncode, 0, lint.stderr)
        payload = json.loads(lint.stdout)
        self.assertEqual(payload["contract_version"], 1)
        self.assertEqual(payload["errors"], 0)

    def test_query_filters_metadata_and_does_not_treat_registry_ids_as_citations(self) -> None:
        result = run("query", "--root", str(self.vault), "--type", "reference", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        by_path = {item["path"]: item for item in payload["results"]}
        self.assertEqual(by_path["00-context/evidence-register.md"]["source_ids"], [])
        self.assertIn("00-context/review-log.md", by_path)

    def test_review_records_event_and_reschedules_approved_decision(self) -> None:
        decision = self._add_evidence_and_decision()
        before = decision.read_text(encoding="utf-8")
        self.assertRegex(before, r"(?m)^review_by: \d{4}-\d{2}-\d{2}$")
        # `new` stamps `created` from the wall clock, so the review date must be
        # derived from it too; a fixed date turns red once the calendar passes it.
        review_day = dt.date.today()
        next_review = (review_day + dt.timedelta(days=105)).isoformat()
        result = run(
            "review", "--root", str(self.vault), "record", "D-001",
            "--reviewer", "Research", "--outcome", "confirmed",
            "--today", review_day.isoformat(), "--next-review", next_review,
            "--note", "Evidence rechecked",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        after = decision.read_text(encoding="utf-8")
        self.assertIn(f"review_by: {next_review}", after)
        log = (self.vault / "00-context/review-log.md").read_text(encoding="utf-8")
        self.assertIn("Evidence rechecked", log)
        self.assertIn("[[06-decisions/d-001-use-append-only-review-history]]", log)
        lint = run("lint", "--root", str(self.vault))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_snapshot_is_deterministic_and_detects_content_drift(self) -> None:
        first = build_snapshot(self.vault)
        second = build_snapshot(self.vault)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        report = compare_snapshot(self.vault, first)
        self.assertTrue(report["matches"])
        self.assertTrue(report["content_matches"])
        company = self.vault / "00-context/company.md"
        company.write_text(company.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        report = compare_snapshot(self.vault, first)
        self.assertFalse(report["matches"])
        self.assertIn("00-context/company.md", report["changed"])

    def test_snapshot_cli_output_is_relative_to_vault_root(self) -> None:
        # Mirrors CI: --output paths are vault-relative even when cwd is outside.
        written = run(
            "snapshot",
            "--root",
            str(self.vault),
            "--today",
            "2026-09-17",
            "--output",
            ".whykit/ci-snapshot.json",
            cwd=ROOT,
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        target = self.vault / ".whykit" / "ci-snapshot.json"
        self.assertTrue(target.is_file())
        verified = run(
            "verify-snapshot",
            ".whykit/ci-snapshot.json",
            "--root",
            str(self.vault),
            "--today",
            "2026-09-17",
            "--json",
            cwd=ROOT,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        payload = json.loads(verified.stdout)
        self.assertTrue(payload["matches"])

    def test_snapshot_separates_content_drift_from_time_based_health_drift(self) -> None:
        decision = self._add_evidence_and_decision()
        text = decision.read_text(encoding="utf-8")
        text = text.replace(
            next(line for line in text.splitlines() if line.startswith("review_by:")),
            "review_by: 2026-10-15",
        )
        decision.write_text(text, encoding="utf-8")
        baseline = build_snapshot(self.vault, today=dt.date(2026, 9, 1))
        report = compare_snapshot(self.vault, baseline, today=dt.date(2026, 10, 20))
        self.assertTrue(report["content_matches"])
        self.assertTrue(report["health_changed"])
        self.assertIn("review_due", report["health_delta"])

    def test_typed_graph_has_evidence_edges_without_registry_self_edge(self) -> None:
        self._add_evidence_and_decision()
        graph = build_graph(self.vault)
        evidence_edges = [edge for edge in graph["edges"] if edge["type"] == "evidence"]
        self.assertTrue(any(edge["to"] == "evidence:E-001" for edge in evidence_edges))
        self.assertFalse(any(edge["from"] == "00-context/evidence-register" for edge in evidence_edges))
        self.assertEqual(graph["contract_version"], 1)

    def test_context_pack_is_bounded_and_resolves_evidence(self) -> None:
        self._add_evidence_and_decision()
        result = run("context", "D-001", "--root", str(self.vault), "--max-chars", "80", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["evidence"][0]["id"], "E-001")
        self.assertTrue(payload["content_truncated"])
        self.assertLessEqual(len(payload["content"]), 80)

    def test_multi_context_pack_deduplicates_evidence_and_enforces_body_budget(self) -> None:
        self._add_evidence_and_decision()
        report = build_pack(
            self.vault,
            targets=["D-001", "D-001"],
            query="append-only",
            max_docs=4,
            max_chars=120,
        )
        self.assertEqual(report["contract_version"], 1)
        self.assertEqual(report["format"], "whykit.context-bundle/v1")
        self.assertLessEqual(report["budget"]["used_chars"], 120)
        self.assertEqual([item["id"] for item in report["evidence"]], ["E-001"])
        self.assertGreaterEqual(report["resolved"], 1)

    def test_policy_profiles_make_local_pass_and_ci_fail_on_unanswered_agent_contract(self) -> None:
        local = run("check", "--root", str(self.vault), "--profile", "local", "--json")
        self.assertEqual(local.returncode, 0, local.stdout + local.stderr)
        ci = run("check", "--root", str(self.vault), "--profile", "ci", "--json")
        self.assertEqual(ci.returncode, 1)
        payload = json.loads(ci.stdout)
        self.assertFalse(payload["passed"])
        self.assertGreater(payload["lint"]["warnings"], 0)

    def test_release_profile_requires_concrete_policy_configuration(self) -> None:
        # A syntactically valid starter policy is not a release-ready policy.
        report = run_check(self.vault, profile_name="release", base=None)
        by_name = {item["name"]: item for item in report["checks"]}
        self.assertFalse(by_name["policy_configuration"]["passed"])
        self.assertIn("placeholder", by_name["policy_configuration"]["detail"])

    def test_invalid_policy_is_a_lint_error_not_a_traceback(self) -> None:
        (self.vault / "whykit.toml").write_text("format_version = 999\n", encoding="utf-8")
        lint = run("lint", "--root", str(self.vault), "--json")
        self.assertEqual(lint.returncode, 1)
        payload = json.loads(lint.stdout)
        self.assertIn("config.invalid", {item["code"] for item in payload["findings"]})
        status = run("status", "--root", str(self.vault))
        self.assertEqual(status.returncode, 2)
        self.assertNotIn("Traceback", status.stderr)


    def test_evidence_retire_moves_row_and_preserves_replacement_chain(self) -> None:
        first = run(
            "new", "--root", str(self.vault), "evidence",
            "--source", "Old report", "--type", "report",
            "--location", "https://example.test/old", "--claims", "Old claim",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run(
            "new", "--root", str(self.vault), "evidence",
            "--source", "New report", "--type", "report",
            "--location", "https://example.test/new", "--claims", "Replacement claim",
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        today = dt.date.today().isoformat()
        retired = run(
            "evidence", "--root", str(self.vault), "retire", "E-001",
            "--why", "Superseded by a newer source", "--replaced-by", "E-002",
            "--today", today, "--json",
        )
        self.assertEqual(retired.returncode, 0, retired.stderr)
        payload = json.loads(retired.stdout)
        self.assertEqual(payload["state"], "retired")
        self.assertEqual(payload["replaced_by"], "E-002")
        listing = json.loads(run("evidence", "--root", str(self.vault), "list", "--json").stdout)
        by_id = {item["id"]: item for item in listing["evidence"]}
        self.assertEqual(by_id["E-001"]["state"], "retired")
        self.assertEqual(by_id["E-001"]["replaced_by"], "E-002")
        self.assertEqual(by_id["E-002"]["state"], "active")
        lint = run("lint", "--root", str(self.vault))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_init_stamps_frontmatter_with_creation_date(self) -> None:
        # Bracket the wall clock so a run that crosses midnight still passes.
        days = {self.init_day.isoformat(), dt.date.today().isoformat()}
        home = (self.vault / "Home.md").read_text(encoding="utf-8")
        created = re.search(r"(?m)^created: (\S+)$", home)
        updated = re.search(r"(?m)^last_updated: (\S+)$", home)
        self.assertIsNotNone(created)
        self.assertIn(created.group(1), days)
        self.assertEqual(updated.group(1) if updated else None, created.group(1))

    def test_doctor_has_json_contract(self) -> None:
        result = run("doctor", "--root", str(self.vault), "--json")
        self.assertIn(result.returncode, (0, 1), result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["contract_version"], 1)
        self.assertIn("checks", payload)

    def test_machine_contract_schemas_cover_required_top_level_fields(self) -> None:
        self._add_evidence_and_decision()
        payloads = {
            "query-result.schema.json": query_vault(self.vault, text="append-only"),
            "context-pack.schema.json": build_context(self.vault, "D-001", include_body=False),
            "context-bundle.schema.json": build_pack(self.vault, targets=["D-001"], max_chars=0),
            "snapshot.schema.json": build_snapshot(self.vault, today=dt.date(2026, 9, 22)),
            "graph.schema.json": build_graph(self.vault),
            "check-report.schema.json": run_check(self.vault, profile_name="local", today=dt.date(2026, 9, 22)),
        }
        for schema_name, payload in payloads.items():
            with self.subTest(schema=schema_name):
                schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
                self.assertTrue(set(schema.get("required", ())).issubset(payload))


class AppendOnlyReviewHistoryTests(unittest.TestCase):
    def test_review_log_append_is_allowed_but_rewrite_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            self.assertEqual(run("init", str(vault)).returncode, 0)
            subprocess.run(["git", "init", "-q", str(vault)], check=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
            subprocess.run(["git", "-C", str(vault), "commit", "-qm", "base"], check=True)
            base = subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()

            log = vault / "00-context/review-log.md"
            text = log.read_text(encoding="utf-8")
            text += "| 2026-09-22 | [[Home]] | Test | confirmed | — | 2027-01-01 | ok |\n"
            log.write_text(text, encoding="utf-8")
            subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
            subprocess.run(["git", "-C", str(vault), "commit", "-qm", "append"], check=True)
            allowed = run("history", "--root", str(vault), "--base", base, "--head", "HEAD")
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

            base2 = subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            log.write_text(log.read_text(encoding="utf-8").replace("confirmed", "archived", 1), encoding="utf-8")
            subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
            subprocess.run(["git", "-C", str(vault), "commit", "-qm", "rewrite"], check=True)
            blocked = run("history", "--root", str(vault), "--base", base2, "--head", "HEAD")
            self.assertEqual(blocked.returncode, 1)
            self.assertIn("review-log.md", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
