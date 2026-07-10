"""Tests for the 0.2 search pipeline."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.rerank.models import RerankOutput

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.branch_executor import BranchExecutionBatch
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.query_rewrite_models import QueryVariant
from kindly_web_search_mcp_server.search.understanding.models import (
    QueryUnderstandingResult,
)
from kindly_web_search_mcp_server.settings import settings


def _understanding(intent: str = "general") -> QueryUnderstandingResult:
    return QueryUnderstandingResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=0.92,
        rationale="query can benefit from rewrite",
    )


def _branch_batch(branches: list[tuple[str, str]]) -> BranchExecutionBatch:
    result_lists = [
        [
            WebSearchResult(
                title=title,
                link=f"https://example.com/{idx}",
                snippet=f"{title} snippet",
                providers=["searxng"],
            )
        ]
        for idx, (_, title) in enumerate(branches, start=1)
    ]
    return BranchExecutionBatch(
        result_lists=result_lists,
        branch_queries=[query for query, _ in branches],
        branch_providers=[["searxng"] for _ in branches],
        branch_metadata=[{"branch_index": idx} for idx, _ in enumerate(branches)],
    )


def test_keyword_extractor_defers_rake_import_until_extraction(monkeypatch) -> None:
    """Query-rewrite setup must not load the RAKE runtime before extraction."""
    keyword_module_name = "kindly_web_search_mcp_server.search.keyword_extract"
    rake_module_name = "rake_nltk"
    module_names_to_clear = [
        module_name
        for module_name in sys.modules
        if module_name == keyword_module_name
        or module_name.startswith(f"{keyword_module_name}.")
        or module_name == rake_module_name
        or module_name.startswith(f"{rake_module_name}.")
    ]
    for module_name in module_names_to_clear:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    keyword_extract = importlib.import_module(keyword_module_name)
    importlib.reload(keyword_extract)

    assert not any(
        module_name == rake_module_name or module_name.startswith(f"{rake_module_name}.")
        for module_name in sys.modules
    )


def test_run_search_pipeline_rewrites_searches_and_reranks() -> None:
    from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline

    async def _run() -> None:
        batch = _branch_batch(
            [
                ("langchain agent react", "A"),
                ("langchain docs react", "B"),
            ]
        )

        with (
            patch.object(settings, "web_results_index_enabled", False),
            patch(
                "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding",
                new_callable=AsyncMock,
            ) as mock_understanding,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants",
                new_callable=AsyncMock,
            ) as mock_rewrite,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.execute_search_branches",
                new_callable=AsyncMock,
            ) as mock_execute,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.rerank_results",
                new_callable=AsyncMock,
            ) as mock_rerank,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.maybe_extract_entities",
                new_callable=AsyncMock,
            ) as mock_entities,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.record_search_request",
            ) as mock_record_search,
        ):
            mock_understanding.return_value = _understanding()
            mock_rewrite.return_value = (
                [
                    QueryVariant(
                        kind="original",
                        target="keyword",
                        query="langchain agent react",
                        why="original",
                        weight=1.0,
                    ),
                    QueryVariant(
                        kind="official_docs",
                        target="keyword",
                        query="langchain docs react",
                        why="docs",
                        weight=1.0,
                    ),
                ],
                "vercel",
                None,
                None,
            )
            mock_execute.return_value = batch
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(
                results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[
                    : kwargs.get("top_k", 10)
                ],
                embedding_context=None,
            )
            mock_entities.side_effect = lambda *args, **kwargs: kwargs.get("results", [])

            response = await run_search_pipeline(
                "langchain agent react",
                num_results=1,
                rewrite=True,
                diagnostics=None,
                research_goal="find docs",
                search_options=SearchOptions(),
            )

        assert response.query == "langchain agent react"
        assert response.results[0].title == "A"
        assert mock_understanding.awaited
        assert mock_rewrite.awaited
        assert mock_execute.awaited
        assert mock_rerank.awaited
        assert mock_entities.awaited
        mock_record_search.assert_called_once()

    asyncio.run(_run())


def test_run_search_pipeline_routes_variant_targets_to_matching_providers() -> None:
    from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline

    async def _run() -> None:
        batch = _branch_batch(
            [
                ("FastMCP docs", "Keyword"),
                ("FastMCP grounding", "Neural"),
            ]
        )
        captured: dict[str, object] = {}

        with (
            patch.object(settings, "web_results_index_enabled", False),
            patch(
                "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding",
                new_callable=AsyncMock,
            ) as mock_understanding,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants",
                new_callable=AsyncMock,
            ) as mock_rewrite,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.execute_search_branches",
                new_callable=AsyncMock,
            ) as mock_execute,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.rerank_results",
                new_callable=AsyncMock,
            ) as mock_rerank,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.maybe_extract_entities",
                new_callable=AsyncMock,
            ) as mock_entities,
        ):
            mock_understanding.return_value = _understanding()
            mock_rewrite.return_value = (
                [
                    QueryVariant(
                        kind="keyword_refined",
                        target="keyword",
                        query="FastMCP resources tools",
                        why="original",
                        weight=1.0,
                    ),
                    QueryVariant(
                        kind="neural_refined",
                        target="neural",
                        query="Find official FastMCP documentation and examples.",
                        why="neural",
                        weight=1.0,
                    ),
                ],
                "vercel",
                None,
                None,
            )

            async def _execute(branch_specs, **kwargs):  # noqa: ANN001
                captured["branch_specs"] = branch_specs
                return batch

            mock_execute.side_effect = _execute
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(
                results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[
                    : kwargs.get("top_k", 10)
                ],
                embedding_context=None,
            )
            mock_entities.side_effect = lambda *args, **kwargs: kwargs.get("results", [])

            await run_search_pipeline(
                "FastMCP resources tools docs prompt as tools code mode",
                num_results=2,
                rewrite=True,
                diagnostics=None,
                research_goal="compare docs and grounded examples",
                search_options=SearchOptions(),
            )

        branch_specs = captured["branch_specs"]
        # Original free branch plus target-routed rewrite branches.
        assert len(branch_specs) >= 2
        assert branch_specs[0].branch_type == "original_free"
        assert branch_specs[0].providers is not None
        assert branch_specs[1].branch_type == "keyword_refined"
        assert branch_specs[1].providers is not None
        assert mock_rewrite.awaited
        assert mock_execute.awaited
        assert mock_rerank.awaited
        assert mock_entities.awaited

    asyncio.run(_run())


def test_run_search_pipeline_without_rewrite_keeps_original_query() -> None:
    from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline

    async def _run() -> None:
        batch = _branch_batch([("fastmcp docs", "A")])

        with (
            patch.object(settings, "web_results_index_enabled", False),
            patch(
                "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding",
                new_callable=AsyncMock,
            ) as mock_understanding,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants",
                new_callable=AsyncMock,
            ) as mock_rewrite,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.execute_search_branches",
                new_callable=AsyncMock,
            ) as mock_execute,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.rerank_results",
                new_callable=AsyncMock,
            ) as mock_rerank,
            patch(
                "kindly_web_search_mcp_server.search.pipeline.maybe_extract_entities",
                new_callable=AsyncMock,
            ) as mock_entities,
        ):
            mock_understanding.return_value = _understanding()
            mock_execute.return_value = batch
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(
                results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[
                    : kwargs.get("top_k", 10)
                ],
                embedding_context=None,
            )
            mock_entities.side_effect = lambda *args, **kwargs: kwargs.get("results", [])

            response = await run_search_pipeline(
                "fastmcp docs",
                num_results=1,
                rewrite=False,
                diagnostics=None,
                research_goal=None,
                search_options=SearchOptions(),
            )

        assert response.query == "fastmcp docs"
        assert response.results[0].title == "A"
        assert mock_understanding.awaited
        assert mock_rewrite.await_count == 0
        assert mock_execute.awaited
        assert mock_rerank.awaited
        assert mock_entities.awaited

    asyncio.run(_run())
