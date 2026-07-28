from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from kindly_web_search_mcp_server.search.providers import gemma_serp


@pytest.mark.asyncio
async def test_search_gemma_uses_pollinations_json_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLLINATIONS_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-2.5-flash-lite",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "results": [
                                        {
                                            "url": "https://example.com/one",
                                            "title": "One",
                                            "snippet": "First result.",
                                        },
                                        {
                                            "url": "https://example.com/two",
                                            "title": "Two",
                                            "snippet": "Second result.",
                                        },
                                    ]
                                }
                            ),
                        }
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await gemma_serp.search_gemma(
            "pollinations chat completions",
            num_results=2,
            arguments={
                "queries": ["Pollinations API", "Pollinations chat completions"],
                "research_goal": "Find the current official API contract.",
            },
            http_client=client,
        )

    assert len(results) == 2
    assert results[0].providers == ["gemma"]
    assert results[0].diagnostics[0]["model"] == "google/gemini-2.5-flash-lite"
    assert results[0].diagnostics[0]["grounding"] is True
    assert results[0].diagnostics[0]["grounding_method"] == "native_web_search"

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert requests[0].url == gemma_serp.POLLINATIONS_CHAT_COMPLETIONS_URL
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert payload["model"] == "gemini-fast"
    assert payload["tools"] == [{"type": "google_search"}]
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 4096
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "no browser" not in payload["messages"][0]["content"].lower()
    assert "<query decomposition>" in payload["messages"][0]["content"]
    assert "Use grounding with Google Search" in payload["messages"][0]["content"]
    assert date.today().isoformat() in payload["messages"][0]["content"]
    assert "<query>" in payload["messages"][1]["content"]
    assert (
        '["Pollinations API", "Pollinations chat completions"]' in payload["messages"][1]["content"]
    )
    assert "Find the current official API contract." in payload["messages"][1]["content"]
    assert "seed queries for the same focused topic" in payload["messages"][1]["content"]
    assert "intended research outcome" in payload["messages"][1]["content"]


def test_parse_response_rejects_non_search_urls() -> None:
    data = {
        "choices": [
            {"message": {"content": '{"results":[{"url":"javascript:alert(1)","title":"bad"}]}'}}
        ]
    }

    assert gemma_serp._parse_response(data) == []


@pytest.mark.asyncio
async def test_search_gemma_requires_pollinations_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)

    assert await gemma_serp.search_gemma("query", num_results=5) == []
