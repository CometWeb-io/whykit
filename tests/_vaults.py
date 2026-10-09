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
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"whykit init {' '.join(flags)} failed: {result.stderr}")
        _TEMPLATES[key] = (template, stamped)
    template, stamped = _TEMPLATES[key]
    shutil.copytree(template, destination, symlinks=True)
    return stamped


def historical_decision(root: Path, title: str, *, status: str = "approved", **kwargs):
    """Construct legacy/invalid data for reader tests, without an approval claim."""
    from whykit.scaffold import (
        DECISION_STATUS_TO_LOG, _frontmatter_replace, _update_decision_log_status_text,
        create_decision,
    )
    did, path = create_decision(root, title, status="draft", **kwargs)
    path.write_text(_frontmatter_replace(path.read_text(encoding="utf-8"), "status", status), encoding="utf-8")
    log = root / "06-decisions/decision-log.md"
    today = kwargs.get("today", dt.date.today())
    text = _update_decision_log_status_text(log.read_text(encoding="utf-8"), did, DECISION_STATUS_TO_LOG[status], today)
    predecessor = kwargs.get("supersedes")
    if predecessor and status == "approved":
        old = next((root / "06-decisions").glob(f"{predecessor.lower()}-*.md"))
        old_text = _frontmatter_replace(old.read_text(encoding="utf-8"), "status", "superseded")
        old.write_text(_frontmatter_replace(old_text, "superseded_by", did), encoding="utf-8")
        text = _update_decision_log_status_text(text, predecessor, "superseded", today)
    log.write_text(text, encoding="utf-8")
    return did, path


def fill_decision(path: Path) -> None:
    """Complete synthetic reasoning; production users write their own record."""
    from whykit.placeholders import ALTERNATIVES_EMPTY_ROW, SCAFFOLD_SECTION_PROMPTS
    text = path.read_text(encoding="utf-8")
    replacements = {
        SCAFFOLD_SECTION_PROMPTS["Context"]: "The team needs a documented process.",
        SCAFFOLD_SECTION_PROMPTS["Decision"]: "Use the process described by this record.",
        SCAFFOLD_SECTION_PROMPTS["Rationale"]: "The cited evidence supports the chosen process.",
        ALTERNATIVES_EMPTY_ROW: "| Keep the old process | Familiar | Missing audit trail | Does not meet the requirement |",
        "### Positive\n\n-\n": "### Positive\n\n- A clear audit trail.\n",
        "### Negative and trade-offs\n\n-\n": "### Negative and trade-offs\n\n- Review takes time.\n",
    }
    for old, new in replacements.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def approved_decision(root: Path, title: str, *, today: dt.date, review_by: str | None = None, **kwargs):
    """Exercise the real preview/apply workflow with complete synthetic data."""
    from whykit.review import approve_decision
    from whykit.scaffold import create_decision
    did, path = create_decision(root, title, today=today, **kwargs)
    fill_decision(path)
    plan = approve_decision(root, did, reviewer="Ada Example", today=today, next_review=review_by)
    approve_decision(root, did, reviewer="Ada Example", today=today, next_review=review_by,
                     write=True, expected_sha256=plan["expected_sha256"])
    return did, path
