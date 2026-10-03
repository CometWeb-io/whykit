#!/usr/bin/env python3
"""Create or update the one WhyKit comment on a pull request.

Used by the composite Action (``comment: "true"``). The body is the Markdown
written by ``whykit diff --format markdown``; its first line is a hidden
marker naming the vault, so each vault keeps exactly one comment that is edited
on every push instead of a new comment per run.

Only a comment that carries the marker *and* was written by a bot account is
ever edited: a person quoting the marker cannot have their comment replaced.
The token comes from the ``GITHUB_TOKEN`` environment variable and is never
printed. Failures to talk to the API are reported as workflow warnings and do
not fail the job: the comment is a convenience, the gate is a separate step.

Standard library only, like the rest of WhyKit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MARKER_RE = re.compile(r"^<!-- whykit-diff[^\n]*-->$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/(?!\.\.?$)[A-Za-z0-9_.-]+$")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
PAGE_SIZE = 100
MAX_PAGES = 50
TIMEOUT = 30


class ApiError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


def _api_base() -> str:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    # The token must never travel in clear text; plain HTTP is only for a
    # server on this machine (the test suite).
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
        raise ApiError(None, f"refusing a non-HTTPS API URL: {base}")
    return base


def _request(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "whykit-action")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - scheme checked above
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, f"{method} {urllib.parse.urlsplit(url).path} returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ApiError(None, f"{method} {urllib.parse.urlsplit(url).path} failed: {exc}") from None
    return json.loads(body) if body else None


def find_comment(base: str, repository: str, pull: int, marker: str, token: str) -> int | None:
    """The ID of this vault's existing comment, or None."""
    for page in range(1, MAX_PAGES + 1):
        url = f"{base}/repos/{repository}/issues/{pull}/comments?per_page={PAGE_SIZE}&page={page}"
        comments = _request("GET", url, token) or []
        for comment in comments:
            body = str(comment.get("body") or "")
            user = comment.get("user") or {}
            if body.split("\n", 1)[0].rstrip("\r") == marker and user.get("type") == "Bot":
                return int(comment["id"])
        if len(comments) < PAGE_SIZE:
            return None
    return None


def upsert(base: str, repository: str, pull: int, body: str, token: str) -> str:
    marker = body.split("\n", 1)[0].rstrip("\r")
    if not MARKER_RE.match(marker):
        raise ValueError("the comment body must start with the whykit-diff marker line")
    existing = find_comment(base, repository, pull, marker, token)
    if existing is not None:
        _request("PATCH", f"{base}/repos/{repository}/issues/comments/{existing}", token, {"body": body})
        return f"updated comment {existing}"
    created = _request("POST", f"{base}/repos/{repository}/issues/{pull}/comments", token, {"body": body})
    return f"created comment {created.get('id') if isinstance(created, dict) else ''}".rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", required=True, help="Markdown file written by `whykit diff --format markdown`")
    parser.add_argument("--repository", required=True, help="owner/name")
    parser.add_argument("--pull", required=True, help="pull request number")
    args = parser.parse_args(argv)
    if not REPOSITORY_RE.match(args.repository) or not args.pull.isdigit():
        print("::error::pr_comment: invalid repository or pull request number")
        return 2
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("::warning::WhyKit comment skipped: no token (set the github-token input)")
        return 0
    with open(args.body, encoding="utf-8") as handle:
        body = handle.read()
    try:
        outcome = upsert(_api_base(), args.repository, int(args.pull), body, token)
    except ValueError as exc:
        print(f"::error::pr_comment: {exc}")
        return 2
    except ApiError as exc:
        hint = (
            " Grant the job `pull-requests: write`; tokens for forks and Dependabot are read-only."
            if exc.status in (401, 403, 404)
            else ""
        )
        print(f"::warning::WhyKit could not post the decision diff comment: {exc}.{hint}")
        return 0
    print(f"WhyKit decision diff: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
