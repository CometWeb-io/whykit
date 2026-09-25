#!/usr/bin/env python3
"""Fail when commit identities or attribution trailers name AI tools."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

AI_TOOL_IDENTITY = re.compile(
    r"(?<![a-z0-9])(?:claude|anthropic|grok|xai|chatgpt|openai|codex|"
    r"copilot|gemini|cursor|cline|windsurf)(?![a-z0-9])",
    re.IGNORECASE,
)
COMMIT_FORMAT = "%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00"


def _commit_records(repo: Path) -> list[tuple[str, tuple[str, ...], str]]:
    result = subprocess.run(
        ["git", "log", "--all", "HEAD", f"--format={COMMIT_FORMAT}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.split("\0")
    records: list[tuple[str, tuple[str, ...], str]] = []
    for offset in range(0, len(fields) - 1, 6):
        chunk = fields[offset : offset + 6]
        if len(chunk) != 6:
            break
        sha, author_name, author_email, committer_name, committer_email, message = chunk
        if not sha.strip():
            continue
        records.append(
            (
                sha.strip(),
                tuple(
                    value.strip()
                    for value in (
                        author_name,
                        author_email,
                        committer_name,
                        committer_email,
                    )
                ),
                message.strip("\n"),
            )
        )
    return records


def scan_repository(repo: Path) -> tuple[int, list[str]]:
    records = _commit_records(repo)
    offending: list[str] = []
    for sha, identities, message in records:
        if any(AI_TOOL_IDENTITY.search(value) for value in identities):
            offending.append(sha)
            continue

        parsed = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            cwd=repo,
            check=True,
            capture_output=True,
            input=message,
            text=True,
        )
        if any(AI_TOOL_IDENTITY.search(value) for value in parsed.stdout.splitlines()):
            offending.append(sha)
    return len(records), offending


def main() -> int:
    repo = Path.cwd()
    try:
        count, offending = scan_repository(repo)
    except (OSError, subprocess.CalledProcessError):
        print("Unable to inspect Git commit attribution metadata.", file=sys.stderr)
        return 2

    if offending:
        print(
            "AI-tool identity or attribution trailer found in commit metadata: "
            + ", ".join(offending)
            + ". Identity values are omitted from this output."
        )
        return 1

    print(f"Checked {count} commits; no AI-tool identities or attribution trailers found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
