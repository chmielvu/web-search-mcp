from __future__ import annotations

import argparse
import os


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
    from .server import main as server_main

    parser = _build_arg_parser()
    args, forwarded_args = parser.parse_known_args(argv)

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
