"""Policy-profile quality gates for local work, CI and releases."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .contract import emit_error, vault_not_found
from .config import ConfigError, configuration_readiness, get_profile, load_config
from .immutability import changed_records
from .lint import _parse_date, find_vault_root, is_vault_root, lint, rel, path_cache
from .console import emit_machine, one_line
from .rule_policy import Override, describe_override, secret_scan_skipped


def _git_repo(root: Path) -> bool:
    if not shutil.which("git"):
        return False
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_clean(root: Path) -> bool:
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0 and not result.stdout.strip()


@path_cache()
def run_check(
    root: Path,
    *,
    profile_name: str,
    base: str | None = None,
    head: str = "HEAD",
    today: dt.date | None = None,
) -> dict:
    config, config_path = load_config(root)
    profile = get_profile(config, profile_name)
    applied: list[Override] = []
    files, findings = lint(
        root,
        orphans=bool(profile["orphans"]),
        secrets=bool(profile["secrets"]),
        hub_links=bool(profile.get("require_hub_links", False)),
        today=today,
        overrides=applied,
    )
    if not profile["secrets"]:
        applied.append(secret_scan_skipped(f"profile {profile_name} (secrets = false)"))
    errors = [item for item in findings if item.level == "error"]
    warnings = [item for item in findings if item.level == "warning"]
    checks: list[dict] = []

    lint_passed = not errors and not (profile["strict"] and warnings)
    checks.append({
        "name": "lint",
        "passed": lint_passed,
        "detail": f"{len(files)} files, {len(errors)} errors, {len(warnings)} warnings",
    })

    git_repo = _git_repo(root)
    if profile["require_git"]:
        checks.append({"name": "git_repository", "passed": git_repo, "detail": "git work tree required"})
    else:
        checks.append({"name": "git_repository", "passed": True, "detail": "not required" if not git_repo else "detected"})

    if profile["require_clean_tree"]:
        clean = git_repo and _git_clean(root)
        checks.append({"name": "clean_tree", "passed": clean, "detail": "working tree must be clean"})
    else:
        checks.append({"name": "clean_tree", "passed": True, "detail": "not required"})

    if profile["require_configured"]:
        ready, reasons = configuration_readiness(config, config_path)
        checks.append({
            "name": "policy_configuration",
            "passed": ready,
            "detail": "repository policy is configured" if ready else "; ".join(reasons),
        })
    else:
        checks.append({"name": "policy_configuration", "passed": True, "detail": "not required"})

    history_mode = profile["history"]
    if history_mode == "off":
        checks.append({"name": "history", "passed": True, "detail": "disabled by profile"})
    elif base:
        if not git_repo:
            checks.append({"name": "history", "passed": False, "detail": "--base supplied but vault is not in a git work tree"})
        else:
            try:
                blocked = changed_records(base, head, str(root))
            except subprocess.CalledProcessError as exc:
                checks.append({"name": "history", "passed": False, "detail": (exc.stderr or str(exc)).strip()})
            else:
                checks.append({
                    "name": "history",
                    "passed": not blocked,
                    "detail": "immutable reasoning unchanged" if not blocked else f"{len(blocked)} immutable decision change(s)",
                    "blocked": [{"status": status, "path": path} for status, path in blocked],
                })
    elif history_mode == "required":
        checks.append({"name": "history", "passed": False, "detail": "profile requires --base <git-ref>"})
    else:
        checks.append({"name": "history", "passed": True, "detail": "optional; pass --base to enforce"})

    passed = all(item["passed"] for item in checks)
    lint_report: dict = {
        "files": len(files),
        "errors": len(errors),
        "warnings": len(warnings),
        "findings": [asdict(item) for item in findings],
    }
    if applied:
        lint_report["overrides"] = [entry.report() for entry in applied]
    return {
        "contract_version": 1,
        "profile": profile_name,
        "profile_config": profile,
        "config_source": rel(root, config_path) if config_path else "built-in defaults",
        "as_of": (today or dt.date.today()).isoformat(),
        "passed": passed,
        "checks": checks,
        "lint": lint_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit check", description="Run a versioned local/CI/release WhyKit policy profile.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--profile", default="ci", help="policy profile from whykit.toml (default: ci)")
    parser.add_argument("--base", help="base git ref for immutable-history verification")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--today", help="evaluate review dates as of this ISO date")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--format", choices=("text", "json", "github"), default=None, help="output format (default: text)")
    args = parser.parse_args(argv)
    if args.json and args.format not in (None, "json"):
        return emit_error(
            "usage",
            f"--json conflicts with --format {args.format}\nhint: pass one of them to `whykit check`",
            json_mode=True,
        )
    fmt = "json" if args.json else (args.format or "text")
    args.json = fmt == "json"
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=args.json)
    today = None
    if args.today:
        # Same YYYY-MM-DD contract as `whykit lint --today`; fromisoformat alone
        # would also accept forms such as 20260917 or 2026-W38-4.
        today = _parse_date(args.today)
        if today is None:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=args.json)
    try:
        report = run_check(root, profile_name=args.profile, base=args.base, head=args.head, today=today)
    except ConfigError as exc:
        return emit_error("invalid_config", str(exc), json_mode=args.json)
    if args.json:
        emit_machine(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if fmt == "github":
            annotations = gate_annotations(report, root)
            if annotations:
                emit_machine("\n".join(annotations))
        state = "PASS" if report["passed"] else "FAIL"
        print(f"WhyKit {report['profile']} gate — {state}")
        print(f"  config  {one_line(report['config_source'])}")
        width = max(len(check["name"]) for check in report["checks"])
        for check in report["checks"]:
            marker = "OK" if check["passed"] else "FAIL"
            print(f"  {marker:<4} {check['name']:<{width}}  {one_line(check['detail'])}")
            for item in check.get("blocked", []):
                print(f"         {item['status']}  {one_line(item['path'])}")
        if report["lint"]["errors"] or report["lint"]["warnings"]:
            print(f"  lint findings  {report['lint']['errors']} error(s), {report['lint']['warnings']} warning(s)")
        _print_gate_findings(report)
        for entry in report["lint"].get("overrides", []):
            if entry["security"]:
                print(f"  policy  {one_line(describe_override(entry))}")
    return 0 if report["passed"] else 1


MAX_GATE_FINDINGS = 20


def gate_annotations(report: dict, root: Path) -> list[str]:
    """GitHub workflow commands for a gate report.

    Every lint finding is annotated at its level, so a reviewer sees warnings
    inline even when the profile does not fail on them. Rewritten decision
    history is an error on the record; any other failed check is an error
    without a file.
    """
    from .ci_formats import github_annotations, source_root, workflow_command

    lines = github_annotations(report["lint"]["findings"], root)
    for entry in report["lint"].get("overrides", []):
        if entry["security"]:
            text = describe_override(entry)
            lines.append(workflow_command(
                "notice", text if entry.get("skipped_by") else f"whykit.toml policy: {text}", title="WhyKit policy",
            ))
    _, prefix = source_root(root)
    for check in report["checks"]:
        if check["passed"] or check["name"] == "lint":
            continue
        blocked = check.get("blocked") or []
        for item in blocked:
            path = item["path"].split(" -> ")[0]
            lines.append(workflow_command(
                "error",
                f"Accepted decision reasoning is append-only ({item['status']}); supersede it with a new record instead of rewriting it.",
                file=f"{prefix}{path}",
                title="WhyKit history",
            ))
        if not blocked:
            lines.append(workflow_command("error", f"{check['name']}: {check['detail']}", title=f"WhyKit {report['profile']} gate"))
    lint_check = next((c for c in report["checks"] if c["name"] == "lint"), None)
    if lint_check is not None and not lint_check["passed"] and not report["lint"]["errors"]:
        lines.append(workflow_command(
            "error",
            f"profile {report['profile']} is strict: {report['lint']['warnings']} warning(s) fail this gate",
            title=f"WhyKit {report['profile']} gate",
        ))
    return lines


def _print_gate_findings(report: dict) -> None:
    """Show the findings that failed the lint check.

    A CI log is often all a reviewer sees; a bare "5 warnings" sends them off
    to rerun the linter locally just to learn what broke the gate.
    """
    lint_check = next((c for c in report["checks"] if c["name"] == "lint"), None)
    if lint_check is None or lint_check["passed"]:
        return
    strict = bool(report["profile_config"].get("strict"))
    failing = [
        item for item in report["lint"]["findings"]
        if item["level"] == "error" or (strict and item["level"] == "warning")
    ]
    failing.sort(key=lambda item: (item["level"] != "error", item["path"], item["line"] or 0))
    print("")
    print("  findings that fail this gate:")
    for item in failing[:MAX_GATE_FINDINGS]:
        location = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
        print(f"    {item['level']:<7} {one_line(location)}  [{item['code']}] {one_line(item['message'])}")
    if len(failing) > MAX_GATE_FINDINGS:
        print(f"    ... {len(failing) - MAX_GATE_FINDINGS} more; run `whykit lint` for the full list")
    if strict and any(item["level"] == "warning" for item in failing):
        print("  this profile is strict: warnings fail it (see `whykit policy`)")
