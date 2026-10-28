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
import errno
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import NoReturn

from . import __version__
from .contract import (
    CONTRACT_VERSION,
    ISSUES_URL,
    argv_wants_json,
    describe_os_error,
    emit_error,
    error_payload,
    vault_not_found,
)
from .lint import find_vault_root, is_markdown_name, is_vault_root, iter_markdown, lint as run_lint, rel
from .completion import SHELLS, render_completion
from .messages import print_no_vault
from .vault_index import VaultIndex
from .console import emit_machine, harden_stdio

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

# WhyKit's own write lock, crash-recovery journal and parse cache. Machine state, not knowledge.
.whykit/mutation.lock
.whykit/transactions/
.whykit/cache/
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
    for path in iter_markdown(target):
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

    json_mode = bool(getattr(args, "json", False))
    if getattr(args, "minimal", False) and getattr(args, "full", False):
        return emit_error("usage", "--minimal and --full cannot be used together", json_mode=json_mode)

    minimal_layout = not getattr(args, "full", False)
    # Do not Path.resolve(): that follows symlinks and can write outside the
    # path the user named. Expand ~ and absolutize only.
    target = _absolute_without_following_symlinks(args.target)
    linked = _first_symlink_on_path(target)
    if linked is not None:
        return emit_error("unsafe_path", f"refusing to initialize through a symlink: {linked}", json_mode=json_mode)
    if _is_whykit_package_source_root(target):
        return emit_error(
            "invalid_target",
            f"refusing to initialize inside WhyKit's own package source tree: {target}",
            json_mode=json_mode,
        )
    if target.exists() and not target.is_dir():
        return emit_error("invalid_target", f"refusing to initialize a non-directory target: {target}", json_mode=json_mode)
    if target.exists() and any(target.iterdir()) and not args.force:
        return emit_error(
            "target_exists",
            f"refusing to write into non-empty directory: {target}",
            hint="use --force only when you have reviewed the destination",
            json_mode=json_mode,
        )
    if not TEMPLATE_DIR.is_dir():
        return emit_error("missing_dependency", "template is missing from this installation", json_mode=json_mode)

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
            return emit_error("unsafe_path", f"refusing unsafe init destination: {exc}", json_mode=json_mode)
        preserved = 0
        for source in sorted(prepared.rglob("*")):
            relative = source.relative_to(prepared)
            try:
                if source.is_dir():
                    safe_vault_dir(target, relative)
                    continue
                destination = safe_vault_target(target, relative)
            except (OSError, RuntimeError, ValueError) as exc:
                return emit_error("unsafe_path", f"refusing unsafe init destination: {exc}", json_mode=json_mode)
            if destination.is_symlink():
                return emit_error("unsafe_path", f"refusing to overwrite symlink: {destination}", json_mode=json_mode)
            if destination.exists():
                if not destination.is_file():
                    return emit_error(
                        "invalid_target",
                        f"refusing to replace a non-file destination: {destination}",
                        json_mode=json_mode,
                    )
                if not args.force:
                    return emit_error("target_exists", f"refusing to overwrite existing file: {destination}", json_mode=json_mode)
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

    if json_mode:
        import json
        emit_machine(json.dumps({
            "contract_version": CONTRACT_VERSION,
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
        print("Layout: full starter with optional workstreams (--full)")
    print()
    print("Next, in order:")
    print("  1. Answer every TODO in AGENTS.md - that file is the contract agents work under.")
    print("  2. Replace the starter content in 00-context/ before treating anything as canonical.")
    # Match the README cold-install path (`uv sync` + `uv run whykit` from the
    # checkout), where bare `whykit` is not on PATH. An installed tool has no
    # checkout to run from, so it gets the bare command instead.
    if _is_whykit_package_source_root(SOURCE_ROOT):
        print(f"  3. From the WhyKit checkout: uv run whykit lint --root {target}")
    else:
        print(f"  3. Check it: whykit lint --root {target}")
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
    if getattr(args, "format", None):
        argv += ["--format", args.format]
    from .lint import main as lint_main
    return lint_main(argv)



def cmd_new(args: argparse.Namespace) -> int:
    from .scaffold import main as new_main
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    argv.append(args.new_kind)
    if args.new_kind == "decision":
        if args.title is not None:
            argv.append(args.title)
        if args.from_path:
            argv += ["--from", args.from_path]
        if args.write:
            argv.append("--write")
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
        if args.sensitivity:
            argv += ["--sensitivity", args.sensitivity]
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
        emit_error(
            "usage",
            f"--json conflicts with --format {args.format}\nhint: pass one of them to `whykit {command}`",
            json_mode=True,
        )
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
    if args.ranking != "substring":
        argv += ["--ranking", args.ranking]
    if args.json:
        argv.append("--json")
    return query_main(argv)


def cmd_retrieve(args: argparse.Namespace) -> int:
    from .hybrid_retrieval import retrieve_hybrid
    from .messages import parse_iso_date

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        report = retrieve_hybrid(root, args.text, valid_on=parse_iso_date("--valid-on", args.valid_on),
                                 validity=args.validity, decision_role=args.decision_role,
                                 doc_type=args.doc_type, status=args.status, owner=args.owner,
                                 sensitivity=args.sensitivity, source_id=args.source_id, tag=args.tag,
                                 canonical_only=args.canonical_only, limit=args.limit,
                                 allowed_sensitivities=_workflow_scope(args))
    except ValueError:
        return emit_error("invalid_argument", "invalid retrieval query, date, filters or limits", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ranked" else 1


def cmd_agent(args: argparse.Namespace) -> int:
    from .agent_eval import evaluate, evaluation_succeeded
    from .agent_preflight import check_proposal
    from .messages import parse_iso_date

    try:
        if args.agent_command == "eval":
            if not 0 <= args.min_pass_rate <= 1:
                raise ValueError("invalid evaluation threshold")
            report = evaluate(args.cases, args.responses)
            code = 0 if evaluation_succeeded(report, args.min_pass_rate) else 1
        else:
            root = _resolve_vault(args.root)
            if root is None or not is_vault_root(root):
                return vault_not_found(args.root, json_mode=True)
            today = parse_iso_date("--today", args.today) if args.today else dt.date.today()
            report = check_proposal(root, args.proposal, today=today)
            if args.context:
                from .compiled_context import verify_compiled
                raw_context = _read_context_report(args.context)
                verification = verify_compiled(root, raw_context, today=today,
                                                allowed_sensitivities=_workflow_scope(args))
                report["context_verification"] = {"status": verification["status"], "execution_authorized": False}
                current = verification["status"] == "current"
                covered: set[str] = set()
                if current:
                    compiled = json.loads(raw_context)
                    covered = {identity for item in compiled["contexts"]
                               if item.get("applicability", {}).get("premise") is True
                               and isinstance(identity := item.get("record", {}).get("decision_id"), str)
                               and re.fullmatch(r"D-[0-9]{3,}", identity)}
                missing = False
                for finding in report["findings"]:
                    problem = "context_not_current" if not current else "context_missing_decision"
                    if not current or finding["decision_id"] not in covered:
                        finding["problems"] = sorted(set(finding["problems"]) | {problem})
                        missing = True
                if missing:
                    report["verdict"] = "blocked"
            code = 0 if report["verdict"] == "pass" else 1
    except ValueError:
        return emit_error("invalid_argument", "invalid agent evaluation or proposal input", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


def _workflow_scope(args: argparse.Namespace) -> set[str]:
    return set(SENSITIVITIES[:SENSITIVITIES.index(args.max_sensitivity) + 1])


def _read_context_report(path: Path) -> bytes:
    from .compiled_context import MAX_REPORT_BYTES
    from .io import open_regular_binary

    with open_regular_binary(path) as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("context report exceeds the input limit")
    return raw


def cmd_compile_context(args: argparse.Namespace) -> int:
    from .compiled_context import compile_context, serialize_compiled
    from .messages import parse_iso_date

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        today = parse_iso_date("--today", args.today) if args.today else None
        report = compile_context(root, targets=args.targets, query=args.query,
                                 ranking=args.ranking, max_docs=args.max_docs,
                                 max_bytes=args.max_bytes, today=today, agent=args.agent,
                                 allowed_sensitivities=_workflow_scope(args))
        wire = serialize_compiled(report)
    except ValueError:
        return emit_error("invalid_argument", "invalid context inputs, scope or byte budget", json_mode=True)
    # Write the measured UTF-8 bytes directly: Windows newline translation would exceed the cap.
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        sys.stdout.write(wire)
    else:
        sys.stdout.flush()
        binary.write(wire.encode("utf-8"))
        binary.flush()
    return 0 if report["status"] == "compiled" else 1


def cmd_verify_context(args: argparse.Namespace) -> int:
    from .compiled_context import verify_compiled
    from .messages import parse_iso_date

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        today = parse_iso_date("--today", args.today) if args.today else None
        report = verify_compiled(root, _read_context_report(args.report), today=today,
                                 allowed_sensitivities=_workflow_scope(args))
    except ValueError:
        return emit_error("invalid_argument", "invalid context report or date", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "current" else 1


def cmd_deep_impact(args: argparse.Namespace) -> int:
    from .dependency_graph import deep_impact

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        report = deep_impact(root, args.target, max_depth=args.max_depth, max_nodes=args.max_nodes,
                             allowed_sensitivities=_workflow_scope(args))
    except (ValueError, OSError, UnicodeError):
        return emit_error("invalid_argument", "invalid impact target or traversal limits", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return int(not report["exists"])


def cmd_knowledge_at(args: argparse.Namespace) -> int:
    from .messages import parse_iso_date
    from .temporal_graph import knowledge_at

    root = Path(args.root).expanduser().resolve() if args.root else (_resolve_vault(None) or Path.cwd())
    try:
        if not root.is_dir():
            raise ValueError("historical vault root is not a directory")
        report = knowledge_at(root, recorded_revision=args.recorded_revision,
                              valid_on=parse_iso_date("--valid-on", args.valid_on), target=args.target,
                              max_nodes=args.max_nodes, allowed_sensitivities=_workflow_scope(args))
    except (ValueError, RuntimeError, subprocess.CalledProcessError):
        return emit_error("invalid_argument", "invalid or unavailable historical knowledge snapshot", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["exists"] else 1


def cmd_artifact(args: argparse.Namespace) -> int:
    from .artifact_store import capture_artifact

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        report = capture_artifact(root, args.evidence, args.source_file, recorded_at=args.recorded_at,
                                  write=args.write, expected_sha256=args.expect_hash)
    except ValueError:
        return emit_error("invalid_argument", "invalid or changed artifact inputs; review a fresh preview", json_mode=True)
    emit_machine(json.dumps({"contract_version": 1, **report}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .github_import import import_github

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        report = import_github(root, args.evidence, args.input, kind=args.kind,
                               repository=args.repository, recorded_at=args.recorded_at,
                               write=args.write, expected_sha256=args.expect_hash)
    except ValueError:
        return emit_error("invalid_argument", "invalid or changed GitHub export inputs; review a fresh preview", json_mode=True)
    emit_machine(json.dumps({"contract_version": 1, **report}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    from .artifact_store import _strict_json
    from .claim_ledger import MAX_CLAIM_BYTES, create_claim, list_claims, read_claim, verify_claim
    from .io import open_regular_binary
    from .messages import parse_iso_date

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        code = 0
        if args.claim_command == "create":
            links = []
            if args.links:
                with open_regular_binary(args.links) as stream:
                    raw = stream.read(MAX_CLAIM_BYTES + 1)
                if len(raw) > MAX_CLAIM_BYTES:
                    raise ValueError("claim links exceed input limit")
                links = _strict_json(raw.decode("utf-8"))
            today = parse_iso_date("--today", args.today) if args.today else None
            report = create_claim(root, args.statement, links, title=args.title or " ".join(args.statement.split())[:80],
                                  owner=args.owner, sensitivity=args.sensitivity, today=today,
                                  valid_from=args.valid_from, valid_until=args.valid_until,
                                  write=args.write, expected_sha256=args.expect_hash)
        elif args.claim_command == "list":
            report = {"claims": list_claims(root, allowed_sensitivities=_workflow_scope(args))}
        elif args.claim_command == "show":
            row = read_claim(root, args.target, allowed_sensitivities=_workflow_scope(args))
            report = {"exists": row is not None, "record": row}
            code = 0 if row is not None else 1
        else:
            today = parse_iso_date("--today", args.today) if args.today else None
            report = verify_claim(root, args.target, allowed_sensitivities=_workflow_scope(args), today=today)
            code = 0 if report["exists"] and not report["unlinked"] and all(
                link["status"] == "matched" and link["source_state"] == "current" for link in report["links"]
            ) else 1
    except ValueError:
        return emit_error("invalid_argument", "invalid or changed claim inputs; review a fresh preview", json_mode=True)
    emit_machine(json.dumps({"contract_version": 1, **report}, ensure_ascii=False, sort_keys=True))
    return code


def cmd_workspace(args: argparse.Namespace) -> int:
    from .messages import parse_iso_date
    from .workspace import build_workspace_status

    try:
        today = parse_iso_date("--today", args.today) if args.today else None
        report = build_workspace_status(args.manifest, today=today)
    except ValueError:
        return emit_error("invalid_argument", "invalid local workspace manifest or date", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return int(any("error" in item or item["status"]["errors"] for item in report["vaults"]))


def _read_workflow_json(path: Path, maximum: int):
    from .artifact_store import _read_regular, _strict_json

    raw = _read_regular(path, max_bytes=maximum)
    assert raw is not None
    return _strict_json(raw.decode("utf-8"))


def cmd_outcome(args: argparse.Namespace) -> int:
    from .messages import parse_iso_date
    from .outcome_ledger import MAX_OUTCOME_BYTES, create_outcome, list_outcomes, read_outcome, verify_outcome

    root = _resolve_vault(args.root)
    if root is None or not is_vault_root(root):
        return vault_not_found(args.root, json_mode=True)
    try:
        code = 0
        if args.outcome_command == "create":
            data = _read_workflow_json(args.input, MAX_OUTCOME_BYTES)
            today = parse_iso_date("--today", args.today) if args.today else None
            report = create_outcome(root, data, owner=args.owner, title=args.title, today=today,
                                    write=args.write, expected_sha256=args.expect_hash)
        elif args.outcome_command == "list":
            report = {"outcomes": list_outcomes(root, allowed_sensitivities=_workflow_scope(args))}
        elif args.outcome_command == "show":
            row = read_outcome(root, args.target, allowed_sensitivities=_workflow_scope(args))
            report = {"exists": row is not None, "record": row}
            code = 0 if row is not None else 1
        else:
            today = parse_iso_date("--today", args.today) if args.today else None
            report = verify_outcome(root, args.target, today=today, allowed_sensitivities=_workflow_scope(args))
            code = 0 if report["observed_source_verified"] else 1
    except ValueError:
        return emit_error("invalid_argument", "invalid or changed outcome inputs; review a fresh preview", json_mode=True)
    emit_machine(json.dumps({"contract_version": 1, **report}, ensure_ascii=False, sort_keys=True))
    return code


def cmd_knowledge(args: argparse.Namespace) -> int:
    from .knowledge_v2 import (
        MAX_BUNDLE_BYTES, UnsupportedMigrationPlatform, export_migration_bundle,
        project_knowledge_v2, restore_migration_bundle,
    )

    try:
        if args.knowledge_command == "restore":
            report = restore_migration_bundle(_read_workflow_json(args.bundle, MAX_BUNDLE_BYTES), args.destination,
                                              write=args.write, expected_sha256=args.expect_hash)
        else:
            root = _resolve_vault(args.root)
            if root is None or not is_vault_root(root):
                return vault_not_found(args.root, json_mode=True)
            if args.knowledge_command == "view":
                report = project_knowledge_v2(root, allowed_sensitivities=_workflow_scope(args))
            else:
                report = export_migration_bundle(root, args.output, full_local_scope=args.private,
                                                 write=args.write, expected_sha256=args.expect_hash)
    except UnsupportedMigrationPlatform:
        return emit_error("operation_rejected", "safe knowledge export and restore require a supported POSIX host", json_mode=True)
    except ValueError:
        return emit_error("invalid_argument", "invalid, unavailable or changed knowledge migration inputs", json_mode=True)
    emit_machine(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


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
        argv += ["--reviewer", args.reviewer]
        if args.review_command == "record":
            argv += ["--outcome", args.outcome]
        if getattr(args, "write", False):
            argv.append("--write")
        if getattr(args, "expected_sha256", None):
            argv += ["--expect-hash", args.expected_sha256]
        if args.next_review:
            argv += ["--next-review", args.next_review]
        if getattr(args, "note_text", None):
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
    if args.snapshot_format:
        argv += ["--format", args.snapshot_format]
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
    if args.format:
        argv += ["--format", args.format]
    return check_main(argv)


def cmd_policy(args: argparse.Namespace) -> int:
    from .config import ConfigError, config_summary
    vault = _resolve_vault(args.root)
    if vault is None:
        return vault_not_found(args.root, json_mode=args.json)
    try:
        payload = config_summary(vault)
    except ConfigError as exc:
        return emit_error("invalid_config", str(exc), json_mode=args.json)
    if args.json:
        import json
        emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"WhyKit policy — {payload['source']}")
        defaults = payload["config"]["defaults"]
        print(f"  default owner          {defaults['owner']}")
        print(f"  default sensitivity    {defaults['sensitivity']}")
        print(f"  decision review days   {defaults['decision_review_days']}")
        print(f"  status due days        {defaults['status_due_days']}")
        for name, profile in payload["config"]["profiles"].items():
            print(f"  profile {name:<10} strict={profile['strict']} orphans={profile['orphans']} secrets={profile['secrets']} history={profile['history']}")
        rules = payload["config"].get("rules") or {}
        if rules:
            print(f"  custom rules           {len(rules.get('custom', []))} (see `whykit rules`)")
            print(f"  rule overrides         {', '.join(sorted(rules.get('overrides', {}))) or 'none'}")
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
    if args.compare is not None:
        argv += ["--compare", args.compare]
    if args.write:
        argv.append("--write")
    if args.json:
        argv.append("--json")
    return adopt_main(argv)


def cmd_history(args: argparse.Namespace) -> int:
    from .immutability import main as immutability_main
    argv: list[str] = []
    if args.base is not None:
        argv += ["--base", args.base]
    if args.head is not None:
        argv += ["--head", args.head]
    if args.staged:
        argv.append("--staged")
    if args.root:
        argv += ["--root", args.root]
    if args.json:
        argv.append("--json")
    code = immutability_main(argv)
    if code in (0, 1) and not args.staged and args.head in (None, "HEAD"):
        _warn_uncommitted_markdown(_resolve_vault(args.root))
    return code


def cmd_diff(args: argparse.Namespace) -> int:
    from .diff import main as diff_main
    argv = ["--base", args.base, "--head", args.head]
    if args.root:
        argv += ["--root", args.root]
    if args.today:
        argv += ["--today", args.today]
    if args.format:
        argv += ["--format", args.format]
    if args.json:
        argv.append("--json")
    return diff_main(argv)


def _uncommitted_markdown(vault: Path) -> list[str]:
    """Markdown under *vault* that differs from HEAD (staged, unstaged or new)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain", "--untracked-files=all", "--", "."],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if is_markdown_name(path):
            paths.append(path)
    return paths


def _warn_uncommitted_markdown(vault: Path | None) -> None:
    """Say so when `history` compared commits but the edits are not committed yet.

    `history` checks revisions, so an uncommitted rewrite of accepted reasoning
    passes locally and only fails once it reaches CI. That reads as a false OK.
    """
    if vault is None:
        return
    pending = _uncommitted_markdown(vault)
    if not pending:
        return
    sys.stdout.flush()
    print(
        f"note: {len(pending)} uncommitted Markdown change(s) were not checked; "
        "history compares commits only (e.g. "
        f"{pending[0]})",
        file=sys.stderr,
    )
    print("hint: commit the change, then rerun `whykit history --base <ref>`", file=sys.stderr)


PRE_COMMIT_HOOK = """#!/bin/sh
# Installed by `whykit install-hooks`.
set -e
root=$(git rev-parse --show-toplevel)
cd "$root"
# __WHYKIT_VAULT_CD__

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


def _git_hooks_location(vault: Path) -> tuple[Path, str] | None:
    """Where Git runs hooks for ``vault``, and the vault's path inside the work tree.

    The vault may be a subdirectory of a larger repository or sit in a linked
    worktree (where `.git` is a file), and `core.hooksPath` may move the hooks,
    so ask Git instead of assuming `<vault>/.git/hooks`.
    """
    from .immutability import GitScopeError, _git_prefix, git

    if not shutil.which("git"):
        return None
    try:
        prefix = _git_prefix(str(vault))
        output = git("rev-parse", "--git-path", "hooks", root=str(vault))
    except GitScopeError:
        raise OSError(errno.EINVAL, "Git vault scope could not be verified; ambiguous paths are refused") from None
    except subprocess.CalledProcessError:
        return None
    if not output.endswith("\n") or not output[:-1] or any(char in output[:-1] for char in "\r\n\0"):
        raise OSError(errno.EINVAL, "Git hook location could not be verified; ambiguous paths are refused")
    hooks = Path(output[:-1])
    if not hooks.is_absolute():
        hooks = vault / hooks
    return hooks, prefix


def _render_pre_commit_hook(prefix: str) -> str:
    """The hook runs from the work-tree root; step into the vault when it is a subdirectory."""
    line = f"cd -- {shlex.quote(prefix.rstrip('/'))}" if prefix else ""
    return PRE_COMMIT_HOOK.replace("# __WHYKIT_VAULT_CD__\n", f"{line}\n" if line else "")


def cmd_install_hooks(args: argparse.Namespace) -> int:
    vault = _resolve_vault(args.root)
    if vault is None:
        print_no_vault(args.root)
        return 2
    located = _git_hooks_location(vault)
    if located is None:
        print(f"not a git repository: {vault}", file=sys.stderr)
        print("run `git init` first - the ledger's guarantees come from version control", file=sys.stderr)
        return 2
    hooks, prefix = located
    target = hooks / "pre-commit"
    if target.exists() and not args.force:
        print(f"a pre-commit hook already exists: {target}", file=sys.stderr)
        print("inspect it, then re-run with --force to replace it", file=sys.stderr)
        return 2
    hooks.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_pre_commit_hook(prefix), encoding="utf-8")
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
    if args.root:
        argv += ["--root", args.root]
    return rules_main(argv)

def cmd_doctor(args: argparse.Namespace) -> int:
    import json
    from .config import ConfigError, load_config

    today: dt.date | None = None
    if getattr(args, "today", None):
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}\nhint: use YYYY-MM-DD", json_mode=args.json)
    vault = _resolve_vault(args.root)
    checks: list[dict[str, object]] = []

    def record(label: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"name": label, "passed": bool(ok), "detail": detail, "required": required})

    record("python", sys.version_info >= (3, 11), sys.version.split()[0])
    record("whykit", True, __version__)
    if vault is None:
        record("vault_root", False, "no vault found - run `whykit init <dir>` or pass --root")
        payload: dict[str, object] = {"contract_version": CONTRACT_VERSION, "root": None, "passed": False, "checks": checks}
        if args.json:
            emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
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
        inside = subprocess.run(["git", "-C", str(vault), "rev-parse", "--git-dir"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        tracked = inside.returncode == 0
        record("git_repository", tracked, "tracked" if tracked else "not a git repository yet - `git init`", required=False)
        located = _git_hooks_location(vault) if tracked else None
        hook_installed = bool(located and (located[0] / "pre-commit").exists())
        record("pre_commit_hook", hook_installed, "installed" if hook_installed else "not installed - `whykit install-hooks`", required=False)

    record("node_explorer", bool(shutil.which("node")), shutil.which("node") or "not found (only needed for `serve`)", required=False)

    # Same findings as `whykit lint` and `whykit status`, so the counts agree.
    files, findings = run_lint(vault, [], today=today)
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
    payload = {"contract_version": CONTRACT_VERSION, "root": str(vault), "passed": passed, "checks": checks}
    if args.json:
        emit_machine(json.dumps(payload, ensure_ascii=False, indent=2))
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
    from .sensitivity import note_sensitivity_level, classified_evidence, evidence_register_notes

    result: list[str] = []
    for note in index.notes:
        if note_sensitivity_level(note) != 0:
            result.append(index.relative(note.path))
    active, retired, _, per_row = classified_evidence(index)
    # The private viewer serves the raw table, including incomplete/placeholder rows.
    if per_row:
        result.extend(index.relative(note.path) for note in evidence_register_notes(index)
                      if index.relative(note.path) not in result)
    result.extend(f"evidence:{key}" for key, row in (*active.items(), *retired.items()) if row["sensitivity"] != "public")
    return result


def cmd_recover(args: argparse.Namespace) -> int:
    import json
    from .io import vault_mutation_lock

    root = _resolve_vault(args.root)
    if root is None:
        return vault_not_found(args.root, json_mode=args.json)
    with vault_mutation_lock(root):
        pass  # Recovery and terminal-journal collection finish before the lock yields.
    if args.json:
        emit_machine(json.dumps({"contract_version": 1, "recovery_completed": True}))
    else:
        print("Vault recovery completed.")
    return 0

def cmd_explorer_index(args: argparse.Namespace) -> int:
    argv: list[str] = []
    if args.root:
        argv += ["--root", args.root]
    if getattr(args, "today", None):
        argv += ["--today", args.today]
    if getattr(args, "private", False):
        argv += ["--private"]
    if getattr(args, "publication_preview", False):
        argv += ["--publication-preview"]
    from .explorer_index import main as explorer_index_main
    return explorer_index_main(argv)


REPOSITORY_URL = "https://github.com/CometWeb-io/whykit"


def _install_origin() -> dict | None:
    """Where this copy was installed from: its PEP 610 ``direct_url.json``.

    Installers write it for a local directory (``uv tool install ./whykit``,
    ``pip install .``) or a VCS URL, and not for a package index.
    """
    import json
    from importlib import metadata

    try:
        raw = metadata.distribution("whykit").read_text("direct_url.json")
    except (metadata.PackageNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _serve_hint(origin: dict | None, vault: str) -> list[str]:
    """What to do when the running copy has no Explorer next to it."""
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    target = shlex.quote(str(Path(vault).expanduser().resolve()))
    url = str((origin or {}).get("url") or "")
    if url.startswith("file://") and isinstance((origin or {}).get("dir_info"), dict):
        checkout = Path(url2pathname(urlparse(url).path))
        if (checkout / "apps" / "explorer" / "package.json").is_file():
            return [
                f"This copy was installed from the checkout at {checkout}, which has the Explorer.",
                "Run it from that checkout:",
                f"  cd {shlex.quote(str(checkout))} && uv run whykit serve {target}",
            ]
        return [
            f"This copy was installed from {checkout}, which no longer has apps/explorer/.",
            "Clone the repository and run `whykit serve` from the clone:",
            f"  git clone {REPOSITORY_URL}",
        ]
    vcs = (origin or {}).get("vcs_info")
    if url and isinstance(vcs, dict) and vcs.get("vcs") == "git":
        lines = [
            "This copy was installed from Git. Clone the same source and run `whykit serve` from the clone:",
            f"  git clone {shlex.quote(url)} whykit",
        ]
        commit = str(vcs.get("commit_id") or "")
        if re.fullmatch(r"[0-9a-f]{7,64}", commit):
            lines.append(f"  git -C whykit checkout {commit}")
        lines.append(f"  cd whykit && uv run whykit serve {target}")
        return lines
    return [
        "Clone the repository and run `whykit serve` from the clone:",
        f"  git clone {REPOSITORY_URL}",
        f"  cd whykit && uv run whykit serve {target}",
    ]


def cmd_lsp(args: argparse.Namespace) -> int:
    argv: list[str] = ["--debounce", str(args.debounce)]
    if args.root:
        argv += ["--root", args.root]
    if args.today:
        argv += ["--today", args.today]
    from .lsp import main as lsp_main
    return lsp_main(argv)


def _serve_target(args: argparse.Namespace) -> str:
    """The vault ``serve`` should show: the positional argument or ``--root``."""
    positional, root = getattr(args, "vault", None), getattr(args, "root", None)
    if positional and root and Path(positional).expanduser().resolve() != Path(root).expanduser().resolve():
        raise ValueError(f"two different vaults given: {positional} and --root {root}; pass one")
    return positional or root or "."


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        target = _serve_target(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    explorer = SOURCE_ROOT / "apps" / "explorer"
    if not (explorer / "package.json").exists():
        print("The Explorer ships with the source repository, not the package.", file=sys.stderr)
        for line in _serve_hint(_install_origin(), target):
            print(line, file=sys.stderr)
        return 2
    npm = shutil.which("npm")
    if not npm:
        print("npm is required for the Explorer", file=sys.stderr)
        return 2
    vault = _resolve_vault(None if target == "." else target) or Path(target).expanduser().resolve()
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
    env["WHYKIT_EXPLORER_PRIVATE"] = "1"
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
  70   internal error: a bug in WhyKit, not a problem with the vault
  130  interrupted

Run `whykit <command> -h` for a command's options.
Docs: https://github.com/CometWeb-io/whykit#readme"""

# Top-level help groups the commands by the job they do, so ~30 commands read
# as five verbs rather than one flat list. Every command belongs to exactly one
# group; tests/test_cli_consistency.py holds this table to the parser.
COMMAND_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("author", "create and change records", ("init", "adopt", "new", "review", "evidence", "artifact", "claim", "outcome", "import")),
    ("check", "gate a vault or a change", ("lint", "check", "status", "trace", "history", "diff", "snapshot", "verify-snapshot", "agent", "verify-context")),
    ("explore", "find and hand over context", ("query", "retrieve", "context", "pack", "graph", "backlinks", "impact", "compile-context", "deep-impact", "knowledge-at")),
    ("integrate", "connect hooks, viewers and shells", ("install-hooks", "explorer-index", "serve", "lsp", "completion")),
    ("maintain", "inspect policy, rules and setup", ("policy", "rules", "doctor", "recover", "workspace", "knowledge")),
)

COMMANDS_PREAMBLE = (
    "Vault commands take --root DIR before or after the command (default: the\n"
    "nearest vault), --json for one JSON document on stdout, and --today\n"
    "YYYY-MM-DD where a result depends on the date."
)


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors honour the machine contract.

    argparse reports a bad command line before any command runs, so the
    ``--json`` choice is read from the raw arguments (see ``main``). Human
    output and the exit code (2) are exactly argparse's own.
    """

    json_errors = False
    # The command path being parsed (e.g. "review list"), read from the raw
    # arguments in ``main`` so a top-level usage error can name its subcommand.
    command_path = ""

    def error(self, message: str) -> NoReturn:
        hint = _usage_hint(message, _Parser.command_path)
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        if hint:
            sys.stderr.write(f"hint: {hint}\n")
        if _Parser.json_errors:
            import json
            emit_machine(json.dumps(
                error_payload("usage", f"{self.prog}: error: {message}", hint or f"run `{self.prog} -h` for the accepted options"),
                ensure_ascii=False,
                indent=2,
            ))
        self.exit(2)


def _usage_hint(message: str, command_path: str = "") -> str | None:
    """A next step for argparse errors where the message alone misleads."""
    if "invalid choice: 'accepted'" in message and "draft" in message:
        # Decision logs render the approved state as "Accepted", so people type it.
        return "decision logs display approved decisions as \"Accepted\"; create a draft, complete it, then use `whykit review approve`"
    if message.startswith("unrecognized arguments") and command_path:
        # argparse reports these from the top-level parser, whose usage line
        # lists no subcommand options at all.
        return f"`whykit {command_path} -h` lists the options of `whykit {command_path}`"
    return None


class _TopParser(_Parser):
    """The ``whykit`` parser: help lists commands grouped by job, and a
    leading ``--root DIR`` is handed to the command that follows it."""

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[no-untyped-def]
        raw = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(_hoist_root(raw, set(_subcommand_helps(self))), namespace)

    def format_help(self) -> str:
        if self.prog != "whykit":
            return super().format_help()
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        formatter.add_text(self.description)
        formatter.start_section("options")
        formatter.add_arguments([a for a in self._actions if a.option_strings])
        formatter.end_section()
        text = formatter.format_help().rstrip("\n") + "\n\n"
        return text + _grouped_commands(self) + "\n" + (self.epilog or "") + "\n"


def _subcommand_helps(parser: argparse.ArgumentParser) -> dict[str, str]:
    for action in parser._actions:  # argparse has no public introspection API
        if isinstance(action, argparse._SubParsersAction):
            return {str(choice.dest): str(choice.help or "") for choice in action._choices_actions}
    return {}


def _grouped_commands(parser: argparse.ArgumentParser) -> str:
    import textwrap

    helps = _subcommand_helps(parser)
    width = max(len(name) for name in helps) + 2
    columns = max(60, min(shutil.get_terminal_size().columns, 100) - 2)
    lines = ["commands:", *(f"  {line}" for line in COMMANDS_PREAMBLE.splitlines()), ""]
    for job, blurb, names in COMMAND_GROUPS:
        lines.append(f"  {job} - {blurb}")
        for name in names:
            wrapped = textwrap.wrap(helps.get(name, ""), max(20, columns - width - 4)) or [""]
            lines.append(f"    {name:<{width}}{wrapped[0]}".rstrip())
            lines.extend(f"    {'':<{width}}{rest}" for rest in wrapped[1:])
    return "\n".join(lines) + "\n"


def _command_path(parser: argparse.ArgumentParser, raw: list[str]) -> str:
    """The subcommand names in *raw*, in order (e.g. ``review list``)."""
    path: list[str] = []
    current: argparse.ArgumentParser | None = parser
    for token in raw:
        if token == "--" or current is None:
            break
        children = next(
            (a.choices for a in current._actions if isinstance(a, argparse._SubParsersAction)), None
        )
        if not children:
            break
        if token in children:
            path.append(token)
            current = children[token]
    return " ".join(path)


def _hoist_root(raw: list[str], commands: set[str]) -> list[str]:
    """Move a leading ``--root DIR`` behind the command name.

    Every vault command accepts ``--root`` after its name; ``whykit --root DIR
    lint`` used to fail with "invalid choice: 'DIR'". Only a leading
    ``--root`` directly followed by a command is moved, so the command's own
    parser still decides whether it accepts the option.
    """
    if raw and raw[0] == "--root" and len(raw) >= 3 and raw[2] in commands:
        return [raw[2], raw[0], raw[1], *raw[3:]]
    if raw and raw[0].startswith("--root=") and len(raw) >= 2 and raw[1] in commands:
        return [raw[1], raw[0], *raw[2:]]
    return raw


def _add_leaf_root(parser: argparse.ArgumentParser) -> None:
    """Accept ``--root`` after a nested action as well as before it.

    ``whykit new --root V decision ...`` was the only spelling that worked,
    and ``whykit new decision ... --root V`` -- the form every other command
    accepts -- failed with "unrecognized arguments". SUPPRESS keeps a value
    given at the parent level from being overwritten by the leaf default.
    """
    parser.add_argument("--root", default=argparse.SUPPRESS, help=ROOT_HELP)


def build_parser() -> argparse.ArgumentParser:
    parser = _TopParser(
        prog="whykit",
        description="WhyKit - a Git-native evidence and decision ledger.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"whykit {__version__}")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="do not read or update the parse cache in .whykit/cache/ (same as WHYKIT_NO_CACHE=1)",
    )
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
        help="also create the optional starter workstreams (strategy, website, automation, operations, research) and their templates",
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
    lint_cmd.add_argument("--format", choices=("text", "json", "sarif", "github"), default=None, help="output format: text (default), json, sarif (SARIF 2.1.0) or github (workflow annotations)")
    lint_cmd.set_defaults(func=cmd_lint)

    new = sub.add_parser("new", help="create a decision, evidence row or note", description="Create a record and keep the vault indexes in sync.")
    new.add_argument("--root", help=ROOT_HELP)
    new_sub = new.add_subparsers(dest="new_kind", required=True, metavar="<kind>", title="kinds")
    new_decision = new_sub.add_parser("decision", help="create a decision record and index row")
    new_decision.add_argument("title", nargs="?", help="decision title, e.g. \"Adopt usage-based pricing\" (optional with --from)")
    new_decision.add_argument("--from", dest="from_path", metavar="FILE", help="promote an existing ADR (MADR, Nygard, Y-statement or Polish headings), e.g. one staged by `adopt`; prints the mapping unless --write")
    new_decision.add_argument("--write", action="store_true", help="with --from: create the record and its decision-log row (default: dry run)")
    new_decision.add_argument("--owner", help="accountable person (default: policy defaults.owner)")
    new_decision.add_argument("--status", choices=("draft", "in_review"), default="draft", help="initial status (default: %(default)s)")
    new_decision.add_argument("--sensitivity", choices=SENSITIVITIES, help="default: policy defaults.sensitivity")
    new_decision.add_argument("--source", action="append", default=[], dest="source_ids", metavar="E-NNN", help="supporting evidence ID (repeatable)")
    new_decision.add_argument("--review-by", metavar="YYYY-MM-DD", help="date by which the decision must be re-checked")
    new_decision.add_argument("--supersedes", metavar="D-NNN", help="decision this one replaces")
    new_decision.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(new_decision)
    new_decision.set_defaults(func=cmd_new)

    new_evidence = new_sub.add_parser("evidence", help="append a source to the evidence register")
    new_evidence.add_argument("--source", required=True, dest="source_name", help="who or what produced the evidence")
    new_evidence.add_argument("--location", required=True, help="URL or path where the source can be checked")
    new_evidence.add_argument("--type", required=True, dest="evidence_type", help="kind of source, e.g. interview, analytics, report")
    new_evidence.add_argument("--claims", required=True, help="what the source supports, in one line")
    new_evidence.add_argument("--date", metavar="YYYY-MM-DD", help="when the source was produced (default: today)")
    new_evidence.add_argument("--accessed", metavar="YYYY-MM-DD", help="when it was last checked (default: today)")
    new_evidence.add_argument("--sensitivity", choices=SENSITIVITIES, help="optional row label; cannot lower the register floor")
    new_evidence.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(new_evidence)
    new_evidence.set_defaults(func=cmd_new)

    new_note = new_sub.add_parser("note", help="create a draft note in a workstream")
    new_note.add_argument("title", help="note title")
    new_note.add_argument("--workstream", required=True, help="existing workstream directory, e.g. notes")
    new_note.add_argument("--owner", help="default: policy defaults.owner")
    new_note.add_argument("--type", choices=("strategy", "research", "framework", "specification", "guide", "reference"), default="guide", dest="doc_type", help="document type (default: %(default)s)")
    new_note.add_argument("--sensitivity", choices=SENSITIVITIES, help="default: policy defaults.sensitivity")
    new_note.add_argument("--link-from", metavar="PATH", help="vault-relative Markdown map to link the new note from")
    new_note.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(new_note)
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
    query.add_argument("--ranking", choices=("substring", "bm25"), default="substring", help="opt-in text ranking (default: %(default)s)")
    query.add_argument("--json", action="store_true", help=JSON_HELP)
    query.set_defaults(func=cmd_query)

    retrieve = sub.add_parser("retrieve", help="combine lexical matches and typed dependencies within a current visible scope")
    retrieve.add_argument("text", help="text query; graph proximity does not establish semantic support")
    retrieve.add_argument("--root", help=ROOT_HELP)
    retrieve.add_argument("--valid-on", required=True, help="evaluate declared validity on this YYYY-MM-DD date")
    retrieve.add_argument("--validity", choices=("active_or_unknown", "active"), default="active_or_unknown", help="admit explicit unknown validity or require active intervals (default: %(default)s)")
    retrieve.add_argument("--decision-role", choices=("premise", "reference"), default="premise", help="require applicable decisions or explicitly retrieve references (default: %(default)s)")
    retrieve.add_argument("--type", dest="doc_type", help="filter by document type before ranking and graph construction")
    retrieve.add_argument("--status", help="filter by status")
    retrieve.add_argument("--owner", help="filter by owner")
    retrieve.add_argument("--sensitivity", choices=SENSITIVITIES, help="filter to one classification within the ceiling")
    retrieve.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
    retrieve.add_argument("--source", dest="source_id", metavar="E-NNN", help="only records citing this evidence ID")
    retrieve.add_argument("--tag", help="filter by tag")
    retrieve.add_argument("--canonical-only", action="store_true", help="only canonical documents")
    retrieve.add_argument("--limit", type=int, default=20, help="maximum results, 0–100 (default: %(default)s)")
    retrieve.add_argument("--json", action="store_true", help=JSON_HELP)
    retrieve.set_defaults(func=cmd_retrieve, json=True)

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

    compile_cmd = sub.add_parser("compile-context", help="compile current context within an exact UTF-8 output budget")
    compile_cmd.add_argument("targets", nargs="*", help="vault-relative paths, D-NNN or E-NNN")
    compile_cmd.add_argument("--root", help=ROOT_HELP)
    compile_cmd.add_argument("--query", help="add records matching this text query")
    compile_cmd.add_argument("--ranking", choices=("substring", "bm25", "hybrid"), default="substring", help="query ranking (default: %(default)s)")
    compile_cmd.add_argument("--max-docs", type=int, default=8, help="maximum records (default: %(default)s)")
    compile_cmd.add_argument("--max-bytes", type=int, default=32768, help="complete UTF-8 JSON output budget, including newline (default: %(default)s)")
    compile_cmd.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
    compile_cmd.add_argument("--today", help=TODAY_HELP)
    compile_cmd.add_argument("--for", dest="agent", choices=("generic", "cursor", "claude", "codex"), default="generic", help="agent host preamble (default: %(default)s)")
    compile_cmd.add_argument("--json", action="store_true", help=JSON_HELP)
    compile_cmd.set_defaults(func=cmd_compile_context, json=True)

    verify_context = sub.add_parser("verify-context", help="refuse stale, altered or differently scoped compiled context")
    verify_context.add_argument("report", type=Path, help="local compiled-context JSON file")
    verify_context.add_argument("--root", help=ROOT_HELP)
    verify_context.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="expected local output classification ceiling (default: %(default)s)")
    verify_context.add_argument("--today", help=TODAY_HELP)
    verify_context.add_argument("--json", action="store_true", help=JSON_HELP)
    verify_context.set_defaults(func=cmd_verify_context, json=True)

    deep = sub.add_parser("deep-impact", help="explain typed reverse dependency paths with bounded traversal")
    deep.add_argument("target", help="vault-relative path, D-NNN or E-NNN")
    deep.add_argument("--root", help=ROOT_HELP)
    deep.add_argument("--max-depth", type=int, default=8, help="maximum reverse dependency distance (default: %(default)s)")
    deep.add_argument("--max-nodes", type=int, default=1000, help="maximum visited nodes (default: %(default)s)")
    deep.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
    deep.add_argument("--json", action="store_true", help=JSON_HELP)
    deep.set_defaults(func=cmd_deep_impact, json=True)

    knowledge = sub.add_parser("knowledge-at", help="read a pinned Git snapshot with explicit validity intervals")
    knowledge.add_argument("--root", help=ROOT_HELP)
    knowledge.add_argument("--recorded-revision", default="HEAD", help="Git revision to resolve and pin (default: %(default)s)")
    knowledge.add_argument("--valid-on", required=True, help="evaluate explicit validity intervals on this YYYY-MM-DD date")
    knowledge.add_argument("--target", help="optional vault-relative path, D-NNN or E-NNN")
    knowledge.add_argument("--max-nodes", type=int, default=1000, help="maximum returned nodes (default: %(default)s)")
    knowledge.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
    knowledge.add_argument("--json", action="store_true", help=JSON_HELP)
    knowledge.set_defaults(func=cmd_knowledge_at, json=True)

    agent = sub.add_parser("agent", help="evaluate supplied answers or inspect proposal dependencies")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_eval = agent_sub.add_parser("eval", help="score supplied JSONL answers offline")
    agent_eval.add_argument("--cases", required=True, type=Path, help="local benchmark case JSONL file")
    agent_eval.add_argument("--responses", required=True, type=Path, help="local supplied-answer JSONL file; no model calls")
    agent_eval.add_argument("--min-pass-rate", type=float, default=1.0, help="minimum case pass fraction from 0 to 1 (default: %(default)s)")
    agent_eval.add_argument("--json", action="store_true", help=JSON_HELP)
    agent_eval.set_defaults(func=cmd_agent, json=True)
    agent_preflight = agent_sub.add_parser("preflight", help="inspect declared dependencies without authorizing execution")
    agent_preflight.add_argument("--root", help=ROOT_HELP)
    agent_preflight.add_argument("--proposal", required=True, type=Path, help="local JSON proposal with declared decision IDs")
    agent_preflight.add_argument("--context", type=Path, help="require current compiled context covering every declared decision")
    agent_preflight.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="expected compiled-context classification ceiling (default: %(default)s)")
    agent_preflight.add_argument("--today", help=TODAY_HELP)
    agent_preflight.add_argument("--json", action="store_true", help=JSON_HELP)
    agent_preflight.set_defaults(func=cmd_agent, json=True)

    workspace = sub.add_parser("workspace", help="inspect explicitly listed local vaults")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_status = workspace_sub.add_parser("status", help="report local-only counts across vaults")
    workspace_status.add_argument("manifest", type=Path, help="local TOML manifest listing vault aliases and roots")
    workspace_status.add_argument("--today", help=TODAY_HELP)
    workspace_status.add_argument("--json", action="store_true", help=JSON_HELP)
    workspace_status.set_defaults(func=cmd_workspace, json=True)

    imports = sub.add_parser("import", help="capture saved provider exports without fetching or granting authority")
    import_sub = imports.add_subparsers(dest="import_command", required=True)
    github = import_sub.add_parser("github", help="preview a saved issue, pull request or workflow-run REST object")
    github.add_argument("--root", help=ROOT_HELP)
    github.add_argument("--input", required=True, type=Path, help="one bounded local GitHub REST JSON object; never fetched")
    github.add_argument("--evidence", required=True, help="existing restricted E-NNN with matching canonical resource location")
    github.add_argument("--kind", required=True, choices=("issue", "pull-request", "workflow-run"), help="explicit REST object profile; no shape guessing")
    github.add_argument("--repository", required=True, help="expected owner/repo; declared binding, not authenticated identity")
    github.add_argument("--recorded-at", required=True, help="declared local observation time, ISO UTC with seconds")
    github.add_argument("--write", action="store_true", help="apply only the import identified by --expect-hash")
    github.add_argument("--expect-hash", help="expected_sha256 from a reviewed import preview")
    github.add_argument("--json", action="store_true", help=JSON_HELP)
    github.set_defaults(func=cmd_import, json=True)

    outcome = sub.add_parser("outcome", help="record declared decision measurements without causal claims")
    outcome_sub = outcome.add_subparsers(dest="outcome_command", required=True)
    outcome_create = outcome_sub.add_parser("create", help="preview a restricted draft outcome, then apply its reviewed hash")
    outcome_create.add_argument("--root", help=ROOT_HELP)
    outcome_create.add_argument("--input", required=True, type=Path, help="local JSON metric, samples, decision and implementation references")
    outcome_create.add_argument("--owner", required=True, help="person responsible for the declaration; attribution only")
    outcome_create.add_argument("--title", help="note title (default: metric name)")
    outcome_create.add_argument("--today", help=TODAY_HELP)
    outcome_create.add_argument("--write", action="store_true", help="apply only the outcome identified by --expect-hash")
    outcome_create.add_argument("--expect-hash", help="expected_sha256 from a reviewed outcome preview")
    outcome_create.add_argument("--json", action="store_true", help=JSON_HELP)
    outcome_create.set_defaults(func=cmd_outcome, json=True)
    for name in ("list", "show", "verify"):
        child = outcome_sub.add_parser(name, help=f"{name} outcomes within an explicit local sensitivity ceiling")
        if name != "list":
            child.add_argument("target", metavar="O-NNN", help="canonical outcome ID")
        child.add_argument("--root", help=ROOT_HELP)
        child.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
        if name == "verify":
            child.add_argument("--today", help=TODAY_HELP)
        child.add_argument("--json", action="store_true", help=JSON_HELP)
        child.set_defaults(func=cmd_outcome, json=True)

    knowledge = sub.add_parser("knowledge", help="view the v2 contract or migrate exact source bytes")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    view = knowledge_sub.add_parser("view", help="project the five knowledge kinds within the permitted scope")
    view.add_argument("--root", help=ROOT_HELP)
    view.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
    view.add_argument("--json", action="store_true", help=JSON_HELP)
    view.set_defaults(func=cmd_knowledge, json=True)
    bundle = knowledge_sub.add_parser("bundle", help="preview or export a full private migration bundle outside its vault")
    bundle.add_argument("--root", help=ROOT_HELP)
    bundle.add_argument("--private", action="store_true", help="explicitly include all local classifications and raw source bytes")
    bundle.add_argument("--output", required=True, type=Path, help="new local bundle file outside the source vault")
    bundle.add_argument("--write", action="store_true", help="create only the reviewed bundle identified by --expect-hash")
    bundle.add_argument("--expect-hash", help="expected_sha256 from a reviewed export preview")
    bundle.add_argument("--json", action="store_true", help=JSON_HELP)
    bundle.set_defaults(func=cmd_knowledge, json=True)
    restore = knowledge_sub.add_parser("restore", help="preview or restore exact data into a new destination; no Git history transfer")
    restore.add_argument("bundle", type=Path, help="local v2 migration bundle JSON file")
    restore.add_argument("--into", dest="destination", required=True, type=Path, help="new destination directory that must not exist")
    restore.add_argument("--write", action="store_true", help="create only the destination identified by --expect-hash")
    restore.add_argument("--expect-hash", help="expected_sha256 from a reviewed restore preview")
    restore.add_argument("--json", action="store_true", help=JSON_HELP)
    restore.set_defaults(func=cmd_knowledge, json=True)

    artifact = sub.add_parser("artifact", help="capture immutable local evidence versions")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    capture = artifact_sub.add_parser("capture", help="preview a local source capture, then apply its reviewed hash")
    capture.add_argument("--root", help=ROOT_HELP)
    capture.add_argument("--evidence", required=True, metavar="E-NNN", help="existing evidence register ID")
    capture.add_argument("--source-file", required=True, type=Path, help="local regular file to capture; URLs are never fetched")
    capture.add_argument("--recorded-at", required=True, help="UTC acquisition timestamp, e.g. 2026-10-27T12:00:00Z")
    capture.add_argument("--write", action="store_true", help="apply only the capture identified by --expect-hash")
    capture.add_argument("--expect-hash", help="expected_sha256 from a reviewed capture preview")
    capture.add_argument("--json", action="store_true", help=JSON_HELP)
    capture.set_defaults(func=cmd_artifact, json=True)

    claim = sub.add_parser("claim", help="record and verify versioned, source-linked statements")
    claim_sub = claim.add_subparsers(dest="claim_command", required=True)
    claim_create = claim_sub.add_parser("create", help="preview a draft Markdown claim, then apply its reviewed hash")
    claim_create.add_argument("statement", help="exact statement to record as a draft claim")
    claim_create.add_argument("--root", help=ROOT_HELP)
    claim_create.add_argument("--title", help="note title (default: first 80 characters of the statement)")
    claim_create.add_argument("--owner", required=True, help="person responsible for the claim; attribution only")
    claim_create.add_argument("--links", type=Path, help="JSON array of exact source anchors and declared assessments")
    claim_create.add_argument("--sensitivity", choices=SENSITIVITIES, default="restricted", help="requested label; claim quotes retain a restricted floor (default: %(default)s)")
    claim_create.add_argument("--valid-from", help="asserted inclusive validity start as YYYY-MM-DD")
    claim_create.add_argument("--valid-until", help="asserted exclusive validity end as YYYY-MM-DD")
    claim_create.add_argument("--today", help=TODAY_HELP)
    claim_create.add_argument("--write", action="store_true", help="apply only the claim identified by --expect-hash")
    claim_create.add_argument("--expect-hash", help="expected_sha256 from a reviewed claim preview")
    claim_create.add_argument("--json", action="store_true", help=JSON_HELP)
    claim_create.set_defaults(func=cmd_claim, json=True)
    for name in ("list", "show", "verify"):
        child = claim_sub.add_parser(name, help=f"{name} claims within an explicit local sensitivity ceiling")
        if name != "list":
            child.add_argument("target", metavar="C-NNN", help="canonical claim ID")
        child.add_argument("--root", help=ROOT_HELP)
        child.add_argument("--max-sensitivity", choices=SENSITIVITIES, default="public", help="local output classification ceiling (default: %(default)s)")
        if name == "verify":
            child.add_argument("--today", help="evaluate source observations through this UTC YYYY-MM-DD day (default: today)")
        child.add_argument("--json", action="store_true", help=JSON_HELP)
        child.set_defaults(func=cmd_claim, json=True)

    review = sub.add_parser("review", help="list review work or record a review event")
    review.add_argument("--root", help=ROOT_HELP)
    review_sub = review.add_subparsers(dest="review_command", required=True, metavar="<action>", title="actions")
    review_list = review_sub.add_parser("list", help="show upcoming/overdue reviews")
    review_list.add_argument("--today", help=TODAY_HELP)
    review_list.add_argument("--due-days", type=int, metavar="N", help="review window in days (default: policy)")
    review_list.add_argument("--owner", help="only reviews owned by this person")
    review_list.add_argument("--overdue-only", action="store_true", help="hide reviews that are not yet due")
    review_list.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(review_list)
    review_list.set_defaults(func=cmd_review)
    review_record = review_sub.add_parser("record", help="record a review event")
    review_record.add_argument("target", help="vault-relative path or D-NNN")
    review_record.add_argument("--reviewer", required=True, help="who performed the review")
    review_record.add_argument("--outcome", choices=("confirmed", "update-required", "supersede-required", "archived"), default="confirmed", help="review result (default: %(default)s)")
    review_record.add_argument("--next-review", metavar="YYYY-MM-DD", help="next review date")
    review_record.add_argument("--note", default="", dest="note_text", help="short note for the review log")
    review_record.add_argument("--today", help="record the review as of this YYYY-MM-DD date")
    review_record.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(review_record)
    review_record.set_defaults(func=cmd_review)

    review_approve = review_sub.add_parser("approve", help="preview approval, then apply the reviewed snapshot")
    review_approve.add_argument("target", help="vault-relative decision path or D-NNN")
    review_approve.add_argument("--reviewer", required=True, help="who reviewed the snapshot; an attribution, not authentication")
    review_approve.add_argument("--next-review", metavar="YYYY-MM-DD", help="default: policy decision_review_days after approval")
    review_approve.add_argument("--today", help="approval date as YYYY-MM-DD")
    review_approve.add_argument("--write", action="store_true", help="apply only the snapshot identified by --expect-hash")
    review_approve.add_argument("--expect-hash", dest="expected_sha256", help="expected_sha256 from a reviewed preview")
    review_approve.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(review_approve)
    review_approve.set_defaults(func=cmd_review)

    snapshot = sub.add_parser("snapshot", help="create a deterministic vault snapshot")
    snapshot.add_argument("--root", help=ROOT_HELP)
    snapshot.add_argument("--today", help=TODAY_HELP)
    snapshot.add_argument("--output", metavar="PATH", help="write the JSON snapshot here instead of stdout")
    snapshot.add_argument("--compact", action="store_true", help="emit compact JSON")
    snapshot.add_argument(
        "--format", dest="snapshot_format", choices=("v1", "v2"), default=None,
        help="snapshot format: v2 (default) hashes text with CRLF line endings and a UTF-8 BOM "
        "normalized, so checkouts on different platforms match; v1 hashes raw bytes",
    )
    snapshot.add_argument("--json", action="store_true", help="accepted for symmetry; stdout is always JSON unless --output is given")
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
    check.add_argument("--format", choices=("text", "json", "github"), default=None, help="output format: text (default), json or github (workflow annotations)")
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
    _add_leaf_root(evidence_list)
    evidence_list.set_defaults(func=cmd_evidence)
    evidence_retire = evidence_sub.add_parser("retire", help="retire active evidence")
    evidence_retire.add_argument("id", metavar="E-NNN", help="evidence ID to retire")
    evidence_retire.add_argument("--why", required=True, dest="reason", help="why the evidence no longer holds")
    evidence_retire.add_argument("--replaced-by", metavar="E-NNN", help="evidence that supersedes it")
    evidence_retire.add_argument("--today", help="retire as of this YYYY-MM-DD date")
    evidence_retire.add_argument("--json", action="store_true", help=JSON_HELP)
    _add_leaf_root(evidence_retire)
    evidence_retire.set_defaults(func=cmd_evidence)

    adopt = sub.add_parser("adopt", help="inventory existing Markdown and stage it for a vault")
    adopt.add_argument("source", help="directory of existing Markdown to inventory")
    adopt.add_argument("--into", "--root", dest="into", metavar="VAULT", help="vault to stage into (default: nearest vault); --root is the same option, spelled as on every other command")
    adopt.add_argument("--owner", default="TODO", help="owner for adopted notes (default: %(default)s)")
    adopt.add_argument("--profile", choices=("generic", "adr-only", "obsidian-loose"), default="generic", help="how to interpret the source (default: %(default)s)")
    adopt.add_argument("--write", action="store_true", help="write the staged files (default: dry run)")
    adopt.add_argument("--json", action="store_true", help=JSON_HELP)
    adopt.add_argument("--compare", metavar="DIR", help="read-only preservation check at matching relative paths; cannot combine with --write")
    adopt.set_defaults(func=cmd_adopt)

    history = sub.add_parser("history", help="verify that accepted decisions were not rewritten")
    history.add_argument("--base", metavar="REF", help="base commit or ref, e.g. origin/main (required unless --staged, which defaults to HEAD)")
    history.add_argument("--head", default=None, metavar="REF", help="head commit or ref (default: HEAD)")
    history.add_argument("--staged", action="store_true", help="check the staged changes against --base instead of a head commit (for pre-commit hooks)")
    history.add_argument("--root", help=ROOT_HELP)
    history.add_argument("--json", action="store_true", help=JSON_HELP)
    history.set_defaults(func=cmd_history)

    diff = sub.add_parser(
        "diff",
        help="show what changed in decisions, evidence and lint findings between two commits",
        description="Compare the vault at the merge base of --base and --head with --head, read from Git objects without a checkout.",
    )
    diff.add_argument("--base", required=True, metavar="REF", help="base commit or ref, e.g. origin/main")
    diff.add_argument("--head", default="HEAD", metavar="REF", help="head commit or ref (default: %(default)s)")
    diff.add_argument("--root", help=ROOT_HELP)
    diff.add_argument("--today", help=TODAY_HELP)
    diff.add_argument(
        "--format", choices=("text", "json", "markdown", "github"), default=None,
        help="output format: text (default), json, markdown (a pull request comment) or github (workflow annotations)",
    )
    diff.add_argument("--json", action="store_true", help=JSON_HELP)
    diff.set_defaults(func=cmd_diff)

    rules = sub.add_parser("rules", help="list lint rules or explain one rule code")
    rules.add_argument("code", nargs="?", help="rule code to explain, e.g. evidence.unknown_id")
    rules.add_argument("--json", action="store_true", help=JSON_HELP)
    rules.add_argument("--markdown", action="store_true", help="emit the complete Markdown table")
    rules.add_argument("--root", help="vault whose whykit.toml custom rules and overrides are listed too (default: nearest vault, if any)")
    rules.set_defaults(func=cmd_rules)

    doctor = sub.add_parser("doctor", help="check prerequisites and vault integrity")
    doctor.add_argument("--root", help=ROOT_HELP)
    doctor.add_argument("--today", help=TODAY_HELP)
    doctor.add_argument("--json", action="store_true", help=JSON_HELP)
    doctor.set_defaults(func=cmd_doctor)

    hooks = sub.add_parser("install-hooks", help="install the pre-commit vault check")
    hooks.add_argument("--root", help=ROOT_HELP)
    hooks.add_argument("--force", action="store_true", help="replace an existing pre-commit hook")
    hooks.set_defaults(func=cmd_install_hooks)

    recover = sub.add_parser("recover", help="recover interrupted writes and collect completed transaction journals")
    recover.add_argument("--root", help=ROOT_HELP)
    recover.add_argument("--json", action="store_true", help=JSON_HELP)
    recover.set_defaults(func=cmd_recover)

    explorer_index = sub.add_parser(
        "explorer-index",
        help="export the Explorer vault index from the canonical Python parser",
    )
    explorer_index.add_argument("--root", help=ROOT_HELP)
    explorer_index.add_argument("--today", help=TODAY_HELP)
    explorer_index.add_argument("--json", action="store_true", default=True, help="accepted for symmetry; output is always JSON")
    explorer_index.add_argument("--private", action="store_true", help="full local index; never publish this output")
    explorer_index.add_argument("--publication-preview", action="store_true", help="private withheld-path diagnostics; never publish this report")
    explorer_index.set_defaults(func=cmd_explorer_index)

    serve = sub.add_parser("serve", help="run the optional Explorer (source checkout only)")
    serve.add_argument("vault", nargs="?", default=None, help="vault to serve (default: current directory)")
    serve.add_argument("--root", help="vault to serve, as on every other command (same as the positional vault)")
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind (default: %(default)s)")
    serve.add_argument("--port", type=int, default=5173, help="port to listen on (default: %(default)s)")
    serve.add_argument("--allow-sensitive-network", action="store_true", help="allow non-loopback serving even when non-public docs are present")
    serve.set_defaults(func=cmd_serve)

    lsp = sub.add_parser(
        "lsp",
        help="run the read-only language server for editors (stdio)",
        description="Run the read-only WhyKit language server over stdio: lint diagnostics, "
        "wikilink and ID completion, hover, go-to-definition and document links. It never writes a file.",
    )
    lsp.add_argument("--root", help="serve only this vault (default: the vault around each open file)")
    lsp.add_argument("--debounce", type=int, default=300, metavar="MS", help="milliseconds to wait after an edit before linting (default: %(default)s)")
    lsp.add_argument("--today", help=TODAY_HELP)
    lsp.add_argument("--stdio", action="store_true", help="accepted for editor clients that pass it; stdio is the only transport")
    lsp.set_defaults(func=cmd_lsp)

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


def _wants_json(args: argparse.Namespace) -> bool:
    """Whether this invocation's stdout is a JSON document."""
    command = getattr(args, "command", None)
    if command == "explorer-index":
        return True
    if command == "snapshot":
        return not getattr(args, "output", None)
    if command in ("graph", "pack") and getattr(args, "format", None) in (None, "json"):
        return True
    if command in ("lint", "check", "diff") and getattr(args, "format", None) == "json":
        return True
    return bool(getattr(args, "json", False))


_WINDOWS = sys.platform == "win32"

# Commands that only read the vault.  They reuse and refresh the parse cache;
# commands that write the vault, or read other directories (`diff` exports
# commits into a temporary directory), never touch it.
_CACHED_COMMANDS = frozenset({
    "lint", "check", "status", "snapshot", "verify-snapshot", "query", "context", "pack",
    "graph", "backlinks", "impact", "trace", "explorer-index", "doctor",
})


def _uses_parse_cache(args: argparse.Namespace) -> bool:
    if getattr(args, "no_cache", False):
        return False
    command = getattr(args, "command", None)
    if command == "review":
        return getattr(args, "review_command", None) == "list"
    return command in _CACHED_COMMANDS


def _reader_gone(exc: OSError) -> bool:
    """Whether *exc* means the reader of stdout went away (`| head -1`).

    POSIX raises BrokenPipeError (EPIPE). Windows reports a write to a pipe
    whose reader closed as EINVAL; that errno never carries a file name there,
    which keeps a real filesystem refusal on the io_error path.
    """
    if isinstance(exc, BrokenPipeError) or exc.errno == errno.EPIPE:
        return True
    return _WINDOWS and exc.errno == errno.EINVAL and exc.filename is None


def _silence_stdout() -> None:
    """Point stdout at devnull so the interpreter's final flush stays quiet."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _run_command(args: argparse.Namespace, func) -> int:
    """Bind config, data and rendered output to one read; discard raced output."""
    from contextlib import redirect_stdout
    import tempfile
    from .io import vault_read_lock, vault_mutation_lock

    read_only = args.command in _CACHED_COMMANDS | {"policy", "rules", "history"}
    read_only = read_only or (args.command == "evidence" and getattr(args, "evidence_command", None) == "list")
    read_only = read_only or (args.command == "review" and args.review_command == "list")
    read_only = read_only or (args.command == "agent" and args.agent_command == "preflight")
    read_only = read_only or args.command in {"deep-impact", "verify-context"}
    read_only = read_only or (args.command == "claim" and args.claim_command != "create")
    root = _resolve_vault(getattr(args, "root", None)) if read_only else None
    if args.command == "lint":
        from .lint import resolve_root
        resolved, _, _note = resolve_root(args.root, args.paths)
        root = resolved if is_vault_root(resolved) else None
    if root is None:
        return func(args)
    # Spill large reports to a private temporary file instead of doubling their RAM.
    scope = vault_mutation_lock(root) if args.command in {"graph", "snapshot"} and args.output else vault_read_lock(root)
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+", encoding="utf-8", errors="surrogateescape") as output:
        with redirect_stdout(output), scope:
            result = func(args)
        output.seek(0)
        stream = sys.stdout
        machine = _wants_json(args) or args.command in {"graph", "pack"} or (args.command in {"lint", "check"} and args.format in {"sarif", "github"})
        from .console import is_utf
        reconfigure = getattr(stream, "reconfigure", None)
        switch = machine and not is_utf(stream) and reconfigure is not None
        if switch:
            assert reconfigure is not None
            encoding, errors = stream.encoding, stream.errors
            stream.flush()
            reconfigure(encoding="utf-8", errors="surrogateescape")
        try:
            shutil.copyfileobj(output, stream)
        finally:
            if switch:
                assert reconfigure is not None
                stream.flush()
                reconfigure(encoding=encoding, errors=errors)
        return result

def main(argv: list[str] | None = None) -> int:
    # Before any output: an ASCII or legacy code-page console must degrade
    # glyphs, not abort the report with UnicodeEncodeError.
    harden_stdio()
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    raw = _hoist_root(raw, set(_subcommand_helps(parser)))
    _Parser.json_errors = argv_wants_json(raw)
    _Parser.command_path = _command_path(parser, raw)
    try:
        args = parser.parse_args(raw)
    finally:
        _Parser.json_errors = False
        _Parser.command_path = ""
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        from .parse_cache import persistent

        with persistent(_uses_parse_cache(args)):
            code = _run_command(args, func)
        # Flush here, not at interpreter exit: a short report sits in the buffer
        # until then, and a reader that already left (`| head -1`) would turn
        # into an "Exception ignored" message and exit status 120.
        sys.stdout.flush()
        return code
    except KeyboardInterrupt:
        return emit_error("interrupted", "interrupted", json_mode=_wants_json(args))
    except OSError as exc:
        if _reader_gone(exc):
            # The reader went away (`whykit query | head`): exit 1 quietly,
            # without a second traceback from the final flush.
            _silence_stdout()
            return 1
        # A filesystem refusal is a user-facing condition, not a crash.
        # WHYKIT_DEBUG=1 restores the traceback for bug reports.
        if os.environ.get("WHYKIT_DEBUG"):
            raise
        return emit_error(
            "io_error",
            f"cannot complete `whykit {args.command}`: {describe_os_error(exc)}\n"
            "hint: check the path and its permissions; set WHYKIT_DEBUG=1 for a traceback",
            json_mode=_wants_json(args),
        )
    except Exception as exc:  # noqa: BLE001 - the last line of defence for the contract
        # A bug in WhyKit. Keep stdout one JSON document under --json, and keep
        # the traceback (which can quote vault paths and content) off by default.
        if os.environ.get("WHYKIT_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        detail = str(exc).strip().splitlines()
        reason = f"{type(exc).__name__}: {detail[0]}" if detail else type(exc).__name__
        return emit_error(
            "internal_error",
            f"internal error in `whykit {args.command}`: {reason}\n"
            f"hint: this is a bug in WhyKit; please report it at {ISSUES_URL} "
            "with the command you ran and the output of the same command under WHYKIT_DEBUG=1",
            json_mode=_wants_json(args),
        )


if __name__ == "__main__":
    raise SystemExit(main())
