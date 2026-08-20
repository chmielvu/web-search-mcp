from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

from kindly_web_search_mcp_server.models import WebSearchResponse, WebSearchResult
from kindly_web_search_mcp_server.server import mcp


@pytest.mark.asyncio
async def test_web_search_invoked_through_server_mcp_succeeds_without_type_error():
    """Verify web_search called through the server instance with full middleware stack succeeds."""
    mock_response = WebSearchResponse(
        results=[
            WebSearchResult(
                title="Python Documentation",
                link="https://docs.python.org",
                snippet="Official documentation for Python.",
            )
        ],
        total_results=1,
        providers_used=["searxng"],
        query="python documentation",
    )

    with patch(
        "kindly_web_search_mcp_server.search.service.execute_web_search",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.return_value = mock_response

        async with Client(mcp) as client:
            result = await client.call_tool("web_search", {"query": "python documentation"})

            assert result is not None
            assert hasattr(result, "content") or hasattr(result, "structured_content")
            mock_exec.assert_awaited_once()
