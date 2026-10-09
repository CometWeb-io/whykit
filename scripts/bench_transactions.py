"""Measure durable transaction latency and assert bounded journal retention.

Run outside the unit suite: uv run python scripts/bench_transactions.py --count 100000
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whykit.io import apply_transaction, vault_mutation_lock  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    samples: list[float] = []
    checkpoints = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="whykit-journals-") as tmp:
        root = Path(tmp).resolve()
        for n in range(1, args.count + 1):
            before = time.perf_counter()
            with vault_mutation_lock(root):
                apply_transaction(root, {root / "record.md": str(n)})
            samples.append(time.perf_counter() - before)
            if n in {1000, 10000, args.count}:
                retained = len(list((root / ".whykit/transactions").iterdir()))
                assert retained == 0, retained
                window = sorted(samples[-1000:])
                checkpoints.append({"writes": n, "retained_journals": retained,
                                    "p50_ms": statistics.median(window) * 1000,
                                    "p95_ms": window[min(len(window)-1, int(len(window)*0.95))] * 1000})
                print(json.dumps(checkpoints[-1]), file=sys.stderr, flush=True)
        assert (root / "record.md").read_text(encoding="utf-8") == str(args.count)
    print(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                      "durable_fsync": True, "elapsed_seconds": time.perf_counter()-started,
                      "checkpoints": checkpoints}, indent=2))


if __name__ == "__main__":
    main()
