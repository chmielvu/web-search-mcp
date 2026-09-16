#!/usr/bin/env python3
"""MCP stdio smoke test for Web MCP container."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

REQUIRED_TOOLS = {"web_search", "fetch_content", "get_suggestions"}
DEFAULT_SEARCH_QUERY = "python asyncio"
DEFAULT_SUGGEST_QUERY = "python asyn"
FALLBACK_CONTENT_URL = "https://example.com"
URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MCP smoke checks against containerized server")
    parser.add_argument(
        "--docker-command",
        default="docker",
        help="Container runtime command",
    )
    parser.add_argument(
        "--image",
        default="web-mcp:latest",
        help="Container image for MCP server",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Query for web_search (defaults to --suggest-query if provided)",
    )
    parser.add_argument(
        "--suggest-query",
        default=None,
        help="Query prefix for get_suggestions (defaults to --query if provided)",
    )
    parser.add_argument(
        "--content-url",
        default=None,
        help="URL for fetch_content (defaults to first URL found in web_search output)",
    )
    parser.add_argument("--limit", type=int, default=3, help="Result limit for web_search")
    parser.add_argument("--max-length", type=int, default=800, help="Max length for fetch_content")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full tool response blocks",
    )
    return parser.parse_args()


def resolve_queries(args: argparse.Namespace) -> None:
    if args.query and not args.suggest_query:
        args.suggest_query = args.query
    elif args.suggest_query and not args.query:
        args.query = args.suggest_query
    elif not args.query and not args.suggest_query:
        args.query = DEFAULT_SEARCH_QUERY
        args.suggest_query = DEFAULT_SUGGEST_QUERY


def shorten(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def extract_text_blocks(result: CallToolResult) -> list[str]:
    blocks: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            blocks.append(text)
    return blocks


def first_url_from_blocks(text_blocks: list[str]) -> str | None:
    for block in text_blocks:
        for match in URL_PATTERN.findall(block):
            return match.rstrip(".,;)")
    return None


def content_url_candidates(explicit_url: str | None, web_text_blocks: list[str]) -> list[str]:
    if explicit_url:
        return [explicit_url]

    primary = first_url_from_blocks(web_text_blocks)
    if not primary:
        return [FALLBACK_CONTENT_URL]
    if primary == FALLBACK_CONTENT_URL:
        return [primary]
    return [primary, FALLBACK_CONTENT_URL]


def emit(status: str, message: str) -> None:
    print(f"[{status}] {message}")


def emit_tool_summary(name: str, result: CallToolResult) -> None:
    emit(
        "PASS" if not result.isError else "FAIL",
        f"{name}: isError={result.isError} content_items={len(result.content)}",
    )


def emit_verbose_blocks(name: str, blocks: list[str]) -> None:
    print(f"{name} output:")
    if not blocks:
        print("  (no text content)")
        return
    for idx, block in enumerate(blocks, start=1):
        preview = shorten(block, 1200)
        print(f"  --- block {idx} ---")
        for line in preview.splitlines():
            print(f"  {line}")


async def call_tool_checked(
    session: ClientSession,
    name: str,
    arguments: dict[str, object],
    *,
    verbose: bool,
) -> list[str]:
    result = await session.call_tool(name, arguments)
    blocks = extract_text_blocks(result)
    emit_tool_summary(name, result)
    if verbose:
        emit_verbose_blocks(name, blocks)
    if result.isError:
        raise RuntimeError(f"{name} returned error")
    return blocks


async def run_checks(args: argparse.Namespace) -> None:
    server_parameters = StdioServerParameters(
        command=args.docker_command,
        args=["run", "--rm", "-i", args.image],
    )

    emit("INFO", f"Starting MCP server via: {args.docker_command} run --rm -i {args.image}")

    async with stdio_client(server_parameters) as streams:
        async with ClientSession(*streams) as session:
            init = await session.initialize()
            emit(
                "PASS",
                f"initialize: server={init.serverInfo.name} version={init.serverInfo.version}",
            )

            tools_result = await session.list_tools()
            available_tools = {tool.name for tool in tools_result.tools}
            emit("PASS", f"list_tools: {', '.join(sorted(available_tools))}")

            missing = REQUIRED_TOOLS - available_tools
            if missing:
                raise RuntimeError(f"Missing expected tools: {', '.join(sorted(missing))}")

            web_text_blocks = await call_tool_checked(
                session,
                "web_search",
                {
                    "query": args.query,
                    "limit": args.limit,
                },
                verbose=args.verbose,
            )

            await call_tool_checked(
                session,
                "get_suggestions",
                {
                    "query": args.suggest_query,
                },
                verbose=args.verbose,
            )

            fetch_candidates = content_url_candidates(args.content_url, web_text_blocks)
            fetch_success = False
            for candidate_url in fetch_candidates:
                emit("INFO", f"fetch_content url={candidate_url}")
                result = await session.call_tool(
                    "fetch_content",
                    {
                        "url": candidate_url,
                        "max_length": args.max_length,
                    },
                )
                blocks = extract_text_blocks(result)
                emit_tool_summary("fetch_content", result)
                if args.verbose:
                    emit_verbose_blocks("fetch_content", blocks)
                if not result.isError:
                    fetch_success = True
                    break

            if not fetch_success:
                raise RuntimeError("fetch_content returned error for all candidate URLs")

    emit("PASS", "Smoke test completed")


def main() -> None:
    args = parse_args()
    resolve_queries(args)
    emit("INFO", f"web_search query={args.query}")
    emit("INFO", f"suggestions query={args.suggest_query}")

    try:
        asyncio.run(run_checks(args))
    except Exception as exc:  # pragma: no cover - exercised in real smoke runs
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
