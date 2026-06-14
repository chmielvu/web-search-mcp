from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from scripts.brightdata_probe_runner import run_probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iteratively probe the BrightData MCP search tools."
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Query string to test. Can be passed multiple times.",
    )
    args = parser.parse_args()

    queries = args.queries or ["python", "OpenAI", "github"]

    try:
        asyncio.run(run_probe(queries))
    except Exception as exc:  # noqa: BLE001 - diagnostic probe
        print(f"Probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
