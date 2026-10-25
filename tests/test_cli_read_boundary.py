"""Public CLI reports include policy/formatting and protect coordination state."""
from __future__ import annotations

import concurrent.futures
import contextlib
import io as streams
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit import cli, io


class CliReadBoundaryTests(unittest.TestCase):
    def test_first_writer_race_discards_the_entire_rendered_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            def handler(args):
                print("MIXED_REPORT_CANARY")
                def mutate():
                    with io.vault_mutation_lock(root):
                        io.apply_transaction(root, {root / "notes/a.md": "# A\n"})
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(mutate).result(timeout=5)
                return 0
            output = streams.StringIO()
            with patch.object(cli, "cmd_query", handler), contextlib.redirect_stdout(output), contextlib.redirect_stderr(streams.StringIO()):
                code = cli.main(["query", "--root", str(root), "--json"])
            self.assertEqual(code, 2)
            self.assertNotIn("MIXED_REPORT_CANARY", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "io_error")

    def test_policy_rules_and_positional_lint_fail_closed_on_pending_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            io.stage_transaction(root, {root / "notes/a.md": "# A\n"})
            for argv in (["policy", "--root", str(root), "--json"], ["rules", "--root", str(root), "--json"],
                         ["lint", str(root), "--json"]):
                with self.subTest(command=argv[0]), contextlib.redirect_stdout(streams.StringIO()) as output, contextlib.redirect_stderr(streams.StringIO()):
                    self.assertEqual(cli.main(argv), 2)
                    self.assertEqual(json.loads(output.getvalue())["error"]["code"], "io_error")

    def test_exports_refuse_coordination_and_git_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            for command in ("graph", "snapshot"):
                for destination in (".whykit/mutation.lock", ".whykit/transactions/a", ".whykit/cache/a", ".git/config", ".WHYKIT/MUTATION.LOCK", ".GIT/config", ".whykit./mutation.lock ", ".whykit/mutation.lock:stream"):
                    with self.subTest(command=command, destination=destination), contextlib.redirect_stdout(streams.StringIO()), contextlib.redirect_stderr(streams.StringIO()):
                        self.assertEqual(cli.main([command, "--root", str(root), "--output", destination]), 2)
            with contextlib.redirect_stdout(streams.StringIO()):
                self.assertEqual(cli.main(["snapshot", "--root", str(root), "--output", ".whykit/snapshot.json"]), 0)
            self.assertTrue((root / ".whykit/snapshot.json").is_file())

    def test_ascii_console_still_receives_utf8_json_after_buffered_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            (root / "notes/probe.md").write_text('---\ntitle: "Résumé — 😀"\nsensitivity: public\n---\nProbe\n', encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "PYTHONIOENCODING": "ascii", "WHYKIT_NO_CACHE": "1"}
            result = subprocess.run([sys.executable, "-m", "whykit.cli", "query", "Probe", "--root", str(root), "--json"],
                                    capture_output=True, env=env, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            titles = [item["title"] for item in json.loads(result.stdout)["results"]]
            self.assertIn("Résumé — 😀", titles)

    def test_positional_lint_selects_its_own_vault_for_the_outer_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            def handler(args):
                self.assertIsNotNone(io._owned_lock(io._HELD_LOCKS.get() or {}, root))
                print("{}")
                return 0
            with patch.object(cli, "cmd_lint", handler), contextlib.redirect_stdout(streams.StringIO()):
                self.assertEqual(cli.main(["lint", str(root), "--json"]), 0)

    def test_check_annotations_keep_utf8_paths_on_ascii_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            (root / "notes/Łódź.md").write_text("# Missing required metadata\n", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "PYTHONIOENCODING": "ascii", "WHYKIT_NO_CACHE": "1"}
            result = subprocess.run([sys.executable, "-m", "whykit.cli", "check", "--root", str(root), "--format", "github"],
                                    capture_output=True, env=env, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("file=notes/Łódź.md", result.stdout.decode("utf-8"))
