from __future__ import annotations

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from mcp.types import ToolAnnotations

from kindly_web_search_mcp_server.errors import classify_error

from .models import AgenticResearchRequest
from .runner import run_agentic_web_research


def register_agentic_web_research_tools(mcp: object) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Agentic Web Research",
            readOnlyHint=True,
            idempotentHint=False,
            openWorldHint=True,
        )
    )
    async def agentic_web_research(
        query: str,
        research_goal: str | None = None,
        depth: str = "normal",
        ctx: Context = CurrentContext(),
    ) -> dict:
        """LangChain/LangGraph ReAct research agent.

        Use this when you want the model to choose among the dedicated search,
        fetch, rerank, and expansion tools directly instead of calling the legacy
        full `web_search` pipeline.
        """
        await ctx.info(f"Agentic web research started: {query[:120]}")
        try:
            result = await run_agentic_web_research(
                AgenticResearchRequest(
                    query=query,
                    research_goal=research_goal,
                    depth=depth,  # type: ignore[arg-type]
                )
            )
        except Exception as exc:
            await ctx.info(f"Agentic web research failed: {type(exc).__name__}")
            return classify_error(exc, provider="agentic_web_research").to_dict()

        await ctx.info(
            f"Agentic web research completed with {len(result.sources)} sources and "
            f"{result.knowledge_graph_summary.node_count} graph nodes"
        )
        return result.model_dump(exclude_none=True)
