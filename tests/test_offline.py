"""WhyKit contacts nothing.

The documentation promises no telemetry and no network use. A vault holds the
material a company is least willing to hand to a third party, so the promise is
checked here: every vault command runs in-process with the socket layer broken.
`serve` is excluded because it starts the optional local Explorer by design.
"""
from __future__ import annotations

import contextlib
import datetime as dt
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
                 "--status", "draft", "--source", "E-001"),
                ("new", "--root", r, "note", "Example note", "--workstream", "notes", "--link-from", "Home.md"),
                ("lint", "--root", r),
                ("status", "--root", r),
                ("graph", "--root", r),
                ("backlinks", "D-001", "--root", r),
                ("impact", "E-001", "--root", r),
                ("trace", "--root", r),
                ("query", "Example", "--root", r),
                ("retrieve", "Example", "--root", r, "--valid-on", "2026-09-17", "--max-sensitivity", "restricted"),
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
                    if argv[:4] == ("new", "--root", r, "decision"):
                        from _vaults import fill_decision
                        from whykit.review import approve_decision
                        fill_decision(next((vault / "06-decisions").glob("d-001-*.md")))
                        with _no_network():
                            plan = approve_decision(vault, "D-001", reviewer="Team")
                            approve_decision(vault, "D-001", reviewer="Team", write=True,
                                             expected_sha256=plan["expected_sha256"])

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
                ("recover", "--root", r, "--json"),
                ("evidence", "--root", r, "retire", "E-001", "--why", "Example retirement"),
            ):
                with self.subTest(command=argv[0]):
                    self.assertEqual(self._run(*argv), 0, argv)

    def test_language_server_session_runs_without_network(self) -> None:
        import json

        from whykit import lsp

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            self.assertEqual(self._run("init", str(vault)), 0)
            home = vault.resolve() / "Home.md"
            uri = lsp.path_to_uri(home)
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"rootUri": lsp.path_to_uri(vault.resolve()), "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "initialized", "params": {}},
                {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {
                    "uri": uri, "languageId": "markdown", "version": 1,
                    "text": home.read_text(encoding="utf-8") + "\n[[missing]] E-001\n"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
                 "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}}},
                {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
                {"jsonrpc": "2.0", "method": "exit"},
            ]
            stdin = io.BytesIO(b"".join(
                b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
                for body in (json.dumps(m).encode() for m in messages)
            ))
            stdout = io.BytesIO()
            with _no_network():
                code = lsp.serve(stdin, stdout, debounce=0)
            self.assertEqual(code, 0)
            self.assertIn(b"publishDiagnostics", stdout.getvalue())

    def test_provenance_and_context_workflows_run_without_network(self) -> None:
        from whykit.compiled_context import compile_context, serialize_compiled

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            vault = base / "vault"
            shutil.copytree(ROOT / "examples" / "provenance", vault,
                            ignore=shutil.ignore_patterns(".whykit", ".git"))
            source = base / "source.txt"
            source.write_text("Local synthetic source.\n", encoding="utf-8")
            context = base / "context.json"
            with _no_network():
                report = compile_context(vault, targets=["D-003"], today=dt.date(2026, 9, 17),
                                         allowed_sensitivities={"public", "internal", "confidential", "restricted"})
                context.write_text(serialize_compiled(report), encoding="utf-8")
            manifest = base / "workspace.toml"
            manifest.write_text('workspace_version = 1\naudience = "local"\n[[vaults]]\nalias = "fixture"\nroot = "vault"\n', encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(vault)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-qm", "synthetic snapshot"], check=True, capture_output=True)
            r = str(vault)
            steps = (
                ("agent", "eval", "--cases", str(ROOT / "examples/agent-eval/cases.jsonl"),
                 "--responses", str(ROOT / "examples/agent-eval/responses.jsonl")),
                ("agent", "preflight", "--root", r, "--proposal", str(vault / "proposal.json"),
                 "--context", str(context), "--max-sensitivity", "restricted", "--today", "2026-09-17"),
                ("workspace", "status", str(manifest), "--today", "2026-09-17"),
                ("query", "decision", "--root", r, "--ranking", "bm25", "--json"),
                ("compile-context", "D-003", "--root", r, "--max-sensitivity", "restricted", "--today", "2026-09-17"),
                ("compile-context", "--query", "observations", "--ranking", "hybrid", "--root", r,
                 "--max-sensitivity", "restricted", "--today", "2026-09-17"),
                ("verify-context", str(context), "--root", r, "--max-sensitivity", "restricted", "--today", "2026-09-17"),
                ("deep-impact", "E-004", "--root", r, "--max-sensitivity", "restricted"),
                ("knowledge-at", "--root", r, "--valid-on", "2026-09-17", "--max-sensitivity", "restricted"),
                ("artifact", "capture", "--root", r, "--evidence", "E-004", "--source-file", str(source),
                 "--recorded-at", "2026-09-17T12:00:00Z"),
                ("claim", "create", "Unlinked draft", "--root", r, "--owner", "Example Owner"),
                ("claim", "list", "--root", r, "--max-sensitivity", "restricted"),
                ("claim", "show", "C-001", "--root", r, "--max-sensitivity", "restricted"),
                ("claim", "verify", "C-001", "--root", r, "--max-sensitivity", "restricted"),
            )
            for argv in steps:
                with self.subTest(command=argv[:2]):
                    self.assertEqual(self._run(*argv), 0, argv)

    def test_outcome_and_migration_workflows_run_without_network(self) -> None:
        import json

        from whykit.knowledge_v2 import _NATIVE_IO_AVAILABLE

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            vault = base / "vault"
            shutil.copytree(ROOT / "examples/outcomes", vault,
                            ignore=shutil.ignore_patterns(".whykit", ".git"))
            r = str(vault)
            for args in (
                ("outcome", "create", "--root", r, "--input", str(vault / "outcome-input.json"),
                 "--owner", "Ada Example", "--today", "2026-09-17"),
                ("outcome", "list", "--root", r, "--max-sensitivity", "restricted"),
                ("outcome", "show", "O-001", "--root", r, "--max-sensitivity", "restricted"),
                ("outcome", "verify", "O-001", "--root", r, "--max-sensitivity", "restricted", "--today", "2026-09-17"),
                ("knowledge", "view", "--root", r, "--max-sensitivity", "restricted"),
            ):
                self.assertEqual(self._run(*args), 0, args)
            if _NATIVE_IO_AVAILABLE:
                bundle = base / "bundle.json"
                for args in (
                    ("knowledge", "bundle", "--root", r, "--private", "--output", str(bundle)),
                    ("knowledge", "restore", str(bundle), "--into", str(base / "restored")),
                ):
                    output = io.StringIO()
                    with _no_network(), contextlib.redirect_stdout(output):
                        self.assertEqual(main(list(args)), 0)
                    plan = json.loads(output.getvalue())
                    self.assertEqual(self._run(*args, "--write", "--expect-hash", plan["expected_sha256"]), 0)

    def test_saved_github_import_runs_without_network(self) -> None:
        import json

        from _vaults import fresh_vault
        from whykit.scaffold import create_evidence

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve() / "vault"
            fresh_vault(root)
            source = ROOT / "examples/github-import/issue.json"
            with _no_network():
                eid = create_evidence(root, source="Synthetic issue", kind="dataset",
                                      location="https://github.com/example-labs/fixture/issues/7",
                                      claims="Untrusted fixture metadata", sensitivity="restricted")
                args = ["import", "github", "--root", str(root), "--kind", "issue",
                        "--repository", "example-labs/fixture", "--evidence", eid,
                        "--input", str(source), "--recorded-at", "2026-09-17T12:00:00Z"]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(args), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(self._run(*args, "--write", "--expect-hash", plan["expected_sha256"]), 0)

    def test_the_command_list_covers_the_cli(self) -> None:
        # A new subcommand must be added to the offline run, or excluded here
        # with a reason.
        parser = build_parser()
        subparsers = next(a for a in parser._actions if a.dest == "command")
        covered = {
            "init", "new", "lint", "status", "graph", "backlinks", "impact", "query", "retrieve", "context",
            "pack", "review", "evidence", "snapshot", "verify-snapshot", "policy", "rules",
            "adopt", "explorer-index", "history", "check", "install-hooks", "doctor", "recover",
            "trace", "completion", "diff", "lsp", "agent", "workspace", "artifact", "claim",
            "compile-context", "verify-context", "deep-impact", "knowledge-at", "outcome", "knowledge", "import",
        }
        excluded = {"serve"}  # starts the optional Explorer dev server
        self.assertEqual(set(subparsers.choices), covered | excluded)


if __name__ == "__main__":
    unittest.main()
