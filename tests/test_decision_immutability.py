from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "whykit.py"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


class DecisionImmutabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        run("git", "init", "-q", cwd=self.root)
        run("git", "config", "user.email", "test@example.invalid", cwd=self.root)
        run("git", "config", "user.name", "WhyKit test", cwd=self.root)
        d = self.root / "06-decisions"
        d.mkdir()
        (d / "d-001-accepted.md").write_text("---\nstatus: approved\n---\n# Accepted\n", encoding="utf-8")
        (d / "d-002-draft.md").write_text("---\nstatus: draft\n---\n# Draft\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "base", cwd=self.root)
        self.base = run("git", "rev-parse", "HEAD", cwd=self.root).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check(self) -> subprocess.CompletedProcess[str]:
        return run(sys.executable, str(SCRIPT), "history", "--base", self.base, "--head", "HEAD", cwd=self.root)

    def test_approved_record_cannot_be_rewritten(self) -> None:
        p = self.root / "06-decisions/d-001-accepted.md"
        p.write_text(p.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "rewrite", cwd=self.root)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("d-001-accepted.md", proc.stderr)

    def test_draft_record_remains_editable(self) -> None:
        p = self.root / "06-decisions/d-002-draft.md"
        p.write_text(p.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "edit draft", cwd=self.root)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_approved_record_allows_review_date_refresh_only(self) -> None:
        p = self.root / "06-decisions/d-001-accepted.md"
        p.write_text(
            "---\nstatus: approved\nlast_updated: 2026-09-22\nreview_by: 2027-03-22\n---\n# Accepted\n", encoding="utf-8"
        )
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "refresh review metadata", cwd=self.root)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_approved_record_allows_metadata_only_supersession(self) -> None:
        p = self.root / "06-decisions/d-001-accepted.md"
        p.write_text(
            "---\nstatus: superseded\nlast_updated: 2026-09-22\nsuperseded_by: D-003\n---\n# Accepted\n", encoding="utf-8"
        )
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "supersede metadata", cwd=self.root)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_supersession_cannot_smuggle_a_reasoning_rewrite(self) -> None:
        p = self.root / "06-decisions/d-001-accepted.md"
        p.write_text(
            "---\nstatus: superseded\nlast_updated: 2026-09-22\nsuperseded_by: D-003\n---\n# Accepted\nrewritten rationale\n", encoding="utf-8"
        )
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "bad supersession", cwd=self.root)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("d-001-accepted.md", proc.stderr)


class DecisionImmutabilityQuotedStatusTests(unittest.TestCase):
    def _repo_with_status(self, status_line: str) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        run("git", "init", "-q", cwd=root)
        run("git", "config", "user.email", "test@example.invalid", cwd=root)
        run("git", "config", "user.name", "WhyKit test", cwd=root)
        d = root / "06-decisions"
        d.mkdir()
        (d / "d-001-accepted.md").write_text(f"---\n{status_line}\n---\n# Accepted\n", encoding="utf-8")
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-qm", "base", cwd=root)
        base = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
        return tmp, root, base

    def test_quoted_approved_status_is_immutable(self) -> None:
        tmp, root, base = self._repo_with_status('status: "approved"')
        try:
            p = root / "06-decisions/d-001-accepted.md"
            p.write_text(p.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
            run("git", "add", ".", cwd=root)
            run("git", "commit", "-qm", "rewrite", cwd=root)
            proc = run(sys.executable, str(SCRIPT), "history", "--base", base, "--head", "HEAD", cwd=root)
            self.assertEqual(proc.returncode, 1, proc.stderr)
        finally:
            tmp.cleanup()

    def test_approved_status_with_comment_is_immutable(self) -> None:
        tmp, root, base = self._repo_with_status("status: approved # accepted by council")
        try:
            p = root / "06-decisions/d-001-accepted.md"
            p.write_text(p.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
            run("git", "add", ".", cwd=root)
            run("git", "commit", "-qm", "rewrite", cwd=root)
            proc = run(sys.executable, str(SCRIPT), "history", "--base", base, "--head", "HEAD", cwd=root)
            self.assertEqual(proc.returncode, 1, proc.stderr)
        finally:
            tmp.cleanup()


class NestedVaultImmutabilityTests(unittest.TestCase):
    """Regression: vault as a subdirectory of the Git work tree.

    ``git diff --name-status`` returns work-tree-relative paths
    (``vault/06-decisions/...``). History enforcement must still catch rewrites
    when ``--root`` points at that nested vault — the layout CI itself uses for
    ``examples/northline``.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "test@example.invalid", cwd=self.repo)
        run("git", "config", "user.name", "WhyKit test", cwd=self.repo)
        self.vault = self.repo / "vault"
        decisions = self.vault / "06-decisions"
        decisions.mkdir(parents=True)
        (decisions / "d-001-approved.md").write_text(
            "---\nstatus: approved\n---\n# Approved\n",
            encoding="utf-8",
        )
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-qm", "base", cwd=self.repo)
        self.base = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_nested_vault_history_is_enforced(self) -> None:
        record = self.vault / "06-decisions" / "d-001-approved.md"
        record.write_text(
            record.read_text(encoding="utf-8") + "\nrewritten reasoning\n",
            encoding="utf-8",
        )
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-qm", "rewrite", cwd=self.repo)
        proc = run(
            sys.executable,
            str(SCRIPT),
            "history",
            "--base",
            self.base,
            "--head",
            "HEAD",
            "--root",
            str(self.vault),
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("d-001-approved.md", proc.stderr)


if __name__ == "__main__":
    unittest.main()
