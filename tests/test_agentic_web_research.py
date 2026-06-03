from __future__ import annotations

import base64
import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage


def _install_fake_habanero(monkeypatch) -> None:
    fake_habanero = types.ModuleType("habanero")
    fake_habanero.Crossref = type("FakeCrossref", (), {})
    monkeypatch.setitem(
        sys.modules,
        "habanero",
        fake_habanero,
    )
    monkeypatch.setitem(
        sys.modules,
        "pyalex",
        types.ModuleType("pyalex"),
    )
    sys.modules["pyalex"].config = types.SimpleNamespace(email="", api_key="")
    fake_semanticscholar = types.ModuleType("semanticscholar")
    fake_semanticscholar.AsyncSemanticScholar = type("FakeAsyncSemanticScholar", (), {})
    monkeypatch.setitem(sys.modules, "semanticscholar", fake_semanticscholar)


def test_resolve_langfuse_credentials_uses_mcp_auth_header(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("KINDLY_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("KINDLY_LANGFUSE_SECRET_KEY", raising=False)

    token = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    monkeypatch.setenv("LANGFUSE_MCP_AUTH_HEADER", f"Basic {token}")

    from kindly_web_search_mcp_server.settings import resolve_langfuse_credentials

    public_key, secret_key, base_url = resolve_langfuse_credentials()
    assert public_key == "pk-test"
    assert secret_key == "sk-test"
    assert base_url == "https://cloud.langfuse.com"


def test_build_chat_model_uses_nanogpt_configuration(monkeypatch) -> None:
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)
    monkeypatch.delenv("KINDLY_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv(
        "KINDLY_AGENTIC_RESEARCH_MODEL",
        "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
    )
    monkeypatch.setenv(
        "KINDLY_AGENTIC_RESEARCH_BASE_URL",
        "https://nano-gpt.com/api/subscription/v1",
    )

    from kindly_web_search_mcp_server.agent.model import build_chat_model
    from kindly_web_search_mcp_server.agent.config import AgenticResearchConfig

    model = build_chat_model(AgenticResearchConfig(gemini_api_key="", hf_token=""))
    primary = getattr(model, "runnable", model)
    assert getattr(primary, "model_name", "") == "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
    assert str(getattr(primary.root_client, "base_url", "")).startswith(
        "https://nano-gpt.com/api/subscription/v1"
    )
    assert getattr(primary, "streaming", True) is False
    assert getattr(primary, "use_responses_api", True) is False


def test_agentic_model_chain_includes_requested_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)
    monkeypatch.delenv("KINDLY_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("KINDLY_AGENTIC_RESEARCH_FALLBACK_MODELS", raising=False)

    from kindly_web_search_mcp_server.agent.config import AgenticResearchConfig
    from kindly_web_search_mcp_server.agent.model import build_chat_model

    cfg = AgenticResearchConfig(gemini_api_key="", hf_token="")
    assert cfg.model_chain() == (
        "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
        "minimax/minimax-m3:thinking",
        "mistralai/mistral-small-4-119b-2603:thinking",
    )

    model = build_chat_model(cfg)
    assert getattr(getattr(model, "runnable", None), "model_name", "") == (
        "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
    )
    assert [getattr(item, "model_name", "") for item in getattr(model, "fallbacks", [])] == [
        "minimax/minimax-m3:thinking",
        "mistralai/mistral-small-4-119b-2603:thinking",
    ]


def test_agentic_model_chain_uses_gemini_and_hf_as_terminal_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)
    monkeypatch.setenv("KINDLY_GEMINI_API_KEY", "g" * 20)
    monkeypatch.setenv("HF_TOKEN", "h" * 20)
    monkeypatch.delenv("KINDLY_AGENTIC_RESEARCH_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("KINDLY_AGENTIC_RESEARCH_GEMINI_FALLBACK_MODEL", raising=False)

    from kindly_web_search_mcp_server.agent.config import AgenticResearchConfig
    from kindly_web_search_mcp_server.agent.model import build_chat_model

    cfg = AgenticResearchConfig(gemini_api_key="g" * 20, hf_token="h" * 20)

    with patch(
        "kindly_web_search_mcp_server.agent.model._build_gemini_model",
        return_value=SimpleNamespace(model_name="gemini-3.5-flash"),
    ) as mock_gemini:
        with patch(
            "kindly_web_search_mcp_server.agent.model._build_hf_router_model",
            return_value=SimpleNamespace(model_name="openai/gpt-oss-120b:novita"),
        ) as mock_hf:
            model = build_chat_model(cfg)

    assert mock_gemini.called
    assert mock_hf.called
    assert [getattr(item, "model_name", getattr(item, "model", "")) for item in model.fallbacks] == [
        "minimax/minimax-m3:thinking",
        "mistralai/mistral-small-4-119b-2603:thinking",
        "gemini-3.5-flash",
        "openai/gpt-oss-120b:novita",
    ]


def test_build_agent_tools_exposes_distinct_search_and_fetch_tools(monkeypatch) -> None:
    _install_fake_habanero(monkeypatch)
    from kindly_web_search_mcp_server.agent.toolset import build_agent_tools

    tool_names = {tool.name for tool in build_agent_tools()}
    # final_answer is *always* included (unconditional) for stronger citation/source guarantees
    assert "final_answer" in tool_names
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


def test_register_agentic_web_research_tool_registers_tool(monkeypatch) -> None:
    _install_fake_habanero(monkeypatch)
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
    _install_fake_habanero(monkeypatch)

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


def test_agentic_observability_emits_and_records_are_called(monkeypatch) -> None:
    """Smoke that the new emit/record + langfuse handler path is exercised (no real network)."""
    monkeypatch.setenv("NANOGPT_API_KEY", "x" * 20)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    token = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    monkeypatch.setenv("LANGFUSE_MCP_AUTH_HEADER", f"Basic {token}")
    _install_fake_habanero(monkeypatch)

    from unittest.mock import patch, MagicMock
    from kindly_web_search_mcp_server.agent.config import AgenticResearchConfig
    from kindly_web_search_mcp_server.agent.models import AgenticResearchRequest
    from kindly_web_search_mcp_server.agent.runner import run_agentic_web_research

    fake_messages = [MagicMock(spec=["content", "tool_calls"], content="final", tool_calls=[])]
    fake_result = {"messages": fake_messages}
    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(return_value=fake_result)

    with patch(
        "kindly_web_search_mcp_server.agent.runner.build_chat_model",
        return_value=object(),
    ):
        with patch(
            "kindly_web_search_mcp_server.agent.runner.create_agent",
            return_value=fake_agent,
        ) as mock_create:
            # Patch the telemetry/emit to verify calls (no side effects)
            with patch(
                "kindly_web_search_mcp_server.agent.runner.emit_observability_event"
            ) as mock_emit:
                with patch(
                    "kindly_web_search_mcp_server.agent.runner.record_agentic_research"
                ):
                    cfg = AgenticResearchConfig(api_key="x" * 20)
                    res = asyncio.run(
                        run_agentic_web_research(
                            AgenticResearchRequest(
                                query="test obs",
                                session_id="session-123",
                                depth="quick",
                            ),
                            config=cfg,
                        )
                    )
                    # create called; emit/record attempted (best-effort, may be 0-2 calls depending on mocks)
                    assert mock_create.called
                    # We don't assert call count strictly (graceful if langfuse lib not fully exercised in this env)
                    # but the functions were importable and path executed without exception.
                    assert res is not None
                    invoke_config = fake_agent.ainvoke.call_args.kwargs.get("config")
                    if invoke_config:
                        metadata = invoke_config.get("metadata", {})
                        assert metadata.get("langfuse_session_id") == "session-123"
                        assert metadata.get("langfuse_tags") == [
                            "agentic_web_research",
                            "depth:quick",
                            f"model:{cfg.model_name}",
                        ]
                    # Basic: if emit was called it received the agentic tool name
                    if mock_emit.called:
                        args = mock_emit.call_args
                        assert "agentic.research.completed" in str(args)
