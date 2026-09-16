#!/usr/bin/env python3
"""
Web MCP Server - MCP server for web search and content extraction.

This server provides three tools:
- web_search: Search the web using SearxNG with Google fallback
- fetch_content: Extract readable content from URLs
- get_suggestions: Get search query suggestions
"""

import asyncio
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ListToolsResult,
    ResourceLink,
    TextContent,
    Tool,
)

from web_mcp import __version__
from web_mcp.config import settings
from web_mcp.search.provider_registry import close_search_provider
from web_mcp.tools import ALL_TOOLS, TOOL_HANDLERS
from web_mcp.utils.logger import get_logger, setup_logging

logger = get_logger("web_mcp")

server: Server | None = None
shutdown_requested = False
McpContent = TextContent | ImageContent | AudioContent | ResourceLink | EmbeddedResource
ToolHandler = Callable[..., Awaitable[Any]]


def create_server() -> Server:
    """Create and configure the MCP server."""
    srv = Server(
        name="web-mcp",
        version=__version__,
    )

    @srv.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> ListToolsResult:
        """Return list of available tools."""
        tools: list[Tool] = []
        for raw_schema in ALL_TOOLS:
            schema = cast(dict[str, Any], raw_schema)
            tool = Tool(
                name=cast(str, schema["name"]),
                description=cast(str, schema["description"]),
                inputSchema=cast(dict[str, Any], schema["inputSchema"]),
            )
            tools.append(tool)
        logger.debug(f"Listed {len(tools)} tools")
        return ListToolsResult(tools=tools)

    @srv.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Handle tool invocation."""
        request_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()

        logger.info(
            f"Calling tool: {tool_name}",
            extra={
                "tool": tool_name,
                "arguments": arguments,
                "request_id": request_id,
            },
        )

        if tool_name not in TOOL_HANDLERS:
            logger.error(f"Unknown tool: {tool_name}")
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Error: Unknown tool '{tool_name}'",
                    )
                ],
                isError=True,
            )

        try:
            handler = cast(ToolHandler, TOOL_HANDLERS[tool_name])
            result = await handler(**arguments)
            content: list[McpContent]

            if hasattr(result, "to_mcp_response"):
                response_content = cast(list[dict[str, str]], result.to_mcp_response())
                content = [
                    TextContent(
                        type="text",
                        text=item.get("text", ""),
                    )
                    for item in response_content
                ]
            else:
                content = [
                    TextContent(
                        type="text",
                        text=str(result),
                    )
                ]

            is_error = hasattr(result, "error") and bool(result.error)
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "Tool call completed",
                extra={
                    "tool": tool_name,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                    "result_count": len(content),
                    "is_error": is_error,
                },
            )
            return CallToolResult(content=content, isError=is_error)

        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                f"Tool execution failed: {e}",
                extra={
                    "tool": tool_name,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                    "error": str(e),
                },
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Error executing tool '{tool_name}': {str(e)}",
                    )
                ],
                isError=True,
            )

    return srv


async def run_server() -> None:
    """Run the MCP server with stdio transport."""
    global server, shutdown_requested

    setup_logging(
        level=settings.LOG_LEVEL,
        json_format=settings.JSON_LOGS,
        name="web_mcp",
    )

    logger.info(f"Starting Web MCP Server v{__version__}")
    logger.info(f"SearxNG URL: {settings.SEARXNG_URL}")
    logger.info(f"Fallback enabled: {settings.FALLBACK_ENABLED}")

    server = create_server()

    def signal_handler(signum: int, frame: object | None) -> None:
        global shutdown_requested
        del frame
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        shutdown_requested = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    async with stdio_server() as (read_stream, write_stream):
        logger.info("Server started, waiting for connections...")

        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
        except Exception as e:
            if not shutdown_requested:
                logger.error(f"Server error: {e}")
                raise
        finally:
            await close_search_provider()
            logger.info("Server stopped")


def main() -> None:
    """Main entry point."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Web MCP Server shutdown complete")


if __name__ == "__main__":
    main()
