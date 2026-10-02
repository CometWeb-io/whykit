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
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .lint import find_vault_root, is_vault_root, lint as run_lint, rel
from .completion import SHELLS, render_completion
from .messages import print_no_vault
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

# WhyKit's own write lock and crash-recovery journal. Machine state, not knowledge.
.whykit/mutation.lock
.whykit/transactions/
"""
VAULT_GITIGNORE_MARKER = "# WhyKit protective defaults (managed by whykit init)"
# Points at the repository when running from a checkout; harmless in a wheel.
SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_NAME_RE = re.compile(r"(?m)^name\s*=\s*[\"']whykit[\"']")
# macOS (and similar) expose these as root-level directory aliases. Following
# them does not redirect a user-named vault into a different tree; it is how
# the OS spells the same path. Refuse every other symlink on the init path.
_ROOT_DIR_ALIASES = frozenset({"var", "tmp", "etc", "private"})


def _resolve_vault(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if is_vault_root(path) else None
    return find_vault_root()


def _absolute_without_following_symlinks(raw: str) -> Path:
    """Expand ``~`` and make the path absolute without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _first_symlink_on_path(path: Path) -> Path | None:
    """Return the first refused symlink from the filesystem root through ``path``.

    Root-level OS aliases such as macOS ``/var`` → ``/private/var`` are allowed
    when they appear as an ancestor. The named target itself is never followed
    when it is a symlink, and any non-alias ancestor symlink is refused so
    init cannot write through a redirect into another directory.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f"path must be absolute: {path}")
    anchor = Path(path.anchor)
    current = anchor
    for part in path.parts[1:]:
        current = current / part
        try:
            if not current.is_symlink():
                continue
        except OSError:
            return None
        is_root_alias_ancestor = (
            current.parent == anchor
            and current.name in _ROOT_DIR_ALIASES
            and current != path
        )
        if is_root_alias_ancestor:
            continue
        return current
    return None


def _is_whykit_package_source_root(path: Path) -> bool:
    """True when ``path`` is a WhyKit package checkout (``src/whykit`` + project pyproject)."""
    pyproject = path / "pyproject.toml"
    package_dir = path / "src" / "whykit"
    if pyproject.is_symlink() or package_dir.is_symlink():
        return False
    if not pyproject.is_file() or not package_dir.is_dir():
        return False
    if not (package_dir / "cli.py").is_file() and not (package_dir / "__init__.py").is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return _PROJECT_NAME_RE.search(text) is not None


def _stamp_vault_dates(target: Path, today: dt.date | None = None) -> None:
    """Make a newly generated vault honest about when its starter files were created."""
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

    from .io import atomic_write_bytes, atomic_write_text, safe_vault_dir, safe_vault_target

    if getattr(args, "minimal", False) and getattr(args, "full", False):
        print("--minimal and --full cannot be used together", file=sys.stderr)
        return 2

    minimal_layout = not getattr(args, "full", False)
    # Do not Path.resolve(): that follows symlinks and can write outside the
    # path the user named. Expand ~ and absolutize only.
    target = _absolute_without_following_symlinks(args.target)
    linked = _first_symlink_on_path(target)
    if linked is not None:
        print(f"refusing to initialize through a symlink: {linked}", file=sys.stderr)
        return 2
    if _is_whykit_package_source_root(target):
        print(
            "refusing to initialize inside WhyKit's own package source tree: "
            f"{target}",
            file=sys.stderr,
        )
        return 2
    if target.exists() and not target.is_dir():
        print(f"refusing to initialize a non-directory target: {target}", file=sys.stderr)
        return 2
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
        try:
            gitignore = safe_vault_target(target, ".gitignore")
            if gitignore.exists() and not gitignore.is_file():
                raise RuntimeError(f"expected a regular .gitignore file: {gitignore}")
            existing_ignore = gitignore.read_text(encoding="utf-8") if gitignore.exists() else None
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"refusing unsafe init destination: {exc}", file=sys.stderr)
            return 2
        preserved = 0
        for source in sorted(prepared.rglob("*")):
            relative = source.relative_to(prepared)
            try:
                if source.is_dir():
                    safe_vault_dir(target, relative)
                    continue
                destination = safe_vault_target(target, relative)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"refusing unsafe init destination: {exc}", file=sys.stderr)
                return 2
            if destination.is_symlink():
                print(f"refusing to overwrite symlink: {destination}", file=sys.stderr)
                return 2
            if destination.exists():
                if not destination.is_file():
                    print(f"refusing to replace a non-file destination: {destination}", file=sys.stderr)
                    return 2
                if not args.force:
                    print(f"refusing to overwrite existing file: {destination}", file=sys.stderr)
                    return 2
                preserved += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(destination, source.read_bytes())

    # Deliberately no LICENSE: the vault holds the adopter's own knowledge, and
    # copying WhyKit's Apache-2.0 text into it would appear to license their
    # company's strategy and decisions under it.
    if existing_ignore is None:
        atomic_write_text(gitignore, VAULT_GITIGNORE)
    elif not existing_ignore.endswith(VAULT_GITIGNORE):
        separator = "" if not existing_ignore or existing_ignore.endswith("\n") else "\n"
        atomic_write_text(
            gitignore,
            existing_ignore + separator + "\n" + VAULT_GITIGNORE_MARKER + "\n" + VAULT_GITIGNORE,
        )

    if getattr(args, "json", False):
        import json
        print(json.dumps({
            "contract_version": 1,
            "root": str(target),
            "layout": "minimal" if minimal_layout else "full",
            "preserved": preserved,
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"WhyKit vault created: {target}")
    if preserved:
        print(f"Preserved {preserved} existing template file(s); only missing files were added.")
    if minimal_layout:
        print("Layout: vendor-neutral (default)")
    else:
        print("Layout: full GTM workstream starter (--full)")
    print()
    print("Next, in order:")
    print("  1. Answer every TODO in AGENTS.md - that file is the contract agents work under.")
    print("  2. Replace the starter content in 00-context/ before treating anything as canonical.")
    # Match the README cold-install path (`uv sync` + `uv run whykit` from the
    # checkout). Bare `whykit` is not on PATH after that install.
    print(f"  3. From the WhyKit checkout: uv run whykit lint --root {target}")
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


def _json_format(args: argparse.Namespace, command: str) -> str | None:
    """Fold the shared ``--json`` flag into a command's ``--format`` choice."""
    if not getattr(args, "json", False):
        return args.format or "json"
    if args.format not in (None, "json"):
        print(f"--json conflicts with --format {args.format}", file=sys.stderr)
        print(f"hint: pass one of them to `whykit {command}`", file=sys.stderr)
        return None
    return "json"


def cmd_graph(args: argparse.Namespace) -> int:
    from .graph import main as graph_main
    fmt = _json_format(args, "graph")
    if fmt is None:
        return 2
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv += ["--format", fmt]
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


def cmd_trace(args: argparse.Namespace) -> int:
    from .trace import main as trace_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    if args.decision:
        argv += ["--decision", args.decision]
    if args.today:
        argv += ["--today", args.today]
    if args.max_age_days is not None:
        argv += ["--max-age-days", str(args.max_age_days)]
    for flag in ("gaps_only", "strict", "json"):
        if getattr(args, flag):
            argv.append("--" + flag.replace("_", "-"))
    return trace_main(argv)


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
    fmt = _json_format(args, "pack")
    if fmt is None:
        return 2
    argv: list[str] = list(args.targets)
    if args.root:
        argv += ["--root", args.root]
    if args.query:
        argv += ["--query", args.query]
    argv += ["--max-docs", str(args.max_docs), "--max-chars", str(args.max_chars), "--format", fmt]
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
        print_no_vault(args.root)
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
    if args.json:
        argv.append("--json")
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
        print_no_vault(args.root)
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
        record("vault_root", False, "no vault found - run `whykit init <dir>` or pass --root")
        payload: dict[str, object] = {"contract_version": 1, "root": None, "passed": False, "checks": checks}
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


ROOT_HELP = "vault root (default: nearest vault at or above the working directory)"
TODAY_HELP = "evaluate review dates as of this YYYY-MM-DD date instead of today"
JSON_HELP = "emit machine-readable JSON on stdout"
SENSITIVITIES = ("public", "internal", "confidential", "restricted")

EPILOG = """\
exit codes:
  0    success (no errors; no warnings under --strict)
  1    the check ran and found problems that should fail the build
  2    the tool could not run: no vault, invalid input or configuration
  130  interrupted

Run `whykit <command> -h` for a command's options.
Docs: https://github.com/CometWeb-io/whykit#readme"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whykit",
        description="WhyKit - a Git-native evidence and decision ledger.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"whykit {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", title="commands")

    init = sub.add_parser("init", help="create a new vault", description="Create a new WhyKit vault from the bundled template.")
    init.add_argument("target", help="directory to create (must be empty unless --force)")
    init.add_argument("--force", action="store_true", help="add missing template files in a non-empty directory without replacing existing files")
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
    init.add_argument("--json", action="store_true", help=JSON_HELP)
    init.set_defaults(func=cmd_init)

    lint_cmd = sub.add_parser("lint", help="check a vault", description="Check vault structure, links, evidence and decisions.")
    lint_cmd.add_argument("paths", nargs="*", help="Markdown files or directories to check, relative to the vault root (default: whole vault)")
    lint_cmd.add_argument("--root", help=ROOT_HELP)
    lint_cmd.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 1)")
    lint_cmd.add_argument("--quiet", action="store_true", help="print only the summary line")
    lint_cmd.add_argument("--json", action="store_true", help=JSON_HELP)
    lint_cmd.add_argument("--no-orphans", action="store_true", help="skip orphan-note warnings")
    lint_cmd.add_argument("--no-secrets", action="store_true", help="skip the secret scan")
    lint_cmd.add_argument("--today", help=TODAY_HELP)
    lint_cmd.set_defaults(func=cmd_lint)

    new = sub.add_parser("new", help="create a decision, evidence row or note", description="Create a record and keep the vault indexes in sync.")
    new.add_argument("--root", help=ROOT_HELP)
    new_sub = new.add_subparsers(dest="new_kind", required=True, metavar="<kind>", title="kinds")
    new_decision = new_sub.add_parser("decision", help="create a decision record and index row")
    new_decision.add_argument("title", help="decision title, e.g. \"Adopt usage-based pricing\"")
    new_decision.add_argument("--owner", help="accountable person (default: policy defaults.owner)")
    new_decision.add_argument("--status", choices=("draft", "in_review", "approved", "superseded", "archived"), default="draft", help="initial status (default: %(default)s)")
    new_decision.add_argument("--sensitivity", choices=SENSITIVITIES, help="default: policy defaults.sensitivity")
    new_decision.add_argument("--source", action="append", default=[], dest="source_ids", metavar="E-NNN", help="supporting evidence ID (repeatable)")
    new_decision.add_argument("--review-by", metavar="YYYY-MM-DD", help="date by which the decision must be re-checked")
    new_decision.add_argument("--supersedes", metavar="D-NNN", help="decision this one replaces")
    new_decision.add_argument("--json", action="store_true", help=JSON_HELP)
    new_decision.set_defaults(func=cmd_new)

    new_evidence = new_sub.add_parser("evidence", help="append a source to the evidence register")
    new_evidence.add_argument("--source", required=True, dest="source_name", help="who or what produced the evidence")
    new_evidence.add_argument("--location", required=True, help="URL or path where the source can be checked")
    new_evidence.add_argument("--type", required=True, dest="evidence_type", help="kind of source, e.g. interview, analytics, report")
    new_evidence.add_argument("--claims", required=True, help="what the source supports, in one line")
    new_evidence.add_argument("--date", metavar="YYYY-MM-DD", help="when the source was produced (default: today)")
    new_evidence.add_argument("--accessed", metavar="YYYY-MM-DD", help="when it was last checked (default: today)")
    new_evidence.add_argument("--json", action="store_true", help=JSON_HELP)
    new_evidence.set_defaults(func=cmd_new)

    new_note = new_sub.add_parser("note", help="create a draft note in a workstream")
    new_note.add_argument("title", help="note title")
    new_note.add_argument("--workstream", required=True, help="existing workstream directory, e.g. notes")
    new_note.add_argument("--owner", help="default: policy defaults.owner")
    new_note.add_argument("--type", choices=("strategy", "research", "framework", "specification", "guide", "reference"), default="guide", dest="doc_type", help="document type (default: %(default)s)")
    new_note.add_argument("--sensitivity", choices=SENSITIVITIES, help="default: policy defaults.sensitivity")
    new_note.add_argument("--link-from", metavar="PATH", help="vault-relative Markdown map to link the new note from")
    new_note.add_argument("--json", action="store_true", help=JSON_HELP)
    new_note.set_defaults(func=cmd_new)

    status = sub.add_parser("status", help="summarize vault health and review queue")
    status.add_argument("--root", help=ROOT_HELP)
    status.add_argument("--json", action="store_true", help=JSON_HELP)
    status.add_argument("--today", help=TODAY_HELP)
    status.add_argument("--due-days", type=int, metavar="N", help="review window in days (default: policy defaults.status_due_days)")
    status.add_argument("--strict", action="store_true", help="exit 1 when anything needs attention")
    status.set_defaults(func=cmd_status)

    graph = sub.add_parser("graph", help="export the vault wikilink graph")
    graph.add_argument("--root", help=ROOT_HELP)
    graph.add_argument("--format", choices=("json", "dot", "mermaid", "obsidian"), default=None, help="output format (default: json)")
    graph.add_argument("--json", action="store_true", help="shorthand for --format json")
    graph.add_argument("--canonical-only", action="store_true", help="only include canonical documents")
    graph.add_argument("--output", metavar="PATH", help="write inside the vault (e.g. .whykit/graph.json) instead of stdout")
    graph.set_defaults(func=cmd_graph)

    backlinks = sub.add_parser("backlinks", help="list inbound links to a note, decision or evidence ID")
    backlinks.add_argument("target", help="vault-relative path, D-NNN or E-NNN")
    backlinks.add_argument("--root", help=ROOT_HELP)
    backlinks.add_argument("--json", action="store_true", help=JSON_HELP)
    backlinks.set_defaults(func=cmd_backlinks)

    impact = sub.add_parser("impact", help="show what depends on evidence, a decision or a document")
    impact.add_argument("target", help="vault-relative path, D-NNN or E-NNN")
    impact.add_argument("--root", help=ROOT_HELP)
    impact.add_argument("--json", action="store_true", help=JSON_HELP)
    impact.set_defaults(func=cmd_impact)

    trace = sub.add_parser("trace", help="trace decisions to their evidence and flag missing, retired or stale sources")
    trace.add_argument("--root", help=ROOT_HELP)
    trace.add_argument("--decision", metavar="D-NNN", help="trace only this decision")
    trace.add_argument("--today", help="evaluate evidence age as of this YYYY-MM-DD date instead of today")
    trace.add_argument("--max-age-days", type=int, metavar="N", help="treat evidence as stale after N days when policy has no age for its type")
    trace.add_argument("--gaps-only", action="store_true", help="list only live decisions with gaps")
    trace.add_argument("--strict", action="store_true", help="exit 1 when any live decision has a gap")
    trace.add_argument("--json", action="store_true", help=JSON_HELP)
    trace.set_defaults(func=cmd_trace)

    query = sub.add_parser("query", help="query notes by text, metadata and evidence")
    query.add_argument("text", nargs="?", help="free-text search (optional)")
    query.add_argument("--root", help=ROOT_HELP)
    query.add_argument("--type", dest="doc_type", help="filter by document type")
    query.add_argument("--status", help="filter by status")
    query.add_argument("--owner", help="filter by owner")
    query.add_argument("--sensitivity", help="filter by sensitivity")
    query.add_argument("--source", dest="source_id", metavar="E-NNN", help="only records citing this evidence ID")
    query.add_argument("--tag", help="filter by tag")
    query.add_argument("--canonical-only", action="store_true", help="only canonical documents")
    query.add_argument("--limit", type=int, default=100, metavar="N", help="maximum results (default: %(default)s)")
    query.add_argument("--json", action="store_true", help=JSON_HELP)
    query.set_defaults(func=cmd_query)

    context = sub.add_parser("context", help="build an evidence-aware context pack for one target")
    context.add_argument("target", help="vault-relative path, D-NNN or E-NNN")
    context.add_argument("--root", help=ROOT_HELP)
    context.add_argument("--max-chars", type=int, default=20_000, metavar="N", help="body character budget (default: %(default)s)")
    context.add_argument("--no-body", action="store_true", help="omit the document body")
    context.add_argument("--json", action="store_true", help=JSON_HELP)
    context.set_defaults(func=cmd_context)

    pack = sub.add_parser("pack", help="build a bounded multi-record context bundle")
    pack.add_argument("targets", nargs="*", help="vault-relative paths, D-NNN or E-NNN")
    pack.add_argument("--root", help=ROOT_HELP)
    pack.add_argument("--query", help="add records matching this text query")
    pack.add_argument("--max-docs", type=int, default=8, metavar="N", help="maximum records (default: %(default)s)")
    pack.add_argument("--max-chars", type=int, default=30_000, metavar="N", help="total body character budget (default: %(default)s)")
    pack.add_argument("--canonical-only", action="store_true", help="only canonical documents")
    pack.add_argument("--format", choices=("json", "markdown"), default=None, help="output format (default: json)")
    pack.add_argument("--json", action="store_true", help="shorthand for --format json")
    pack.add_argument("--for", dest="agent", choices=("generic", "cursor", "claude", "codex"), default="generic", help="tailor the preamble to an agent host (default: %(default)s)")
    pack.set_defaults(func=cmd_pack)

    review = sub.add_parser("review", help="list review work or record a review event")
    review.add_argument("--root", help=ROOT_HELP)
    review_sub = review.add_subparsers(dest="review_command", required=True, metavar="<action>", title="actions")
    review_list = review_sub.add_parser("list", help="show upcoming/overdue reviews")
    review_list.add_argument("--today", help=TODAY_HELP)
    review_list.add_argument("--due-days", type=int, metavar="N", help="review window in days (default: policy)")
    review_list.add_argument("--owner", help="only reviews owned by this person")
    review_list.add_argument("--overdue-only", action="store_true", help="hide reviews that are not yet due")
    review_list.add_argument("--json", action="store_true", help=JSON_HELP)
    review_list.set_defaults(func=cmd_review)
    review_record = review_sub.add_parser("record", help="record a review event")
    review_record.add_argument("target", help="vault-relative path or D-NNN")
    review_record.add_argument("--reviewer", required=True, help="who performed the review")
    review_record.add_argument("--outcome", choices=("confirmed", "update-required", "supersede-required", "archived"), default="confirmed", help="review result (default: %(default)s)")
    review_record.add_argument("--next-review", metavar="YYYY-MM-DD", help="next review date")
    review_record.add_argument("--note", default="", dest="note_text", help="short note for the review log")
    review_record.add_argument("--today", help="record the review as of this YYYY-MM-DD date")
    review_record.add_argument("--json", action="store_true", help=JSON_HELP)
    review_record.set_defaults(func=cmd_review)

    snapshot = sub.add_parser("snapshot", help="create a deterministic vault snapshot")
    snapshot.add_argument("--root", help=ROOT_HELP)
    snapshot.add_argument("--today", help=TODAY_HELP)
    snapshot.add_argument("--output", metavar="PATH", help="write the JSON snapshot here instead of stdout")
    snapshot.add_argument("--compact", action="store_true", help="emit compact JSON")
    snapshot.set_defaults(func=cmd_snapshot)

    verify_snapshot = sub.add_parser("verify-snapshot", help="compare the vault with a prior snapshot")
    verify_snapshot.add_argument("snapshot", help="baseline snapshot JSON (relative paths resolve against the vault root)")
    verify_snapshot.add_argument("--root", help=ROOT_HELP)
    verify_snapshot.add_argument("--today", help=TODAY_HELP)
    verify_snapshot.add_argument("--json", action="store_true", help=JSON_HELP)
    verify_snapshot.set_defaults(func=cmd_verify_snapshot)

    check = sub.add_parser("check", help="run a local/CI/release policy gate")
    check.add_argument("--root", help=ROOT_HELP)
    check.add_argument("--profile", default="ci", help="policy profile: local, ci, release or a custom one (default: %(default)s)")
    check.add_argument("--base", metavar="REF", help="base Git ref for the immutable-history check")
    check.add_argument("--head", default="HEAD", metavar="REF", help="head Git ref (default: %(default)s)")
    check.add_argument("--today", help=TODAY_HELP)
    check.add_argument("--json", action="store_true", help=JSON_HELP)
    check.set_defaults(func=cmd_check)

    policy = sub.add_parser("policy", help="show the effective repository-local policy")
    policy.add_argument("--root", help=ROOT_HELP)
    policy.add_argument("--json", action="store_true", help=JSON_HELP)
    policy.set_defaults(func=cmd_policy)

    evidence = sub.add_parser("evidence", help="inspect and manage evidence lifecycle")
    evidence.add_argument("--root", help=ROOT_HELP)
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True, metavar="<action>", title="actions")
    evidence_list = evidence_sub.add_parser("list", help="list evidence rows")
    evidence_list.add_argument("--state", choices=("all", "active", "retired"), default="all", help="which rows to list (default: %(default)s)")
    evidence_list.add_argument("--json", action="store_true", help=JSON_HELP)
    evidence_list.set_defaults(func=cmd_evidence)
    evidence_retire = evidence_sub.add_parser("retire", help="retire active evidence")
    evidence_retire.add_argument("id", metavar="E-NNN", help="evidence ID to retire")
    evidence_retire.add_argument("--why", required=True, dest="reason", help="why the evidence no longer holds")
    evidence_retire.add_argument("--replaced-by", metavar="E-NNN", help="evidence that supersedes it")
    evidence_retire.add_argument("--today", help="retire as of this YYYY-MM-DD date")
    evidence_retire.add_argument("--json", action="store_true", help=JSON_HELP)
    evidence_retire.set_defaults(func=cmd_evidence)

    adopt = sub.add_parser("adopt", help="inventory existing Markdown and stage it for a vault")
    adopt.add_argument("source", help="directory of existing Markdown to inventory")
    adopt.add_argument("--into", metavar="VAULT", help="vault to stage into (default: nearest vault)")
    adopt.add_argument("--owner", default="TODO", help="owner for adopted notes (default: %(default)s)")
    adopt.add_argument("--profile", choices=("generic", "adr-only", "obsidian-loose"), default="generic", help="how to interpret the source (default: %(default)s)")
    adopt.add_argument("--write", action="store_true", help="write the staged files (default: dry run)")
    adopt.add_argument("--json", action="store_true", help=JSON_HELP)
    adopt.set_defaults(func=cmd_adopt)

    history = sub.add_parser("history", help="verify that accepted decisions were not rewritten")
    history.add_argument("--base", required=True, metavar="REF", help="base commit or ref, e.g. origin/main")
    history.add_argument("--head", default="HEAD", metavar="REF", help="head commit or ref (default: %(default)s)")
    history.add_argument("--root", help=ROOT_HELP)
    history.add_argument("--json", action="store_true", help=JSON_HELP)
    history.set_defaults(func=cmd_history)

    rules = sub.add_parser("rules", help="list lint rules or explain one rule code")
    rules.add_argument("code", nargs="?", help="rule code to explain, e.g. evidence.unknown_id")
    rules.add_argument("--json", action="store_true", help=JSON_HELP)
    rules.add_argument("--markdown", action="store_true", help="emit the complete Markdown table")
    rules.set_defaults(func=cmd_rules)

    doctor = sub.add_parser("doctor", help="check prerequisites and vault integrity")
    doctor.add_argument("--root", help=ROOT_HELP)
    doctor.add_argument("--json", action="store_true", help=JSON_HELP)
    doctor.set_defaults(func=cmd_doctor)

    hooks = sub.add_parser("install-hooks", help="install the pre-commit vault check")
    hooks.add_argument("--root", help=ROOT_HELP)
    hooks.add_argument("--force", action="store_true", help="replace an existing pre-commit hook")
    hooks.set_defaults(func=cmd_install_hooks)

    explorer_index = sub.add_parser(
        "explorer-index",
        help="export the Explorer vault index from the canonical Python parser",
    )
    explorer_index.add_argument("--root", help=ROOT_HELP)
    explorer_index.add_argument("--today", help=TODAY_HELP)
    explorer_index.add_argument("--json", action="store_true", default=True, help="accepted for symmetry; output is always JSON")
    explorer_index.set_defaults(func=cmd_explorer_index)

    serve = sub.add_parser("serve", help="run the optional Explorer (source checkout only)")
    serve.add_argument("vault", nargs="?", default=".", help="vault to serve (default: current directory)")
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind (default: %(default)s)")
    serve.add_argument("--port", type=int, default=5173, help="port to listen on (default: %(default)s)")
    serve.add_argument("--allow-sensitive-network", action="store_true", help="allow non-loopback serving even when non-public docs are present")
    serve.set_defaults(func=cmd_serve)

    completion = sub.add_parser(
        "completion",
        help="print a shell completion script",
        description="Print a completion script for your shell.",
        epilog=COMPLETION_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    completion.add_argument("shell", choices=SHELLS, help="target shell")
    completion.set_defaults(func=cmd_completion)

    return parser


COMPLETION_EPILOG = """\
install:
  bash  eval "$(whykit completion bash)"        # add to ~/.bashrc
  zsh   eval "$(whykit completion zsh)"         # add to ~/.zshrc
  fish  whykit completion fish > ~/.config/fish/completions/whykit.fish"""


def cmd_completion(args: argparse.Namespace) -> int:
    print(render_completion(build_parser(), args.shell), end="")
    return 0


def _describe_os_error(exc: OSError) -> str:
    reason = exc.strerror or exc.__class__.__name__
    if exc.filename is not None and exc.filename2 is not None:
        return f"{reason}: {exc.filename} -> {exc.filename2}"
    if exc.filename is not None:
        return f"{reason}: {exc.filename}"
    return str(exc) or reason


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # The reader went away (`whykit query | head`). Point stdout at devnull
        # so the interpreter's final flush does not print a second traceback.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 1
    except OSError as exc:
        # A filesystem refusal is a user-facing condition, not a crash.
        # WHYKIT_DEBUG=1 restores the traceback for bug reports.
        if os.environ.get("WHYKIT_DEBUG"):
            raise
        print(f"cannot complete `whykit {args.command}`: {_describe_os_error(exc)}", file=sys.stderr)
        print("hint: check the path and its permissions; set WHYKIT_DEBUG=1 for a traceback", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
