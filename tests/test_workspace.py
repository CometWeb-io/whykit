"""Local multi-vault status: useful counts without copying private records."""
from __future__ import annotations

import contextlib
import datetime as dt
import errno
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import fresh_vault  # noqa: E402
from whykit.parse_cache import persistent  # noqa: E402
from whykit.status import build_status  # noqa: E402
from whykit.workspace import WorkspaceError, _local_path, _regular_tree, build_workspace_status, load_workspace  # noqa: E402

TODAY = dt.date(2026, 10, 27)


def contents(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.first = self.base / "private-root-canary"
        self.second = self.base / "second"
        fresh_vault(self.first)
        fresh_vault(self.second)
        self.manifest = self.base / "workspace.toml"
        self.write_manifest([("alpha", self.first.name), ("beta", self.second.name)])

    def write_manifest(self, entries: list[tuple[str, str]], *, audience: str = "local") -> None:
        text = f'workspace_version = 1\naudience = {json.dumps(audience)}\n'
        for alias, root in entries:
            text += f'\n[[vaults]]\nalias = {json.dumps(alias)}\nroot = {json.dumps(root)}\n'
        self.manifest.write_text(text, encoding="utf-8")

    def read(self) -> tuple[dict, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            report = build_workspace_status(self.manifest, today=TODAY)
        return report, err.getvalue()

    def test_counts_match_existing_status_with_each_vault_policy_and_no_writes(self) -> None:
        (self.first / "whykit.toml").write_text('[defaults]\nstatus_due_days = 7\n', encoding="utf-8")
        (self.second / "whykit.toml").write_text('[defaults]\nstatus_due_days = 60\n', encoding="utf-8")
        before = contents(self.base)
        report = build_workspace_status(self.manifest, today=TODAY)
        self.assertEqual(contents(self.base), before)
        self.assertEqual(report["audience"], "local")
        self.assertEqual(report["as_of"], TODAY.isoformat())
        for item, root, days in zip(report["vaults"], (self.first, self.second), (7, 60), strict=True):
            expected = build_status(root, today=TODAY, due_days=days)
            self.assertEqual(item["status"], {
                "documents": expected["documents"], "errors": expected["errors"],
                "warnings": expected["warnings"], "review_due": len(expected["review_queue"]),
                "review_overdue": expected["review_overdue"], "due_days": days,
            })
        schema = json.loads((ROOT / "schemas/workspace-status.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])

    def test_private_note_metadata_and_finding_text_never_leave_the_vault(self) -> None:
        note = self.first / "notes/private-file-canary.md"
        note.write_text('''---
title: private-title-canary
type: note
status: approved
owner: private-owner-canary
sensitivity: restricted
review_by: 2026-10-26
source_of_truth: false
---
private-body-canary [[private-link-canary]]
''', encoding="utf-8")
        direct = build_status(self.first, today=TODAY)
        self.assertTrue(direct["review_queue"])
        self.assertTrue(direct["findings"])
        report, err = self.read()
        self.assertGreater(report["vaults"][0]["status"]["errors"], 0)
        encoded = json.dumps(report) + err
        for canary in (str(self.base), self.first.name, "private-file-canary", "private-title-canary",
                       "private-owner-canary", "private-body-canary", "private-link-canary", "restricted"):
            self.assertNotIn(canary, encoded)
        self.assertEqual(report["vaults"][0]["status"]["review_overdue"], direct["review_overdue"])
        self.assertEqual(err, "")

    def test_ambient_persistent_cache_does_not_turn_the_report_into_a_write(self) -> None:
        before = contents(self.base)
        with persistent():
            report = build_workspace_status(self.manifest, today=TODAY)
            self.assertIn("status", report["vaults"][0])
        self.assertEqual(contents(self.base), before)

    def test_untrusted_dynamic_status_fields_are_not_forwarded(self) -> None:
        untrusted = build_status(self.first, today=TODAY)
        untrusted.update({"new_private_field": "future-canary", "sensitivity": {"label-canary": 1}})
        with mock.patch("whykit.workspace.build_status", return_value=untrusted):
            report = build_workspace_status(self.manifest, today=TODAY)
        self.assertNotIn("canary", json.dumps(report))
        self.assertNotIn("findings", report["vaults"][0]["status"])

    def test_order_is_stable_and_paths_are_relative_to_manifest_not_cwd(self) -> None:
        self.write_manifest([("zeta", self.first.name), ("Alpha", str(self.second))])
        report = build_workspace_status(self.manifest, today=TODAY)
        self.assertEqual([item["alias"] for item in report["vaults"]], ["Alpha", "zeta"])
        self.assertEqual(report, build_workspace_status(self.manifest, today=TODAY))
        result = subprocess.run(
            [sys.executable, "-c", "import datetime, json, sys; from pathlib import Path; "
             "from whykit.workspace import build_workspace_status; "
             "print(json.dumps(build_workspace_status(Path(sys.argv[1]), today=datetime.date(2026, 10, 27))))",
             str(self.manifest)],
            cwd=self.first, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), report)
        self.assertEqual(result.stderr, "")

    def test_one_invalid_vault_does_not_hide_the_valid_vault(self) -> None:
        empty = self.base / "empty"
        empty.mkdir()
        self.write_manifest([("valid", self.first.name), ("missing", "absent"), ("empty", "empty")])
        report, err = self.read()
        self.assertEqual(err, "")
        by_alias = {item["alias"]: item for item in report["vaults"]}
        self.assertIn("status", by_alias["valid"])
        for alias in ("empty", "missing"):
            self.assertEqual(by_alias[alias]["error"], "vault_not_found")
        schema = json.loads((ROOT / "schemas/workspace-status.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(report, schema), [])

    def test_invalid_policy_and_read_failures_have_no_exception_details(self) -> None:
        (self.first / "whykit.toml").write_text('private_config_canary = [\n', encoding="utf-8")
        report, err = self.read()
        self.assertEqual(report["vaults"][0]["error"], "invalid_config")
        self.assertNotIn("private_config_canary", json.dumps(report) + err)
        for exc in (PermissionError("private-exception-canary"), RuntimeError("private-exception-canary"),
                    OverflowError("private-exception-canary")):
            with self.subTest(exc=exc), mock.patch("whykit.workspace.build_status", side_effect=exc):
                report, err = self.read()
            self.assertEqual(report["vaults"][1]["error"], "read_failed")
            self.assertNotIn("private-exception-canary", json.dumps(report) + err)

    def test_non_vault_roots_are_rejected_without_inventory_or_locking(self) -> None:
        empty = self.base / "empty"
        empty.mkdir()
        file = self.base / "plain-file"
        file.write_text("not a vault", encoding="utf-8")
        for root in (empty, file, self.base / "missing"):
            with self.subTest(root=root):
                self.write_manifest([("alpha", str(root))])
                with mock.patch("whykit.workspace._regular_tree") as inventory, \
                        mock.patch("whykit.workspace.vault_read_lock") as lock:
                    report = build_workspace_status(self.manifest, today=TODAY)
                inventory.assert_not_called()
                lock.assert_not_called()
                self.assertEqual(report["vaults"], [{"alias": "alpha", "error": "vault_not_found"}])

    def test_pending_recovery_refuses_only_that_vault(self) -> None:
        transaction = self.first / ".whykit/transactions/example"
        transaction.mkdir(parents=True)
        (transaction / "READY").write_text("", encoding="utf-8")
        before = contents(self.base)
        report, _ = self.read()
        self.assertEqual(report["vaults"][0]["error"], "read_failed")
        self.assertIn("status", report["vaults"][1])
        self.assertEqual(contents(self.base), before)

    def test_duplicate_case_aliases_rejected_before_any_vault_is_read(self) -> None:
        self.write_manifest([("alpha", self.first.name), ("ALPHA", self.second.name)])
        with mock.patch("whykit.workspace.build_status") as reader:
            with self.assertRaisesRegex(WorkspaceError, "^duplicate_alias$"):
                load_workspace(self.manifest)
            reader.assert_not_called()

    def test_duplicate_root_spellings_and_nested_roots_are_rejected(self) -> None:
        for other in (self.first.name, f"{self.first.name}/../{self.first.name}",
                      str(self.first), f"{self.first.name}/notes"):
            with self.subTest(other=other):
                self.write_manifest([("alpha", self.first.name), ("beta", other)])
                with self.assertRaisesRegex(WorkspaceError, "^overlapping_roots$"):
                    load_workspace(self.manifest)
        # Ancestors must be rejected regardless of manifest order.
        self.write_manifest([("alpha", f"{self.first.name}/notes"), ("beta", self.first.name)])
        with self.assertRaisesRegex(WorkspaceError, "^overlapping_roots$"):
            load_workspace(self.manifest)

    def test_public_and_other_audiences_are_refused_before_status_reads(self) -> None:
        for audience in ("public", "internal", "restricted", "LOCAL", ""):
            with self.subTest(audience=audience):
                self.write_manifest([("alpha", self.first.name)], audience=audience)
                with mock.patch("whykit.workspace.build_status") as reader:
                    with self.assertRaisesRegex(WorkspaceError, "^local_only$"):
                        build_workspace_status(self.manifest, today=TODAY)
                    reader.assert_not_called()

    def test_case_alias_of_same_directory_is_not_counted_twice(self) -> None:
        alias = self.first.with_name(self.first.name.upper())
        if not alias.exists():
            self.skipTest("filesystem paths are case-sensitive")
        self.write_manifest([("alpha", str(self.first)), ("beta", str(alias))])
        with self.assertRaisesRegex(WorkspaceError, "^overlapping_roots$"):
            load_workspace(self.manifest)

    def test_invalid_manifests_and_aliases_do_not_echo_values(self) -> None:
        valid = self.manifest.read_text(encoding="utf-8")
        for raw in ("private-manifest-canary = [", valid.replace("workspace_version = 1", "workspace_version = true"),
                    valid.replace('audience = "local"\n', ""), valid + 'unexpected = "private-manifest-canary"\n',
                    'workspace_version = 1\naudience = "local"\nvaults = []\n',
                    valid.replace('alias = "alpha"', 'alias = "../private-manifest-canary"'),
                    valid.replace('alias = "alpha"', 'alias = "private-manifest-canary\\n"'),
                    valid.replace('root = "second"', 'root = 42')):
            with self.subTest(raw=raw):
                self.manifest.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(WorkspaceError, "^invalid_manifest$") as caught:
                    build_workspace_status(self.manifest, today=TODAY)
                self.assertNotIn("private-manifest-canary", str(caught.exception))

    def symlink(self, target: Path, link: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except NotImplementedError:
            self.skipTest("symlinks unavailable")
        except OSError as exc:
            if exc.errno in {errno.EPERM, errno.EACCES, errno.ENOTSUP}:
                self.skipTest("symlink permission unavailable")
            raise

    def test_symlinked_manifest_root_or_ancestor_is_refused(self) -> None:
        linked_manifest = self.base / "linked.toml"
        self.symlink(self.manifest, linked_manifest)
        with self.assertRaisesRegex(WorkspaceError, "^unsafe_path$"):
            load_workspace(linked_manifest)
        link = self.base / "linked-root"
        self.symlink(self.first, link)
        for raw in (link.name, f"{link.name}/notes", f"{link.name}/../{self.second.name}"):
            with self.subTest(raw=raw):
                self.write_manifest([("alpha", raw)])
                with self.assertRaisesRegex(WorkspaceError, "^unsafe_path$"):
                    load_workspace(self.manifest)

    def test_file_directory_and_dangling_symlinks_fail_closed_before_status_reads(self) -> None:
        for target in (self.second / "Home.md", self.second, self.base / "absent", self.first / "Home.md"):
            link = self.first / "notes/redirect"
            with self.subTest(target=target):
                self.symlink(target, link)
                try:
                    with mock.patch("whykit.workspace.build_status", wraps=build_status) as reader:
                        report = build_workspace_status(self.manifest, today=TODAY)
                        self.assertEqual(report["vaults"][0]["error"], "unsafe_path")
                        self.assertIn("status", report["vaults"][1])
                        self.assertEqual([call.args[0] for call in reader.call_args_list], [self.second])
                finally:
                    link.unlink()

    def test_config_symlink_is_not_read(self) -> None:
        outside = self.base / "private-policy.toml"
        outside.write_text('private-policy-canary = [\n', encoding="utf-8")
        (self.first / "whykit.toml").unlink()
        self.symlink(outside, self.first / "whykit.toml")
        report, err = self.read()
        self.assertEqual(report["vaults"][0]["error"], "unsafe_path")
        self.assertNotIn("private-policy-canary", json.dumps(report) + err)

    def test_windows_reparse_metadata_is_refused_on_paths_and_in_vault_tree(self) -> None:
        # Deterministic metadata check; actual Windows junction behavior needs Windows CI.
        redirect = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with mock.patch.object(Path, "lstat", return_value=redirect):
            with self.assertRaisesRegex(WorkspaceError, "^unsafe_path$"):
                _local_path(self.first)
            with self.assertRaisesRegex(WorkspaceError, "^unsafe_path$"):
                _regular_tree(self.first)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_file_is_refused_without_blocking(self) -> None:
        (self.first / ".whykit").mkdir()
        for relative in ("notes/input.md", ".whykit/mutation.lock"):
            with self.subTest(relative=relative):
                path = self.first / relative
                os.mkfifo(path)
                try:
                    with mock.patch("whykit.workspace.vault_read_lock", side_effect=AssertionError("must preflight before lock")):
                        # Isolate this vault: the healthy one would acquire a lock.
                        self.write_manifest([("alpha", self.first.name)])
                        report, _ = self.read()
                    self.assertEqual(report["vaults"][0]["error"], "unsafe_path")
                finally:
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
