"""Bounded concurrent execution for decomposed search branches.

Each branch fires ``dispatch_providers`` with its own query and provider set.
All branches run concurrently; the branch-level deadline collects partial
results from branches that finish in time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..utils.async_helpers import task_completed_successfully
from .options import SearchOptions
from .provider_dispatch import dispatch_providers
from .provider_plan import ProviderExecutionPlan
from .provider_options import ProviderOptionBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchBranchSpec:
    index: int
    query: str
    branch_type: str
    weight: float
    providers: list[str] | None
    provider_options_by_name: dict[str, ProviderOptionBundle] | None
    max_results: int
    reason: str
    must_keep_terms: list[str] | None = None


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


async def execute_search_branches(
    branches: list[SearchBranchSpec],
    *,
    http_client: httpx.AsyncClient,
    search_options: SearchOptions | None,
    provider_plan: ProviderExecutionPlan | None = None,
    max_concurrency: int | None = None,
    deadline_seconds: float | None = None,
    run_key: str | None = None,
) -> BranchExecutionBatch:
    """Fire all branches concurrently, collect results within deadline.

    Each branch calls ``dispatch_providers`` which handles the per-provider
    deadline internally.  The branch-level deadline is a hard wall that
    collects whatever completed.
    """
    from ..utils.diagnostics import Diagnostics  # noqa: PLC0415

    selected_branches = _limit_branches(branches)
    if not selected_branches:
        return BranchExecutionBatch([], [], [], [], [])

    concurrency = max(
        1,
        min(
            max_concurrency or settings.query_decomposition_max_concurrency,
            len(selected_branches),
        ),
    )
    semaphore = asyncio.Semaphore(concurrency)

    # Branch deadline: provider_group_deadline × 3 (branches fan out further).
    branch_deadline = deadline_seconds or (settings.provider_group_deadline_seconds * 3)

    async def _run_branch(spec: SearchBranchSpec) -> SearchBranchResult:
        async with semaphore:
            start = time.perf_counter()
            provider_options_by_name = (
                spec.provider_options_by_name
                or (provider_plan.options.bundles if provider_plan else None)
            )
            # Resolve ProviderConfig objects for this branch's provider names.
            resolved_configs: list = []
            if spec.providers and provider_plan:
                from .provider_config import resolve_provider_configs  # noqa: PLC0415

                resolved_configs = list(
                    resolve_provider_configs(spec.providers, intent="general")
                )
            try:
                results = await dispatch_providers(
                    spec.query,
                    resolved_configs,
                    http_client,
                    deadline_seconds=settings.provider_group_deadline_seconds,
                    search_options=search_options,
                    provider_options_by_name=provider_options_by_name,
                    run_key=run_key,
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

    branch_tasks = [
        asyncio.create_task(_run_branch(spec), name=f"branch-{spec.index}")
        for spec in selected_branches
    ]

    done, pending = await asyncio.wait(branch_tasks, timeout=branch_deadline)

    if pending:
        logger.warning(
            "%d of %d branches exceeded %.1fs deadline, cancelling",
            len(pending),
            len(branch_tasks),
            branch_deadline,
        )
        for t in pending:
            t.cancel()
        await asyncio.wait(pending, timeout=2.0)

    # Collect completed branch results; timed-out branches produce empty results.
    branch_results = [
        t.result() if task_completed_successfully(t) else SearchBranchResult(
            spec=selected_branches[i],
            latency_ms=branch_deadline * 1000,
            results=[],
        )
        for i, t in enumerate(branch_tasks)
    ]

    if pending:
        logger.warning(
            "Branch execution: %d of %d branches exceeded deadline",
            len(pending),
            len(selected_branches),
        )

    return BranchExecutionBatch(
        result_lists=[result.results for result in branch_results],
        branch_queries=[result.spec.query for result in branch_results],
        branch_providers=[result.spec.providers for result in branch_results],
        list_weights=[result.spec.weight for result in branch_results],
        branch_metadata=[result.metadata for result in branch_results],
    )
