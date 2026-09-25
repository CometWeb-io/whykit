from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_commit_attribution.py"
sys.path.insert(0, str(ROOT / "scripts"))

from check_commit_attribution import scan_repository  # noqa: E402


class CommitAttributionTests(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    def commit(self, repo: Path, message: str, *, identity: str = "Maintainer") -> str:
        (repo / "note.md").write_text(message.splitlines()[0] + "\n", encoding="utf-8")
        self.git(
            repo,
            "-c",
            "user.name=" + identity,
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qam",
            message,
        )
        return self.git(repo, "rev-parse", "HEAD")

    def new_repo(self, path: Path) -> None:
        self.git(path, "init", "-q")
        (path / "note.md").write_text("test\n", encoding="utf-8")
        self.git(path, "add", "note.md")
        self.commit(path, "Initial record")

    def test_ai_name_in_commit_body_is_not_an_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.new_repo(repo)
            self.commit(repo, "Document Claude Code compatibility\n\nMentioned in prose only.")

            count, offending = scan_repository(repo)

        self.assertEqual(count, 2)
        self.assertEqual(offending, [])

    def test_ai_coauthor_trailer_is_detected_without_printing_its_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.new_repo(repo)
            sha = self.commit(
                repo,
                "Add a feature\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>",
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn(sha, result.stdout)
        self.assertNotIn("Claude", result.stdout)
        self.assertNotIn("anthropic.com", result.stdout)

    def test_ai_commit_identity_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.new_repo(repo)
            sha = self.commit(repo, "Generated change", identity="Codex")

            count, offending = scan_repository(repo)

        self.assertEqual(count, 2)
        self.assertEqual(offending, [sha])


if __name__ == "__main__":
    unittest.main()
