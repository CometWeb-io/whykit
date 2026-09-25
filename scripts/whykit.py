#!/usr/bin/env python3
"""Run WhyKit from a source checkout without installing the package.

This shim is for repository contributors and keeps the source tree as the only
import path. See the README for the current end-user installation path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from whykit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
