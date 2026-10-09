"""Readers must not expose the midpoint of a governed multi-file write."""
from __future__ import annotations

import concurrent.futures
import contextvars
import tempfile
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit import io
from whykit.context import build_context
from whykit.explorer_index import build_explorer_index
from whykit.mcp_server import VaultTools
from whykit.status import build_status
from whykit.trace import build_trace
from whykit.vault_index import VaultIndex


class ReadIsolationTests(unittest.TestCase):
    def test_public_readers_wait_for_the_whole_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            halfway, release = threading.Event(), threading.Event()
            original = io.atomic_write_bytes
            first = root / "notes/a.md"
            second = root / "notes/b.md"

            def write(path, data):
                original(path, data)
                if path == first:
                    halfway.set()
                    if not release.wait(5):
                        raise TimeoutError("test writer barrier")

            def mutate():
                with io.vault_mutation_lock(root):
                    io.apply_transaction(root, {first: "# A\n", second: "# B\n"})

            readers = [lambda: VaultIndex.load(root), lambda: build_context(root, "Home"),
                       lambda: build_trace(root), lambda: build_status(root),
                       lambda: build_explorer_index(root, private=True),
                       lambda: VaultTools(root).status()]
            with patch.object(io, "atomic_write_bytes", write), concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
                writer = pool.submit(mutate)
                self.assertTrue(halfway.wait(5))
                futures = [pool.submit(reader) for reader in readers]
                try:
                    for reader in futures:
                        with self.assertRaises(concurrent.futures.TimeoutError):
                            reader.result(timeout=0.05)
                finally:
                    release.set()
                writer.result(timeout=5)
                for reader in futures:
                    reader.result(timeout=5)
            index = VaultIndex.load(root)
            self.assertIsNotNone(index.note_for(first))
            self.assertIsNotNone(index.note_for(second))

    def test_pending_crash_fails_closed_until_writer_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            with io.vault_mutation_lock(root):
                tx = io.stage_transaction(root, {root / "notes/a.md": "# A\n"})
            with self.assertRaises(OSError):
                VaultIndex.load(root)
            with io.vault_mutation_lock(root):
                pass
            self.assertFalse(tx.exists())
            self.assertIsNotNone(VaultIndex.load(root).note_for(root / "notes/a.md"))

    def test_read_does_not_create_local_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            VaultIndex.load(root)
            self.assertFalse((root / ".whykit").exists())

    def test_first_writer_is_detected_without_creating_state_for_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            def mutate():
                with io.vault_mutation_lock(root):
                    io.apply_transaction(root, {root / "notes/a.md": "# A\n"})
            with self.assertRaisesRegex(OSError, "first governed write"):
                with io.vault_read_lock(root):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(mutate).result(timeout=5)
            self.assertIsNotNone(VaultIndex.load(root).note_for(root / "notes/a.md"))

    def test_cli_in_another_process_cannot_read_a_writer_midpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            with io.vault_mutation_lock(root):
                io.apply_transaction(root, {root / "notes/a.md": "# A\n"})
                env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "WHYKIT_NO_CACHE": "1"}
                child = subprocess.Popen([sys.executable, "-m", "whykit.cli", "query", "--root", str(root), "--json"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                try:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.communicate(timeout=0.3)
                except BaseException:
                    child.kill()
                    child.communicate()
                    raise
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertIn("notes/a.md", stdout)

    def test_copied_context_in_another_thread_still_waits_for_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                with io.vault_mutation_lock(root):
                    io.apply_transaction(root, {root / "notes/a.md": "# A\n"})
                    context = contextvars.copy_context()
                    reader = pool.submit(context.run, VaultIndex.load, root)
                    with self.assertRaises(concurrent.futures.TimeoutError):
                        reader.result(timeout=0.05)
                reader.result(timeout=5)

    def test_expired_copied_context_does_not_reuse_a_released_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "vault"
            fresh_vault(root)
            with io.vault_mutation_lock(root):
                context = contextvars.copy_context()
            io.stage_transaction(root, {root / "notes/a.md": "# A\n"})
            with self.assertRaises(OSError):
                context.run(VaultIndex.load, root)
