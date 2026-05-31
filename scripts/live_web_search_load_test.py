from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def ensure_script_dir_on_path() -> None:
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live MCP web_search load/observability probe."
    )
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--num-results", type=int, default=3)
    parser.add_argument("--mode", choices=["mixed", "literal"], default="mixed")
    parser.add_argument("--query", help="Use the same explicit query for every call.")
    parser.add_argument(
        "--providers",
        help="Comma-separated provider override, e.g. searxng,ddg",
    )
    parser.add_argument(
        "--command",
        help="MCP stdio command. Defaults to repo .venv kindly-web-search.exe",
    )
    parser.add_argument("--command-args", nargs="*", default=["--stdio"])
    parser.add_argument("--cwd", help="MCP server cwd. Defaults to repo root.")
    parser.add_argument("--output-dir", default="outputs/live-web-search")
    parser.add_argument("--run-id")
    parser.add_argument("--timeout-seconds", type=float, default=150.0)
    parser.add_argument(
        "--linger-seconds",
        type=float,
        default=0.0,
        help="Keep the MCP process alive after the last call so OTEL exporters can flush.",
    )
    return parser.parse_args()


def main() -> int:
    ensure_script_dir_on_path()
    from live_web_search_probe_lib import run_probe

    raw_path, summary_path = asyncio.run(run_probe(parse_args()))
    print(f"raw={raw_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
