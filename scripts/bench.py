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
    python3 scripts/bench.py --notes 5000 --mcp            # MCP tool latency, cold and repeated
    python3 scripts/bench.py --notes 5000 --memory         # peak traced memory per command
    python3 scripts/bench.py --notes 5000 --cache warm     # with the parse cache primed

A synthetic vault is written by ``tests/synthetic_vault.py`` into a temporary
directory (or ``--work``) and is byte-identical for the same ``--notes``.

``--cache`` selects the parse cache in ``.whykit/cache/``: ``off`` (the
default) runs every command with ``WHYKIT_NO_CACHE=1``; ``cold`` deletes the
cache before each command, so the command parses everything and writes a new
one; ``warm`` primes the cache once and leaves it in place.  Timings are wall
clock and CPU time (user + system) of the command's process; on a busy machine
the CPU time is the steadier of the two.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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

# (tool, arguments) for --mcp. Each tool is called through the SDK-free
# `VaultTools.call`, the same entry point the MCP server uses.
MCP_CALLS: tuple[tuple[str, dict], ...] = (
    ("query", {"text": "pipeline"}),
    ("context", {"target": "D-010"}),
    ("impact", {"target": "E-010"}),
    ("status", {"today": TODAY}),
    ("pack", {"targets": ["D-010"], "query": "pipeline"}),
    ("trace", {"today": TODAY}),
    ("backlinks", {"target": "D-010"}),
)

# A fresh server per tool for the cold call, then repeated calls on it.
_MCP_RUNNER = """
import json, statistics, sys, time
sys.path.insert(0, {src!r})
from whykit.mcp_server import VaultTools
out = {{}}
for name, arguments in {calls!r}:
    tools = VaultTools({vault!r})
    times = []
    for _ in range({repeat}):
        started = time.perf_counter()
        payload, failed = tools.call(name, arguments)
        times.append(time.perf_counter() - started)
        if failed and payload.get("error", {{}}).get("code") == "internal_error":
            raise SystemExit(f"{{name}}: {{payload}}")
    out[name] = {{"cold": round(times[0], 3), "repeat": round(statistics.median(times[1:]), 3)}}
print(json.dumps(out))
"""

_RUNNER = """
import sys
sys.path.insert(0, {src!r})
from whykit.cli import main
raise SystemExit(main({argv!r}))
"""

# Peak memory traced by `tracemalloc` while the command runs (after the CLI
# module is imported), including the output text it builds.
_MEMORY = """
import contextlib, io, sys, tracemalloc
sys.path.insert(0, {src!r})
from whykit.cli import main
tracemalloc.start()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    try:
        main({argv!r})
    except SystemExit:
        pass
print(tracemalloc.get_traced_memory()[1])
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


CACHE_MODES = ("off", "cold", "warm")


def cache_env(mode: str) -> dict[str, str]:
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    env.pop("WHYKIT_NO_CACHE", None)
    if mode == "off":
        env["WHYKIT_NO_CACHE"] = "1"
    return env


def drop_cache(vault: Path) -> None:
    shutil.rmtree(vault / ".whykit" / "cache", ignore_errors=True)


def _children_cpu() -> float:
    try:
        import resource
    except ImportError:  # Windows: no rusage, report wall clock only
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run(
    src: Path, vault: Path, name: str, argv: list[str], profile_dir: Path | None, cache: str = "off",
) -> tuple[float, int, bytes, float]:
    # `--root` belongs to the top-level command (for `review`, before the action).
    full = [argv[0], "--root", str(vault), *argv[1:]]
    template = _PROFILER if profile_dir else _RUNNER
    code = template.format(src=str(src), argv=full, out=str(profile_dir / f"{name}.prof") if profile_dir else "")
    if cache == "cold":
        drop_cache(vault)
    cpu = _children_cpu()
    started = time.perf_counter()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=vault, env=cache_env(cache))
    elapsed = time.perf_counter() - started
    return elapsed, result.returncode, result.stdout, _children_cpu() - cpu


def run_memory(src: Path, vault: Path, argv: list[str], cache: str = "off") -> int:
    full = [argv[0], "--root", str(vault), *argv[1:]]
    if cache == "cold":
        drop_cache(vault)
    result = subprocess.run(
        [sys.executable, "-c", _MEMORY.format(src=str(src), argv=full)], capture_output=True, text=True, cwd=vault,
        env=cache_env(cache),
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return int(result.stdout.strip())


def run_mcp(src: Path, vault: Path, repeat: int) -> dict[str, dict[str, float]]:
    code = _MCP_RUNNER.format(src=str(src), vault=str(vault), calls=list(MCP_CALLS), repeat=repeat)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=vault,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return json.loads(result.stdout)


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
    parser.add_argument("--mcp", action="store_true", help="time each MCP tool in-process instead of the CLI")
    parser.add_argument("--repeat", type=int, default=5, help="calls per MCP tool, the first one cold (default: 5)")
    parser.add_argument("--memory", action="store_true", help="report each command's peak traced memory instead of time")
    parser.add_argument("--cache", choices=CACHE_MODES, default="off", help="parse cache: off (default), cold or warm")
    parser.add_argument("--runs", type=int, default=1, help="run each command N times and keep the best (default: 1)")
    args = parser.parse_args(argv)
    if args.mcp and args.repeat < 2:
        parser.error("--repeat must be at least 2")

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

        if args.cache == "warm":
            drop_cache(vault)
            run(Path(args.src).resolve(), vault, "prime", list(COMMANDS[0][1]), None, "warm")

        if args.memory:
            peaks: dict[str, float] = {}
            for name, command in COMMANDS:
                if args.only and name not in args.only:
                    continue
                peaks[name] = round(min(
                    run_memory(Path(args.src).resolve(), vault, command, args.cache) for _ in range(max(1, args.runs))
                ) / 2**20, 1)
                if not args.json:
                    print(f"{name:<16}{peaks[name]:8.1f} MB", flush=True)
            if args.json:
                print(json.dumps({"vault": str(vault), "peak_mb": peaks}, indent=2))
            return 0

        if args.mcp:
            report = run_mcp(Path(args.src).resolve(), vault, args.repeat)
            if args.json:
                print(json.dumps({"vault": str(vault), "mcp": report}, indent=2))
            else:
                print(f"{'tool':<12}{'cold':>8}{'repeat':>9}")
                for name, row in report.items():
                    print(f"{name:<12}{row['cold']:7.3f}s{row['repeat']:8.3f}s")
            return 0

        timings: dict[str, float] = {}
        cpu_times: dict[str, float] = {}
        mismatches: list[str] = []
        for name, command in COMMANDS:
            if args.only and name not in args.only:
                continue
            best: tuple[float, int, bytes, float] | None = None
            for _ in range(max(1, args.runs)):
                attempt = run(Path(args.src).resolve(), vault, name, command, profile_dir, args.cache)
                if best is None or attempt[3] < best[3]:
                    best = attempt
            assert best is not None
            elapsed, code, stdout, cpu = best
            timings[name] = round(elapsed, 3)
            cpu_times[name] = round(cpu, 3)
            blob = f"exit={code}\n".encode() + stdout
            if record:
                (record / f"{name}.out").write_bytes(blob)
            if compare:
                expected = (compare / f"{name}.out").read_bytes()
                if _VOLATILE.sub(b"", expected) != _VOLATILE.sub(b"", blob):
                    mismatches.append(name)
            if not args.json:
                flag = "  DIFF" if name in mismatches else ""
                print(f"{name:<16}{elapsed:8.2f}s{cpu:8.2f}s cpu  exit={code}{flag}", flush=True)
        if args.json:
            print(json.dumps(
                {"vault": str(vault), "cache": args.cache, "timings": timings, "cpu": cpu_times, "mismatches": mismatches},
                indent=2,
            ))
        if mismatches:
            print(f"output differs for: {', '.join(mismatches)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
