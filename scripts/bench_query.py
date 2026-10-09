"""Measure in-process MCP query contention; this does not qualify client transports."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests")]
from synthetic_vault import generate  # noqa: E402


def peak_rss() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=int, default=1000)
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 8, 32])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--vault", type=Path, help="reuse an existing synthetic vault without changing it")
    parser.add_argument("--src", type=Path, default=REPO / "src", help="implementation to measure")
    parser.add_argument("--text", action="append", help="substring query (repeatable; default: pipeline)")
    args = parser.parse_args()
    if min([args.notes, args.runs, *args.clients]) < 1:
        parser.error("notes, clients and runs must be positive")
    source = args.src.resolve()
    if not (source / "whykit" / "__init__.py").is_file():
        parser.error("--src must contain the WhyKit package")
    results = []
    sys.path.insert(0, str(source))
    import whykit
    if Path(whykit.__file__).resolve().parent != source / "whykit":
        parser.error("loaded WhyKit does not belong to --src")
    from whykit.mcp_server import VaultTools
    from whykit.query import query_vault
    with tempfile.TemporaryDirectory(prefix="whykit-query-") as tmp:
        root = args.vault.resolve() if args.vault else Path(tmp).resolve() / "vault"
        if not args.vault:
            generate(root, args.notes)
        # Let fixture timestamps leave NoteCache's racy-clean window.
        time.sleep(2.1)

        def call():
            before = time.perf_counter()
            report = tools.query(text=text, limit=1)
            assert report["results"] == expected["results"]
            assert report["total"] == expected["total"]
            return time.perf_counter() - before

        for clients in args.clients:
            for text in args.text or ["pipeline"]:
                tools = VaultTools(root, max_sensitivity="restricted")
                expected = query_vault(root, text=text, limit=1)
                cold = call()
                samples = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                    for _ in range(args.runs):
                        samples.extend(pool.map(lambda _: call(), range(clients)))
                ordered = sorted(samples)
                results.append({"clients": clients, "text": text, "matches": expected["total"],
                                "calls": len(samples), "cold_seconds": cold,
                                "p50_seconds": statistics.median(samples),
                                "p95_seconds": ordered[min(len(ordered)-1, int(len(ordered)*0.95))],
                                "process_peak_rss_bytes": peak_rss()})
    print(json.dumps({"platform": platform.platform(), "notes_requested": args.notes if not args.vault else None,
                      "source": str(source),
                      "scope": "shared in-process tool handlers; cumulative process RSS; no network clients", "results": results}, indent=2))


if __name__ == "__main__":
    main()
