"""Shared wording for user-facing CLI errors.

Every subcommand has to say "there is no vault here" or "that is not a date" at
some point. Saying it the same way everywhere, with the fix on the next line,
is what makes the tool feel like one program rather than fifteen scripts.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import TextIO

INIT_HINT = "create one with `whykit init <dir>`"


def no_vault(explicit: str | None = None, *, cwd: Path | None = None) -> str:
    """Describe why no vault was found, and what to do about it."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return (
            f"not a WhyKit vault: {path}\n"
            f"hint: a vault has Home.md and 00-context/ at its top level; "
            f"check --root, or {INIT_HINT}"
        )
    where = (cwd or Path.cwd()).resolve()
    return (
        f"no WhyKit vault found at or above {where}\n"
        f"hint: run from inside a vault, pass --root <vault>, or {INIT_HINT}"
    )


def print_no_vault(explicit: str | None = None, *, stream: TextIO | None = None) -> None:
    print(no_vault(explicit), file=stream or sys.stderr)


def invalid_date(flag: str, value: str) -> str:
    return f"{flag} is not a real ISO date: {value}\nhint: use YYYY-MM-DD, e.g. {dt.date.today().isoformat()}"


def parse_iso_date(flag: str, value: str) -> dt.date:
    """Parse a YYYY-MM-DD option value, raising ValueError with a usable message."""
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(invalid_date(flag, value)) from None
