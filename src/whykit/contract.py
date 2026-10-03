"""The machine contract shared by every command that writes JSON to stdout.

Success payloads carry ``contract_version``. Failures that stop a command from
producing its report are written as one error object instead::

    {"contract_version": 1, "error": {"code": "...", "message": "...", "hint": "..."}}

``code`` is stable and listed in :data:`ERROR_CODES`; ``message`` and ``hint``
are for people and may be reworded. Exit codes do not change: the error object
is an additional channel, and the same human-readable text still goes to
stderr, so scripts that already read stderr keep working.

See ``docs/automation.md`` for the full policy and ``schemas/`` for the shapes.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .console import emit_machine
from .messages import no_vault

CONTRACT_VERSION = 1

# Stable error codes. Adding a code is a compatible change; renaming or removing
# one is a breaking change and needs a CHANGELOG entry, exactly like a lint rule
# code. Keep this table, schemas/error.schema.json and docs/automation.md in
# step; tests/test_machine_contract.py enforces it.
ERROR_CODES: dict[str, str] = {
    "usage": "The command line could not be parsed, or options conflict.",
    "invalid_argument": "An option or argument value is malformed or out of range (a date, a number, an ID, a snapshot file).",
    "invalid_target": "The named source or destination exists but cannot be used by this command.",
    "vault_not_found": "No WhyKit vault at --root, or at or above the working directory.",
    "invalid_config": "whykit.toml could not be read or failed validation.",
    "not_found": "The vault record named as the command's target does not exist.",
    "unsafe_path": "A path was refused because it escapes the vault or passes through a symlink.",
    "target_exists": "The command refused to overwrite existing content.",
    "operation_rejected": "The vault refused the requested change, e.g. an already retired ID or a reference to an unknown record.",
    "missing_dependency": "A required external tool or bundled file is missing (git, the vault template).",
    "git_error": "Git could not answer, e.g. an unknown ref or a directory that is not a repository.",
    "vault_invalid": "The vault is broken in a way that blocks this command: lint errors, or a missing register or log.",
    "io_error": "The filesystem refused a read or write.",
    "interrupted": "The command was interrupted.",
    "internal_error": "WhyKit itself failed: an unexpected exception, which is a bug to report.",
}

# Where the internal_error hint sends people. Kept here so the CLI and the docs
# test agree on one address.
ISSUES_URL = "https://github.com/CometWeb-io/whykit/issues"

# Each code has exactly one exit code. 1 means the command ran and the answer is
# a problem with the vault (a missing target, a broken vault); 2 means it could
# not run as asked; 70 means WhyKit crashed. Everything not listed here exits 2.
ERROR_EXIT_CODES: dict[str, int] = {code: 2 for code in ERROR_CODES} | {
    "not_found": 1,
    "vault_invalid": 1,
    "interrupted": 130,
    # EX_SOFTWARE from sysexits.h: distinct from 1 and 2, so a CI job can tell
    # "WhyKit broke" from "the vault is wrong" and "the job is misconfigured".
    "internal_error": 70,
}


class TargetNotFound(ValueError):
    """The record a command was asked to act on does not exist.

    A ``ValueError`` so callers that already catch ``ValueError`` keep working;
    the CLI reports it as ``not_found`` with exit code 1, matching read commands
    that report a missing target as ``"exists": false`` with exit code 1.
    """


# The JSON Schema in ``schemas/`` that each command's JSON stdout follows.
# Every command that accepts ``--json`` (or always writes JSON) is listed; a
# test walks the argument parser to keep this table complete.
OUTPUT_SCHEMAS: dict[str, str] = {
    "init": "init-result.schema.json",
    "lint": "lint-report.schema.json",
    "new decision": "record-create.schema.json",
    "new evidence": "record-create.schema.json",
    "new note": "record-create.schema.json",
    "status": "status-report.schema.json",
    "graph": "graph.schema.json",
    "backlinks": "backlinks-report.schema.json",
    "impact": "impact-report.schema.json",
    "trace": "trace-report.schema.json",
    "query": "query-result.schema.json",
    "context": "context-pack.schema.json",
    "pack": "context-bundle.schema.json",
    "review list": "review-queue.schema.json",
    "review record": "review-record-result.schema.json",
    "snapshot": "snapshot.schema.json",
    "verify-snapshot": "snapshot-verify.schema.json",
    "check": "check-report.schema.json",
    "policy": "policy.schema.json",
    "evidence list": "evidence-list.schema.json",
    "evidence retire": "evidence-retire-result.schema.json",
    "adopt": "adopt-report.schema.json",
    "history": "history-report.schema.json",
    "diff": "diff-report.schema.json",
    "rules": "rule-catalog.schema.json",
    "rules <code>": "rule-detail.schema.json",
    "doctor": "doctor-report.schema.json",
    "explorer-index": "explorer-index.schema.json",
}
ERROR_SCHEMA = "error.schema.json"

# Non-default machine formats that are not the JSON contract itself (so they
# carry no top-level ``contract_version``) but still have a published schema.
# ``--format github`` is GitHub's workflow-command text and has none.
FORMAT_SCHEMAS: dict[str, str] = {
    "lint --format sarif": "lint-sarif.schema.json",
}

_HINT_PREFIX = "hint: "


def _split_hint(text: str) -> tuple[str, str | None]:
    """Separate trailing ``hint: ...`` lines from a human error message."""
    lines = text.rstrip("\n").split("\n")
    hints: list[str] = []
    while lines and lines[-1].startswith(_HINT_PREFIX):
        hints.insert(0, lines.pop()[len(_HINT_PREFIX):])
    message = "\n".join(lines).strip()
    return message, ("\n".join(hints) if hints else None)


def error_payload(code: str, message: str, hint: str | None = None) -> dict[str, Any]:
    """Build the error object. ``code`` must be one of :data:`ERROR_CODES`."""
    if code not in ERROR_CODES:
        raise ValueError(f"unregistered error code: {code}")
    return {
        "contract_version": CONTRACT_VERSION,
        "error": {"code": code, "message": message, "hint": hint},
    }


def emit_error(
    code: str,
    text: str,
    *,
    json_mode: bool,
    hint: str | None = None,
    json_hint: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Report a failure and return its exit code (see :data:`ERROR_EXIT_CODES`).

    ``text`` is printed to stderr exactly as before, so human output does not
    change. ``hint``, when given, is printed verbatim on the next line; when it
    is omitted, trailing ``hint: ...`` lines of ``text`` become the JSON hint.
    ``json_hint`` sets the JSON hint without adding a line to the human output.
    With ``json_mode`` the error object is also written to stdout.
    """
    if code not in ERROR_CODES:
        raise ValueError(f"unregistered error code: {code}")
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    print(text, file=err)
    if hint is not None:
        print(hint, file=err)
    if json_mode:
        message, embedded_hint = _split_hint(text)
        if json_hint is None and hint is not None:
            json_hint = hint[len(_HINT_PREFIX):] if hint.startswith(_HINT_PREFIX) else hint
        if json_hint is None:
            json_hint = embedded_hint
        # Same UTF-8-safe path as every success payload (cp1252 consoles included).
        emit_machine(json.dumps(error_payload(code, message, json_hint), ensure_ascii=False, indent=2), file=out)
    return ERROR_EXIT_CODES[code]


def describe_os_error(exc: OSError) -> str:
    """One readable line for a filesystem refusal (no ``[Errno 13]`` prefix)."""
    reason = exc.strerror or exc.__class__.__name__
    if exc.filename is not None and exc.filename2 is not None:
        return f"{reason}: {exc.filename} -> {exc.filename2}"
    if exc.filename is not None:
        return f"{reason}: {exc.filename}"
    return str(exc) or reason


def vault_not_found(explicit: str | None, *, json_mode: bool) -> int:
    """The shared "no vault here" failure."""
    return emit_error("vault_not_found", no_vault(explicit), json_mode=json_mode)


def argv_wants_json(argv: list[str]) -> bool:
    """Best-effort: did this command line ask for JSON on stdout?

    Used only where the parsed namespace is not available yet, i.e. when the
    command line itself cannot be parsed. ``--json`` may be abbreviated, as
    argparse allows.
    """
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token == "--":
            break
        if len(token) >= 3 and token.startswith("--j") and "--json".startswith(token):
            return True
        if token == "--format=json":
            return True
        if token == "--format" and index + 1 < len(tokens) and tokens[index + 1] == "json":
            return True
    return "explorer-index" in tokens[:1]
