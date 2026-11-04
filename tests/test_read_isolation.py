"""Readers must not expose the midpoint of a governed multi-file write."""
from __future__ import annotations

import concurrent.futures
import asyncio
import contextvars
import errno
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
    def assert_lock_refused_in_child(self, root, mode, *, swap_fifo=False):
        child = subprocess.run(
            [sys.executable, "-c", """
import sys
import os
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from whykit import io
lock = io.vault_read_lock if sys.argv[2] == 'read' else io.vault_mutation_lock
real_open = os.open
def swapped_open(path, flags, *args, **kwargs):
    Path(path).unlink()
    os.mkfifo(path)
    return real_open(path, flags, *args, **kwargs)
try:
    with (patch.object(io.os, 'open', side_effect=swapped_open) if sys.argv[3] == 'swap' else nullcontext()), lock(Path(sys.argv[1]), timeout=0):
        raise SystemExit('unsafe mutation lock was accepted')
except (OSError, RuntimeError):
    print('refused')
""", str(root), mode, "swap" if swap_fifo else "static"], capture_output=True, text=True, timeout=2,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout, "refused\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_fifo_mutation_lock_fails_without_blocking_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            os.mkfifo(root / ".whykit/mutation.lock")
            for mode in ("read", "write"):
                with self.subTest(mode=mode):
                    self.assert_lock_refused_in_child(root, mode)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_fifo_swapped_in_at_open_also_fails_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            path = root / ".whykit/mutation.lock"
            for mode in ("read", "write"):
                with self.subTest(mode=mode):
                    if path.exists():
                        path.unlink()
                    path.write_bytes(b"original")
                    self.assert_lock_refused_in_child(root, mode, swap_fifo=True)

    def test_directory_mutation_lock_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit/mutation.lock").mkdir(parents=True)
            for mode in ("read", "write"):
                with self.subTest(mode=mode):
                    self.assert_lock_refused_in_child(root, mode)

    @unittest.skipIf(os.name == "nt", "symlink creation may require privileges")
    def test_symlink_mutation_lock_cannot_read_or_overwrite_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            target = root / "private.txt"
            target.write_bytes(b"private lock target")
            (root / ".whykit/mutation.lock").symlink_to(target)
            for mode in ("read", "write"):
                with self.subTest(mode=mode):
                    self.assert_lock_refused_in_child(root, mode)
                    self.assertEqual(target.read_bytes(), b"private lock target")

    @unittest.skipIf(os.name == "nt", "requires a POSIX character device")
    def test_lock_checks_the_opened_descriptor_and_closes_special_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            path = root / ".whykit/mutation.lock"
            for lock in (io.vault_read_lock, io.vault_mutation_lock):
                with self.subTest(lock=lock.__name__):
                    path.write_bytes(b"unchanged")
                    descriptor = os.open(os.devnull, os.O_RDWR)
                    try:
                        with patch.object(io.os, "open", return_value=descriptor):
                            with self.assertRaises(OSError):
                                with lock(root, timeout=0):
                                    self.fail("special file descriptor accepted")
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                        self.assertEqual(path.read_bytes(), b"unchanged")
                    finally:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires no-follow open")
    def test_last_component_symlink_swap_is_refused_at_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            path = root / ".whykit/mutation.lock"
            target = root / "private.txt"
            target.write_bytes(b"private lock target")
            real_open = os.open
            def swapped_open(filename, flags, *args, **kwargs):
                path.unlink()
                path.symlink_to(target)
                return real_open(filename, flags, *args, **kwargs)
            for lock in (io.vault_read_lock, io.vault_mutation_lock):
                with self.subTest(lock=lock.__name__):
                    if path.is_symlink():
                        path.unlink()
                    path.write_bytes(b"original")
                    with patch.object(io.os, "open", side_effect=swapped_open):
                        with self.assertRaises(OSError):
                            with lock(root, timeout=0):
                                self.fail("symlink swap accepted")
                    self.assertEqual(target.read_bytes(), b"private lock target")

    def test_an_async_child_cannot_inherit_the_parent_write_lock(self):
        async def check(root):
            async def read():
                with self.assertRaises(OSError):
                    with io.vault_read_lock(root, timeout=0.01):
                        self.fail("child task reused its parent's exclusive lock")
            with io.vault_mutation_lock(root):
                await asyncio.create_task(read())
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(check(Path(tmp).resolve()))

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
                       lambda: build_explorer_index(root),
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

    def test_failed_shared_acquisition_does_not_unlock_or_mask_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".whykit").mkdir()
            (root / ".whykit/mutation.lock").write_bytes(b"writer")
            handles = []
            def denied(handle):
                handles.append(handle)
                raise PermissionError(errno.EACCES, "writer owns range")
            with patch.object(io, "_acquire_shared", side_effect=denied), patch.object(
                io, "_release_exclusive", side_effect=PermissionError(errno.EACCES, "not owned")
            ) as release:
                with self.assertRaises(OSError) as caught:
                    with io.vault_read_lock(root, timeout=0):
                        self.fail("busy lock yielded a reader")
                self.assertEqual(caught.exception.errno, errno.EBUSY)
                release.assert_not_called()
            self.assertTrue(handles)
            self.assertTrue(all(handle.closed for handle in handles))
