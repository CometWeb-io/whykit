#!/usr/bin/env python3
"""Rehearse a WhyKit release locally, without releasing anything.

    python3 scripts/release_rehearsal.py
    python3 scripts/release_rehearsal.py --commit <sha> --work-dir /tmp/rehearsal
    python3 scripts/release_rehearsal.py --skip-build        # version, notes, commands only

Runs, in order, against the exact commit you name (default ``HEAD``):

1. the working tree is clean and checked out at that commit, and no tag for the
   candidate version exists yet;
2. the next version is computed from the ``[Unreleased]`` section of
   ``CHANGELOG.md`` (or from the newest dated section once the release pull
   request has moved the entries) and compared with ``__version__``;
3. the wheel and sdist are built twice with ``SOURCE_DATE_EPOCH`` from the
   commit, then checked with ``scripts/check_dist.py`` (including byte-identical
   rebuilds) and ``twine check --strict``;
4. the wheel is installed into a fresh virtual environment for every supported
   Python found on this machine and smoke-tested: ``init``, ``lint``, a strict
   ``lint`` and ``trace`` of the Northline example, and an MCP client that lists
   the server's tools; the sdist is installed once and smoke-tested too;
5. release notes are written from the changelog;
6. the exact commands the maintainer runs to tag and push are printed.

It never creates a tag, pushes, uploads or dispatches a workflow: the only Git
subcommands it can run are read-only ones, enforced in ``_git``. ``uv`` runs
with ``UV_OFFLINE=1`` unless ``--online`` is given, so a rehearsal also proves
the build needs nothing from the network once the uv cache is warm.

Standard library only. Exit 0 when every step passed (a step may be PENDING,
which means the release pull request still has work to do), 1 when a step
failed, 2 when the rehearsal could not run at all.
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "whykit"
REPOSITORY = "CometWeb-io/whykit"
NORTHLINE_TODAY = "2026-09-17"
EXPECTED_MCP_TOOLS = {"query", "context", "impact", "status", "pack", "trace", "backlinks"}
# Subsections a changelog release section may hold, in the order they are
# written. Anything else is a typo that would end up in the release notes.
SECTION_ORDER = (
    "Highlights", "Breaking changes", "Added", "Changed", "Deprecated",
    "Removed", "Fixed", "Security", "Performance",
)
# Read-only Git subcommands. Tagging, pushing and anything that writes refs or
# the working tree are deliberately absent; see _git().
READ_ONLY_GIT = frozenset({"rev-parse", "status", "log", "merge-base", "cat-file"})

VERSION_LINE = re.compile(r'(?m)^__version__ = "([^"]+)"$')
FINAL_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEV_OF = re.compile(r"^(\d+\.\d+\.\d+)\.dev\d+$")
RELEASE_HEADING = re.compile(r"^## \[([^\]]+)\](?:\s+[—-]\s+(.*))?\s*$")
SUBSECTION = re.compile(r"(?m)^### (.+?)\s*$")
BULLET = re.compile(r"(?m)^- ")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUPPORTED_PYTHON = re.compile(r'"Programming Language :: Python :: (3\.\d+)"')


class RehearsalError(Exception):
    """The rehearsal cannot run at all (exit 2)."""


# --------------------------------------------------------------------------
# Changelog and version arithmetic (pure, unit-tested)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Section:
    title: str          # "Unreleased" or a version such as "0.2.0"
    suffix: str         # what follows the dash: a date or a note
    body: str           # text below the heading, without the link footer

    @property
    def subsections(self) -> dict[str, str]:
        parts = SUBSECTION.split(self.body)
        return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}

    @property
    def is_empty(self) -> bool:
        return not BULLET.search(self.body)


@dataclasses.dataclass(frozen=True)
class Candidate:
    version: str
    reason: str
    section: Section     # the section whose entries become the release notes
    previous: str | None


def parse_changelog(text: str) -> list[Section]:
    """Split a Keep-a-Changelog file into its ``## [...]`` sections, in order."""
    sections: list[Section] = []
    title: str | None = None
    suffix = ""
    lines: list[str] = []

    def flush() -> None:
        if title is not None:
            body = "\n".join(line for line in lines if not re.match(r"^\[[^\]]+\]:\s", line))
            sections.append(Section(title, suffix, body.strip("\n")))

    for line in text.splitlines():
        match = RELEASE_HEADING.match(line)
        if match:
            flush()
            title, suffix, lines = match.group(1), (match.group(2) or "").strip(), []
        elif title is not None:
            lines.append(line)
    flush()
    return sections


def _final(version: str) -> tuple[int, int, int] | None:
    match = FINAL_VERSION.match(version)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def change_kind(section: Section) -> str:
    """``major``-worthy, ``minor`` or ``patch``, read from the section's content."""
    subsections = section.subsections
    breaking = subsections.get("Breaking changes", "")
    if BULLET.search(breaking) or "**BREAKING" in section.body:
        return "breaking"
    if any(BULLET.search(subsections.get(name, "")) for name in ("Added", "Deprecated", "Removed")):
        return "minor"
    return "patch"


def bump(previous: str, kind: str) -> str:
    parts = _final(previous)
    if parts is None:
        raise ValueError(f"{previous!r} is not a final X.Y.Z version")
    major, minor, patch = parts
    if kind == "breaking":
        # Before 1.0 a breaking change bumps the minor version (SemVer item 4).
        return f"{major + 1}.0.0" if major >= 1 else f"{major}.{minor + 1}.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def next_version(sections: list[Section], existing_tags: frozenset[str] | set[str] = frozenset()) -> Candidate:
    """The version the next tag must carry, and why.

    While ``[Unreleased]`` has entries, bump the newest final version listed in
    the changelog by what those entries contain. After the release pull request
    has moved them under a dated heading, the candidate is that heading, as long
    as it has not been tagged yet.
    """
    unreleased = next((s for s in sections if s.title == "Unreleased"), None)
    released = [s for s in sections if _final(s.title) is not None]
    if unreleased is not None and not unreleased.is_empty:
        if not released:
            raise RehearsalError("CHANGELOG.md has no earlier X.Y.Z section to bump from")
        previous = max(released, key=lambda s: _final(s.title) or (0, 0, 0)).title
        kind = change_kind(unreleased)
        version = bump(previous, kind)
        reason = {
            "breaking": "[Unreleased] lists breaking changes",
            "minor": "[Unreleased] adds, deprecates or removes features",
            "patch": "[Unreleased] has only changes, fixes, security or performance entries",
        }[kind]
        return Candidate(version, f"{reason}; bumped from {previous}", unreleased, previous)
    if not released:
        raise RehearsalError("CHANGELOG.md has nothing to release: [Unreleased] is empty and no X.Y.Z section exists")
    newest = released[0]
    if f"v{newest.title}" in existing_tags:
        raise RehearsalError(
            f"[Unreleased] is empty and the newest section, {newest.title}, is already tagged v{newest.title}"
        )
    older = [s.title for s in released[1:]]
    return Candidate(newest.title, f"the newest changelog section, {newest.title}, is not tagged yet", newest, older[0] if older else None)


def check_sections(section: Section) -> list[str]:
    problems = []
    seen = list(section.subsections)
    for name in seen:
        if name not in SECTION_ORDER:
            problems.append(f"unknown changelog subsection '### {name}' (allowed: {', '.join(SECTION_ORDER)})")
    known = [name for name in seen if name in SECTION_ORDER]
    if known != sorted(known, key=SECTION_ORDER.index):
        problems.append(f"changelog subsections are out of order: {', '.join(known)} (expected order: {', '.join(SECTION_ORDER)})")
    if len(seen) != len(set(seen)):
        problems.append("a changelog subsection appears twice")
    return problems


def version_alignment(package_version: str, candidate: str) -> tuple[str, str]:
    """``PASS``, ``PENDING`` (a .devN of the candidate) or ``FAIL``, with a detail line."""
    if package_version == candidate:
        return "PASS", f"__version__ is {candidate}"
    dev = DEV_OF.match(package_version)
    if dev and dev.group(1) == candidate:
        return "PENDING", (
            f"__version__ is {package_version}; the release pull request sets it to {candidate}"
        )
    return "FAIL", f"__version__ is {package_version}, but the changelog calls for {candidate}"


def changelog_dating(candidate: Candidate) -> tuple[str, str, str]:
    """Whether the release pull request has given the entries their dated heading."""
    name = "changelog section is dated"
    if candidate.section.title == "Unreleased":
        return "PENDING", name, f'the release pull request moves [Unreleased] under "## [{candidate.version}] - YYYY-MM-DD"'
    if not ISO_DATE.match(candidate.section.suffix):
        return "FAIL", name, f"## [{candidate.version}] is followed by {candidate.section.suffix!r}, not a YYYY-MM-DD date"
    return "PASS", name, candidate.section.suffix


def release_notes(candidate: Candidate, existing_tags: frozenset[str] | set[str] = frozenset()) -> str:
    """Release notes from the candidate's changelog section.

    A compare link to a tag that does not exist would 404, so the notes link
    the previous version only when it was really tagged; the first release
    links its own tag instead.
    """
    version = candidate.version
    link = (
        f"https://github.com/{REPOSITORY}/compare/v{candidate.previous}...v{version}"
        if candidate.previous and f"v{candidate.previous}" in existing_tags
        else f"https://github.com/{REPOSITORY}/releases/tag/v{version}"
    )
    body = candidate.section.body.strip()
    return f"# WhyKit {version}\n\n{body}\n\nFull changelog: {link}\n"


def owner_commands(version: str, commit: str, *, pending: bool) -> str:
    tag = f"v{version}"
    steps = []
    if pending:
        steps.append(textwrap.dedent(f"""\
            # Before tagging: merge a release pull request that only sets
            # __version__ = "{version}" in src/whykit/__init__.py, updates the README
            # status line and moves [Unreleased] under "## [{version}] - YYYY-MM-DD".
            # Then rerun this rehearsal on the merged main commit: the digests
            # above belong to {commit[:12]} and will differ from the tag's."""))
        target = "<merged-main-sha>"
    else:
        target = commit
    steps.append(textwrap.dedent(f"""\
        git switch main
        git pull --ff-only
        test "$(git rev-parse HEAD)" = "{target}"
        git tag -a {tag} -m "WhyKit {version}" {target}
        git push origin {tag}"""))
    steps.append(textwrap.dedent("""\
        # Then watch the Release workflow: compare the digests in its run summary
        # with the ones above, and approve the `pypi` environment only if they match."""))
    return "\n".join(steps)


# --------------------------------------------------------------------------
# Side effects: Git (read-only), uv, subprocesses
# --------------------------------------------------------------------------


def _git_process(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if not args or args[0] not in READ_ONLY_GIT:
        raise RehearsalError(f"refusing to run 'git {' '.join(args)}': the rehearsal only reads the repository")
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)


def _git(root: Path, *args: str) -> str:
    result = _git_process(root, *args)
    if result.returncode != 0:
        raise RehearsalError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _tags(root: Path) -> set[str]:
    return {line.strip() for line in _git(root, "rev-parse", "--symbolic", "--tags").splitlines() if line.strip()}


@dataclasses.dataclass
class Step:
    status: str   # PASS, FAIL, SKIP or PENDING
    name: str
    detail: str = ""


class Rehearsal:
    def __init__(self, root: Path, work: Path, *, online: bool, echo: bool = True) -> None:
        self.root = root
        self.work = work
        self.steps: list[Step] = []
        self.echo = echo
        self.env = {key: value for key, value in os.environ.items() if key not in {"VIRTUAL_ENV", "PYTHONPATH"}}
        if not online:
            self.env["UV_OFFLINE"] = "1"

    def record(self, status: str, name: str, detail: str = "") -> Step:
        step = Step(status, name, detail)
        self.steps.append(step)
        if self.echo:
            print(f"{status:<7} {name}" + (f" — {detail}" if detail else ""), flush=True)
        return step

    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv, cwd=cwd or self.root, env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    @property
    def failed(self) -> bool:
        return any(step.status == "FAIL" for step in self.steps)

    @property
    def pending(self) -> bool:
        return any(step.status == "PENDING" for step in self.steps)


OFFLINE_MISS = "Network connectivity is disabled"


def _tail(result: subprocess.CompletedProcess[str], lines: int = 6) -> str:
    output = (result.stderr or result.stdout or "").strip()
    text = " | ".join(output.splitlines()[-lines:])
    if OFFLINE_MISS in output:
        text += " (the uv cache is cold for this step: rerun once with --online, after which offline runs work)"
    return text


def supported_pythons(root: Path) -> list[str]:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return sorted(set(SUPPORTED_PYTHON.findall(text)), key=lambda v: tuple(int(p) for p in v.split(".")))


def find_python(rehearsal: Rehearsal, version: str) -> str | None:
    # --system skips virtual environments, such as the checkout's own .venv:
    # the smoke must start from a base interpreter.
    result = rehearsal.run(["uv", "python", "find", "--system", "--no-project", version], timeout=60)
    path = result.stdout.strip()
    return path if result.returncode == 0 and path else None


def _bin(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))


MCP_CLIENT = textwrap.dedent("""\
    import asyncio, json, sys
    from mcp import Client, StdioServerParameters

    async def main():
        params = StdioServerParameters(command=sys.argv[1], args=["--root", sys.argv[2]])
        async with asyncio.timeout(30):
            async with Client(params, raise_exceptions=True, read_timeout_seconds=10) as client:
                tools = await client.list_tools()
                info = client.server_info
        print(json.dumps({"version": info.version, "tools": sorted(t.name for t in tools.tools)}))

    asyncio.run(main())
    """)


def smoke(rehearsal: Rehearsal, label: str, python: str, archive: Path, version: str, *, mcp: bool, constraints: Path | None) -> None:
    venv = rehearsal.work / f"venv-{label}"
    shutil.rmtree(venv, ignore_errors=True)
    created = rehearsal.run(["uv", "venv", "--python", python, str(venv)], timeout=120)
    if created.returncode != 0:
        rehearsal.record("FAIL", f"smoke {label}: create venv", _tail(created))
        return
    py = str(_bin(venv, "python"))
    requirement = f"{archive}[mcp]" if mcp else str(archive)
    install = ["uv", "pip", "install", "--python", py, requirement]
    if mcp and constraints is not None:
        install += ["--constraint", str(constraints)]
    installed = rehearsal.run(install, timeout=300)
    if installed.returncode != 0:
        if mcp:
            rehearsal.record("FAIL", f"smoke {label}: install wheel with the mcp extra", _tail(installed) + " (or pass --skip-mcp)")
        else:
            rehearsal.record("FAIL", f"smoke {label}: install {archive.name}", _tail(installed))
        return
    whykit = str(_bin(venv, "whykit"))
    vault = rehearsal.work / f"vault-{label}"
    shutil.rmtree(vault, ignore_errors=True)
    northline = rehearsal.root / "examples" / "northline"
    checks: list[tuple[str, list[str], str | None]] = [
        ("--version", [whykit, "--version"], version),
        ("init", [whykit, "init", str(vault)], None),
        ("lint (fresh vault)", [whykit, "lint", "--root", str(vault)], None),
        ("lint --strict (Northline)", [whykit, "lint", "--root", str(northline), "--strict", "--today", NORTHLINE_TODAY], None),
        ("trace --strict (Northline)", [whykit, "trace", "--root", str(northline), "--strict", "--today", NORTHLINE_TODAY], None),
    ]
    for name, argv, expect in checks:
        # Run from the work directory so nothing can pick up the checkout's sources.
        result = rehearsal.run(argv, cwd=rehearsal.work, timeout=300)
        if result.returncode != 0:
            rehearsal.record("FAIL", f"smoke {label}: whykit {name}", f"exit {result.returncode}: {_tail(result)}")
            return
        if expect is not None and expect not in result.stdout:
            rehearsal.record("FAIL", f"smoke {label}: whykit {name}", f"expected {expect!r}, got {result.stdout.strip()!r}")
            return
    if mcp:
        script = rehearsal.work / "mcp_list_tools.py"
        script.write_text(MCP_CLIENT, encoding="utf-8")
        result = rehearsal.run([py, str(script), str(_bin(venv, "whykit-mcp")), str(northline)], cwd=rehearsal.work, timeout=120)
        if result.returncode != 0:
            rehearsal.record("FAIL", f"smoke {label}: MCP list-tools", _tail(result))
            return
        listed = json.loads(result.stdout.strip().splitlines()[-1])
        if set(listed["tools"]) != EXPECTED_MCP_TOOLS or listed["version"] != version:
            rehearsal.record("FAIL", f"smoke {label}: MCP list-tools", f"got {listed}")
            return
    what = "init, lint, trace" + (", MCP list-tools" if mcp else "")
    rehearsal.record("PASS", f"smoke {label}", f"{what} ({python})")


def build_and_check(rehearsal: Rehearsal, version: str, epoch: str) -> dict[str, str] | None:
    rehearsal.env["SOURCE_DATE_EPOCH"] = epoch
    first, second = rehearsal.work / "dist", rehearsal.work / "dist-rebuild"
    for target in (first, second):
        shutil.rmtree(target, ignore_errors=True)
        built = rehearsal.run(["uv", "build", "--out-dir", str(target)])
        if built.returncode != 0:
            rehearsal.record("FAIL", "build sdist and wheel", _tail(built))
            return None
    rehearsal.record("PASS", "build sdist and wheel twice", f"SOURCE_DATE_EPOCH={epoch}")
    checked = rehearsal.run([
        sys.executable, str(rehearsal.root / "scripts" / "check_dist.py"), str(first),
        "--reproducible-against", str(second), "--version", version,
    ])
    if checked.returncode != 0:
        rehearsal.record("FAIL", "check_dist (contents, metadata, reproducibility)", _tail(checked, 20))
        return None
    digests: dict[str, str] = {}
    for line in checked.stdout.splitlines():
        digest, _, name = line.partition("  ")
        if name:
            digests[name] = digest
    rehearsal.record("PASS", "check_dist (contents, metadata, byte-identical rebuild)")
    archives = sorted(glob.glob(str(first / "*")))
    twine = rehearsal.run(["uv", "run", "--locked", "--only-group", "dist", "twine", "check", "--strict", *archives])
    if twine.returncode != 0:
        rehearsal.record("FAIL", "twine check --strict", _tail(twine))
        return None
    rehearsal.record("PASS", "twine check --strict")
    return digests


def mcp_constraints(rehearsal: Rehearsal) -> Path | None:
    """Pin the MCP SDK and its dependencies to uv.lock, so the smoke uses the locked set."""
    result = rehearsal.run([
        "uv", "export", "--locked", "--no-dev", "--extra", "mcp", "--no-emit-project",
        "--no-hashes", "--no-header", "--no-annotate",
    ], timeout=120)
    if result.returncode != 0:
        return None
    path = rehearsal.work / "mcp-constraints.txt"
    path.write_text(result.stdout, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def rehearse(args: argparse.Namespace, root: Path = ROOT) -> int:
    # Resolve first: on macOS a temporary directory under /var is really under
    # /private/var, and the work-dir containment check must compare like with like.
    root = root.resolve()
    if shutil.which("git") is None:
        raise RehearsalError("git is not on PATH")
    try:
        commit = _git(root, "rev-parse", "--verify", f"{args.commit}^{{commit}}").strip()
    except RehearsalError as exc:
        raise RehearsalError(f"{args.commit!r} is not a commit in {root}") from exc
    work = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="whykit-rehearsal-"))
    if work == root or root in work.parents:
        raise RehearsalError(f"--work-dir {work} is inside the repository; the build would dirty the tree it checks")
    work.mkdir(parents=True, exist_ok=True)
    rehearsal = Rehearsal(root, work, online=args.online)
    print(f"Rehearsing {NAME} at {commit} (work directory: {work})\n", flush=True)

    # 1. The tree under test is exactly the commit.
    head = _git(root, "rev-parse", "HEAD").strip()
    dirty = _git(root, "status", "--porcelain", "--untracked-files=normal").strip()
    if head != commit:
        rehearsal.record("FAIL", "checkout matches the commit", f"HEAD is {head[:12]}; run `git switch --detach {commit[:12]}` first")
    elif dirty:
        rehearsal.record("FAIL", "working tree is clean", f"{len(dirty.splitlines())} changed or untracked path(s); commit or remove them (do not stash)")
    else:
        rehearsal.record("PASS", "clean working tree at the commit", commit[:12])
    if _git_process(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main").returncode == 0:
        if _git_process(root, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main").returncode == 0:
            rehearsal.record("PASS", "commit is on origin/main", "as of the last fetch; this rehearsal does not fetch")
        else:
            rehearsal.record("PENDING", "commit is on origin/main", "not yet; the release workflow refuses a tag that is not on main")

    # 2. Version and notes from the changelog.
    tags = _tags(root)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    candidate = next_version(parse_changelog(changelog), tags)
    rehearsal.record("PASS", f"next version is {candidate.version}", candidate.reason)
    for problem in check_sections(candidate.section):
        rehearsal.record("FAIL", "changelog structure", problem)
    if f"v{candidate.version}" in tags:
        rehearsal.record("FAIL", f"tag v{candidate.version} does not exist yet", "it already exists locally; a version is never reused")
    package_version = VERSION_LINE.search((root / "src" / NAME / "__init__.py").read_text(encoding="utf-8"))
    if package_version is None:
        raise RehearsalError("cannot find __version__ in src/whykit/__init__.py")
    built_version = package_version.group(1)
    status, detail = version_alignment(built_version, candidate.version)
    rehearsal.record(status, "version alignment", detail)
    rehearsal.record(*changelog_dating(candidate))
    notes_path = work / f"release-notes-v{candidate.version}.md"
    notes_path.write_text(release_notes(candidate, tags), encoding="utf-8")
    rehearsal.record("PASS", "release notes written", str(notes_path))

    # 3 and 4. Build, check, install, smoke.
    digests: dict[str, str] | None = None
    if args.skip_build:
        rehearsal.record("SKIP", "build, check and smoke", "--skip-build")
    else:
        if shutil.which("uv") is None:
            raise RehearsalError("uv is not on PATH; install it from https://docs.astral.sh/uv/")
        epoch = _git(root, "log", "-1", "--format=%ct", commit).strip()
        digests = build_and_check(rehearsal, built_version, epoch)
        if digests is not None:
            wheel = next((rehearsal.work / "dist").glob("*.whl"))
            sdist = next((rehearsal.work / "dist").glob("*.tar.gz"))
            constraints = None if args.skip_mcp else mcp_constraints(rehearsal)
            wanted = args.python or supported_pythons(root)
            interpreters: list[str] = []
            for version in wanted:
                interpreter = find_python(rehearsal, version)
                if interpreter is None:
                    rehearsal.record("SKIP", f"smoke wheel on Python {version}", "not installed here; CI covers it (`uv python install` to add it)")
                    continue
                interpreters.append(interpreter)
                smoke(rehearsal, f"wheel-py{version}", interpreter, wheel, built_version, mcp=not args.skip_mcp, constraints=constraints)
            if interpreters:
                # The sdist must rebuild the wheel; once, on the lowest Python found.
                smoke(rehearsal, "sdist", interpreters[0], sdist, built_version, mcp=False, constraints=None)
            else:
                rehearsal.record("FAIL", "smoke wheel", f"none of Python {', '.join(wanted)} is installed")
        if _git(root, "status", "--porcelain", "--untracked-files=normal").strip() != dirty:
            rehearsal.record("FAIL", "the rehearsal left the working tree untouched", "building changed tracked or untracked files")

    # 5 and 6. Summary and the maintainer's commands.
    print("\n" + "=" * 72)
    if digests:
        print("Archive digests (SHA-256) of this rehearsal:")
        for name, digest in sorted(digests.items()):
            print(f"  {digest}  {name}")
        print()
    if rehearsal.failed:
        print(f"NOT READY: {sum(s.status == 'FAIL' for s in rehearsal.steps)} step(s) failed. Nothing was tagged or pushed.")
        return 1
    state = "READY TO TAG" if not rehearsal.pending else "REHEARSAL PASSED, RELEASE PULL REQUEST STILL PENDING"
    print(f"{state}. This script has not tagged, pushed or uploaded anything.")
    print("The maintainer runs, and only the maintainer runs:\n")
    print(textwrap.indent(owner_commands(candidate.version, commit, pending=rehearsal.pending), "    "))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rehearse a WhyKit release locally: build, check, install and smoke-test, then print the tag commands. Never tags, pushes or uploads.",
    )
    parser.add_argument("--commit", default="HEAD", help="commit to rehearse; it must be checked out with a clean tree (default: HEAD)")
    parser.add_argument("--work-dir", help="directory for builds, virtual environments and release notes (default: a new temporary directory, kept)")
    parser.add_argument("--python", action="append", metavar="X.Y", help="smoke-test only this Python (repeatable; default: every supported version installed here)")
    parser.add_argument("--skip-build", action="store_true", help="only check the tree, compute the version, write release notes and print the commands")
    parser.add_argument("--skip-mcp", action="store_true", help="install the wheel without the mcp extra and skip the MCP list-tools smoke")
    parser.add_argument("--online", action="store_true", help="let uv use the network (default: UV_OFFLINE=1, everything from the local cache)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return rehearse(args)
    except RehearsalError as exc:
        print(f"release_rehearsal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
