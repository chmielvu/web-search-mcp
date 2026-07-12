from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from kindly_web_search_mcp_server.llm.models import LLMEndpoint
from kindly_web_search_mcp_server.llm.router import LLMRouter
from kindly_web_search_mcp_server.utils.http_client import OutboundCallError, request_json


@pytest.mark.asyncio
async def test_request_json_rejects_non_object_without_leaking_query() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=["wrong"], request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OutboundCallError) as captured:
            await request_json(
                client,
                "GET",
                "https://example.test/path?api_key=secret&q=private",
                provider="example",
            )
    error = captured.value
    assert error.category == "invalid_shape"
    assert error.host == "example.test"
    assert error.path == "/path"
    assert "secret" not in str(error)
    assert "private" not in str(error)


@pytest.mark.asyncio
async def test_router_preserves_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(
        content="",
        annotations=[{"type": "url_citation", "url": "https://example.test"}],
        provider_specific_fields={"grounding": {"chunks": [1]}},
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
    )

    async def fake_completion(**kwargs: object) -> object:
        assert kwargs["tools"] == [{"googleSearch": {}}]
        return response

    monkeypatch.setattr("kindly_web_search_mcp_server.llm.router.acompletion", fake_completion)
    router = LLMRouter(
        (
            LLMEndpoint(
                name="test",
                model="model",
                base_url="https://example.test",
                api_key="secret",
                timeout_seconds=1,
            ),
        )
    )
    generation = await router.complete_text(
        messages=[{"role": "user", "content": "query"}],
        tools=[{"googleSearch": {}}],
    )
    assert generation.annotations
    assert generation.provider_specific_fields == {"grounding": {"chunks": [1]}}
