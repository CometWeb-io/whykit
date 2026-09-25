"""Concurrent ID allocation and ledger mutations must not collide."""
from __future__ import annotations

import concurrent.futures
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"

sys.path.insert(0, str(ROOT / "src"))

from whykit.scaffold import create_decision, create_evidence  # noqa: E402

EVIDENCE_ID_RE = re.compile(r"^E-\d{3,}$")
DECISION_ID_RE = re.compile(r"^D-\d{3,}$")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True)


class MutationLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        result = run("init", "--minimal", str(self.vault))
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parallel_evidence_ids_are_unique(self) -> None:
        workers = 12

        def make_one(index: int) -> str:
            return create_evidence(
                self.vault,
                source=f"source-{index}",
                location=f"https://example.invalid/{index}",
                kind="interview",
                claims=f"claim-{index}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            ids = list(pool.map(make_one, range(workers)))

        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(EVIDENCE_ID_RE.fullmatch(value) for value in ids))
        register = (self.vault / "00-context" / "evidence-register.md").read_text(encoding="utf-8")
        for evidence_id in ids:
            self.assertEqual(register.count(f"| {evidence_id} |"), 1)

    def test_parallel_writers_can_create_metadata_directory_at_the_same_time(self) -> None:
        metadata_dir = self.vault / ".whykit"
        shutil.rmtree(metadata_dir, ignore_errors=True)
        rendezvous = threading.Barrier(2)
        original_mkdir = Path.mkdir

        def concurrent_mkdir(path: Path, *args, **kwargs) -> None:
            if path == metadata_dir:
                rendezvous.wait(timeout=5)
            original_mkdir(path, *args, **kwargs)

        def make_one(index: int) -> str:
            return create_evidence(
                self.vault,
                source=f"racing-source-{index}",
                location=f"https://example.invalid/racing/{index}",
                kind="interview",
                claims=f"racing-claim-{index}",
            )

        with patch.object(Path, "mkdir", concurrent_mkdir):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(make_one, range(2)))

        self.assertEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(metadata_dir.is_dir())

    def test_parallel_decision_ids_are_unique(self) -> None:
        workers = 8

        def make_one(index: int) -> str:
            decision_id, _ = create_decision(self.vault, f"Parallel decision {index}")
            return decision_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            ids = list(pool.map(make_one, range(workers)))

        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(DECISION_ID_RE.fullmatch(value) for value in ids))
        log = (self.vault / "06-decisions" / "decision-log.md").read_text(encoding="utf-8")
        for decision_id in ids:
            self.assertEqual(log.count(f"| {decision_id} |"), 1)


if __name__ == "__main__":
    unittest.main()
