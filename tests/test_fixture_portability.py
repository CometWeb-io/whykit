"""Fixture snapshots keep wire paths and pinned dates on every host."""
import datetime as dt
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from types import SimpleNamespace
import unittest

from _vaults import fresh_vault
from test_artifact_store import tree_bytes
from whykit.lint import load_note


class FixturePortabilityTests(unittest.TestCase):
    def test_snapshot_keys_keep_portable_paths_and_machine_state_filter(self) -> None:
        for path_type in (PurePosixPath, PureWindowsPath):
            with self.subTest(path_type=path_type):
                entries = [SimpleNamespace(is_file=lambda: True, read_bytes=lambda: b"exact bytes",
                                           relative_to=lambda _, name=name, path_type=path_type: path_type(name))
                           for name in ("00-context/evidence-register.md", ".whykit/mutation.lock")]
                snapshot = tree_bytes(SimpleNamespace(rglob=lambda _, entries=entries: entries))
                self.assertEqual(snapshot["00-context/evidence-register.md"], b"exact bytes")
                self.assertEqual([key for key in snapshot if not key.startswith(".whykit/")],
                                 ["00-context/evidence-register.md"])

    def test_explicit_fixture_dates_have_separate_cached_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = fresh_vault(root / "current")
            for name, day in (("first", dt.date(2026, 10, 27)), ("next", dt.date(2026, 10, 28)),
                              ("first-again", dt.date(2026, 10, 27))):
                vault = root / name
                self.assertEqual(fresh_vault(vault, today=day), day)
                for relative in ("00-context/evidence-register.md", "06-decisions/decision-log.md"):
                    self.assertEqual(load_note(vault / relative).front["created"], day.isoformat())
            self.assertEqual(fresh_vault(root / "current-again"), current)
            self.assertEqual(load_note(root / "current" / "06-decisions/decision-log.md").front["created"],
                             current.isoformat())
