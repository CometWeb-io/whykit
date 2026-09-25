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
    def test_init_force_preserves_existing_content_and_gitignore_rules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            target.mkdir()
            original = {
                "Home.md": "# My map\n",
                "AGENTS.md": "# My operating contract\n",
                "whykit.toml": "# My policy\n",
            }
            for name, content in original.items():
                (target / name).write_text(content, encoding="utf-8")
            custom_ignore = "private_exports/\n!.env\n"
            (target / ".gitignore").write_text(custom_ignore, encoding="utf-8")

            for _ in range(2):
                result = run("init", "--force", str(target))
                self.assertEqual(result.returncode, 0, result.stderr)
                for name, content in original.items():
                    self.assertEqual((target / name).read_text(encoding="utf-8"), content)
                self.assertTrue((target / "06-decisions" / "decision-log.md").exists())
                merged_ignore = (target / ".gitignore").read_text(encoding="utf-8")
                self.assertTrue(merged_ignore.startswith(custom_ignore))
                self.assertIn(".import-staging/", merged_ignore)
                self.assertGreater(merged_ignore.rfind("\n.env\n"), merged_ignore.index("!.env"))
                self.assertEqual(merged_ignore.count("# WhyKit protective defaults"), 1)

    def test_init_force_restores_protective_rules_after_a_later_negation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault"
            self.assertEqual(run("init", str(target)).returncode, 0)
            gitignore = target / ".gitignore"
            gitignore.write_text(gitignore.read_text(encoding="utf-8") + "!.env\n", encoding="utf-8")

            result = run("init", "--force", str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            merged = gitignore.read_text(encoding="utf-8")
            self.assertGreater(merged.rfind("\n.env\n"), merged.index("!.env"))
            self.assertEqual(merged.count("# WhyKit protective defaults"), 1)
            subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
            for ignored in (".env", ".import-staging/raw.md"):
                with self.subTest(ignored=ignored):
                    check = subprocess.run(["git", "-C", str(target), "check-ignore", "-q", ignored])
                    self.assertEqual(check.returncode, 0)

            self.assertEqual(run("init", "--force", str(target)).returncode, 0)
            self.assertEqual(gitignore.read_text(encoding="utf-8"), merged)

    def test_init_refuses_existing_file_target_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "existing.txt"
            target.write_text("keep this file\n", encoding="utf-8")
            for force in (False, True):
                with self.subTest(force=force):
                    args = ("init", "--force", str(target)) if force else ("init", str(target))
                    result = run(*args)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(target.read_text(encoding="utf-8"), "keep this file\n")

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

    def test_init_force_does_not_write_through_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "vault"
            outside = base / "outside"
            target.mkdir()
            outside.mkdir()
            sentinel = outside / "README.md"
            sentinel.write_text("outside must stay unchanged\n", encoding="utf-8")
            try:
                (target / "06-decisions").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")

            result = run("init", "--force", str(target))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside must stay unchanged\n")
            self.assertFalse((outside / "decision-log.md").exists())

    def test_init_force_refuses_symlinked_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "vault"
            target.mkdir()
            sentinel = base / "keep.txt"
            sentinel.write_text("keep this file\n", encoding="utf-8")
            try:
                (target / ".gitignore").symlink_to(sentinel)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")

            result = run("init", "--minimal", "--force", str(target))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep this file\n")
            self.assertTrue((target / ".gitignore").is_symlink())


class FilesystemSandboxTests(unittest.TestCase):
    def test_export_commands_refuse_symlink_output_inside_vault(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            sentinel = vault / "notes" / "keep.md"
            sentinel.write_text("keep this note\n", encoding="utf-8")
            output = vault / ".whykit" / "export.json"
            output.parent.mkdir()
            try:
                output.symlink_to(sentinel)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")

            for command in ("graph", "snapshot"):
                with self.subTest(command=command):
                    result = run(command, "--root", str(vault), "--output", ".whykit/export.json")
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep this note\n")

    def test_export_commands_accept_absolute_output_using_the_requested_root_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            self.assertEqual(run("init", "--minimal", str(vault)).returncode, 0)
            for command in ("graph", "snapshot"):
                with self.subTest(command=command):
                    output = vault / ".whykit" / f"{command}.json"
                    result = run(command, "--root", str(vault), "--output", str(output))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output.is_file())

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
