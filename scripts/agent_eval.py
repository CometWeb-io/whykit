"""Source-checkout CLI for the offline agent evaluation library."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from whykit.agent_eval import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
