"""Recorded Git snapshots and asserted validity are independent time axes."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from _jsonschema import validate  # noqa: E402
from test_dependency_graph import write_note, write_register  # noqa: E402
from whykit.temporal_graph import knowledge_at  # noqa: E402
from whykit import diff as git_diff  # noqa: E402


class TemporalGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.root = self.repo / "kb"
        write_note(self.root, "Home.md")
        write_register(self.root)
        self.git("init", "-q")
        write_note(self.root, "old.md", "Original captured text", decision_id="D-001", status="approved",
                   valid_from="2026-10-15", valid_until="2026-11-01")
        self.first = self.commit("initial", "2026-10-20T10:00:00+02:00")

    def git(self, *args: str, **kwargs) -> str:
        return subprocess.run(["git", "-c", "user.name=Pat Example", "-c", "user.email=pat@example.com", *args],
                              cwd=self.repo, check=True, capture_output=True, text=True, **kwargs).stdout.strip()

    def commit(self, title: str, when: str = "2026-10-27T12:00:00+01:00") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", title,
                 env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
        return self.git("rev-parse", "HEAD")

    def report(self, **options) -> dict:
        return knowledge_at(self.root, valid_on=dt.date(2026, 10, 1), **options)

    def test_same_valid_on_changes_only_when_recorded_snapshot_changes(self) -> None:
        write_note(self.root, "old.md", "Corrected source", decision_id="D-001", status="approved",
                   valid_from="2026-09-01", valid_until="2026-11-01")
        second = self.commit("late correction")
        before = self.report(recorded_revision=self.first, target="D-001")
        after = self.report(recorded_revision=second, target="D-001")
        self.assertEqual(before["nodes"][0]["validity"]["state"], "not_yet")
        self.assertEqual(after["nodes"][0]["validity"]["state"], "active")
        self.assertEqual(after["recorded_revision"], second)
        self.assertEqual(after["recorded_at"], "2026-10-27T12:00:00+01:00")
        self.assertEqual(after["valid_on"], "2026-10-01")
        self.assertEqual(after["recorded_time_source"], "git_committer_assertion")
        schema = json.loads((ROOT / "schemas/temporal-graph.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(before, schema), [])
        self.assertEqual(validate(after, schema), [])

    def test_half_open_bounds_unknown_and_invalid_intervals_do_not_infer_created(self) -> None:
        cases = {
            "active.md": {"valid_from": "2026-10-01"},
            "expired.md": {"valid_from": "2026-09-01", "valid_until": "2026-10-01"},
            "unknown.md": {"created": "2020-01-01", "last_updated": "2026-01-01"},
            "end-only.md": {"valid_until": "2026-11-01"},
            "invalid.md": {"valid_from": "2026-10-02", "valid_until": "2026-10-01"},
            "empty.md": {"valid_from": ""},
            "alias.md": {"Valid_From": "2026-09-01"},
            "bad-date.md": {"valid_from": "2026-02-30"},
        }
        for path, metadata in cases.items():
            write_note(self.root, path, **metadata)
        self.commit("interval fixtures")
        states = {n["path"]: n["validity"]["state"] for n in self.report()["nodes"]}
        for path, expected in {"active.md": "active", "expired.md": "expired", "unknown.md": "unknown",
                               "end-only.md": "unknown", "invalid.md": "invalid", "empty.md": "invalid",
                               "alias.md": "invalid", "bad-date.md": "invalid"}.items():
            self.assertEqual(states[path], expected, path)
        for valid_on in ("not-a-date", "20261001", dt.datetime(2026, 10, 1)):
            with self.subTest(valid_on=valid_on), self.assertRaises(ValueError):
                knowledge_at(self.root, valid_on=valid_on)

    def test_snapshots_preserve_historical_paths_and_explicit_supersession(self) -> None:
        self.git("mv", "kb/old.md", "kb/renamed.md")
        write_note(self.root, "new.md", decision_id="D-002", supersedes="D-001", status="approved",
                   valid_from="2026-09-01")
        write_note(self.root, "renamed.md", decision_id="D-001", status="superseded", valid_from="2026-09-01")
        self.commit("record replacement")
        old = self.report(recorded_revision=self.first, target="D-001")
        report = self.report()
        self.assertEqual(old["nodes"][0]["path"], "old.md")
        self.assertIn({"from": "document:new.md", "to": "document:renamed.md", "type": "supersedes"}, report["edges"])
        self.assertIn({"node": "document:renamed.md", "reason": "overlapping_supersession"}, report["review_signals"])

    def test_dirty_working_tree_index_refs_and_head_are_not_modified_or_used(self) -> None:
        write_note(self.root, "old.md", title="dirty-working-tree-canary", valid_from="2000-01-01")
        self.git("add", "kb/old.md")
        write_note(self.root, "untracked.md", "untracked-body-canary")
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        report = self.report(target="D-001")
        after = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertNotIn("canary", json.dumps(report))
        self.assertEqual(report["recorded_revision"], self.first)

    def test_option_like_tree_and_missing_revisions_are_refused_before_materialization(self) -> None:
        for revision in ("--help", "--output=outside", "HEAD:kb/Home.md", "unknown-revision"):
            with self.subTest(revision=revision), mock.patch("whykit.temporal_graph.git_diff.materialize") as materialize:
                with self.assertRaises(subprocess.CalledProcessError):
                    self.report(recorded_revision=revision)
                materialize.assert_not_called()

    def test_git_replacement_objects_cannot_change_bytes_under_reported_sha(self) -> None:
        write_note(self.root, "old.md", title="replacement-canary", valid_from="2000-01-01")
        replacement = self.commit("replacement fixture")
        self.git("replace", self.first, replacement)
        with mock.patch("whykit.temporal_graph.git_diff.materialize") as materialize:
            with self.assertRaisesRegex(ValueError, "replacement objects"):
                self.report(recorded_revision=self.first)
            materialize.assert_not_called()

    def test_private_filter_applies_before_snapshot_graph_counts(self) -> None:
        before = self.report(allowed_sensitivities={"public"})
        write_note(self.root, "private-canary.md", "[[old]]", sensitivity="restricted")
        write_note(self.root, "alias-canary.md", "[[old]]", sensitivity="public", Sensitivity="restricted")
        self.commit("private note")
        after = self.report(allowed_sensitivities={"public"})
        self.assertEqual(after["nodes"], before["nodes"])
        self.assertEqual(after["stats"], before["stats"])
        self.assertNotIn("canary", json.dumps(after))
        self.assertIsNone(after["omitted_tree_entries"])
        self.assertEqual(self.report(max_nodes=1)["stats"]["returned_nodes"], 1)
        self.assertTrue(self.report(max_nodes=1)["truncated"])

    def test_temporary_snapshot_is_removed_on_success_and_failure(self) -> None:
        destinations = []
        original = git_diff.materialize

        def capture(*args):
            destinations.append(args[-1])
            return original(*args)

        with mock.patch("whykit.temporal_graph.git_diff.materialize", side_effect=capture):
            self.report()
        self.assertTrue(destinations)
        self.assertTrue(all(not p.exists() for p in destinations))

        def fail(*args):
            destinations.append(args[-1])
            raise RuntimeError("synthetic failure")

        with mock.patch("whykit.temporal_graph.git_diff.materialize", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.report()
        self.assertTrue(all(not p.exists() for p in destinations))

    def test_symlinks_and_unsafe_git_paths_cannot_escape_the_temporary_tree(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            outside = write_note(Path(other), "outside.md", "outside-body-canary")
            try:
                (self.root / "redirect.md").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.commit("symlink fixture")
            report = self.report()
            self.assertEqual(report["omitted_tree_entries"], 1)
            self.assertNotIn("outside-body-canary", json.dumps(report))
            self.assertFalse(self.report(target="redirect")["exists"])
        with mock.patch("whykit.temporal_graph.git_diff._list_tree", return_value=[("100644", "blob", "a" * 40, "../escape.md")]):
            report = self.report()
            self.assertFalse(report["exists"])
            self.assertEqual(report["omitted_tree_entries"], 1)

    def test_case_unicode_and_file_directory_aliases_fail_before_materialization(self) -> None:
        for paths in (("a.md", "A.md"), ("é.md", "e\u0301.md"), ("a", "A/b.md"), ("A/b.md", "a"), (".GIT/config",)):
            entries = [("100644", "blob", "a" * 40, path) for path in paths]
            with self.subTest(paths=paths), \
                    mock.patch("whykit.temporal_graph.git_diff._list_tree", return_value=entries), \
                    mock.patch("whykit.temporal_graph.git_diff.materialize") as materialize:
                with self.assertRaises(ValueError):
                    self.report()
                materialize.assert_not_called()

    def test_duplicate_validity_keys_and_equal_bounds_are_invalid(self) -> None:
        path = write_note(self.root, "duplicate.md", valid_from="2026-09-01")
        path.write_text(path.read_text(encoding="utf-8").replace("\n---\n", '\nvalid_from: "2026-08-01"\n---\n', 1), encoding="utf-8")
        write_note(self.root, "equal.md", valid_from="2026-10-01", valid_until="2026-10-01")
        write_note(self.root, "end-only.md", valid_until="2026-10-01")
        self.commit("invalid and bounded dates")
        states = {n["path"]: n["validity"]["state"] for n in self.report()["nodes"]}
        self.assertEqual(states["duplicate.md"], "invalid")
        self.assertEqual(states["equal.md"], "invalid")
        self.assertEqual(states["end-only.md"], "expired")

    def test_schema_covers_filtered_missing_and_truncated_snapshots(self) -> None:
        schema = json.loads((ROOT / "schemas/temporal-graph.schema.json").read_text(encoding="utf-8"))
        for options in ({"target": "missing"}, {"allowed_sensitivities": {"public"}}, {"max_nodes": 1}):
            with self.subTest(options=options):
                self.assertEqual(validate(self.report(**options), schema), [])


if __name__ == "__main__":
    unittest.main()
