"""WhyKit contacts nothing.

The documentation promises no telemetry and no network use. A vault holds the
material a company is least willing to hand to a third party, so the promise is
checked here: every vault command runs in-process with the socket layer broken.
`serve` is excluded because it starts the optional local Explorer by design.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whykit.cli import build_parser, main  # noqa: E402


class NetworkUsed(AssertionError):
    pass


def _refuse(*_args, **_kwargs):
    raise NetworkUsed("WhyKit attempted network access")


@contextlib.contextmanager
def _no_network():
    with mock.patch.object(socket, "socket", _refuse), \
            mock.patch.object(socket, "create_connection", _refuse), \
            mock.patch.object(socket, "getaddrinfo", _refuse):
        yield


@unittest.skipIf(shutil.which("git") is None, "git is required")
class OfflineTests(unittest.TestCase):
    def _run(self, *argv: str) -> int:
        with _no_network(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return main(list(argv))
            except SystemExit as exc:  # some subcommands exit through argparse-style helpers
                return int(exc.code or 0)

    def test_every_vault_command_runs_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault, docs = base / "vault", base / "docs"
            docs.mkdir()
            (docs / "note.md").write_text("# Imported\n\nSome words.\n", encoding="utf-8")
            r = str(vault)
            steps: list[tuple[str, ...]] = [
                ("init", r),
                ("new", "--root", r, "evidence", "--source", "Example export", "--type", "dataset",
                 "--location", "https://example.com/export.csv", "--claims", "Example claim"),
                ("new", "--root", r, "decision", "Example decision", "--owner", "Team",
                 "--status", "approved", "--source", "E-001"),
                ("new", "--root", r, "note", "Example note", "--workstream", "notes", "--link-from", "Home.md"),
                ("lint", "--root", r),
                ("status", "--root", r),
                ("graph", "--root", r),
                ("backlinks", "D-001", "--root", r),
                ("impact", "E-001", "--root", r),
                ("trace", "--root", r),
                ("query", "Example", "--root", r),
                ("context", "D-001", "--root", r),
                ("pack", "D-001", "--root", r),
                ("review", "--root", r, "list"),
                ("review", "--root", r, "record", "D-001", "--reviewer", "Team"),
                ("evidence", "--root", r, "list"),
                ("snapshot", "--root", r, "--output", ".whykit/snapshot.json"),
                ("verify-snapshot", ".whykit/snapshot.json", "--root", r),
                ("policy", "--root", r),
                ("rules",),
                ("rules", "agents.unconfigured"),
                ("adopt", str(docs), "--into", r),
                ("explorer-index", "--root", r),
                ("completion", "bash"),
                ("completion", "zsh"),
                ("completion", "fish"),
            ]
            for argv in steps:
                with self.subTest(command=argv[0]):
                    self.assertEqual(self._run(*argv), 0, argv)

            subprocess.run(["git", "init", "-q", r], check=True, capture_output=True)
            subprocess.run(["git", "-C", r, "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", r, "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-qm", "init"], check=True, capture_output=True)
            for argv in (
                ("history", "--base", "HEAD", "--root", r),
                ("diff", "--base", "HEAD", "--root", r),
                ("diff", "--base", "HEAD", "--root", r, "--format", "markdown"),
                ("diff", "--base", "HEAD", "--root", r, "--format", "github"),
                ("check", "--root", r, "--profile", "local", "--base", "HEAD"),
                ("install-hooks", "--root", r),
                ("doctor", "--root", r),
                ("evidence", "--root", r, "retire", "E-001", "--why", "Example retirement"),
            ):
                with self.subTest(command=argv[0]):
                    self.assertEqual(self._run(*argv), 0, argv)

    def test_the_command_list_covers_the_cli(self) -> None:
        # A new subcommand must be added to the offline run, or excluded here
        # with a reason.
        parser = build_parser()
        subparsers = next(a for a in parser._actions if a.dest == "command")
        covered = {
            "init", "new", "lint", "status", "graph", "backlinks", "impact", "query", "context",
            "pack", "review", "evidence", "snapshot", "verify-snapshot", "policy", "rules",
            "adopt", "explorer-index", "history", "check", "install-hooks", "doctor",
            "trace", "completion", "diff",
        }
        excluded = {"serve"}  # starts the optional Explorer dev server
        self.assertEqual(set(subparsers.choices), covered | excluded)


if __name__ == "__main__":
    unittest.main()
