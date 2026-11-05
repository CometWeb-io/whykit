"""Claim-aware vaults must not be represented as safe for an older client."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _jsonschema import validate
from _vaults import ROOT
from whykit.cli import main
from whykit.knowledge_v2 import _NATIVE_IO_AVAILABLE


def run_guard(root: Path) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = main(["knowledge", "downgrade-check", "--root", str(root), "--json"])
        except SystemExit as exc:
            code = exc.code
    return code, json.loads(output.getvalue())


class DowngradeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "vault"

    def copy_example(self, name: str) -> None:
        shutil.copytree(ROOT / "examples" / name, self.root,
                        ignore=shutil.ignore_patterns(".whykit", ".git"))

    @unittest.skipUnless(_NATIVE_IO_AVAILABLE, "native safe inventory requires POSIX")
    def test_claim_dependencies_block_even_if_claim_snapshot_changes(self) -> None:
        self.copy_example("outcomes")
        for modified in (False, True):
            if modified:
                claim = self.root / "00-context/claims/c-001.md"
                claim.write_text(claim.read_text(encoding="utf-8") + "\nChanged synthetic claim.\n", encoding="utf-8")
            code, result = run_guard(self.root)
            if not modified:
                self.assertEqual(code, 1, result)
            self.assertIn(code, (1, 2), result)
            if code == 1:
                self.assertEqual(result["verdict"], "blocked")
                self.assertFalse(result["downgrade_authorized"])
            else:
                self.assertIn("error", result)
            self.assertNotIn("C-001", json.dumps(result))
            if code == 1:
                schema = json.loads((ROOT / "schemas/downgrade-check.schema.json").read_text(encoding="utf-8"))
                self.assertEqual(validate(result, schema), [])

    @unittest.skipUnless(_NATIVE_IO_AVAILABLE, "native safe inventory requires POSIX")
    def test_legacy_fixture_never_claims_a_safe_downgrade(self) -> None:
        self.copy_example("approval")
        code, result = run_guard(self.root)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["verdict"], "review_required")
        self.assertFalse(result["downgrade_authorized"])

    def test_unsupported_platform_refuses_instead_of_reporting_safe(self) -> None:
        self.copy_example("approval")
        with patch("whykit.knowledge_v2._NATIVE_IO_AVAILABLE", False):
            code, result = run_guard(self.root)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["error"]["code"], "operation_rejected")

    def test_missing_vault_fails_closed(self) -> None:
        code, result = run_guard(self.root)
        self.assertEqual(code, 2, result)
        self.assertIn("error", result)

    @unittest.skipUnless(_NATIVE_IO_AVAILABLE and os.environ.get("WHYKIT_034"),
                         "real 0.3.4 client is optional; set WHYKIT_034 to its executable")
    def test_real_034_trace_cannot_validate_changed_claim_pin(self) -> None:
        self.copy_example("outcomes")
        old = os.environ["WHYKIT_034"]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        version = subprocess.run([old, "--version"], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(version.stdout.strip(), "whykit 0.3.4")
        claim = self.root / "00-context/claims/c-001.md"
        claim.write_text(claim.read_text(encoding="utf-8") + "\nChanged synthetic claim.\n", encoding="utf-8")
        legacy = subprocess.run([old, "trace", "--root", str(self.root), "--decision", "D-003",
                                 "--today", "2026-09-17", "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(json.loads(legacy.stdout)["summary"]["live_with_gaps"], 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            preflight = main(["agent", "preflight", "--root", str(self.root), "--proposal",
                              str(self.root / "proposal.json"), "--max-sensitivity", "restricted",
                              "--today", "2026-09-17", "--json"])
        self.assertEqual(preflight, 1)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "blocked")
        code, guarded = run_guard(self.root)
        self.assertIn(code, (1, 2), guarded)
        self.assertIsNot(guarded.get("downgrade_authorized"), True)
