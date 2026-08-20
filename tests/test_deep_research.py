"""Tests for the deep_research MCP tool (self-hosted node-DeepResearch engine)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kindly_web_search_mcp_server.deep_research import (
    DEPTH_ALIASES,
    RESEARCH_PRESETS,
    _build_report_markdown,
    _consume_sse_stream,
    _resolve_preset_key,
    deep_research,
)


# ── Preset resolution ──────────────────────────────────────────────────────


def test_resolve_preset_key_defaults_to_standard() -> None:
    assert _resolve_preset_key(None) == "standard"
    assert _resolve_preset_key("") == "standard"
    assert _resolve_preset_key("  ") == "standard"


def test_resolve_preset_key_accepts_canonical_names() -> None:
    assert _resolve_preset_key("quick") == "quick"
    assert _resolve_preset_key("STANDARD") == "standard"
    assert _resolve_preset_key(" Deep ") == "deep"


def test_resolve_preset_key_repairs_synonyms() -> None:
    for alias, canonical in DEPTH_ALIASES.items():
        assert _resolve_preset_key(alias) == canonical


def test_resolve_preset_key_unknown_falls_back_to_standard() -> None:
    assert _resolve_preset_key("ultra-mega") == "standard"


def test_presets_match_omp_extension_contract() -> None:
    assert RESEARCH_PRESETS["quick"]["team_size"] == 1
    assert RESEARCH_PRESETS["quick"]["token_budget"] == 50_000
    assert RESEARCH_PRESETS["standard"]["team_size"] == 2
    assert RESEARCH_PRESETS["standard"]["token_budget"] == 300_000
    assert RESEARCH_PRESETS["deep"]["team_size"] == 3
    assert RESEARCH_PRESETS["deep"]["token_budget"] == 1_000_000


# ── SSE stream parsing ─────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal httpx.Response stand-in for _consume_sse_stream."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _sse_block(event: str, payload: dict) -> list[str]:
    return [f"event: {event}", f"data: {json.dumps(payload)}", ""]


async def test_consume_sse_stream_returns_final_result() -> None:
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    final_payload = {
        "answer": "The answer",
        "references": [{"url": "https://example.com", "title": "Example"}],
        "visitedURLs": ["https://example.com"],
        "readURLs": [],
        "allURLs": ["https://example.com"],
        "usage": {"total_tokens": 1234},
        "isFinal": True,
    }
    lines = (
        _sse_block("action", {"type": "search", "query": "test query"})
        + _sse_block("action", {"type": "visit", "url": "https://example.com"})
        + _sse_block("result", final_payload)
    )
    result = await _consume_sse_stream(_FakeResponse(lines), preset_key="standard", ctx=ctx)
    assert result == final_payload
    assert ctx.report_progress.await_count == 2
    assert ctx.info.await_count == 2


async def test_consume_sse_stream_raises_on_error_event() -> None:
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    lines = _sse_block("error", {"error": "engine exploded"})
    with pytest.raises(Exception, match="engine exploded"):
        await _consume_sse_stream(_FakeResponse(lines), preset_key="quick", ctx=ctx)


async def test_consume_sse_stream_raises_when_no_final_result() -> None:
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    lines = _sse_block("action", {"type": "search", "query": "q"})
    with pytest.raises(Exception, match="Stream closed"):
        await _consume_sse_stream(_FakeResponse(lines), preset_key="quick", ctx=ctx)


# ── Report rendering ───────────────────────────────────────────────────────


def test_build_report_markdown_contains_sections() -> None:
    final = {
        "answer": "Synthesis",
        "references": [
            {"url": "https://a.example", "title": "A", "snippet": "snippet a"},
            {"url": "https://b.example", "title": None, "snippet": None},
        ],
        "visitedURLs": ["https://a.example", "https://b.example"],
        "allURLs": ["https://a.example", "https://b.example"],
        "usage": {"total_tokens": 5000},
    }
    md = _build_report_markdown(
        query="q",
        preset_key="standard",
        team_size=2,
        token_budget=300_000,
        final=final,
    )
    assert "# Deep Research Report: q" in md
    assert "Preset:* `STANDARD`" in md
    assert "Synthesis" in md
    assert "[A](https://a.example)" in md
    assert "[https://b.example](https://b.example)" in md
    assert "**URLs Visited:** 2" in md
    assert "**Total Tokens Used:** 5000" in md


# ── Tool-level behavior ───────────────────────────────────────────────────


async def test_deep_research_rejects_empty_query() -> None:
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    with pytest.raises(Exception, match="cannot be empty"):
        await deep_research(query="", ctx=ctx)


async def test_deep_research_uses_alias_parameters(monkeypatch) -> None:
    """question/topic/prompt aliases feed the query (mirrors OMP extension)."""
    from kindly_web_search_mcp_server import deep_research as dr

    captured: dict[str, Any] = {}

    class _FakeStream:
        def __init__(self, status_code: int, body: bytes) -> None:
            self.status_code = status_code
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self) -> bytes:
            return self._body

    class _FakeClient:
        def stream(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _FakeStream(500, b"internal error")

    async def _fake_get_client():
        return _FakeClient()

    monkeypatch.setattr(dr, "get_http_client", _fake_get_client)
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    with pytest.raises(Exception, match="HTTP 500"):
        await deep_research(question="alias query", ctx=ctx)
    # The alias value must flow into the request payload as the engine query.
    assert captured["kwargs"]["json"]["query"] == "alias query"


async def test_deep_research_http_error_raises_tool_error(monkeypatch) -> None:
    """Non-200 responses surface as ToolError with status details."""
    from kindly_web_search_mcp_server import deep_research as dr

    class _FakeStream:
        def __init__(self, status_code: int, body: bytes) -> None:
            self.status_code = status_code
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self) -> bytes:
            return self._body

    class _FakeClient:
        def stream(self, *args, **kwargs):
            return _FakeStream(500, b"internal error")

    async def _fake_get_client():
        return _FakeClient()

    monkeypatch.setattr(dr, "get_http_client", _fake_get_client)
    ctx = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    with pytest.raises(Exception, match="HTTP 500"):
        await deep_research(query="q", ctx=ctx)


# ── Registration ──────────────────────────────────────────────────────────


def test_register_deep_research_sets_optional_task_config() -> None:
    from kindly_web_search_mcp_server.deep_research import register_deep_research

    class _DummyMCP:
        def __init__(self) -> None:
            self.kwargs: dict = {}
            self.fn = None

        def tool(self, **kwargs):
            self.kwargs = kwargs

            def _decorator(fn):
                self.fn = fn
                return fn

            return _decorator

    mcp = _DummyMCP()
    register_deep_research(mcp)
    assert mcp.fn is deep_research
    assert mcp.kwargs["task"].mode == "optional"
    assert mcp.kwargs["tags"] is not None
    assert "tool:deep_research" in mcp.kwargs["tags"]
