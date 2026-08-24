"""Contract tests for the Brave LLM Context provider."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from kindly_web_search_mcp_server.search.providers.brave import search_brave
from kindly_web_search_mcp_server.search.providers.brave_common import (
    BRAVE_LLM_CONTEXT_URL,
    BraveConfigError,
)


def _run(coro):
    return asyncio.run(coro)


def _mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def test_search_brave_llm_context_parses_grounding(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.delenv("BRAVE_SUGGEST_API_KEY", raising=False)

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        assert request.url.path.endswith("/res/v1/llm/context")
        assert request.url.params.get("freshness") == "pw"
        assert request.url.params.get("goggles") == "https://example.com/goggle"
        return httpx.Response(
            200,
            json={
                "grounding": {
                    "generic": [
                        {
                            "source": "https://docs.example.com/a",
                            "title": "Doc A",
                            "snippet": "Snippet A",
                        },
                        {"source": "", "title": "skip"},
                        {
                            "source": "https://docs.example.com/b",
                            "content": "Body B",
                        },
                    ]
                }
            },
        )

    async def run() -> None:
        client = _mock_client(handler)
        results = await search_brave(
            "openai news",
            num_results=5,
            freshness="week",
            goggles=["https://example.com/goggle"],
            http_client=client,
        )
        await client.aclose()
        assert len(results) == 2
        assert results[0].link == "https://docs.example.com/a"
        assert results[0].title == "Doc A"
        assert results[0].snippet == "Snippet A"
        assert results[1].snippet == "Body B"
        assert captured["url"].startswith(BRAVE_LLM_CONTEXT_URL)
        assert captured["headers"]["x-subscription-token"] == "test-brave-key"

    _run(run())


def test_search_brave_requires_standard_api_key(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_SUGGEST_API_KEY", "suggest-only")
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.providers.brave_common.settings.brave_api_key",
        "",
    )

    async def run() -> None:
        with pytest.raises(BraveConfigError):
            await search_brave("test", num_results=3)

    _run(run())


