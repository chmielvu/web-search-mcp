from __future__ import annotations

import httpx
import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.providers.base import (
    ProviderRequestError,
    get_provider_request_metadata,
    run_provider,
)


@pytest.mark.asyncio
async def test_run_provider_resets_error_metadata_between_calls() -> None:
    responses = [
        httpx.Response(500, json={"error": "temporary"}),
        httpx.Response(200, json={"ok": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async def request(client: httpx.AsyncClient) -> dict[str, bool]:
        response = await client.get("https://provider.example/search")
        response.raise_for_status()
        return response.json()

    def parse_response(payload: dict[str, bool]) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                title="successful result",
                link="https://example.com/result",
                snippet=str(payload["ok"]),
            )
        ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError):
            await run_provider(
                "metadata-probe",
                "query",
                1,
                request=request,
                parse_response=parse_response,
                http_client=client,
            )

        results = await run_provider(
            "metadata-probe",
            "query",
            1,
            request=request,
            parse_response=parse_response,
            http_client=client,
        )

    metadata = get_provider_request_metadata()
    assert len(results) == 1
    assert metadata is not None
    assert metadata.result_class == "nonempty"
    assert metadata.endpoint is None
    assert metadata.http_status is None
    assert metadata.error_type is None
    assert metadata.error_summary is None
