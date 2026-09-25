"""Policy-profile quality gates for local work, CI and releases."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .config import ConfigError, configuration_readiness, get_profile, load_config
from .immutability import changed_records
from .lint import find_vault_root, is_vault_root, lint, rel


def _git_repo(root: Path) -> bool:
    if not shutil.which("git"):
        return False
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_clean(root: Path) -> bool:
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True)
    return result.returncode == 0 and not result.stdout.strip()


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
    files, findings = lint(
        root,
        orphans=bool(profile["orphans"]),
        secrets=bool(profile["secrets"]),
        hub_links=bool(profile.get("require_hub_links", False)),
        today=today,
    )
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
    return {
        "contract_version": 1,
        "profile": profile_name,
        "profile_config": profile,
        "config_source": rel(root, config_path) if config_path else "built-in defaults",
        "as_of": (today or dt.date.today()).isoformat(),
        "passed": passed,
        "checks": checks,
        "lint": {
            "files": len(files),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [asdict(item) for item in findings],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit check", description="Run a versioned local/CI/release WhyKit policy profile.")
    parser.add_argument("--root", help="vault root (default: nearest vault)")
    parser.add_argument("--profile", default="ci", help="policy profile from whykit.toml (default: ci)")
    parser.add_argument("--base", help="base git ref for immutable-history verification")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--today", help="evaluate review dates as of this ISO date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else find_vault_root()
    if root is None or not is_vault_root(root):
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            print(f"--today is not a real ISO date: {args.today}", file=sys.stderr)
            return 2
    try:
        report = run_check(root, profile_name=args.profile, base=args.base, head=args.head, today=today)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        state = "PASS" if report["passed"] else "FAIL"
        print(f"WhyKit {report['profile']} gate — {state}")
        print(f"  config  {report['config_source']}")
        for check in report["checks"]:
            marker = "OK" if check["passed"] else "FAIL"
            print(f"  {marker:<4} {check['name']:<16} {check['detail']}")
        if report["lint"]["errors"] or report["lint"]["warnings"]:
            print(f"  lint findings       {report['lint']['errors']} error(s), {report['lint']['warnings']} warning(s)")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
