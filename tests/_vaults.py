"""Shared fixtures: one real `whykit init` per layout, copied per test.

Spawning the CLI dominates suite runtime. Most tests only need a fresh vault
to mutate, not a fresh run of `init`, so the first request for a layout runs
the real command once and every later request gets a byte-for-byte copy.
Tests about `init` itself must keep calling the CLI directly.
"""
from __future__ import annotations

import atexit
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"

_CACHE_DIR: Path | None = None
_TEMPLATES: dict[tuple[str, ...], tuple[Path, dt.date]] = {}


def _cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path(tempfile.mkdtemp(prefix="whykit-test-vaults-"))
        atexit.register(shutil.rmtree, _CACHE_DIR, True)
    return _CACHE_DIR


def fresh_vault(destination: Path, *flags: str) -> dt.date:
    """Create a vault at `destination` as `whykit init [flags]` would.

    Returns the day the template was stamped, so date assertions can bracket
    the wall clock instead of assuming the run never crosses midnight.
    """
    key = tuple(flags)
    if key not in _TEMPLATES:
        template = _cache_dir() / f"vault-{len(_TEMPLATES)}"
        stamped = dt.date.today()
        result = subprocess.run(
            [sys.executable, str(CLI), "init", *flags, str(template)],
            text=True, capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"whykit init {' '.join(flags)} failed: {result.stderr}")
        _TEMPLATES[key] = (template, stamped)
    template, stamped = _TEMPLATES[key]
    shutil.copytree(template, destination, symlinks=True)
    return stamped
