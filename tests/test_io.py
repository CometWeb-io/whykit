from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from whykit.io import atomic_write_text


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_contents_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.md"
            path.write_text("before\n", encoding="utf-8")
            if os.name == "nt":
                # Windows exposes only the read-only flag through chmod; exact
                # POSIX permission modes are not a portable expectation there.
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                expected_writable = bool(path.stat().st_mode & stat.S_IWRITE)
            else:
                os.chmod(path, 0o640)
            atomic_write_text(path, "after\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "after\n")
            if os.name == "nt":
                actual_writable = bool(path.stat().st_mode & stat.S_IWRITE)
                self.assertEqual(actual_writable, expected_writable)
            else:
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(path.parent.glob(".ledger.md.whykit-tmp-*")), [])

    def test_atomic_write_creates_new_file_without_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "new.md"
            atomic_write_text(path, "content\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "content\n")
            self.assertEqual(list(path.parent.glob(".new.md.whykit-tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
