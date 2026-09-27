"""WhyKit — a Git-native evidence and decision ledger.

Built and maintained by CometWeb. Apache-2.0.

    from whykit import lint_vault, find_vault_root, __version__
    from whykit import lint          # the module, not the function

`lint_vault` is deliberately not called `lint`: a re-export under that name would
shadow the `whykit.lint` submodule for anyone importing it.
"""
from __future__ import annotations

__version__ = "0.3.0.dev0"
__all__ = ["__version__", "TEMPLATE_DIR", "find_vault_root", "is_vault_root", "lint_vault"]

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"

from .lint import find_vault_root, is_vault_root  # noqa: E402
from .lint import lint as lint_vault  # noqa: E402
