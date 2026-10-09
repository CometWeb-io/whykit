"""Synthetic MCP query profile; one vault size per fresh Python process."""
import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
try:
    import resource
except ImportError:  # Windows has no stdlib process RSS reader.
    resource = None

parser = argparse.ArgumentParser()
parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--notes", type=int, required=True)
args = parser.parse_args()
if args.notes < 100:
    parser.error("--notes must be at least 100")
sys.path.insert(0, str(args.checkout / "src"))
sys.path.insert(0, str(args.checkout / "tests"))
from synthetic_vault import generate  # noqa: E402
from whykit.mcp_server import VaultTools  # noqa: E402
from whykit import query  # noqa: E402

with tempfile.TemporaryDirectory(prefix="whykit-mcp-query-profile-") as temp:
    vault = generate(Path(temp) / "vault", notes=args.notes)
    tools = VaultTools(vault)
    # Keep the real cache's racy-file protection; wait for these immutable fixtures.
    time.sleep(2.1)
    times = []
    for phase in ("cold", "warm", "warm"):
        start = time.perf_counter()
        result = tools.scoped(tools.query, "pipeline", limit=20)
        times.append({"phase": phase, "seconds": time.perf_counter() - start})
    tracemalloc.start()
    summary_count = 0
    original = query._summary

    def counted(index, note):
        global summary_count
        summary_count += 1
        return original(index, note)

    query._summary = counted
    try:
        page = tools.scoped(tools.query, "pipeline", limit=1)
        assert page["returned"] == 1 and page["total"] == result["total"]
    finally:
        query._summary = original
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"requested_notes": args.notes, "matches": result["total"],
                      "returned": result["returned"], "times": times,
                      "limit_1_summary_count": summary_count,
                      "limit_1_tracemalloc_peak_bytes": peak,
                      "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024) if resource else None,
                      "cache_hits": tools.notes.hits, "cache_misses": tools.notes.misses,
                      "scope": "SDK-free handler with real per-request path/cache scopes; synthetic corpus, no model/HTTP proof"}))
