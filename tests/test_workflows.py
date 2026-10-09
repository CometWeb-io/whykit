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
from whykit.impact import analyze_impact  # noqa: E402
from whykit.scaffold import create_evidence  # noqa: E402
from whykit.status import build_status  # noqa: E402
from _vaults import approved_decision, fresh_vault  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30)


class RecordWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        fresh_vault(self.vault)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_evidence_allocates_id_and_keeps_register_valid(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "evidence",
            "--source", "Example benchmark",
            "--type", "report",
            "--location", "https://example.test/report",
            "--claims", "Supports the example claim",
            "--date", "2026-09-20",
            "--accessed", "2026-09-21",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("E-001", result.stdout)
        register = (self.vault / "00-context/evidence-register.md").read_text(encoding="utf-8")
        self.assertIn("Example benchmark", register)
        lint = run("lint", "--root", str(self.vault))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_new_decision_allocates_id_updates_log_and_lints(self) -> None:
        evidence = run(
            "new", "--root", str(self.vault), "evidence",
            "--source", "Example source", "--type", "internal",
            "--location", "00-context/company.md", "--claims", "Decision context",
        )
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        result = run(
            "new", "--root", str(self.vault), "decision", "Use a shared context ledger",
            "--owner", "Test owner", "--source", "E-001",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("D-001", result.stdout)
        paths = list((self.vault / "06-decisions").glob("d-001-*.md"))
        self.assertEqual(len(paths), 1)
        log = (self.vault / "06-decisions/decision-log.md").read_text(encoding="utf-8")
        self.assertIn("D-001", log)
        self.assertIn("Use a shared context ledger", log)
        lint = run("lint", "--root", str(self.vault))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_approval_gets_policy_review_date_when_not_supplied(self) -> None:
        before = dt.date.today()
        create_evidence(self.vault, source="Process study", kind="report", location="https://example.com/study",
                        claims="Supports the process", today=before)
        _, path = approved_decision(self.vault, "Ship the policy", owner="Test owner", source_ids=["E-001"], today=before)
        record = path.read_text(encoding="utf-8")
        match = re.search(r"(?m)^review_by: (\S+)$", record)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), (before + dt.timedelta(days=90)).isoformat())

    def test_new_note_stays_inside_an_existing_workstream(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "note", "Research queue",
            "--workstream", "notes", "--owner", "Test owner", "--type", "research",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.vault / "notes/research-queue.md").exists())

    def test_new_note_can_link_from_map_and_avoid_orphan_warning(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "note", "Linked research",
            "--workstream", "notes", "--owner", "Test owner", "--type", "research",
            "--link-from", "Home.md", "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "note")
        self.assertEqual(payload["linked_from"], "Home.md")
        self.assertTrue(payload["strict_linked"])
        self.assertIn("[[notes/linked-research|Linked research]]", (self.vault / "Home.md").read_text(encoding="utf-8"))
        report = json.loads(run("lint", "--root", str(self.vault), "--json").stdout)
        self.assertNotIn("note.orphan", {item["code"] for item in report["findings"]})

    def test_new_note_link_failure_rolls_back_created_note(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "note", "Should roll back",
            "--workstream", "notes", "--link-from", "missing-map.md",
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.vault / "notes/should-roll-back.md").exists())

    def test_new_decision_json_contract(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "decision", "Machine readable creation",
            "--owner", "Test owner", "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["contract_version"], 1)
        self.assertEqual(payload["kind"], "decision")
        self.assertEqual(payload["id"], "D-001")
        self.assertTrue(payload["path"].startswith("06-decisions/d-001-"))

    def test_approved_supersession_updates_only_predecessor_lifecycle_and_log(self) -> None:
        day = dt.date.today()
        create_evidence(self.vault, source="Process study", kind="report", location="https://example.com/study",
                        claims="Supports the process", today=day)
        _, old = approved_decision(self.vault, "Use option A", owner="Test owner", source_ids=["E-001"], today=day)
        old_body_before = old.read_text(encoding="utf-8").split("---", 2)[-1]
        approved_decision(self.vault, "Use option B", owner="Test owner", source_ids=["E-001"],
                          supersedes="D-001", today=day)
        old_text = old.read_text(encoding="utf-8")
        self.assertIn("status: superseded", old_text)
        self.assertIn("superseded_by: D-002", old_text)
        self.assertEqual(old_text.split("---", 2)[-1], old_body_before)
        log = (self.vault / "06-decisions/decision-log.md").read_text(encoding="utf-8")
        self.assertIn("| D-001 | Use option A |", log)
        self.assertIn("| superseded | [[06-decisions/d-001-use-option-a]] |", log)
        lint = run("lint", "--root", str(self.vault))
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertNotIn("decision.supersedes_status", lint.stdout)

    def test_new_decision_refuses_unknown_evidence_before_writing(self) -> None:
        result = run(
            "new", "--root", str(self.vault), "decision", "Bad source reference",
            "--source", "E-999",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown evidence", result.stderr)
        self.assertFalse(list((self.vault / "06-decisions").glob("d-001-*.md")))


class StatusAndGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        fresh_vault(self.vault)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_json_has_review_queue_and_machine_contract(self) -> None:
        result = run("status", "--root", str(self.vault), "--today", "2026-09-22", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("documents", payload)
        self.assertIn("review_queue", payload)
        self.assertIn("finding_codes", payload)

    def test_build_status_surfaces_due_review(self) -> None:
        decision = self.vault / "06-decisions/d-001-test.md"
        decision.write_text(
            "---\n"
            "title: Test\n"
            "aliases: []\n"
            "type: decision\n"
            "decision_id: D-001\n"
            "status: approved\n"
            "owner: Test owner\n"
            "created: 2026-09-01\n"
            "last_updated: 2026-09-01\n"
            "review_by: 2026-09-25\n"
            "source_of_truth: false\n"
            "sensitivity: internal\n"
            "source_ids: []\n"
            "tags: []\n"
            "---\n# Test\n",
            encoding="utf-8",
        )
        log = self.vault / "06-decisions/decision-log.md"
        text = log.read_text(encoding="utf-8")
        text = text.replace(
            "| D-001 |  |  |  | proposed / accepted / superseded |  |",
            "| D-001 | Test | 2026-09-01 | Test owner | accepted | [[06-decisions/d-001-test]] |",
        )
        log.write_text(text, encoding="utf-8")
        report = build_status(self.vault, today=dt.date(2026, 9, 22), due_days=7)
        self.assertEqual(len(report["review_queue"]), 1)
        self.assertEqual(report["review_queue"][0]["days"], 3)

    def test_graph_exports_resolved_links(self) -> None:
        home = self.vault / "Home.md"
        home.write_text(home.read_text(encoding="utf-8") + "\n[[00-context/company]]\n", encoding="utf-8")
        graph = build_graph(self.vault)
        self.assertTrue(any(e["from"] == "Home" and e["to"] == "00-context/company" for e in graph["edges"]))
        self.assertEqual(graph["unresolved"], [])


class ImpactTests(unittest.TestCase):
    def test_evidence_impact_finds_real_consumers(self) -> None:
        root = ROOT / "examples" / "northline"
        report = analyze_impact(root, "E-004")
        self.assertTrue(report["exists"])
        self.assertEqual(report["kind"], "evidence")
        paths = {item["path"] for item in report["references"]}
        self.assertIn("06-decisions/d-006-no-ai-in-h1.md", paths)
        self.assertGreaterEqual(report["reference_count"], 2)

    def test_decision_impact_includes_evidence_and_backlinks(self) -> None:
        root = ROOT / "examples" / "northline"
        report = analyze_impact(root, "D-006")
        self.assertTrue(report["exists"])
        self.assertEqual(report["kind"], "decision")
        self.assertEqual(report["record"]["path"], "06-decisions/d-006-no-ai-in-h1.md")
        self.assertEqual(report["evidence"], ["E-004", "E-005"])

    def test_missing_impact_target_is_explicit(self) -> None:
        result = run("impact", "E-999", "--root", str(ROOT / "examples" / "northline"), "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["state"], "missing")


if __name__ == "__main__":
    unittest.main()
