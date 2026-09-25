"""Filesystem and init safety regressions."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))

from whykit.io import safe_vault_target  # noqa: E402
from whykit.scaffold import create_decision  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True)


class InitSafetyTests(unittest.TestCase):
    def test_init_force_minimal_preserves_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            target.mkdir()
            keep = target / "01-strategy" / "keepme.md"
            keep.parent.mkdir(parents=True)
            keep.write_text("# keep me\n", encoding="utf-8")
            result = run("init", "--minimal", "--force", str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(keep.exists(), "existing adopter file must survive --minimal --force")
            self.assertEqual(keep.read_text(encoding="utf-8"), "# keep me\n")
            self.assertTrue((target / "Home.md").exists())
            self.assertTrue((target / "notes").is_dir())


class FilesystemSandboxTests(unittest.TestCase):
    def test_decision_write_cannot_escape_vault_through_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            outside = base / "outside"
            outside.mkdir()
            vault = base / "vault"
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            decisions = vault / "06-decisions"
            # Replace the decisions directory with a symlink pointing outside.
            for child in list(decisions.iterdir()):
                child.rename(outside / child.name)
            decisions.rmdir()
            decisions.symlink_to(outside)
            with self.assertRaises(RuntimeError):
                create_decision(vault, "Should not escape")
            leaked = list(outside.glob("d-*-should-not-escape.md"))
            self.assertEqual(leaked, [])

    def test_safe_vault_target_refuses_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            outside = base / "outside"
            outside.mkdir()
            vault = base / "vault"
            vault.mkdir()
            (vault / "Home.md").write_text("x", encoding="utf-8")
            (vault / "00-context").mkdir()
            linked = vault / "notes"
            linked.symlink_to(outside)
            with self.assertRaises(RuntimeError):
                safe_vault_target(vault, "notes/escaped.md")


if __name__ == "__main__":
    unittest.main()
