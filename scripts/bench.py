#!/usr/bin/env python3
"""Time WhyKit's read commands on a vault, and optionally record or compare their output.

Each command runs in a fresh interpreter, so the timing includes start-up and
import cost the way a user or CI job pays it.

    python3 scripts/bench.py --notes 5000                  # synthetic vault, timings
    python3 scripts/bench.py --vault examples/northline    # an existing vault
    python3 scripts/bench.py --notes 5000 --record out/    # save stdout + exit codes
    python3 scripts/bench.py --notes 5000 --compare out/   # fail on any byte difference
    python3 scripts/bench.py --notes 5000 --profile prof/  # cProfile dump per command
    python3 scripts/bench.py --notes 5000 --src /old/src   # time another checkout's code

A synthetic vault is written by ``tests/synthetic_vault.py`` into a temporary
directory (or ``--work``) and is byte-identical for the same ``--notes``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TODAY = "2026-09-17"
# The only wall-clock value in any read command's output.
_VOLATILE = re.compile(rb'"generatedAt": "[^"]*"')

# (name, argv after `whykit`, without --root). Targets that do not exist in a
# given vault still exercise the full load path and produce a stable error.
COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("lint", ["lint", "--json", "--today", TODAY]),
    ("status", ["status", "--json", "--today", TODAY]),
    ("snapshot", ["snapshot", "--today", TODAY, "--compact"]),
    ("check", ["check", "--profile", "ci", "--json", "--today", TODAY]),
    ("query", ["query", "pipeline", "--json"]),
    ("query-source", ["query", "--source", "E-010", "--json"]),
    ("context", ["context", "D-010", "--json"]),
    ("pack", ["pack", "D-010", "--query", "pipeline", "--json"]),
    ("trace", ["trace", "--today", TODAY, "--json"]),
    ("explorer-index", ["explorer-index", "--today", TODAY]),
    ("graph", ["graph", "--json"]),
    ("impact", ["impact", "E-010", "--json"]),
    ("backlinks", ["backlinks", "D-010", "--json"]),
    ("review-list", ["review", "list", "--today", TODAY, "--json"]),
)

_RUNNER = """
import sys
sys.path.insert(0, {src!r})
from whykit.cli import main
raise SystemExit(main({argv!r}))
"""

_PROFILER = """
import cProfile, sys
sys.path.insert(0, {src!r})
from whykit.cli import main
code = 0
profiler = cProfile.Profile()
try:
    profiler.runcall(main, {argv!r})
except SystemExit as exc:
    code = exc.code
profiler.dump_stats({out!r})
"""


def run(src: Path, vault: Path, name: str, argv: list[str], profile_dir: Path | None) -> tuple[float, int, bytes]:
    # `--root` belongs to the top-level command (for `review`, before the action).
    full = [argv[0], "--root", str(vault), *argv[1:]]
    template = _PROFILER if profile_dir else _RUNNER
    code = template.format(src=str(src), argv=full, out=str(profile_dir / f"{name}.prof") if profile_dir else "")
    started = time.perf_counter()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=vault, env={**os.environ, "PYTHONHASHSEED": "0"})
    elapsed = time.perf_counter() - started
    return elapsed, result.returncode, result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--notes", type=int, help="generate a synthetic vault of about N notes")
    target.add_argument("--vault", help="benchmark an existing vault")
    parser.add_argument("--work", help="where to write the synthetic vault (default: a temporary directory)")
    parser.add_argument("--src", default=str(REPO / "src"), help="package source to import (default: this checkout)")
    parser.add_argument("--only", action="append", help="run only this command name (repeatable)")
    parser.add_argument("--record", help="write each command's stdout and exit code into this directory")
    parser.add_argument("--compare", help="compare stdout and exit codes with a --record directory")
    parser.add_argument("--profile", help="write a cProfile dump per command into this directory")
    parser.add_argument("--json", action="store_true", help="print timings as JSON")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="whykit-bench-") as tmp:
        if args.vault:
            vault = Path(args.vault).resolve()
        else:
            sys.path.insert(0, str(REPO / "tests"))
            from synthetic_vault import generate

            vault = Path(args.work).resolve() if args.work else Path(tmp) / f"vault-{args.notes}"
            if not (vault / "Home.md").exists():
                generate(vault, args.notes)
        profile_dir = Path(args.profile).resolve() if args.profile else None
        if profile_dir:
            profile_dir.mkdir(parents=True, exist_ok=True)
        record = Path(args.record).resolve() if args.record else None
        if record:
            record.mkdir(parents=True, exist_ok=True)
        compare = Path(args.compare).resolve() if args.compare else None

        timings: dict[str, float] = {}
        mismatches: list[str] = []
        for name, command in COMMANDS:
            if args.only and name not in args.only:
                continue
            elapsed, code, stdout = run(Path(args.src).resolve(), vault, name, command, profile_dir)
            timings[name] = round(elapsed, 3)
            blob = f"exit={code}\n".encode() + stdout
            if record:
                (record / f"{name}.out").write_bytes(blob)
            if compare:
                expected = (compare / f"{name}.out").read_bytes()
                if _VOLATILE.sub(b"", expected) != _VOLATILE.sub(b"", blob):
                    mismatches.append(name)
            if not args.json:
                flag = "  DIFF" if name in mismatches else ""
                print(f"{name:<16}{elapsed:8.2f}s  exit={code}{flag}", flush=True)
        if args.json:
            print(json.dumps({"vault": str(vault), "timings": timings, "mismatches": mismatches}, indent=2))
        if mismatches:
            print(f"output differs for: {', '.join(mismatches)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
