from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kindly-web-search-mcp-server",
        description="Codex-friendly wrapper CLI for the Kindly Web Search MCP server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser(
        "start-mcp-server",
        help="Start the MCP server (stdio by default).",
        description=(
            "Start the Kindly Web Search MCP server. "
            "This is primarily intended for MCP hosts that launch servers via stdio."
        ),
    )
    start.add_argument(
        "--context",
        default=None,
        help="Client context hint (e.g., 'codex'); exposed to the server as KINDLY_MCP_CONTEXT.",
    )

    sync = subparsers.add_parser(
        "sync-analytics",
        help="Sync local DuckDB analytics to MotherDuck for Grafana dashboards.",
    )
    sync.add_argument(
        "--duckdb-path",
        default=None,
        help="Local analytics DuckDB path. Defaults to KINDLY_ANALYTICS_DUCKDB_PATH.",
    )
    sync.add_argument(
        "--motherduck-database",
        default=None,
        help="MotherDuck database name. Defaults to KINDLY_MOTHERDUCK_DATABASE.",
    )
    sync.add_argument(
        "--schema",
        default="kindly_analytics",
        help="MotherDuck schema for analytics tables and views.",
    )
    sync.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum new rows to sync in one pass.",
    )
    sync.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of one sync pass.",
    )
    sync.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Loop interval in seconds. Default 300.",
    )

    return parser


def _has_transport_flag(argv: list[str]) -> bool:
    transport_flags = {
        "--stdio",
        "--sse",
        "--http",
        "--streamable-http",
        "--transport",
    }
    return any(arg.split("=", 1)[0] in transport_flags for arg in argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv()

    parser = _build_arg_parser()
    args, forwarded_args = parser.parse_known_args(argv)

    if args.command == "sync-analytics":
        from .analytics.motherduck_sync import sync_loop, sync_once

        if args.loop:
            sync_loop(
                source_path=args.duckdb_path,
                motherduck_database=args.motherduck_database,
                schema=args.schema,
                interval_seconds=args.interval_seconds,
            )
            return
        result = sync_once(
            source_path=args.duckdb_path,
            motherduck_database=args.motherduck_database,
            schema=args.schema,
            limit=args.limit,
        )
        print(
            "Synced "
            f"{result.inserted_rows} new analytics rows to "
            f"MotherDuck {result.database}.{result.schema} "
            f"({result.source_rows} local rows)."
        )
        return

    from .server import main as server_main

    if forwarded_args[:1] == ["--"]:
        forwarded_args = forwarded_args[1:]

    if not _has_transport_flag(forwarded_args):
        forwarded_args = ["--stdio", *forwarded_args]

    previous_context = os.environ.get("KINDLY_MCP_CONTEXT")
    try:
        if args.context is not None and args.context.strip():
            os.environ["KINDLY_MCP_CONTEXT"] = args.context.strip()
        server_main(forwarded_args)
    finally:
        if previous_context is None:
            os.environ.pop("KINDLY_MCP_CONTEXT", None)
        else:
            os.environ["KINDLY_MCP_CONTEXT"] = previous_context
