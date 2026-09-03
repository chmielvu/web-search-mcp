from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindly_web_search_mcp_server.cli.services.search_web import fetch_web_search_payload
from kindly_web_search_mcp_server.search.contracts import WebSearchRequest
from kindly_web_search_mcp_server.tools.search import web_search


class TestMultiQueryAndRerankingInstructions(unittest.IsolatedAsyncioTestCase):
    async def test_tool_web_search_accepts_queries_and_reranking_instructions(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "query": "q1",
            "results": [],
            "providers_used": ["ddg"],
        }

        with (
            patch(
                "kindly_web_search_mcp_server.search.service.execute_web_search",
                return_value=mock_response,
            ) as mock_exec,
            patch("kindly_web_search_mcp_server.utils.http_client.get_http_client"),
        ):
            ctx = AsyncMock()
            result = await web_search(
                queries=["q1", "q2", "q3", "q4", "q5"],  # >4 queries sliced to 4
                research_goal="goal",
                reranking_instructions="Demote listicles",
                ctx=ctx,
            )
            assert result is not None

            mock_exec.assert_called_once()
            request: WebSearchRequest = mock_exec.call_args.args[0]
            assert request.query == "q1"
            assert request.queries == ("q1", "q2", "q3", "q4")
            assert request.research_goal == "goal"
            assert request.reranking_instructions == "Demote listicles"

    async def test_tool_web_search_validation_errors(self) -> None:
        from fastmcp.exceptions import ToolError

        ctx = AsyncMock()
        with pytest.raises((ValueError, ToolError), match="Either query or queries must be provided"):
            await web_search(query="", queries=[], research_goal="goal", ctx=ctx)

        with pytest.raises((ValueError, ToolError), match="queries must contain at least one non-blank string"):
            await web_search(queries=["  ", ""], research_goal="goal", ctx=ctx)
    async def test_cli_fetch_web_search_payload_multi_query(self) -> None:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"query": "q1", "results": []}
        mock_run = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.cli.services.search_web.execute_web_search",
            return_value=(mock_response, mock_run),
        ) as mock_exec:
            payload = await fetch_web_search_payload(
                query=["query A", "query B"],
                rewrite=True,
                research_goal="test goal",
                reranking_instructions="Prioritize official docs",
            )
            assert payload is not None

            mock_exec.assert_called_once()
            request: WebSearchRequest = mock_exec.call_args.args[0]
            assert request.query == "query A"
            assert request.queries == ("query A", "query B")
            assert request.reranking_instructions == "Prioritize official docs"
