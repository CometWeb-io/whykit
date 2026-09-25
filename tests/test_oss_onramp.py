from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True, timeout=30)


class MinimalInitTests(unittest.TestCase):
    def test_minimal_skips_gtm_workstreams_and_lints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            result = run("init", "--minimal", str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((target / "01-strategy").exists())
            self.assertTrue((target / "notes" / "README.md").is_file())
            self.assertIn(
                "https://github.com/CometWeb-io/whykit",
                (target / "README.md").read_text(encoding="utf-8"),
            )
            lint = run("lint", "--root", str(target))
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
            self.assertIn("0 error", lint.stdout)


class TinyExampleTests(unittest.TestCase):
    def test_tiny_is_strict_clean(self) -> None:
        result = run("lint", "examples/tiny", "--strict", "--today", "2026-09-17", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GraphAndBacklinksTests(unittest.TestCase):
    def test_obsidian_graph_and_backlinks(self) -> None:
        graph = run("graph", "--root", str(ROOT / "examples" / "tiny"), "--format", "obsidian")
        self.assertEqual(graph.returncode, 0, graph.stderr)
        payload = json.loads(graph.stdout)
        self.assertEqual(payload["format"], "whykit.obsidian-graph/v1")
        back = run("backlinks", "D-001", "--root", str(ROOT / "examples" / "tiny"), "--json")
        self.assertEqual(back.returncode, 0, back.stderr)
        report = json.loads(back.stdout)
        self.assertTrue(report["exists"])


class PackPreambleTests(unittest.TestCase):
    def test_pack_for_cursor_adds_preamble(self) -> None:
        result = run(
            "pack", "D-001", "--root", str(ROOT / "examples" / "tiny"),
            "--for", "cursor", "--max-chars", "500",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent"], "cursor")
        self.assertIn("WhyKit", payload["preamble"])


class AdoptScoreTests(unittest.TestCase):
    def test_empty_inventory_does_not_claim_perfect_readiness(self) -> None:
        from whykit.adopt import score_adoption

        self.assertEqual(score_adoption([])["readiness_pct"], 0.0)

    def test_adopt_discloses_markdown_it_could_not_decode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, vault = base / "docs", base / "vault"
            source.mkdir()
            (source / "broken.md").write_bytes(b"\xff\xfe")
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            result = run("adopt", str(source), "--into", str(vault), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("broken.md", payload["scope_note"])
            self.assertEqual(payload["score"]["files"], 0)

    def test_adopt_json_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            source = base / "docs"
            source.mkdir()
            (source / "note.md").write_text("# Hello\n\n" + ("word " * 40) + "\n", encoding="utf-8")
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            result = run("adopt", str(source), "--into", str(vault), "--profile", "obsidian-loose", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("estimated_minutes_to_first_green_lint", payload["score"])
            self.assertEqual(payload["profile"], "obsidian-loose")

    def test_readiness_uses_real_front_matter_rules(self) -> None:
        from whykit.adopt import scan

        valid = ("---\ntitle: Note\ntype: guide\nstatus: draft\nowner: Example owner\n"
                 "created: 2026-09-17\nlast_updated: 2026-09-17\n"
                 "source_of_truth: false\nsensitivity: internal\n---\n\n# Note\n\n"
                 + "An ordinary sentence with enough words to be classified as substantive. " * 3)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            (source / "ready.md").write_text(valid, encoding="utf-8")
            (source / "missing.md").write_text(
                valid.replace("sensitivity: internal\n", ""), encoding="utf-8",
            )
            (source / "invalid.md").write_text(
                valid.replace("status: draft", "status: bogus"), encoding="utf-8",
            )
            by_name = {item.relative: item for item in scan(source)}
            self.assertTrue(by_name["ready.md"].whykit_ready)
            self.assertFalse(by_name["missing.md"].whykit_ready)
            self.assertFalse(by_name["invalid.md"].whykit_ready)

    def test_scan_scores_the_same_bytes_it_hashes_without_rereading(self) -> None:
        from whykit.adopt import scan

        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            content = ("---\ntitle: Note\ntype: guide\nstatus: draft\nowner: Example owner\n"
                       "created: 2026-09-17\nlast_updated: 2026-09-17\n"
                       "source_of_truth: false\nsensitivity: internal\n---\n\n# Note\n\n"
                       + "A sentence with enough words to count as useful source material. " * 3)
            (source / "note.md").write_text(content, encoding="utf-8")
            data = (source / "note.md").read_bytes()
            with patch.object(Path, "read_text", side_effect=AssertionError("unexpected second read")):
                item = scan(source)[0]
            self.assertTrue(item.whykit_ready)
            self.assertEqual(item.sha256, hashlib.sha256(data).hexdigest())

    def test_adopt_warns_about_incompatible_registers_and_non_markdown_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, vault = base / "docs", base / "vault"
            (source / "00-context").mkdir(parents=True)
            (source / "06-decisions").mkdir(parents=True)
            evidence = ("# Evidence\n\n" + "Context and documentation for this evidence register. " * 4
                        + "\n| ID | Source | Type | Date | Location | Claims |\n"
                        + "|---|---|---|---|---|---|\n"
                        + "| E-001 | Example report | report | 2026-09-17 | https://example.com | Claim |\n")
            decision = ("# Decisions\n\n" + "Context and documentation for this decision log. " * 4
                        + "\n| ID | Decision | Date | Owner | Status | Reason | Record | Notes |\n"
                        + "|---|---|---|---|---|---|---|---|\n"
                        + "| D-001 | Example | 2026-09-17 | Owner | accepted | Reason | [[d-001]] | Note |\n")
            (source / "00-context" / "evidence-register.md").write_text(evidence, encoding="utf-8")
            (source / "06-decisions" / "decision-log.md").write_text(decision, encoding="utf-8")
            (source / "data.csv").write_text("id,value\n1,2\n", encoding="utf-8")
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            result = run("adopt", str(source), "--into", str(vault), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            warnings = " ".join(payload["score"]["format_warnings"])
            self.assertIn("evidence-register.md", warnings)
            self.assertIn("decision-log.md", warnings)
            self.assertIn("data.csv", payload["scope_note"])

    def test_canonical_register_tables_need_no_format_warning(self) -> None:
        from whykit.adopt import scan, score_adoption

        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            (source / "evidence-register.md").write_text(
                "# Evidence\n\n" + "Context for this synthetic register. " * 5
                + "\n| ID | Source | Type | Date | Accessed | Location | Claims |\n"
                  "|---|---|---|---|---|---|---|\n"
                  "| E-001 | Source | report | 2026-09-17 | 2026-09-17 | https://example.com | Claim |\n",
                encoding="utf-8",
            )
            (source / "decision-log.md").write_text(
                "# Decisions\n\n" + "Context for this synthetic decision log. " * 5
                + "\n| ID | Decision | Date | Owner | Status | Record |\n"
                  "|---|---|---|---|---|---|\n"
                  "| D-001 | Choice | 2026-09-17 | Owner | accepted | [[d-001]] |\n",
                encoding="utf-8",
            )
            self.assertEqual(score_adoption(scan(source))["format_warnings"], [])

    def test_ingestion_record_retains_full_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, vault = base / "docs", base / "vault"
            source.mkdir()
            content = "# Source\n\n" + "A synthetic source with enough detail to stage for review. " * 4
            (source / "note.md").write_text(content, encoding="utf-8")
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            result = run("adopt", str(source), "--into", str(vault), "--write", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            record = vault / json.loads(result.stdout)["ingestion_record"]
            source_digest = hashlib.sha256((source / "note.md").read_bytes()).hexdigest()
            self.assertIn(source_digest, record.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
