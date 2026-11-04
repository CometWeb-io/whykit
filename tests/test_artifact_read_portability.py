"""Exercise the real bounded reader with Windows path/fd timestamp semantics."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from whykit import artifact_store as artifacts


class ArtifactReadPortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.source = self.directory / "source.bin"
        self.raw = b"snapshot\x00\xff\r\n"
        self.source.write_bytes(self.raw)

    def read_with_metadata(self, *, platform: str = "nt", path_before: dict | None = None,
                           path_after: dict | None = None, fd_before: dict | None = None,
                           fd_after: dict | None = None, fd_ctime_offset: int = 5000,
                           max_bytes: int | None = None, change_bytes: bool = False) -> bytes | None:
        # Only artifact_store.os is replaced. Pathlib and io.open_regular_binary
        # keep the actual host os and open/validate the real fixture descriptor.
        proxy = ModuleType("artifact_read_os_proxy")
        proxy.__dict__.update(vars(os))
        proxy.name = platform
        real_fstat, real_lstat, real_open = os.fstat, Path.lstat, artifacts.open_regular_binary
        original_ctime = real_lstat(self.source).st_ctime_ns
        reading_finished = False

        def snapshot(info, overrides: dict | None):
            fields = {name: getattr(info, name) for name in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            fields.update(overrides or {})
            return SimpleNamespace(**fields)

        def fstat(descriptor):
            overrides = fd_after if reading_finished else fd_before
            return snapshot(real_fstat(descriptor), {"st_ctime_ns": original_ctime + fd_ctime_offset, **(overrides or {})})

        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == self.source:
                return snapshot(info, path_after if reading_finished else path_before)
            return info

        class Handle:
            def __init__(inner, handle):
                inner.handle = handle

            def fileno(inner):
                return inner.handle.fileno()

            def read(inner, count):
                nonlocal reading_finished
                raw = inner.handle.read(count)
                if change_bytes:
                    with self.source.open("r+b") as writer:
                        writer.write(b"x" * len(self.raw))
                reading_finished = True
                return raw

        @contextmanager
        def opened(path):
            with real_open(path) as handle:
                yield Handle(handle)

        proxy.fstat = fstat
        with patch.object(artifacts, "os", proxy), patch.object(Path, "lstat", lstat), patch.object(artifacts, "open_regular_binary", opened):
            return artifacts._read_regular(self.source, max_bytes=len(self.raw) if max_bytes is None else max_bytes)

    def assert_changed(self, **kwargs) -> None:
        with self.assertRaisesRegex(ValueError, "input changed during read; retry"):
            self.read_with_metadata(**kwargs)

    def test_windows_stable_creation_time_and_fd_change_time_are_distinct(self) -> None:
        self.assertEqual(self.read_with_metadata(), self.raw)
        self.assertIs(artifacts.os, os)

    def test_windows_fd_ctime_only_change_still_refuses(self) -> None:
        baseline = self.source.lstat()
        self.assert_changed(fd_after={"st_ctime_ns": baseline.st_ctime_ns + 5001})

    def test_windows_path_ctime_only_change_still_refuses(self) -> None:
        baseline = self.source.lstat()
        self.assert_changed(path_after={"st_ctime_ns": baseline.st_ctime_ns + 1})

    def test_windows_identity_changes_refuse_even_with_same_size_and_mtime(self) -> None:
        baseline = self.source.lstat()
        for frame in ("path_before", "path_after", "fd_before", "fd_after"):
            for field in ("st_ino", "st_dev"):
                with self.subTest(frame=frame, field=field):
                    self.assert_changed(**{frame: {field: getattr(baseline, field) + 1}})

    def test_windows_cross_api_size_and_mtime_still_match(self) -> None:
        baseline = self.source.lstat()
        for field in ("st_size", "st_mtime_ns"):
            changed = {field: getattr(baseline, field) - 1}
            with self.subTest(field=field):
                self.assert_changed(fd_before=changed, fd_after=changed)

    def test_windows_in_place_bytes_change_cannot_hide_behind_restored_mtime(self) -> None:
        baseline = self.source.lstat()
        self.assert_changed(change_bytes=True,
                            path_after={"st_mtime_ns": baseline.st_mtime_ns},
                            fd_after={"st_mtime_ns": baseline.st_mtime_ns, "st_ctime_ns": baseline.st_ctime_ns + 5001})

    def test_posix_full_cross_api_ctime_comparison_is_unchanged(self) -> None:
        self.assertEqual(self.read_with_metadata(platform="posix", fd_ctime_offset=0), self.raw)
        self.assert_changed(platform="posix")

    def test_path_size_limit_precedes_open_and_fd_or_read_growth_refuse(self) -> None:
        with patch.object(artifacts, "open_regular_binary", side_effect=AssertionError("no oversized open")):
            with self.assertRaisesRegex(ValueError, "input exceeds byte limit"):
                artifacts._read_regular(self.source, max_bytes=len(self.raw) - 1)
        with self.assertRaisesRegex(ValueError, "input exceeds byte limit"):
            self.read_with_metadata(fd_before={"st_size": len(self.raw) + 1})
        with self.assertRaisesRegex(ValueError, "input exceeds byte limit"):
            self.read_with_metadata(max_bytes=len(self.raw) - 1,
                                    path_before={"st_size": len(self.raw) - 1}, fd_before={"st_size": len(self.raw) - 1})

    def test_missing_ok_and_missing_required_keep_their_contract(self) -> None:
        missing = self.directory / "absent"
        self.assertIsNone(artifacts._read_regular(missing, max_bytes=10, missing_ok=True))
        with self.assertRaises(FileNotFoundError):
            artifacts._read_regular(missing, max_bytes=10)

    def test_static_symlink_leaf_and_parent_remain_refused(self) -> None:
        leaf, parent = self.directory / "leaf-alias", self.directory / "parent-alias"
        try:
            leaf.symlink_to(self.source)
            parent.symlink_to(self.directory, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"host cannot create test symlinks: {type(exc).__name__}")
        for path in (leaf, parent / self.source.name):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                artifacts._read_regular(path, max_bytes=len(self.raw))
