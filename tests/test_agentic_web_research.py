from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage


def test_build_chat_model_uses_nanogpt_configuration(monkeypatch) -> None:
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)
    monkeypatch.setenv(
        "KINDLY_AGENTIC_RESEARCH_MODEL",
        "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
    )
    monkeypatch.setenv(
        "KINDLY_AGENTIC_RESEARCH_BASE_URL",
        "https://nano-gpt.com/api/subscription/v1",
    )

    from kindly_web_search_mcp_server.agent.model import build_chat_model

    model = build_chat_model()
    assert getattr(model, "model_name", "") == "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
    assert str(getattr(model.root_client, "base_url", "")).startswith(
        "https://nano-gpt.com/api/subscription/v1"
    )
    assert getattr(model, "streaming", False) is True
    assert getattr(model, "use_responses_api", True) is False


def test_build_agent_tools_exposes_distinct_search_and_fetch_tools() -> None:
    from kindly_web_search_mcp_server.agent.toolset import build_agent_tools

    tool_names = {tool.name for tool in build_agent_tools()}
    assert {
        "composio_web_search",
        "search_tavily",
        "search_brave",
        "search_duckduckgo",
        "composio_similarlinks",
        "composio_image_search",
        "get_content",
        "batch_get_content",
        "discover_links",
        "academic_search",
        "rerank_candidates",
    }.issubset(tool_names)


def test_system_prompt_mentions_agentic_policy() -> None:
    from kindly_web_search_mcp_server.agent.config import depth_profile_for
    from kindly_web_search_mcp_server.agent.models import AgenticResearchRequest
    from kindly_web_search_mcp_server.agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        AgenticResearchRequest(query="How does LangGraph ReAct work?", depth="deep"),
        depth_profile_for("deep"),
    )

    assert "Tool budget" in prompt
    assert "composio_web_search" in prompt
    assert "search_tavily" in prompt
    assert "search_brave" in prompt
    assert "rerank_candidates" in prompt
    assert "academic_search" in prompt
    assert "legacy full `web_search` pipeline" in prompt


def test_register_agentic_web_research_tool_registers_tool() -> None:
    from kindly_web_search_mcp_server.agent.mcp import register_agentic_web_research_tools

    class DummyMCP:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def tool(self, *args: object, **kwargs: object):  # noqa: ARG002
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorator

    mcp = DummyMCP()
    register_agentic_web_research_tools(mcp)
    assert "agentic_web_research" in mcp.tools
    assert "LangChain/LangGraph ReAct" in mcp.tools["agentic_web_research"].__doc__


def test_run_agentic_web_research_extracts_sources_and_tool_trace(monkeypatch) -> None:
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)

    from kindly_web_search_mcp_server.agent.config import AgenticResearchConfig
    from kindly_web_search_mcp_server.agent.models import AgenticResearchRequest
    from kindly_web_search_mcp_server.agent.runner import run_agentic_web_research

    tool_call_id = "call_1"
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_duckduckgo",
                    "args": {"query": "langgraph react"},
                    "id": tool_call_id,
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "tool": "search_duckduckgo",
                    "query": "langgraph react",
                    "provider": "ddg",
                    "results": [
                        {
                            "title": "LangGraph docs",
                            "link": "https://example.com/langgraph",
                            "snippet": "LangGraph ReAct...",
                        }
                    ],
                }
            ),
            name="search_duckduckgo",
            tool_call_id=tool_call_id,
        ),
        AIMessage(content="Final answer with https://example.com/langgraph"),
    ]

    fake_result = {"messages": messages}
    fake_agent = SimpleNamespace(ainvoke=AsyncMock(return_value=fake_result))

    with patch(
        "kindly_web_search_mcp_server.agent.runner.build_chat_model",
        return_value=object(),
    ) as mock_model:
        with patch(
            "kindly_web_search_mcp_server.agent.runner.create_agent",
            return_value=fake_agent,
        ) as mock_agent:
            result = asyncio.run(
                run_agentic_web_research(
                    AgenticResearchRequest(
                        query="How does LangGraph ReAct work?",
                        research_goal="Validate the agent loop",
                        depth="normal",
                    ),
                    config=AgenticResearchConfig(api_key="x" * 20),
                )
            )

    assert mock_model.called
    assert mock_agent.called
    assert result.answer == "Final answer with https://example.com/langgraph"
    assert result.tool_trace == ["search_duckduckgo"]
    assert result.sources[0].url == "https://example.com/langgraph"
    assert result.knowledge_graph_summary.url_count == 1
    assert result.knowledge_graph_summary.source_urls == [
        "https://example.com/langgraph"
    ]
    assert result.knowledge_graph_summary.tool_calls["search_duckduckgo"] == 1
    assert result.uncertainties == []
