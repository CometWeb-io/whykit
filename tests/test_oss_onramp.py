from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
