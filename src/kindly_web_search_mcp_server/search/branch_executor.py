"""Bounded concurrent execution for decomposed search branches."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..utils.diagnostics import Diagnostics
from .options import SearchOptions
from ..search_instrumented import search_single_query

logger = logging.getLogger(__name__)

SearchRunner = Callable[
    ...,
    Awaitable[list[WebSearchResult]],
]


@dataclass(frozen=True)
class SearchBranchSpec:
    index: int
    query: str
    branch_type: str
    weight: float
    providers: list[str] | None
    max_results: int
    reason: str
    must_keep_terms: list[str] | None = None
    provider_arguments: dict[str, dict[str, object]] | None = None


@dataclass(frozen=True)
class SearchBranchResult:
    spec: SearchBranchSpec
    latency_ms: float
    results: list[WebSearchResult]

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "branch_index": self.spec.index,
            "branch_query": self.spec.query,
            "branch_type": self.spec.branch_type,
            "branch_weight": self.spec.weight,
            "branch_max_results": self.spec.max_results,
            "branch_reason": self.spec.reason,
            "branch_must_keep_terms": self.spec.must_keep_terms or [],
            "branch_latency_ms": round(self.latency_ms, 3),
            "branch_result_count": len(self.results),
        }


@dataclass(frozen=True)
class BranchExecutionBatch:
    result_lists: list[list[WebSearchResult]]
    branch_queries: list[str]
    branch_providers: list[list[str] | None]
    list_weights: list[float]
    branch_metadata: list[dict[str, Any]]


def _limit_branches(branches: list[SearchBranchSpec]) -> list[SearchBranchSpec]:
    max_branches = max(1, settings.query_decomposition_max_branches)
    return branches[:max_branches]


def select_providers_for_variant(
    variant: Any,
    active_provider_names: list[str],
) -> list[str] | None:
    if variant.target == "all":
        return active_provider_names or None
    if variant.target == "keyword":
        selected = [
            name
            for name in active_provider_names
            if name in {"searxng", "ddg", "brave", "tavily"}
        ]
        return selected if selected else None
    if variant.target == "community":
        selected = [
            name
            for name in active_provider_names
            if name in {"hackernews", "reddit", "github_graphql", "stackexchange"}
        ]
        return selected if selected else None
    selected = [
        name
        for name in active_provider_names
        if name in {"gemini", "composio_llm_search", "jina"}
    ]
    return selected if selected else None


async def execute_search_branches(
    branches: list[SearchBranchSpec],
    *,
    http_client: httpx.AsyncClient,
    diagnostics: Diagnostics | None,
    search_options: SearchOptions | None,
    search_runner: SearchRunner | None = None,
    max_concurrency: int | None = None,
) -> BranchExecutionBatch:
    selected_branches = _limit_branches(branches)
    if not selected_branches:
        return BranchExecutionBatch([], [], [], [], [])

    runner = search_runner or search_single_query
    concurrency = max(
        1,
        min(
            max_concurrency or settings.query_decomposition_max_concurrency,
            len(selected_branches),
        ),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_branch(spec: SearchBranchSpec) -> SearchBranchResult:
        async with semaphore:
            start = time.perf_counter()
            try:
                results = await runner(
                    spec.query,
                    num_results=spec.max_results,
                    http_client=http_client,
                    diagnostics=diagnostics,
                    providers=spec.providers,
                    search_options=search_options,
                    provider_arguments=spec.provider_arguments,
                )
            except Exception as exc:
                logger.warning(
                    "Branch search failed (index=%s type=%s): %s",
                    spec.index,
                    spec.branch_type,
                    exc,
                )
                results = []
            latency_ms = (time.perf_counter() - start) * 1000.0
            return SearchBranchResult(spec=spec, latency_ms=latency_ms, results=results)

    branch_results = await asyncio.gather(
        *[_run_branch(spec) for spec in selected_branches]
    )
    return BranchExecutionBatch(
        result_lists=[result.results for result in branch_results],
        branch_queries=[result.spec.query for result in branch_results],
        branch_providers=[result.spec.providers for result in branch_results],
        list_weights=[result.spec.weight for result in branch_results],
        branch_metadata=[result.metadata for result in branch_results],
    )
