"""Independent policies, failures and identities in a read-only workspace."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _jsonschema import validate
from test_machine_contract import call
from whykit.status import build_status, build_workspace_status

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 17)


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.vaults = [self.base / name for name in ("one", "two")]
        for root in self.vaults:
            shutil.copytree(ROOT / "examples/northline", root, ignore=shutil.ignore_patterns(".whykit"))
        self.schema = json.loads((ROOT / "schemas/workspace-report.schema.json").read_text(encoding="utf-8"))

    def fingerprint(self) -> dict:
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.base.rglob("*") if p.is_file()}

    def test_independent_policy_windows_and_read_only_cli(self) -> None:
        for root, window in zip(self.vaults, (0, 200), strict=True):
            config = root / "whykit.toml"
            config.write_text(config.read_text(encoding="utf-8").replace("status_due_days = 30", f"status_due_days = {window}"), encoding="utf-8")
        before = self.fingerprint()
        code, output, errors = call("workspace", *map(str, self.vaults), "--today", TODAY.isoformat(), "--json")
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(validate(report, self.schema), [])
        status_schema = json.loads((ROOT / "schemas/status-report.schema.json").read_text(encoding="utf-8"))
        for entry, root, window in zip(report["vaults"], self.vaults, (0, 200), strict=True):
            self.assertEqual(entry["due_days"], window)
            self.assertEqual(entry["status"], build_status(root, today=TODAY, due_days=window))
            self.assertEqual(validate(entry["status"], status_schema), [])
        self.assertLess(len(report["vaults"][0]["status"]["review_queue"]), len(report["vaults"][1]["status"]["review_queue"]))
        self.assertEqual(self.fingerprint(), before)

    def test_missing_and_malformed_vaults_do_not_hide_healthy_result(self) -> None:
        (self.vaults[0] / "whykit.toml").write_text("not valid TOML = [", encoding="utf-8")
        report = build_workspace_status([self.base, *self.vaults], today=TODAY)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual([e.get("error", {}).get("code") for e in report["vaults"]], ["vault_not_found", "invalid_config", None])
        self.assertEqual(report["vaults"][2]["status"]["root"], str(self.vaults[1]))
        self.assertEqual(validate(report, self.schema), [])

    def test_lint_policies_and_colliding_record_ids_stay_independent(self) -> None:
        for root in self.vaults:
            home = root / "Home.md"
            home.write_text(home.read_text(encoding="utf-8") + "\n[[missing-workspace-target]]\n", encoding="utf-8")
        with (self.vaults[0] / "whykit.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[rules.overrides."wikilink.missing"]\nlevel = "warning"\nreason = "Synthetic demonstration policy."\n')
        report = build_workspace_status(self.vaults, today=TODAY)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual([e["exit_code"] for e in report["vaults"]], [0, 1])
        self.assertEqual([e["status"]["errors"] for e in report["vaults"]], [0, 1])
        self.assertTrue(all(e["status"]["decisions"] == 11 for e in report["vaults"]))

    def test_overlapping_roots_are_rejected_without_reading_their_notes(self) -> None:
        nested = self.vaults[0] / "nested"
        shutil.copytree(ROOT / "examples/tiny", nested)
        with patch("whykit.status.build_status", wraps=build_status) as read:
            report = build_workspace_status([self.vaults[0], nested], today=TODAY)
        self.assertEqual(report["exit_code"], 2)
        self.assertTrue(all(e["error"]["code"] == "invalid_target" for e in report["vaults"]))
        read.assert_not_called()
        self.assertEqual(validate(report, self.schema), [])

    def test_faults_are_isolated_and_internal_error_has_precedence(self) -> None:
        original = build_status

        def fail(root, **kwargs):
            if root == self.vaults[0]:
                raise RuntimeError("private-sentinel")
            return original(root, **kwargs)

        with patch("whykit.status.build_status", side_effect=fail):
            report = build_workspace_status([self.base / "missing", *self.vaults], today=TODAY)
        self.assertEqual(report["exit_code"], 70)
        self.assertIn("status", report["vaults"][2])
        self.assertNotIn("private-sentinel", json.dumps(report))
        with patch("whykit.status.build_status", side_effect=PermissionError):
            report = build_workspace_status(self.vaults, today=TODAY)
        self.assertEqual(report["exit_code"], 2)
        self.assertTrue(all(e["error"]["code"] == "io_error" for e in report["vaults"]))

    def test_aliases_count_once_and_explicit_window_overrides_each_policy(self) -> None:
        roots = [self.vaults[0], self.vaults[0] / ".." / "one", self.vaults[1]]
        report = build_workspace_status(roots, today=TODAY, due_days=7)
        self.assertEqual(len(report["vaults"]), 2)
        self.assertEqual([e["due_days"] for e in report["vaults"]], [7, 7])
        if hasattr(Path, "symlink_to"):
            alias = self.base / "alias"
            try:
                alias.symlink_to(self.vaults[0], target_is_directory=True)
            except OSError:
                return
            report = build_workspace_status([*self.vaults, alias], today=TODAY)
            self.assertEqual(len(report["vaults"]), 2)

    def test_cli_validation_and_strict_exit(self) -> None:
        for args in (("--today", "2026-02-30"), ("--due-days", "-1")):
            code, output, _ = call("workspace", str(self.vaults[0]), "--json", *args)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output)["error"]["code"], "invalid_argument")
        with self.assertRaises(ValueError):
            build_workspace_status([])
        with patch("whykit.status.build_status", return_value={"errors": 0, "warnings": 1, "review_queue": [], "review_overdue": 0}):
            code, output, _ = call("workspace", str(self.vaults[0]), "--strict", "--json")
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output)["exit_code"], 1)
        code, output, errors = call("workspace", *map(str, self.vaults), "--today", TODAY.isoformat())
        self.assertEqual(code, 0, errors)
        self.assertIn("WhyKit workspace", output)
        self.assertTrue(all(str(root) in output for root in self.vaults))

    def test_case_aliases_on_a_case_insensitive_filesystem_count_once(self) -> None:
        alias = self.base / "ONE"
        if not alias.is_dir():
            self.skipTest("requires a case-insensitive filesystem")
        report = build_workspace_status([self.vaults[0], alias], today=TODAY)
        self.assertEqual(len(report["vaults"]), 1)
        nested = alias / "nested"
        shutil.copytree(ROOT / "examples/tiny", nested)
        report = build_workspace_status([self.vaults[0], nested], today=TODAY)
        self.assertTrue(all(e["error"]["code"] == "invalid_target" for e in report["vaults"]))

    def test_schema_rejects_missing_state_and_conflicting_success_error(self) -> None:
        report = build_workspace_status(self.vaults, today=TODAY)
        del report["vaults"][0]["status"]
        self.assertTrue(validate(report, self.schema))
        report = build_workspace_status(self.vaults, today=TODAY)
        report["vaults"][0]["error"] = {"code": "io_error", "message": "failure"}
        self.assertTrue(validate(report, self.schema))
        report = build_workspace_status(self.vaults, today=TODAY)
        del report["vaults"][0]["status"]["documents"]
        self.assertTrue(validate(report, self.schema))
        with self.assertRaises(ValueError):
            validate({}, {"$ref": "https://untrusted.example/schema.json"})
