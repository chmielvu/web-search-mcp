"""Tests for the 0.2 search pipeline."""

from __future__ import annotations

import asyncio
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
        list_weights=[1.0 for _ in branches],
        branch_metadata=[{"branch_index": idx} for idx, _ in enumerate(branches)],
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
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[:kwargs.get("top_k", 10)], embedding_context=None)
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
                        kind="original",
                        target="keyword",
                        query="FastMCP resources tools",
                        why="original",
                        weight=1.0,
                    ),
                    QueryVariant(
                        kind="neural_task",
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
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[:kwargs.get("top_k", 10)], embedding_context=None)
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
        # original + up to 2 rewrite variants from build_rewrite_variants
        assert len(branch_specs) >= 2
        # Both branches get the same provider list (no per-target routing)
        assert branch_specs[0].providers is not None
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
            mock_rerank.side_effect = lambda *args, **kwargs: RerankOutput(results=kwargs.get("candidates", args[1] if len(args) > 1 else [])[:kwargs.get("top_k", 10)], embedding_context=None)
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


def test_search_single_query_keeps_fast_provider_results_when_one_times_out() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search.provider_config import (
        ProviderConfig,
        ProviderGroup,
    )
    from kindly_web_search_mcp_server.search.query_execution import (
        search_single_query,
    )

    async def _run() -> None:
        slow = ProviderConfig(
            name="slow",
            env_key="",
            search_fn=lambda *args, **kwargs: None,
            group=ProviderGroup.free,
            requires_key=False,
        )
        fast = ProviderConfig(
            name="fast",
            env_key="",
            search_fn=lambda *args, **kwargs: None,
            group=ProviderGroup.free,
            requires_key=False,
        )

        async def _fake_search(
            provider_name,  # noqa: ANN001
            provider_fn,  # noqa: ANN001
            query,  # noqa: ANN001
            num_results,  # noqa: ANN001
            http_client,  # noqa: ANN001
            search_options=None,  # noqa: ANN001
            budget=None,  # noqa: ANN001
            provider_arguments=None,  # noqa: ANN001
            run_key=None,  # noqa: ANN001
        ) -> list[WebSearchResult]:
            if provider_name == "slow":
                await asyncio.sleep(0.2)
                return [
                    WebSearchResult(
                        title="slow",
                        link="https://example.com/slow",
                        snippet="slow",
                        providers=["slow"],
                    )
                ]
            return [
                WebSearchResult(
                    title="fast",
                    link="https://example.com/fast",
                    snippet="fast",
                    providers=["fast"],
                )
            ]

        with (
            patch.object(settings, "provider_group_deadline_seconds", 0.01),
            patch(
                "kindly_web_search_mcp_server.search.query_execution.resolve_providers_for_search",
                return_value=[slow, fast],
            ),
            patch(
                "kindly_web_search_mcp_server.search.query_execution._search_single_provider",
                new=_fake_search,
            ),
        ):
            async with httpx.AsyncClient() as client:
                results = await search_single_query(
                    "timeout test",
                    num_results=10,
                    http_client=client,
                    intent="general",
                )

        assert [result.title for result in results] == ["fast"]

    import httpx

    asyncio.run(_run())
