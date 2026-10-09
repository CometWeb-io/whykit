"""Recovery-safe journal collection and bounded steady-state cost."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from whykit import io


class TransactionLifecycleTests(unittest.TestCase):
    def test_one_thousand_durable_writes_retain_no_journals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for n in range(1000):
                with io.vault_mutation_lock(root):
                    io.apply_transaction(root, {root / "record.md": str(n)})
            self.assertEqual((root / "record.md").read_text(encoding="utf-8"), "999")
            self.assertEqual(list((root / ".whykit/transactions").iterdir()), [])

    def test_only_old_pre_ready_journals_are_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tx = io.stage_transaction(root, {root / "record.md": "must never be applied"})
            (tx / "READY").unlink()
            with io.vault_mutation_lock(root):
                pass
            self.assertTrue(tx.exists())
            then = time.time() - 86401
            os.utime(tx, (then, then))
            with io.vault_mutation_lock(root):
                pass
            self.assertFalse(tx.exists())
            self.assertFalse((root / "record.md").exists())

    def test_partial_gc_without_commit_marker_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tx = io.stage_transaction(root, {root / "record.md": "first"})
            with patch.object(io.shutil, "rmtree", side_effect=OSError("cleanup interruption")):
                with self.assertRaises(OSError):
                    io.commit_transaction(root, tx)
            garbage = tx.with_name("gc-" + tx.name)
            (garbage / "COMMITTED").unlink()
            (root / "record.md").write_text("later edit", encoding="utf-8")
            with io.vault_mutation_lock(root):
                pass
            self.assertFalse(garbage.exists())
            self.assertEqual((root / "record.md").read_text(encoding="utf-8"), "later edit")

    @unittest.skipIf(os.name == "nt", "requires symlink privilege")
    def test_gc_never_follows_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            root.mkdir()
            other = Path(tmp) / "other"
            other.mkdir()
            (other / "keep").write_text("preserve", encoding="utf-8")
            state = io.safe_vault_dir(root, ".whykit/transactions")
            (state / ("gc-" + "a" * 24)).symlink_to(other, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                with io.vault_mutation_lock(root):
                    pass
            self.assertEqual((other / "keep").read_text(encoding="utf-8"), "preserve")
