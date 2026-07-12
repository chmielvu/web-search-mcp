from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from kindly_web_search_mcp_server.models import WebSearchResponse
from kindly_web_search_mcp_server.search.contracts import WebSearchRequest
from kindly_web_search_mcp_server.search.service import execute_web_search


@pytest.mark.asyncio
async def test_service_submits_one_success_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    response = WebSearchResponse(query="query")
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.service.run_search_core",
        AsyncMock(return_value=response),
    )
    submitted: list[object] = []
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.service.submit_search_outcome",
        submitted.append,
    )
    async with httpx.AsyncClient() as client:
        result = await execute_web_search(
            WebSearchRequest(query="query", research_goal="goal"),
            http_client=client,
            run_key="run",
        )
    assert result is response
    assert len(submitted) == 1
    assert getattr(submitted[0], "status") == "success"


@pytest.mark.asyncio
async def test_service_reraises_cancellation_and_submits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.service.run_search_core",
        AsyncMock(side_effect=__import__("asyncio").CancelledError),
    )
    submitted: list[object] = []
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.search.service.submit_search_outcome",
        submitted.append,
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(__import__("asyncio").CancelledError):
            await execute_web_search(
                WebSearchRequest(query="query", research_goal="goal"),
                http_client=client,
                run_key="run",
            )
    assert len(submitted) == 1
    assert getattr(submitted[0], "status") == "cancelled"
    assert not hasattr(submitted[0], "http_client")
