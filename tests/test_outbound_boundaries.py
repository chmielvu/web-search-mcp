from __future__ import annotations


import httpx
import pytest

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
