"""Lint findings in the formats CI systems already read.

Two renderings of the same findings ``whykit lint --json`` reports:

* SARIF 2.1.0 (``--format sarif``), for code-scanning dashboards such as
  GitHub's. ``ruleId`` is the stable rule code, and every rule's ``helpUri``
  points at that code's anchor in ``docs/rules.md``.
* GitHub Actions workflow commands (``--format github``), which the runner
  turns into inline pull-request annotations.

Both speak in paths relative to the Git work tree, because that is what a
pull request diff and a code-scanning upload are keyed on. A vault in a
subdirectory (``--root knowledge``) therefore reports ``knowledge/Home.md``,
not ``Home.md``. Outside Git the paths stay vault-relative.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import __version__
from .rules import RULES, Rule

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/CometWeb-io/whykit"
RULES_DOC_URI = f"{INFORMATION_URI}/blob/main/docs/rules.md"
# The uriBaseId every result location is relative to: the Git work tree root
# (or the vault root outside Git). Code-scanning uploads resolve it against
# the checkout.
SRCROOT = "%SRCROOT%"
FINGERPRINT_KEY = "whykitFinding/v1"


def rule_anchor(code: str) -> str:
    """The fragment that addresses *code* in ``docs/rules.md``."""
    return code


def rule_help_uri(code: str) -> str:
    return f"{RULES_DOC_URI}#{rule_anchor(code)}"


def _git_location(root: Path) -> tuple[Path, str] | None:
    """Return ``(work tree top, vault prefix)`` when *root* is inside Git."""
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "--show-prefix"],
            capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=False,
        )
    except OSError:
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or not lines:
        return None
    prefix = lines[1].strip().replace("\\", "/") if len(lines) > 1 else ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return Path(lines[0].strip()), prefix


def source_root(root: Path) -> tuple[Path, str]:
    """The directory results are relative to, and the vault's prefix in it."""
    located = _git_location(root)
    if located is None:
        return root, ""
    return located


def _source_path(path: str, prefix: str) -> str:
    """A vault-relative finding path as a work-tree-relative POSIX path."""
    path = path.replace("\\", "/")
    if Path(path).is_absolute():
        return path
    if path in ("", "."):
        return prefix.rstrip("/") or "."
    return f"{prefix}{path}"


def _level(finding: Mapping[str, Any]) -> str:
    return "error" if finding["level"] == "error" else "warning"


def _fingerprint(code: str, path: str, message: str) -> str:
    # The line is deliberately left out: editing text above a finding must not
    # turn it into a "new" alert on the code-scanning page.
    digest = hashlib.sha256(f"{code}\0{path}\0{message}".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def is_custom(code: str) -> bool:
    """A team rule from whykit.toml; it has no entry in ``docs/rules.md``."""
    return code.startswith("custom.")


def _sarif_rule(rule: Rule) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": rule.code,
        "shortDescription": {"text": rule.summary},
        "fullDescription": {"text": rule.why},
        "help": {
            "text": f"{rule.summary}\nWhy: {rule.why}\nFix: {rule.fix}",
            "markdown": f"{rule.summary}\n\n**Why:** {rule.why}\n\n**Fix:** {rule.fix}",
        },
        "helpUri": rule_help_uri(rule.code),
        "defaultConfiguration": {"level": rule.default_level},
        "properties": {"tags": ["whykit", rule.code.split(".", 1)[0]]},
    }
    if is_custom(rule.code):
        del entry["helpUri"]
    if rule.security:
        entry["properties"]["tags"].append("security")
    return entry


def _custom_catalog(root: Path) -> list[Rule]:
    """Catalog entries for the vault's own custom rules (none if the policy is invalid)."""
    from .config import ConfigError, load_config
    from .rule_policy import policy_from_config

    try:
        config, _ = load_config(root)
    except ConfigError:
        return []
    return [
        Rule(rule.code, rule.level, rule.summary, rule.why, rule.fix)
        for rule in policy_from_config(config).custom
    ]


def to_sarif(
    findings: Iterable[Mapping[str, Any]],
    root: Path,
    *,
    files: int,
    overrides: Iterable[Any] = (),
) -> dict[str, Any]:
    """Render lint findings (``asdict(Finding)`` mappings) as one SARIF log.

    Findings that a security-relevant override switched off are included as
    results carrying an external ``suppressions`` entry with the policy's
    reason, so a code-scanning dashboard shows them as suppressed instead of
    never learning they existed.
    """
    from dataclasses import asdict

    base, prefix = source_root(root)
    findings = list(findings)
    overrides = list(overrides)
    suppressed: list[tuple[Mapping[str, Any], str]] = []
    for entry in overrides:
        if entry.security:
            suppressed.extend((asdict(item), entry.reason or "") for item in entry.suppressed)
    rules = list(RULES)
    codes = {f["code"] for f in findings} | {f["code"] for f, _ in suppressed}
    if any(is_custom(code) for code in codes):
        rules.extend(_custom_catalog(root))
    index = {rule.code: position for position, rule in enumerate(rules)}
    results: list[dict[str, Any]] = []
    justification = {id(f): reason for f, reason in suppressed}
    ordered = sorted(
        [*findings, *(f for f, _ in suppressed)],
        key=lambda f: (f["path"], f["line"] or 0, f["code"], f["message"]),
    )
    for finding in ordered:
        code = finding["code"]
        if code not in index:
            # Every emitted code is catalogued (tests enforce it); stay valid
            # SARIF even if one slips through.
            rules.append(Rule(code, _level(finding), code, "", ""))
            index[code] = len(rules) - 1
        path = _source_path(finding["path"], prefix)
        location: dict[str, Any] = {
            "physicalLocation": {
                "artifactLocation": {"uri": quote(path, safe="/"), "uriBaseId": SRCROOT},
            },
        }
        if finding["line"]:
            location["physicalLocation"]["region"] = {"startLine": int(finding["line"])}
        result: dict[str, Any] = {
            "ruleId": code,
            "ruleIndex": index[code],
            "level": _level(finding),
            "message": {"text": finding["message"]},
            "locations": [location],
            "partialFingerprints": {FINGERPRINT_KEY: _fingerprint(code, path, finding["message"])},
        }
        if id(finding) in justification:
            result["suppressions"] = [{"kind": "external", "justification": justification[id(finding)]}]
        results.append(result)
    base_uri = base.resolve().as_uri()
    log: dict[str, Any] = {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {
                "driver": {
                    "name": "WhyKit",
                    "semanticVersion": __version__,
                    "informationUri": INFORMATION_URI,
                    "rules": [_sarif_rule(rule) for rule in rules],
                },
            },
            "originalUriBaseIds": {SRCROOT: {"uri": base_uri if base_uri.endswith("/") else base_uri + "/"}},
            "columnKind": "utf16CodeUnits",
            "results": results,
            "properties": {"contract_version": 1, "files": files, "vault_prefix": prefix},
        }],
    }
    # A security rule lowered by policy, or a secret scan switched off, is
    # part of what the log means: say so as a configuration notification.
    notices = [
        {
            "level": "note",
            "message": {"text": f"whykit.toml policy: {entry.describe()}" if not entry.skipped_by else entry.describe()},
            "descriptor": {"id": entry.rule},
        }
        for entry in overrides
        if entry.security
    ]
    if notices:
        log["runs"][0]["invocations"] = [{"executionSuccessful": True, "toolConfigurationNotifications": notices}]
    return log


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def workflow_command(level: str, message: str, *, file: str | None = None, line: int | None = None, title: str | None = None) -> str:
    """One ``::error``/``::warning`` workflow command, escaped per GitHub's rules."""
    props = []
    if file:
        props.append(f"file={_escape_property(file)}")
    if file and line:
        props.append(f"line={int(line)}")
    if title:
        props.append(f"title={_escape_property(title)}")
    head = f"::{level}" + (" " + ",".join(props) if props else "")
    return f"{head}::{_escape_data(message)}"


def github_annotations(findings: Iterable[Mapping[str, Any]], root: Path) -> list[str]:
    """Workflow commands for every finding, errors first."""
    _, prefix = source_root(root)
    ordered = sorted(findings, key=lambda f: (f["level"] != "error", f["path"], f["line"] or 0, f["code"]))
    return [
        workflow_command(
            _level(finding),
            finding["message"] if is_custom(finding["code"]) else f"{finding['message']} ({rule_help_uri(finding['code'])})",
            file=_source_path(finding["path"], prefix),
            line=finding["line"],
            title=f"WhyKit {finding['code']}",
        )
        for finding in ordered
    ]


def override_notices(overrides: Iterable[Any]) -> list[str]:
    """One ``::notice`` per security-relevant override, so CI logs show it."""
    return [
        workflow_command(
            "notice",
            entry.describe() if entry.skipped_by else f"whykit.toml policy: {entry.describe()}",
            title="WhyKit policy",
        )
        for entry in overrides
        if entry.security
    ]
