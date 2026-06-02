from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from .config import AgenticResearchConfig, depth_profile_for
from .knowledge_graph import ResearchKnowledgeGraph
from .model import build_chat_model
from .models import AgenticResearchRequest, AgenticResearchResult
from .prompts import build_system_prompt
from .toolset import build_agent_tools


def _extract_final_answer(messages: list[object]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            return str(content).strip()
    return ""


def _collect_tool_trace(messages: list[object]) -> list[str]:
    trace: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in getattr(message, "tool_calls", []) or []:
                name = tool_call.get("name")
                if isinstance(name, str) and name.strip() and name not in seen:
                    seen.add(name)
                    trace.append(name)
        else:
            name = getattr(message, "name", None)
            if isinstance(name, str) and name.strip() and name not in seen:
                seen.add(name)
                trace.append(name)
    return trace


async def run_agentic_web_research(
    request: AgenticResearchRequest,
    *,
    config: AgenticResearchConfig | None = None,
) -> AgenticResearchResult:
    cfg = config or AgenticResearchConfig()
    profile = depth_profile_for(request.depth)
    model = build_chat_model(cfg)
    system_prompt = build_system_prompt(request, profile, current_time=datetime.now(timezone.utc))
    tool_budget = ToolCallLimitMiddleware(
        run_limit=profile.run_limit,
        exit_behavior="continue",
    )
    agent = create_agent(
        model=model,
        tools=build_agent_tools(),
        system_prompt=system_prompt,
        middleware=[tool_budget],
        name="agentic_web_research",
    )

    user_prompt = (
        f"Research question: {request.query}\n"
        f"Research goal: {request.research_goal or request.query}\n"
        f"Depth: {request.depth}\n"
        "Use the available tools to collect evidence, rerank when needed, and then answer."
    )

    start = time.perf_counter()
    result = await asyncio.wait_for(
        agent.ainvoke({"messages": [HumanMessage(content=user_prompt)]}),
        timeout=profile.timeout_seconds,
    )
    duration = time.perf_counter() - start

    messages = list(result.get("messages", [])) if isinstance(result, dict) else []
    graph = ResearchKnowledgeGraph(request.query)
    graph.ingest_messages(messages)

    answer = _extract_final_answer(messages)
    warnings = []
    extra: dict[str, object] = {}
    if isinstance(result, dict):
        if "structured_response" in result and result["structured_response"] is not None:
            extra["structured_response"] = result["structured_response"]
        if "messages" in result:
            extra["message_count"] = len(result["messages"])

    summary = graph.summary()
    return AgenticResearchResult(
        query=request.query,
        research_goal=request.research_goal,
        depth=request.depth,
        model=cfg.model_name,
        answer=answer,
        sources=graph.source_records(),
        uncertainties=summary.potential_conflicts,
        tool_trace=_collect_tool_trace(messages),
        knowledge_graph_summary=summary,
        run_limit=profile.run_limit,
        duration_seconds=round(duration, 3),
        warnings=warnings,
        extra=extra or None,
    )
