from __future__ import annotations

import asyncio
import logging
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
from ..settings import resolve_langfuse_credentials

# Telemetry reuse for records + dual OTel (must be top-level imports)
from ..telemetry import record_agentic_research
from ..utils.observability import emit_observability_event

# Langfuse (best-effort, for rich ReAct agent tracing to Langfuse while keeping Grafana/DuckDB)
try:
    from langfuse.langchain import CallbackHandler
    from langfuse import get_client as get_langfuse_client

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False

LOGGER = logging.getLogger(__name__)


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
    system_prompt = build_system_prompt(
        request, profile, current_time=datetime.now(timezone.utc)
    )
    tool_budget = ToolCallLimitMiddleware(
        run_limit=profile.run_limit,
        exit_behavior="continue",
    )
    # Build tool list (internal primitives + final_answer for citation guarantees + best-effort external MCP tools if configured)
    tools = build_agent_tools()
    if getattr(cfg, "external_mcp_config", ""):
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            import json

            raw = cfg.external_mcp_config
            if isinstance(raw, str) and raw.strip().startswith("{"):
                mcp_cfg = json.loads(raw)
            elif isinstance(raw, str):
                with open(raw, encoding="utf-8") as f:
                    mcp_cfg = json.load(f)
            else:
                mcp_cfg = raw
            mcp_client = MultiServerMCPClient(mcp_cfg)
            ext_tools = await mcp_client.get_tools()
            existing = {getattr(t, "name", "") for t in tools}
            added = 0
            for t in ext_tools:
                n = getattr(t, "name", "")
                if n and n not in existing:
                    tools.append(t)
                    added += 1
            LOGGER.info("Loaded %d external MCP tool(s) for agentic research", added)
        except Exception as exc:
            LOGGER.warning(
                "External MCP tools configured but could not be loaded (best-effort): %s",
                exc,
            )

    agent = create_agent(
        model=model,
        tools=tools,
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

    # === Langfuse hybrid (CallbackHandler for rich ReAct structure, costs, evals) ===
    # Complements OTel (to Grafana + best-effort Langfuse OTLP) + DuckDB emits.
    # Uses keys from AgenticResearchConfig (env-driven) or standard LANGFUSE_*.
    langfuse_handler = None
    invoke_config: dict[str, object] = {}
    if _LANGFUSE_AVAILABLE:
        lf_pk, lf_sk, lf_base = resolve_langfuse_credentials(
            public_key=cfg.langfuse_public_key,
            secret_key=cfg.langfuse_secret_key,
            base_url=cfg.langfuse_base_url,
            mcp_auth_header=getattr(cfg, "langfuse_mcp_auth_header", ""),
        )
        if lf_pk and lf_sk:
            try:
                langfuse_handler = CallbackHandler(
                    public_key=lf_pk,
                    secret_key=lf_sk,
                    host=lf_base,
                )
                invoke_config["callbacks"] = [langfuse_handler]
                # Propagate agentic metadata (depth, model, goal) for filtering in Langfuse
                invoke_config.setdefault("metadata", {})
                invoke_config["metadata"].update(
                    {
                        "agent.depth": request.depth,
                        "agent.model": cfg.model_name,
                        "agent.research_goal": request.research_goal or request.query,
                        "agent.run_limit": profile.run_limit,
                        "langfuse_session_id": request.session_id or "",
                        "langfuse_tags": [
                            "agentic_web_research",
                            f"depth:{request.depth}",
                            f"model:{cfg.model_name}",
                        ],
                    }
                )
                LOGGER.debug(
                    "Langfuse CallbackHandler attached for agentic_web_research"
                )
            except Exception as exc:  # pragma: no cover - best effort
                LOGGER.debug("Langfuse handler creation skipped: %s", exc)

    start = time.perf_counter()
    result = await asyncio.wait_for(
        agent.ainvoke(
            {"messages": [HumanMessage(content=user_prompt)]},
            config=invoke_config if invoke_config else None,
        ),
        timeout=profile.timeout_seconds,
    )
    duration = time.perf_counter() - start

    messages = list(result.get("messages", [])) if isinstance(result, dict) else []
    graph = ResearchKnowledgeGraph(request.query)
    graph.ingest_messages(messages)

    # Robustness: detect explicit final_answer tool call (structured output)
    # for stronger citation/source guarantees. Extraction is fallback.
    final_payload = None
    for m in reversed(messages or []):
        if getattr(m, "name", None) == "final_answer":
            try:
                content = getattr(m, "content", "")
                if isinstance(content, str) and content.strip():
                    final_payload = __import__("json").loads(content)
                break
            except Exception:
                pass

    answer = _extract_final_answer(messages)
    warnings = []
    extra: dict[str, object] = {}
    if isinstance(result, dict):
        if (
            "structured_response" in result
            and result["structured_response"] is not None
        ):
            extra["structured_response"] = result["structured_response"]
        if "messages" in result:
            extra["message_count"] = len(result["messages"])

    summary = graph.summary()
    sources_list = graph.source_records()

    # Improve extraction + low-coverage warnings (robustness)
    warnings = []
    min_sources = {"quick": 2, "normal": 3, "deep": 5}.get(request.depth, 3)
    if len(sources_list) < min_sources:
        warnings.append(
            f"Low source coverage ({len(sources_list)} sources for depth={request.depth}; "
            "consider using depth=deep, providing a more specific research_goal, or "
            "following up with targeted get_content/batch_get_content calls."
        )

    # Use explicit final_answer payload (if the agent called the tool) for
    # stronger structured guarantees; keep text extraction as fallback.
    if final_payload:
        answer = final_payload.get("answer", answer) or answer
        extra = extra or {}
        extra["final_answer_tool_payload"] = final_payload
        if final_payload.get("gaps"):
            warnings.append(f"Agent-reported gaps: {final_payload['gaps'][:200]}")

    # === Emit + record for Grafana/DuckDB (canonical agentic completion) ===
    try:
        emit_observability_event(
            LOGGER,
            "agentic.research.completed",
            tool_name="agentic_web_research",
            query=request.query,
            depth=request.depth,
            model=cfg.model_name,
            research_goal=request.research_goal,
            sources_count=len(sources_list),
            output_count=len(sources_list),
            tool_calls_count=len(_collect_tool_trace(messages)),
            uncertainties_count=len(summary.potential_conflicts),
            duration_ms=round(duration * 1000, 3),
            duration_seconds=round(duration, 3),
            run_limit=profile.run_limit,
            success=True,
            # Rich payload (full lists/json go to payload_json in DuckDB)
            answer_preview=(answer or "")[:500],
            tool_trace=_collect_tool_trace(messages),
            knowledge_graph_summary=summary.model_dump()
            if hasattr(summary, "model_dump")
            else dict(getattr(summary, "__dict__", {})),
            uncertainties=summary.potential_conflicts,
            sources=[
                s.model_dump()
                if hasattr(s, "model_dump")
                else dict(getattr(s, "__dict__", {}))
                for s in sources_list[:5]
            ],  # sample
        )
        record_agentic_research(
            depth=request.depth,
            model=cfg.model_name,
            success=True,
            sources_count=len(sources_list),
            tool_calls_count=len(_collect_tool_trace(messages)),
            uncertainties_count=len(summary.potential_conflicts),
            duration_seconds=round(duration, 3),
            run_limit=profile.run_limit,
        )
    except Exception as exc:  # pragma: no cover - best effort, never break the agent
        LOGGER.debug("agentic observability emit/record skipped: %s", exc)

    # === Langfuse post-processing scores (using result + kg signals) ===
    if langfuse_handler and _LANGFUSE_AVAILABLE:
        try:
            lf = get_langfuse_client()
            coverage = min(1.0, len(sources_list) / 5.0) if sources_list else 0.0
            lf.score_current_trace(
                name="agentic_source_coverage",
                value=coverage,
                data_type="NUMERIC",
                comment=f"depth={request.depth} model={cfg.model_name}",
            )
            if summary.potential_conflicts:
                lf.score_current_trace(
                    name="agentic_has_uncertainties",
                    value=1,
                    data_type="BOOLEAN",
                    comment=f"{len(summary.potential_conflicts)} conflicts flagged by kg",
                )
            # Optional: trace-level I/O for the whole research (answer + sources sample)
            # (handler usually captures via LC; this augments)
            lf.update_current_trace(
                output={
                    "answer_preview": (answer or "")[:300],
                    "sources_count": len(sources_list),
                },
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.debug("Langfuse post-run score skipped: %s", exc)

    return AgenticResearchResult(
        query=request.query,
        research_goal=request.research_goal,
        depth=request.depth,
        model=cfg.model_name,
        answer=answer,
        sources=sources_list,
        uncertainties=summary.potential_conflicts,
        tool_trace=_collect_tool_trace(messages),
        knowledge_graph_summary=summary,
        run_limit=profile.run_limit,
        duration_seconds=round(duration, 3),
        warnings=warnings,
        extra=extra or None,
    )
