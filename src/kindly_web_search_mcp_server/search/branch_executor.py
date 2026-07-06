"""Bounded concurrent execution for decomposed search branches.

Each branch fires ``dispatch_providers`` with its own query and provider set.
All branches run concurrently; the branch-level deadline collects partial
results from branches that finish in time.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..analytics.observability_store import (
    insert_branch_attempts as analytics_insert_branch_attempts,
)
from .intents import SearchIntent

from .options import SearchOptions
from .provider_dispatch import dispatch_providers
from .provider_plan import ProviderExecutionPlan
from .provider_options import ProviderOptionBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchBranchSpec:
    index: int
    intent: SearchIntent
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
    branch_attempt_id: str
    status: str
    latency_ms: float
    results: list[WebSearchResult]
    error_type: str | None = None
    error_message: str | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "branch_attempt_id": self.branch_attempt_id,
            "branch_index": self.spec.index,
            "branch_intent": self.spec.intent,
            "branch_query": self.spec.query,
            "branch_type": self.spec.branch_type,
            "branch_weight": self.spec.weight,
            "branch_max_results": self.spec.max_results,
            "branch_reason": self.spec.reason,
            "branch_must_keep_terms": self.spec.must_keep_terms or [],
            "branch_status": self.status,
            "branch_error_type": self.error_type,
            "branch_error_message": self.error_message,
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


def _dispatch_kwargs(
    *,
    run_key: str | None,
    branch_index: int | None,
    branch_attempt_id: str | None,
    tool_call_id: str | None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    signature = inspect.signature(dispatch_providers)
    parameters = signature.parameters
    if run_key is not None and "run_key" in parameters:
        kwargs["run_key"] = run_key
    if branch_index is not None and "branch_index" in parameters:
        kwargs["branch_index"] = branch_index
    if branch_attempt_id is not None and "branch_attempt_id" in parameters:
        kwargs["branch_attempt_id"] = branch_attempt_id
    if tool_call_id is not None and "tool_call_id" in parameters:
        kwargs["tool_call_id"] = tool_call_id
    return kwargs


async def execute_search_branches(
    branches: list[SearchBranchSpec],
    *,
    http_client: httpx.AsyncClient,
    search_options: SearchOptions | None,
    provider_plan: ProviderExecutionPlan | None = None,
    max_concurrency: int | None = None,
    deadline_seconds: float | None = None,
    run_key: str | None = None,
    tool_call_id: str | None = None,
) -> BranchExecutionBatch:
    """Fire all branches concurrently, collect results from every branch.

    Each branch calls ``dispatch_providers`` which enforces its own
    per-provider deadline. No branch-level deadline is applied — every branch
    returns whatever ``dispatch_providers`` collected (partial results from
    providers that finished in time).

    Concurrency is limited by an ``asyncio.Semaphore`` and branch execution
    uses ``asyncio.gather`` so a slow branch cannot discard results produced
    before the provider deadline fires.
    """
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

    branch_attempt_ids = {spec.index: str(uuid.uuid4()) for spec in selected_branches}

    async def _run_branch(spec: SearchBranchSpec) -> SearchBranchResult:
        async with semaphore:
            branch_attempt_id = branch_attempt_ids[spec.index]
            start = time.perf_counter()
            provider_options_by_name = spec.provider_options_by_name or (
                provider_plan.options.bundles if provider_plan else None
            )
            resolved_configs: list = []
            if spec.providers and provider_plan:
                from .provider_config import resolve_provider_configs  # noqa: PLC0415

                resolved_configs = list(resolve_provider_configs(spec.providers))
            try:
                dispatch_kwargs = _dispatch_kwargs(
                    run_key=run_key,
                    branch_index=spec.index,
                    branch_attempt_id=branch_attempt_id,
                    tool_call_id=tool_call_id,
                )
                results = await dispatch_providers(
                    spec.query,
                    resolved_configs,
                    http_client,
                    num_results=spec.max_results,
                    deadline_seconds=settings.provider_group_deadline_seconds,
                    search_options=search_options,
                    provider_options_by_name=provider_options_by_name,
                    **dispatch_kwargs,  # type: ignore[arg-type]
                )
                status = "completed"
                error_type = None
                error_message = None
            except asyncio.CancelledError:
                logger.warning(
                    "Branch cancelled (index=%s type=%s)",
                    spec.index,
                    spec.branch_type,
                )
                results = []
                status = "cancelled"
                error_type = "CancelledError"
                error_message = "Branch cancelled"
            except Exception as exc:
                logger.warning(
                    "Branch search failed (index=%s type=%s): %s",
                    spec.index,
                    spec.branch_type,
                    exc,
                )
                results = []
                status = "failed"
                error_type = type(exc).__name__
                error_message = str(exc)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return SearchBranchResult(
                spec=spec,
                branch_attempt_id=branch_attempt_id,
                status=status,
                latency_ms=latency_ms,
                results=results,
                error_type=error_type,
                error_message=error_message,
            )

    branch_coros = [_run_branch(spec) for spec in selected_branches]
    branch_results: list[SearchBranchResult] = list(await asyncio.gather(*branch_coros))

    if run_key:
        try:
            for result in branch_results:
                analytics_insert_branch_attempts(
                    run_key=run_key,
                    tool_call_id=tool_call_id,
                    branch_attempt_id=result.branch_attempt_id,
                    branch_index=result.spec.index,
                    branch_type=result.spec.branch_type,
                    branch_query=result.spec.query,
                    branch_weight=result.spec.weight,
                    provider_names=result.spec.providers or [],
                    provider_count=len(result.spec.providers or []),
                    status=result.status,
                    deadline_seconds=settings.provider_group_deadline_seconds,
                    latency_ms=round(result.latency_ms, 3),
                    result_count=len(result.results),
                    error_type=result.error_type,
                    error_message=result.error_message,
                    payload_json={
                        "reason": result.spec.reason,
                        "must_keep_terms": result.spec.must_keep_terms or [],
                        "intent": result.spec.intent,
                    },
                )
        except Exception as exc:
            logger.debug("analytics insert_branch_attempts failed: %s", exc)

    return BranchExecutionBatch(
        result_lists=[result.results for result in branch_results],
        branch_queries=[result.spec.query for result in branch_results],
        branch_providers=[result.spec.providers for result in branch_results],
        list_weights=[result.spec.weight for result in branch_results],
        branch_metadata=[result.metadata for result in branch_results],
    )
