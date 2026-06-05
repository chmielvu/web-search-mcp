"""Standalone MCP tools for guarded analytics querying and reports."""

from __future__ import annotations

import asyncio
from typing import Literal, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from ..errors import format_tool_error
from .formatting import json_safe_rows
from .queries import run_analytics_query
from .reports import available_reports, run_report
from ..tools.catalog import tool_kwargs


def register_analytics_tools(mcp: Any) -> None:
    """Register read-only analytics tools on the MCP server."""

    @mcp.tool(**tool_kwargs("analytics_query"))
    async def analytics_query(
        question: str,
        scope: Literal["local", "motherduck"] = "local",
        max_rows: int = 100,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Run a guarded, allowlisted analytics query over DuckDB/MotherDuck."""

        await ctx.info(f"Analytics query: {question[:100]}...")
        try:
            result = await asyncio.to_thread(
                run_analytics_query,
                question,
                scope=scope,
                max_rows=max_rows,
            )
        except Exception as exc:
            return format_tool_error(exc, provider="analytics_query")

        await ctx.info(
            f"Analytics query returned {result['row_count']} rows using {result['rationale']}"
        )
        return result

    @mcp.tool(**tool_kwargs("analytics_report"))
    async def analytics_report(
        report_name: str,
        days: int = 7,
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Run a deterministic analytics report and return JSON rows."""

        await ctx.info(f"Analytics report: {report_name}...")
        try:
            table = await asyncio.to_thread(run_report, report_name, days=days)
        except Exception as exc:
            return format_tool_error(exc, provider="analytics_report")

        await ctx.info(
            f"Analytics report {report_name} returned {table.num_rows} rows"
        )
        return {
            "report": report_name,
            "days": days,
            "row_count": table.num_rows,
            "rows": json_safe_rows(table.to_pylist()),
            "available_reports": available_reports(),
        }
