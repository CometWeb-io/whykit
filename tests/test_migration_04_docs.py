"""Exercise the documented upgrade and recovery commands on synthetic vaults."""
from __future__ import annotations

import os
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "migration-0.4.md"
OLD = os.environ.get("WHYKIT_034")
NEW = Path(sys.executable).parent / "whykit"


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=True, **kwargs)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash") and shutil.which("git"),
                     "guide's copy example requires POSIX, bash and Git")
@unittest.skipUnless(NEW.is_file(), "the guide runs the installed `whykit` entry point; use `uv run` so it sits beside the interpreter")
class MigrationGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="whykit-04-guide-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old_stub = self.base / "whykit-034-stub"
        self.old_stub.write_text("#!/bin/sh\nprintf 'whykit 0.3.4\\n'\n", encoding="utf-8")
        self.old_stub.chmod(0o700)
        self.vault = self.base / "vault"
        shutil.copytree(ROOT / "examples" / "approval", self.vault,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))
        run(["git", "init", "-q", str(self.vault)])
        run(["git", "-C", str(self.vault), "add", "."])
        env = dict(os.environ, GIT_AUTHOR_NAME="Example Author", GIT_AUTHOR_EMAIL="author@example.com",
                   GIT_COMMITTER_NAME="Example Author", GIT_COMMITTER_EMAIL="author@example.com")
        run(["git", "-C", str(self.vault), "commit", "-qm", "Synthetic backup fixture"], env=env)
        # The backup must include both untracked and ignored private files.
        (self.vault / "untracked-note.txt").write_text("Synthetic note\n", encoding="utf-8")
        (self.vault / ".git/info/exclude").write_text("local-only.txt\n", encoding="utf-8")
        (self.vault / "local-only.txt").write_text("Synthetic ignored file\n", encoding="utf-8")
        self.blocks = re.findall(r"```bash\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
        self.assertEqual(len(self.blocks), 4)

    def block(self, number: int, old: str, backup: Path | None = None) -> str:
        values = {
            "VAULT": self.vault,
            "BACKUP": backup or self.base / "backup",
            "OLD_WHYKIT": old,
            "NEW_WHYKIT": NEW,
            "RESTORED": self.base / "trial",
            "RECOVERED": self.base / "recovered",
        }
        text = self.blocks[number]
        for name, value in values.items():
            text = re.sub(rf"(?m)^{name}=/private/example/[^\n]*$",
                          lambda match, n=name, v=value: n + "=" + shlex.quote(str(v)), text)
        return text

    @unittest.skipUnless(OLD, "set WHYKIT_034 to an installed 0.3.4 CLI")
    def test_guide_upgrade_then_restore_real_034_with_git_history(self) -> None:
        assert OLD is not None
        self.assertEqual(run([OLD, "--version"]).stdout.strip(), "whykit 0.3.4")
        self.assertRegex(run([str(NEW), "--version"]).stdout.strip(), r"^whykit 0\.[45]\.")
        history = run(["git", "-C", str(self.vault), "rev-parse", "HEAD"]).stdout.strip()
        script = self.block(0, OLD) + self.block(1, OLD)
        script += ('"$NEW_WHYKIT" review record D-001 --root "$RESTORED" '
                   '--reviewer "Example Reviewer" --outcome confirmed --today 2026-09-18 --json\n')
        script += self.block(2, OLD) + self.block(3, OLD)
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script],
                                text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertIn('"verdict": "review_required"', result.stdout)
        backup, trial, recovered = (self.base / name for name in ("backup", "trial", "recovered"))
        self.assertEqual(run(["git", "-C", str(recovered), "rev-parse", "HEAD"]).stdout.strip(), history)
        self.assertEqual((backup / "local-only.txt").read_bytes(),
                         (recovered / "local-only.txt").read_bytes())
        self.assertEqual((backup / "untracked-note.txt").read_bytes(),
                         (recovered / "untracked-note.txt").read_bytes())
        self.assertNotEqual((trial / "00-context/review-log.md").read_bytes(),
                            (recovered / "00-context/review-log.md").read_bytes())

    def test_backup_refuses_a_destination_inside_the_vault(self) -> None:
        target = self.vault / "nested-backup"
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                 self.block(0, str(self.old_stub), target)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vault and backup must not contain one another", result.stderr)
        self.assertFalse(target.exists())

    def test_backup_refuses_an_existing_destination(self) -> None:
        target = self.base / "existing"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("untouched", encoding="utf-8")
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                 self.block(0, str(self.old_stub), target)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(list(target.iterdir()), [marker])

    def test_backup_stops_if_file_inventory_fails(self) -> None:
        commands = self.base / "commands"
        commands.mkdir()
        find_stub = commands / "find"
        find_stub.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        find_stub.chmod(0o700)
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                 self.block(0, str(self.old_stub))],
                                text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse((self.base / "backup").exists())

    def test_backup_does_not_accept_two_missing_git_heads(self) -> None:
        run(["git", "-C", str(self.vault), "symbolic-ref", "HEAD", "refs/heads/unborn"])
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                 self.block(0, str(self.old_stub))],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Needed a single revision", result.stderr)

    def test_documented_downgrade_check_reports_new_records_as_blocked(self) -> None:
        trial = self.base / "trial"
        shutil.copytree(ROOT / "examples/outcomes", trial,
                        ignore=shutil.ignore_patterns(".git", ".whykit"))
        script = (f"NEW_WHYKIT={shlex.quote(str(NEW))}\n"
                  f"RESTORED={shlex.quote(str(trial))}\n" + self.block(2, str(NEW)))
        result = run(["bash", "--noprofile", "--norc", "-c", script])
        report = json.loads(result.stdout)
        self.assertEqual(report["verdict"], "blocked")
        self.assertFalse(report["downgrade_authorized"])


if __name__ == "__main__":
    unittest.main()
