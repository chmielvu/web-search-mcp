"""Tests for search orchestrator: rewrite → merge → rerank.

Simplified system: bypass (2x results) or expand (3x results).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.query_policy import RewritePolicy
from kindly_web_search_mcp_server.search.query_rewrite import (
    QueryRewritePlan,
    QueryVariant,
)


def test_run_web_search_rewrites_merges_and_reranks() -> None:
    """Orchestrator should rewrite, search parallel, merge, and rerank."""
    from kindly_web_search_mcp_server.search.orchestrator import run_web_search

    rewrite_plan = QueryRewritePlan(
        original_query="langchain agent react",
        policy=RewritePolicy(mode="expand", reason="Query can benefit from expansion."),
        variants=[
            QueryVariant(
                kind="original", query="langchain agent react", why="original"
            ),
            QueryVariant(
                kind="official_docs", query="langchain docs react", why="docs"
            ),
        ],
        final_queries=["langchain agent react", "langchain docs react"],
    )

    query_results = [
        [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                providers=["searxng"],
            ),
        ],
        [
            WebSearchResult(
                title="B",
                link="https://example.com/b",
                snippet="snippet b",
                providers=["searxng"],
            ),
        ],
    ]

    async def _run() -> None:
        with (
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query",
                new_callable=AsyncMock,
            ) as mock_rewrite,
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.search_single_query",
                new_callable=AsyncMock,
            ) as mock_single,
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.rerank_results",
                new_callable=AsyncMock,
            ) as mock_rerank,
        ):
            mock_rewrite.return_value = rewrite_plan
            mock_single.side_effect = query_results
            mock_rerank.side_effect = lambda _query, candidates, top_k: candidates[
                :top_k
            ]

            response = await run_web_search(
                "langchain agent react", num_results=1, rewrite=True
            )

        assert response.query == "langchain agent react"
        assert len(response.results) == 1
        assert response.results[0].title == "A"
        assert mock_rewrite.awaited
        assert mock_single.await_count == 2
        # expand mode: max(3x num_results, 9) = 9 per query (min floor)
        assert mock_single.await_args_list[0].kwargs["num_results"] == 9
        assert mock_rerank.await_count == 1

    asyncio.run(_run())


def test_run_web_search_bypass_mode_fetches_2x_results() -> None:
    """Bypass mode should fetch 2x results per query."""
    from kindly_web_search_mcp_server.search.orchestrator import run_web_search

    rewrite_plan = QueryRewritePlan(
        original_query="site:github.com langchain",
        policy=RewritePolicy(mode="bypass", reason="Query contains precision signals."),
        variants=[
            QueryVariant(
                kind="original", query="site:github.com langchain", why="original"
            ),
        ],
        final_queries=["site:github.com langchain"],
    )

    query_results = [
        [
            WebSearchResult(
                title="A",
                link="https://example.com/a",
                snippet="snippet a",
                providers=["searxng"],
            ),
        ],
    ]

    async def _run() -> None:
        with (
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query",
                new_callable=AsyncMock,
            ) as mock_rewrite,
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.search_single_query",
                new_callable=AsyncMock,
            ) as mock_single,
            patch(
                "kindly_web_search_mcp_server.search.orchestrator.rerank_results",
                new_callable=AsyncMock,
            ) as mock_rerank,
        ):
            mock_rewrite.return_value = rewrite_plan
            mock_single.side_effect = query_results
            mock_rerank.side_effect = lambda _query, candidates, top_k: candidates[
                :top_k
            ]

            response = await run_web_search(
                "site:github.com langchain", num_results=1, rewrite=True
            )

        assert response.query == "site:github.com langchain"
        assert len(response.results) == 1
        assert mock_rewrite.awaited
        assert mock_single.await_count == 1
        # bypass mode: max(2x num_results, 6) = 6 per query (min floor)
        assert mock_single.await_args_list[0].kwargs["num_results"] == 6

    asyncio.run(_run())
