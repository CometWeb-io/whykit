#!/usr/bin/env python3
"""The `whykit` command.

Zero runtime dependencies beyond the standard library. Every subcommand runs
in-process rather than shelling out to a sibling script, so the tool behaves the
same whether it was installed with pip, run with `pipx run`, or called from a
checkout via `scripts/whykit.py`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .lint import find_vault_root, is_vault_root, lint as run_lint, rel
from .vault_index import VaultIndex

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"

VAULT_GITIGNORE = """# Raw exports, staged before normalization. Never commit these directly.
.import-staging/

# This repository holds knowledge, never credentials.
.env
*.key
*.pem

# OS and editor noise
.DS_Store
Thumbs.db
.obsidian/workspace.json
.obsidian/workspace-mobile.json
"""
# Points at the repository when running from a checkout; harmless in a wheel.
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _resolve_vault(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if is_vault_root(path) else None
    return find_vault_root()



def _stamp_vault_dates(target: Path, today: dt.date | None = None) -> None:
    """Make a newly generated vault honest about when its starter files were created."""
    import re
    value = (today or dt.date.today()).isoformat()
    for path in target.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 4)
        if end < 0:
            continue
        front = text[:end]
        front = re.sub(r"(?m)^created:\s*.*$", f"created: {value}", front, count=1)
        front = re.sub(r"(?m)^last_updated:\s*.*$", f"last_updated: {value}", front, count=1)
        path.write_text(front + text[end:], encoding="utf-8")

def cmd_init(args: argparse.Namespace) -> int:
    import tempfile

    from .io import atomic_write_bytes, atomic_write_text

    if getattr(args, "minimal", False) and getattr(args, "full", False):
        print("--minimal and --full cannot be used together", file=sys.stderr)
        return 2

    minimal_layout = not getattr(args, "full", False)
    target = Path(args.target).expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"refusing to write into non-empty directory: {target}", file=sys.stderr)
        print("use --force only when you have reviewed the destination", file=sys.stderr)
        return 2
    if not TEMPLATE_DIR.is_dir():
        print("template is missing from this installation", file=sys.stderr)
        return 2

    # Prepare the selected layout in an isolated staging directory first.
    # The vendor-neutral layout trims the workstream-rich starter here, never
    # against an existing adopter directory.
    with tempfile.TemporaryDirectory(prefix="whykit-init-") as tmp:
        prepared = Path(tmp) / "vault"
        shutil.copytree(TEMPLATE_DIR, prepared)
        if minimal_layout:
            from .init_layout import apply_minimal_layout
            apply_minimal_layout(prepared)
        _stamp_vault_dates(prepared)

        target.mkdir(parents=True, exist_ok=True)
        for source in sorted(prepared.rglob("*")):
            relative = source.relative_to(prepared)
            destination = target / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if destination.exists() and not args.force:
                # Empty-destination init should never hit this; --force is
                # required for any overwrite of an existing path.
                print(f"refusing to overwrite existing file: {destination}", file=sys.stderr)
                return 2
            if destination.is_symlink():
                print(f"refusing to overwrite symlink: {destination}", file=sys.stderr)
                return 2
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(destination, source.read_bytes())

    # Deliberately no LICENSE: the vault holds the adopter's own knowledge, and
    # copying WhyKit's Apache-2.0 text into it would appear to license their
    # company's strategy and decisions under it.
    atomic_write_text(target / ".gitignore", VAULT_GITIGNORE)

    print(f"WhyKit vault created: {target}")
    if minimal_layout:
        print("Layout: vendor-neutral (default)")
    else:
        print("Layout: full GTM workstream starter (--full)")
    print()
    print("Next, in order:")
    print("  1. Answer every TODO in AGENTS.md - that file is the contract agents work under.")
    print("  2. Replace the starter content in 00-context/ before treating anything as canonical.")
    print(f"  3. cd {target} && whykit lint")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    argv: list[str] = list(args.paths)
    if args.root:
        argv += ["--root", args.root]
    for flag in ("strict", "quiet", "json", "no_orphans", "no_secrets"):
        if getattr(args, flag, False):
            argv.append("--" + flag.replace("_", "-"))
    if getattr(args, "today", None):
        argv += ["--today", args.today]
    from .lint import main as lint_main
    return lint_main(argv)



def cmd_new(args: argparse.Namespace) -> int:
    from .scaffold import main as new_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv.append(args.new_kind)
    if args.new_kind == "decision":
        argv.append(args.title)
        argv += ["--status", args.status]
        if args.owner:
            argv += ["--owner", args.owner]
        if args.sensitivity:
            argv += ["--sensitivity", args.sensitivity]
        for source_id in args.source_ids:
            argv += ["--source", source_id]
        if args.review_by:
            argv += ["--review-by", args.review_by]
        if args.supersedes:
            argv += ["--supersedes", args.supersedes]
        if args.json:
            argv.append("--json")
    elif args.new_kind == "evidence":
        argv += ["--source", args.source_name, "--location", args.location, "--type", args.evidence_type, "--claims", args.claims]
        if args.date:
            argv += ["--date", args.date]
        if args.accessed:
            argv += ["--accessed", args.accessed]
        if args.json:
            argv.append("--json")
    else:
        argv += [args.title, "--workstream", args.workstream, "--type", args.doc_type]
        if args.owner:
            argv += ["--owner", args.owner]
        if args.sensitivity:
            argv += ["--sensitivity", args.sensitivity]
        if args.link_from:
            argv += ["--link-from", args.link_from]
        if args.json:
            argv.append("--json")
    return new_main(argv)


def cmd_status(args: argparse.Namespace) -> int:
    from .status import main as status_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    if args.json:
        argv.append("--json")
    if args.today:
        argv += ["--today", args.today]
    if args.due_days is not None:
        argv += ["--due-days", str(args.due_days)]
    if args.strict:
        argv.append("--strict")
    return status_main(argv)


def cmd_graph(args: argparse.Namespace) -> int:
    from .graph import main as graph_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv += ["--format", args.format]
    if args.canonical_only:
        argv.append("--canonical-only")
    if getattr(args, "output", None):
        argv += ["--output", args.output]
    return graph_main(argv)


def cmd_backlinks(args: argparse.Namespace) -> int:
    from .backlinks import main as backlinks_main
    argv = [args.target]
    if args.root:
        argv += ["--root", args.root]
    if args.json:
        argv.append("--json")
    return backlinks_main(argv)


def cmd_impact(args: argparse.Namespace) -> int:
    from .impact import main as impact_main
    argv = [args.target]
    if args.root:
        argv += ["--root", args.root]
    if args.json:
        argv.append("--json")
    return impact_main(argv)

def cmd_query(args: argparse.Namespace) -> int:
    from .query import main as query_main
    argv: list[str] = []
    if args.text:
        argv.append(args.text)
    if args.root:
        argv += ["--root", args.root]
    for flag, value in (("--type", args.doc_type), ("--status", args.status), ("--owner", args.owner), ("--sensitivity", args.sensitivity), ("--source", args.source_id), ("--tag", args.tag)):
        if value:
            argv += [flag, value]
    if args.canonical_only:
        argv.append("--canonical-only")
    argv += ["--limit", str(args.limit)]
    if args.json:
        argv.append("--json")
    return query_main(argv)


def cmd_context(args: argparse.Namespace) -> int:
    from .context import main as context_main
    argv = [args.target, "--max-chars", str(args.max_chars)]
    if args.root:
        argv += ["--root", args.root]
    if args.no_body:
        argv.append("--no-body")
    if args.json:
        argv.append("--json")
    return context_main(argv)


def cmd_pack(args: argparse.Namespace) -> int:
    from .pack import main as pack_main
    argv: list[str] = list(args.targets)
    if args.root:
        argv += ["--root", args.root]
    if args.query:
        argv += ["--query", args.query]
    argv += ["--max-docs", str(args.max_docs), "--max-chars", str(args.max_chars), "--format", args.format]
    if args.canonical_only:
        argv.append("--canonical-only")
    if getattr(args, "agent", None):
        argv += ["--for", args.agent]
    return pack_main(argv)


def cmd_review(args: argparse.Namespace) -> int:
    from .review import main as review_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv.append(args.review_command)
    if args.review_command == "list":
        if args.today:
            argv += ["--today", args.today]
        if args.due_days is not None:
            argv += ["--due-days", str(args.due_days)]
        if args.owner:
            argv += ["--owner", args.owner]
        if args.overdue_only:
            argv.append("--overdue-only")
        if args.json:
            argv.append("--json")
    else:
        argv.append(args.target)
        argv += ["--reviewer", args.reviewer, "--outcome", args.outcome]
        if args.next_review:
            argv += ["--next-review", args.next_review]
        if args.note_text:
            argv += ["--note", args.note_text]
        if args.today:
            argv += ["--today", args.today]
        if args.json:
            argv.append("--json")
    return review_main(argv)


def cmd_snapshot(args: argparse.Namespace) -> int:
    from .snapshot import main_snapshot
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    if args.today:
        argv += ["--today", args.today]
    if args.output:
        argv += ["--output", args.output]
    if args.compact:
        argv.append("--compact")
    return main_snapshot(argv)


def cmd_verify_snapshot(args: argparse.Namespace) -> int:
    from .snapshot import main_verify
    argv = [args.snapshot]
    if args.root:
        argv += ["--root", args.root]
    if args.today:
        argv += ["--today", args.today]
    if args.json:
        argv.append("--json")
    return main_verify(argv)


def cmd_check(args: argparse.Namespace) -> int:
    from .check import main as check_main
    argv = ["--profile", args.profile, "--head", args.head]
    if args.root:
        argv += ["--root", args.root]
    if args.base:
        argv += ["--base", args.base]
    if args.today:
        argv += ["--today", args.today]
    if args.json:
        argv.append("--json")
    return check_main(argv)


def cmd_policy(args: argparse.Namespace) -> int:
    from .config import ConfigError, config_summary
    vault = _resolve_vault(args.root)
    if vault is None:
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    try:
        payload = config_summary(vault)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        import json
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"WhyKit policy — {payload['source']}")
        defaults = payload["config"]["defaults"]
        print(f"  default owner          {defaults['owner']}")
        print(f"  default sensitivity    {defaults['sensitivity']}")
        print(f"  decision review days   {defaults['decision_review_days']}")
        print(f"  status due days        {defaults['status_due_days']}")
        for name, profile in payload["config"]["profiles"].items():
            print(f"  profile {name:<10} strict={profile['strict']} orphans={profile['orphans']} secrets={profile['secrets']} history={profile['history']}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    from .evidence import main as evidence_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv.append(args.evidence_command)
    if args.evidence_command == "list":
        argv += ["--state", args.state]
        if args.json:
            argv.append("--json")
    else:
        argv.append(args.id)
        argv += ["--why", args.reason]
        if args.replaced_by:
            argv += ["--replaced-by", args.replaced_by]
        if args.today:
            argv += ["--today", args.today]
        if args.json:
            argv.append("--json")
    return evidence_main(argv)


def cmd_adopt(args: argparse.Namespace) -> int:
    from .adopt import main as adopt_main
    argv = [args.source]
    if args.into:
        argv += ["--into", args.into]
    if args.owner:
        argv += ["--owner", args.owner]
    if args.profile:
        argv += ["--profile", args.profile]
    if args.write:
        argv.append("--write")
    if args.json:
        argv.append("--json")
    return adopt_main(argv)


def cmd_history(args: argparse.Namespace) -> int:
    from .immutability import main as immutability_main
    argv = ["--base", args.base, "--head", args.head]
    if args.root:
        argv += ["--root", args.root]
    return immutability_main(argv)


PRE_COMMIT_HOOK = """#!/bin/sh
# Installed by `whykit install-hooks`.
set -e
root=$(git rev-parse --show-toplevel)
cd "$root"

if ! command -v whykit >/dev/null 2>&1; then
  echo "whykit is not on PATH; skipping the vault check." >&2
  exit 0
fi

if ! whykit check --profile local; then
  echo ""
  echo "WhyKit local policy failed. Fix the errors before committing."
  echo "To commit anyway (and you should have a reason): git commit --no-verify"
  exit 1
fi
"""


def cmd_install_hooks(args: argparse.Namespace) -> int:
    vault = _resolve_vault(args.root)
    if vault is None:
        print("no WhyKit vault found", file=sys.stderr)
        return 2
    hooks = vault / ".git" / "hooks"
    if not hooks.is_dir():
        print(f"not a git repository: {vault}", file=sys.stderr)
        print("run `git init` first - the ledger's guarantees come from version control", file=sys.stderr)
        return 2
    target = hooks / "pre-commit"
    if target.exists() and not args.force:
        print(f"a pre-commit hook already exists: {target}", file=sys.stderr)
        print("inspect it, then re-run with --force to replace it", file=sys.stderr)
        return 2
    target.write_text(PRE_COMMIT_HOOK, encoding="utf-8")
    target.chmod(0o755)
    print(f"pre-commit hook installed: {target}")
    return 0


def _check(label: str, ok: bool, detail: str) -> bool:
    print(f"{'OK' if ok else 'FAIL':<4} {label:<20} {detail}")
    return ok


def _note(label: str, ok: bool, detail: str) -> None:
    """Report something optional. Printing FAIL for a missing nicety trains people
    to ignore the whole report, which is how a real FAIL gets missed."""
    print(f"{'OK' if ok else '--':<4} {label:<20} {detail}")



def cmd_rules(args: argparse.Namespace) -> int:
    from .rules import main as rules_main
    argv: list[str] = []
    if args.code:
        argv.append(args.code)
    if args.json:
        argv.append("--json")
    if args.markdown:
        argv.append("--markdown")
    return rules_main(argv)

def cmd_doctor(args: argparse.Namespace) -> int:
    import json
    from .config import ConfigError, load_config

    vault = _resolve_vault(args.root)
    checks: list[dict[str, object]] = []

    def record(label: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"name": label, "passed": bool(ok), "detail": detail, "required": required})

    record("python", sys.version_info >= (3, 11), sys.version.split()[0])
    record("whykit", True, __version__)
    if vault is None:
        record("vault_root", False, "no vault found - run `whykit init <dir>`")
        payload = {"contract_version": 1, "passed": False, "checks": checks}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in checks:
                marker = "OK" if item["passed"] else "FAIL"
                print(f"{marker:<4} {str(item['name']):<20} {item['detail']}")
        return 1

    record("vault_root", True, str(vault))
    record("evidence_register", (vault / "00-context/evidence-register.md").exists(), "00-context/evidence-register.md")
    record("decision_log", (vault / "06-decisions/decision-log.md").exists(), "06-decisions/decision-log.md")
    record("review_log", (vault / "00-context/review-log.md").exists(), "00-context/review-log.md", required=False)
    record("agent_contract", (vault / "AGENTS.md").exists(), "AGENTS.md")
    try:
        _config, config_path = load_config(vault)
    except ConfigError as exc:
        record("policy", False, str(exc))
    else:
        record("policy", True, rel(vault, config_path) if config_path else "built-in defaults")

    git = shutil.which("git")
    record("git", bool(git), git or "not found")
    tracked = False
    if git:
        inside = subprocess.run(["git", "-C", str(vault), "rev-parse", "--git-dir"], capture_output=True, text=True)
        tracked = inside.returncode == 0
        record("git_repository", tracked, "tracked" if tracked else "not a git repository yet - `git init`", required=False)
        hook = vault / ".git" / "hooks" / "pre-commit"
        record("pre_commit_hook", hook.exists(), "installed" if hook.exists() else "not installed - `whykit install-hooks`", required=False)

    record("node_explorer", bool(shutil.which("node")), shutil.which("node") or "not found (only needed for `serve`)", required=False)

    files, findings = run_lint(vault, [], orphans=False)
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    record("vault_lint", not errors, f"{len(files)} files, {len(errors)} errors, {len(warnings)} warnings")

    overdue = [f for f in findings if f.code in ("review_by.overdue", "decision.review_missing")]
    record(
        "review_hygiene",
        not overdue,
        "every approved decision has a live review date" if not overdue else f"{len(overdue)} document(s) need a review date or re-check",
        required=False,
    )
    sensitive = _sensitive_docs(vault)
    record("sensitive_docs", True, f"{len(sensitive)} confidential/restricted document(s)", required=False)

    passed = all(bool(item["passed"]) for item in checks if item["required"])
    payload = {"contract_version": 1, "root": str(vault), "passed": passed, "checks": checks}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            if item["passed"]:
                marker = "OK"
            elif item["required"]:
                marker = "FAIL"
            else:
                marker = "--"
            print(f"{marker:<4} {str(item['name']):<20} {item['detail']}")
    return 0 if passed else 1


def _sensitive_docs(vault: Path) -> list[str]:
    """Documents marked confidential or restricted (doctor inventory)."""
    index = VaultIndex.load(vault)
    sensitive: list[str] = []
    for note in index.notes:
        if str(note.front.get("sensitivity", "")).lower() in {"confidential", "restricted"}:
            sensitive.append(index.relative(note.path))
    return sensitive


def _non_public_docs(vault: Path) -> list[str]:
    index = VaultIndex.load(vault)
    result: list[str] = []
    for note in index.notes:
        sensitivity = str(note.front.get("sensitivity", "internal")).lower()
        if sensitivity != "public":
            result.append(index.relative(note.path))
    return result


def cmd_explorer_index(args: argparse.Namespace) -> int:
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    if getattr(args, "today", None):
        argv += ["--today", args.today]
    from .explorer_index import main as explorer_index_main
    return explorer_index_main(argv)


def cmd_serve(args: argparse.Namespace) -> int:
    explorer = SOURCE_ROOT / "apps" / "explorer"
    if not (explorer / "package.json").exists():
        print("The Explorer ships with the source repository, not the package.", file=sys.stderr)
        print("Clone the repository and run `whykit serve` from there.", file=sys.stderr)
        return 2
    npm = shutil.which("npm")
    if not npm:
        print("npm is required for the Explorer", file=sys.stderr)
        return 2
    vault = _resolve_vault(None if args.vault == "." else args.vault) or Path(args.vault).expanduser().resolve()
    if not is_vault_root(vault):
        print(f"not a WhyKit vault: {vault}", file=sys.stderr)
        return 2
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if args.host not in loopback_hosts and not args.allow_sensitive_network:
        non_public = _non_public_docs(vault)
        if non_public:
            print("refusing to expose non-public vault content on a non-loopback interface", file=sys.stderr)
            print(f"{len(non_public)} non-public document(s) would be bundled into the Explorer", file=sys.stderr)
            print("use the default 127.0.0.1, or pass --allow-sensitive-network after reviewing the risk", file=sys.stderr)
            return 2
    if not (explorer / "node_modules").exists():
        print("Explorer dependencies are not installed. Run: npm --prefix apps/explorer install", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["WHYKIT_VAULT_DIR"] = str(vault)
    return subprocess.run([npm, "run", "dev", "--", "--host", args.host, "--port", str(args.port)],
                          cwd=explorer, env=env).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whykit", description="WhyKit - evidence and decision ledger")
    parser.add_argument("--version", action="version", version=f"whykit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new vault")
    init.add_argument("target")
    init.add_argument("--force", action="store_true", help="write into a non-empty directory")
    init.add_argument(
        "--minimal",
        action="store_true",
        help="explicitly select the default vendor-neutral layout (legacy alias)",
    )
    init.add_argument(
        "--full",
        action="store_true",
        help="include the optional GTM workstreams (strategy, website, research, etc.)",
    )
    init.set_defaults(func=cmd_init)

    lint_cmd = sub.add_parser("lint", help="check a vault")
    lint_cmd.add_argument("paths", nargs="*")
    lint_cmd.add_argument("--root")
    lint_cmd.add_argument("--strict", action="store_true")
    lint_cmd.add_argument("--quiet", action="store_true")
    lint_cmd.add_argument("--json", action="store_true")
    lint_cmd.add_argument("--no-orphans", action="store_true")
    lint_cmd.add_argument("--no-secrets", action="store_true")
    lint_cmd.add_argument("--today", help="evaluate review dates as of this ISO date")
    lint_cmd.set_defaults(func=cmd_lint)


    new = sub.add_parser("new", help="create a decision, evidence row or note")
    new.add_argument("--root")
    new_sub = new.add_subparsers(dest="new_kind", required=True)
    new_decision = new_sub.add_parser("decision", help="create a decision record and index row")
    new_decision.add_argument("title")
    new_decision.add_argument("--owner", help="default: policy defaults.owner")
    new_decision.add_argument("--status", choices=("draft", "in_review", "approved", "superseded", "archived"), default="draft")
    new_decision.add_argument("--sensitivity", choices=("public", "internal", "confidential", "restricted"), help="default: policy defaults.sensitivity")
    new_decision.add_argument("--source", action="append", default=[], dest="source_ids")
    new_decision.add_argument("--review-by")
    new_decision.add_argument("--supersedes")
    new_decision.add_argument("--json", action="store_true")
    new_decision.set_defaults(func=cmd_new)

    new_evidence = new_sub.add_parser("evidence", help="append a source to the evidence register")
    new_evidence.add_argument("--source", required=True, dest="source_name")
    new_evidence.add_argument("--location", required=True)
    new_evidence.add_argument("--type", required=True, dest="evidence_type")
    new_evidence.add_argument("--claims", required=True)
    new_evidence.add_argument("--date")
    new_evidence.add_argument("--accessed")
    new_evidence.add_argument("--json", action="store_true")
    new_evidence.set_defaults(func=cmd_new)

    new_note = new_sub.add_parser("note", help="create a draft note in a workstream")
    new_note.add_argument("title")
    new_note.add_argument("--workstream", required=True)
    new_note.add_argument("--owner", help="default: policy defaults.owner")
    new_note.add_argument("--type", choices=("strategy", "research", "framework", "specification", "guide", "reference"), default="guide", dest="doc_type")
    new_note.add_argument("--sensitivity", choices=("public", "internal", "confidential", "restricted"), help="default: policy defaults.sensitivity")
    new_note.add_argument("--link-from", help="vault-relative Markdown map to link the new note from")
    new_note.add_argument("--json", action="store_true")
    new_note.set_defaults(func=cmd_new)

    status = sub.add_parser("status", help="summarize vault health and review queue")
    status.add_argument("--root")
    status.add_argument("--json", action="store_true")
    status.add_argument("--today")
    status.add_argument("--due-days", type=int, help="default: policy defaults.status_due_days")
    status.add_argument("--strict", action="store_true")
    status.set_defaults(func=cmd_status)

    graph = sub.add_parser("graph", help="export the vault wikilink graph")
    graph.add_argument("--root")
    graph.add_argument("--format", choices=("json", "dot", "obsidian"), default="json")
    graph.add_argument("--canonical-only", action="store_true")
    graph.add_argument("--output", help="write inside the vault (e.g. .whykit/graph.json)")
    graph.set_defaults(func=cmd_graph)

    backlinks = sub.add_parser("backlinks", help="list inbound links to a note, decision or evidence ID")
    backlinks.add_argument("target")
    backlinks.add_argument("--root")
    backlinks.add_argument("--json", action="store_true")
    backlinks.set_defaults(func=cmd_backlinks)

    impact = sub.add_parser("impact", help="show what depends on evidence, a decision or a document")
    impact.add_argument("target")
    impact.add_argument("--root")
    impact.add_argument("--json", action="store_true")
    impact.set_defaults(func=cmd_impact)

    query = sub.add_parser("query", help="query notes by text, metadata and evidence")
    query.add_argument("text", nargs="?")
    query.add_argument("--root")
    query.add_argument("--type", dest="doc_type")
    query.add_argument("--status")
    query.add_argument("--owner")
    query.add_argument("--sensitivity")
    query.add_argument("--source", dest="source_id")
    query.add_argument("--tag")
    query.add_argument("--canonical-only", action="store_true")
    query.add_argument("--limit", type=int, default=100)
    query.add_argument("--json", action="store_true")
    query.set_defaults(func=cmd_query)

    context = sub.add_parser("context", help="build an evidence-aware context pack for one target")
    context.add_argument("target")
    context.add_argument("--root")
    context.add_argument("--max-chars", type=int, default=20_000)
    context.add_argument("--no-body", action="store_true")
    context.add_argument("--json", action="store_true")
    context.set_defaults(func=cmd_context)

    pack = sub.add_parser("pack", help="build a bounded multi-record context bundle")
    pack.add_argument("targets", nargs="*")
    pack.add_argument("--root")
    pack.add_argument("--query")
    pack.add_argument("--max-docs", type=int, default=8)
    pack.add_argument("--max-chars", type=int, default=30_000)
    pack.add_argument("--canonical-only", action="store_true")
    pack.add_argument("--format", choices=("json", "markdown"), default="json")
    pack.add_argument("--for", dest="agent", choices=("generic", "cursor", "claude", "codex"), default="generic")
    pack.set_defaults(func=cmd_pack)

    review = sub.add_parser("review", help="list review work or record a review event")
    review.add_argument("--root")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    review_list = review_sub.add_parser("list", help="show upcoming/overdue reviews")
    review_list.add_argument("--today")
    review_list.add_argument("--due-days", type=int)
    review_list.add_argument("--owner")
    review_list.add_argument("--overdue-only", action="store_true")
    review_list.add_argument("--json", action="store_true")
    review_list.set_defaults(func=cmd_review)
    review_record = review_sub.add_parser("record", help="record a review event")
    review_record.add_argument("target")
    review_record.add_argument("--reviewer", required=True)
    review_record.add_argument("--outcome", choices=("confirmed", "update-required", "supersede-required", "archived"), default="confirmed")
    review_record.add_argument("--next-review")
    review_record.add_argument("--note", default="", dest="note_text")
    review_record.add_argument("--today")
    review_record.add_argument("--json", action="store_true")
    review_record.set_defaults(func=cmd_review)

    snapshot = sub.add_parser("snapshot", help="create a deterministic vault snapshot")
    snapshot.add_argument("--root")
    snapshot.add_argument("--today")
    snapshot.add_argument("--output")
    snapshot.add_argument("--compact", action="store_true")
    snapshot.set_defaults(func=cmd_snapshot)

    verify_snapshot = sub.add_parser("verify-snapshot", help="compare the vault with a prior snapshot")
    verify_snapshot.add_argument("snapshot")
    verify_snapshot.add_argument("--root")
    verify_snapshot.add_argument("--today")
    verify_snapshot.add_argument("--json", action="store_true")
    verify_snapshot.set_defaults(func=cmd_verify_snapshot)

    check = sub.add_parser("check", help="run a local/CI/release policy gate")
    check.add_argument("--root")
    check.add_argument("--profile", default="ci")
    check.add_argument("--base")
    check.add_argument("--head", default="HEAD")
    check.add_argument("--today")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    policy = sub.add_parser("policy", help="show the effective repository-local policy")
    policy.add_argument("--root")
    policy.add_argument("--json", action="store_true")
    policy.set_defaults(func=cmd_policy)

    evidence = sub.add_parser("evidence", help="inspect and manage evidence lifecycle")
    evidence.add_argument("--root")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_list = evidence_sub.add_parser("list", help="list evidence rows")
    evidence_list.add_argument("--state", choices=("all", "active", "retired"), default="all")
    evidence_list.add_argument("--json", action="store_true")
    evidence_list.set_defaults(func=cmd_evidence)
    evidence_retire = evidence_sub.add_parser("retire", help="retire active evidence")
    evidence_retire.add_argument("id")
    evidence_retire.add_argument("--why", required=True, dest="reason")
    evidence_retire.add_argument("--replaced-by")
    evidence_retire.add_argument("--today")
    evidence_retire.add_argument("--json", action="store_true")
    evidence_retire.set_defaults(func=cmd_evidence)

    adopt = sub.add_parser("adopt", help="inventory existing Markdown and stage it for a vault")
    adopt.add_argument("source")
    adopt.add_argument("--into")
    adopt.add_argument("--owner", default="TODO")
    adopt.add_argument("--profile", choices=("generic", "adr-only", "obsidian-loose"), default="generic")
    adopt.add_argument("--write", action="store_true")
    adopt.add_argument("--json", action="store_true")
    adopt.set_defaults(func=cmd_adopt)

    history = sub.add_parser("history", help="verify that accepted decisions were not rewritten")
    history.add_argument("--base", required=True, help="base commit or ref")
    history.add_argument("--head", default="HEAD")
    history.add_argument("--root")
    history.set_defaults(func=cmd_history)


    rules = sub.add_parser("rules", help="list lint rules or explain one rule code")
    rules.add_argument("code", nargs="?")
    rules.add_argument("--json", action="store_true")
    rules.add_argument("--markdown", action="store_true", help="emit the complete Markdown table")
    rules.set_defaults(func=cmd_rules)

    doctor = sub.add_parser("doctor", help="check prerequisites and vault integrity")
    doctor.add_argument("--root")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    hooks = sub.add_parser("install-hooks", help="install the pre-commit vault check")
    hooks.add_argument("--root")
    hooks.add_argument("--force", action="store_true", help="replace an existing pre-commit hook")
    hooks.set_defaults(func=cmd_install_hooks)

    explorer_index = sub.add_parser(
        "explorer-index",
        help="export the Explorer vault index from the canonical Python parser",
    )
    explorer_index.add_argument("--root")
    explorer_index.add_argument("--today", help="evaluate lint review dates as of this ISO date")
    explorer_index.add_argument("--json", action="store_true", default=True)
    explorer_index.set_defaults(func=cmd_explorer_index)

    serve = sub.add_parser("serve", help="run the optional Explorer (source checkout only)")
    serve.add_argument("vault", nargs="?", default=".")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5173)
    serve.add_argument("--allow-sensitive-network", action="store_true", help="allow non-loopback serving even when non-public docs are present")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
