from __future__ import annotations

import logging
from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from opentelemetry import trace

from kindly_web_search_mcp_server.errors import classify_error
from kindly_web_search_mcp_server.telemetry import (
    create_chain_span,
    record_mcp_tool_call,
    record_tool_details,
)
from kindly_web_search_mcp_server.tools.catalog import tool_kwargs
from kindly_web_search_mcp_server.utils.observability import (
    emit_tool_observability_event,
)

from .models import AgenticResearchRequest
from .runner import run_agentic_web_research

LOGGER = logging.getLogger(__name__)


def register_agentic_web_research_tools(mcp: Any) -> None:  # FastMCP instance
    @mcp.tool(**tool_kwargs("agentic_web_research"))
    async def agentic_web_research(
        query: str,
        research_goal: str | None = None,
        depth: str = "normal",
        ctx: Context = CurrentContext(),
    ) -> dict:
        """Multi-step web research using a ReAct agent that selects tools autonomously.
        Experimental, not idempotent. Use only for open-ended research requiring multiple iterations.
        """
        # Outer MCP boundary instrumentation (Grafana/DuckDB + OTel)
        # Mirrors patterns from server.py for other complex tools (web_search, gemini, etc.)
        with create_chain_span(
            "mcp.tool.agentic_web_research",
            attributes={
                "search.query": query[:500],
                "gen_ai.tool.name": "agentic_web_research",
                "agent.depth": depth,
            },
        ) as root_span:
            if research_goal:
                try:
                    root_span.set_attribute("search.research_goal", research_goal[:300])
                except Exception:
                    pass

            await ctx.info(f"Agentic web research started: {query[:120]}")
            emit_tool_observability_event(
                LOGGER,
                "agentic_web_research",
                "request",
                query=query,
                research_goal=research_goal,
                depth=depth,
            )
            try:
                result = await run_agentic_web_research(
                    AgenticResearchRequest(
                        query=query,
                        research_goal=research_goal,
                        session_id=getattr(ctx, "session_id", None),
                        depth=depth,  # type: ignore[arg-type]
                    )
                )
            except Exception as exc:
                await ctx.info(f"Agentic web research failed: {type(exc).__name__}")
                record_mcp_tool_call("agentic_web_research", success=False)
                emit_tool_observability_event(
                    LOGGER,
                    "agentic_web_research",
                    "error",
                    query=query,
                    error_type=type(exc).__name__,
                )
                return classify_error(exc, provider="agentic_web_research").to_dict()

            # Record success only after the operation actually succeeded.
            record_mcp_tool_call("agentic_web_research", success=True)

            # Success path: rich emit (payload_json captures sources/tool_trace/kg etc. for DuckDB)
            sources_count = len(result.sources)
            tool_calls_count = len(result.tool_trace)
            uncertainties_count = len(result.uncertainties)
            emit_tool_observability_event(
                LOGGER,
                "agentic_web_research",
                "response",
                query=query,
                depth=depth,
                model=getattr(result, "model", "unknown"),
                research_goal=research_goal,
                sources_count=sources_count,
                output_count=sources_count,
                tool_calls_count=tool_calls_count,
                input_count=tool_calls_count,
                uncertainties_count=uncertainties_count,
                duration_ms=round(getattr(result, "duration_seconds", 0.0) * 1000, 3),
                duration_seconds=getattr(result, "duration_seconds", 0.0),
                run_limit=getattr(result, "run_limit", 0),
                answer_preview=(getattr(result, "answer", "") or "")[:400],
                # Full complex data lives in payload_json (views/reports already json_extract answers/sources)
                sources=[
                    s.model_dump() if hasattr(s, "model_dump") else dict(getattr(s, "__dict__", {}))
                    for s in result.sources[:3]
                ],
                tool_trace=result.tool_trace,
                knowledge_graph_summary=result.knowledge_graph_summary.model_dump()
                if hasattr(result.knowledge_graph_summary, "model_dump")
                else dict(getattr(result.knowledge_graph_summary, "__dict__", {})),
            )
            record_tool_details(
                tool_name="agentic_web_research",
                input_query_length=len(query),
                output_result_count=sources_count,
                output_content_length=len(getattr(result, "answer", "") or ""),
            )

            await ctx.info(
                f"Agentic web research completed with {sources_count} sources and "
                f"{getattr(result.knowledge_graph_summary, 'node_count', 0)} graph nodes"
            )

            # Per-run OTel attributes (tool count, depth, model) for filtering/aggregation
            try:
                current = trace.get_current_span()
                if current:
                    current.set_attribute("agent.depth", depth)
                    current.set_attribute("agent.model", getattr(result, "model", "unknown"))
                    current.set_attribute("agent.tool_calls_count", tool_calls_count)
                    current.set_attribute("agent.sources_count", sources_count)
                    current.set_attribute("agent.uncertainties_count", uncertainties_count)
                    current.set_attribute(
                        "agent.duration_seconds",
                        getattr(result, "duration_seconds", 0.0),
                    )
            except Exception:
                pass

            return result.model_dump(exclude_none=True)
