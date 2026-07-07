"""Unified concurrent provider dispatch with hard deadline enforcement.

Each provider coroutine is individually wrapped with ``asyncio.wait_for``
using a per-task deadline BEFORE it becomes a task. This ensures
``CancelledError`` is delivered to each provider separately at the deadline
boundary, not just to the batch-wait wrapper.

All selected providers (free, paid_serp, specialized) fire concurrently.
Provider selection and SERP round-robin happen upstream in
``provider_plan.build_provider_execution_plan`` — this module only handles
the concurrent execution and deadline enforcement.

Results are collected as providers finish.  As soon as enough results have
been gathered to satisfy the caller's ``num_results`` request, the remaining
in-flight providers are cancelled and the merged result set is returned.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Mapping

import httpx

from ..models import WebSearchResult
from ..analytics.observability_store import (
    _candidate_id,
    _canonical_result_id,
    insert_branch_candidates as analytics_insert_branch_candidates,
)
from .merge import merge_search_results
from .options import SearchOptions
from .provider_config import ProviderConfig
from .provider_execution import _search_single_provider
from .provider_options import ProviderOptionBundle

LOGGER = logging.getLogger(__name__)


async def dispatch_providers(
    query: str,
    providers: list[ProviderConfig],
    http_client: httpx.AsyncClient,
    *,
    num_results: int,
    deadline_seconds: float,
    search_options: SearchOptions | None = None,
    provider_options_by_name: Mapping[str, ProviderOptionBundle] | None = None,
    run_key: str | None = None,
    branch_index: int | None = None,
    branch_attempt_id: str | None = None,
    tool_call_id: str | None = None,
) -> list[WebSearchResult]:
    """Fire all providers concurrently, collect results within *deadline_seconds*.

    Each provider coroutine is individually guarded by ``asyncio.wait_for``
    with a hard deadline before it becomes a task. This means the
    ``CancelledError`` is injected directly into the provider coroutine at
    the deadline boundary, not just into a batch-wait wrapper that leaves
    the underlying work running.

    Returns merged results from providers as they complete.  As soon as
    enough results are available to satisfy *num_results*, the remaining
    providers are cancelled so a fast provider (e.g. BrightData ~1s) is not
    held hostage by a slow provider or the group deadline.
    """
    if not providers:
        return []

    async def _call(cfg: ProviderConfig) -> list[WebSearchResult]:
        bundle = (provider_options_by_name or {}).get(cfg.name)
        provider_search_options = (
            bundle.search_options
            if bundle and bundle.search_options is not None
            else search_options
        )
        provider_arguments = bundle.arguments if bundle is not None else None
        return await _search_single_provider(
            cfg.name,
            cfg.search_fn,
            query,
            num_results,
            http_client,
            provider_search_options,
            None,
            provider_arguments,
            run_key=run_key,
            branch_index=branch_index,
            branch_attempt_id=branch_attempt_id,
            tool_call_id=tool_call_id,
            cancel_token=None,
        )

    def _ignore_task_exception(task: asyncio.Task[object]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    tasks = [
        asyncio.create_task(asyncio.wait_for(_call(cfg), timeout=deadline_seconds))
        for cfg in providers
    ]

    all_results: list[list[WebSearchResult]] = []
    result_count = 0
    deadline_handle = asyncio.get_event_loop().time() + deadline_seconds

    while tasks and asyncio.get_event_loop().time() < deadline_handle:
        remaining = deadline_handle - asyncio.get_event_loop().time()
        done, pending = await asyncio.wait(
            tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            tasks.remove(task)
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                continue
            if exc is not None:
                LOGGER.debug("Provider task failed: %s", exc)
                continue
            try:
                result = task.result()
            except asyncio.CancelledError:
                continue
            except Exception:
                continue
            if result:
                all_results.append(result)
                result_count += len(result)
                if result_count >= num_results:
                    pending_tasks = list(pending)
                    LOGGER.info(
                        "Collected %d results from early-finishing providers "
                        "(requested %d); cancelling %d remaining providers",
                        result_count,
                        num_results,
                        len(pending_tasks),
                    )
                    for pending_task in pending_tasks:
                        pending_task.cancel()
                    if pending_tasks:
                        drain_done, drain_pending = await asyncio.wait(
                            pending_tasks, timeout=3.0
                        )
                        for unhandled in drain_pending:
                            unhandled.add_done_callback(_ignore_task_exception)
                        for drain_task in drain_done:
                            drain_task.add_done_callback(_ignore_task_exception)
                        for pending_task in pending_tasks:
                            if pending_task in tasks:
                                tasks.remove(pending_task)
                    break

        if result_count >= num_results:
            break

    # Cancel any stragglers that are still running (deadline fired or enough results).
    if tasks:
        if result_count >= num_results:
            LOGGER.info(
                "Cancelling %d provider tasks after collecting requested results",
                len(tasks),
            )
        else:
            LOGGER.warning(
                "Cancelling %d provider tasks that exceeded %.1fs deadline",
                len(tasks),
                deadline_seconds,
            )
        for task in tasks:
            task.cancel()
        if tasks:
            drain_done, drain_pending = await asyncio.wait(tasks, timeout=3.0)
            for unhandled in drain_pending:
                unhandled.add_done_callback(_ignore_task_exception)
            for drain_task in drain_done:
                drain_task.add_done_callback(_ignore_task_exception)

    if not all_results:
        return []

    merged = merge_search_results(all_results)
    if run_key and branch_attempt_id and branch_index is not None:
        try:
            for rank, result in enumerate(merged, start=1):
                analytics_insert_branch_candidates(
                    run_key=run_key,
                    branch_attempt_id=branch_attempt_id,
                    branch_index=branch_index,
                    candidate_rank=rank,
                    title=result.title,
                    link=result.link,
                    snippet=result.snippet,
                    domain=result.domain or "",
                    providers=result.providers or [],
                    provider_count=result.provider_count,
                    score=result.score,
                    candidate_id=_candidate_id(result.link, result.title, result.snippet),
                    canonical_result_id=_canonical_result_id(result.link),
                    payload_json={
                        "query": query,
                        "tool_call_id": tool_call_id,
                        "branch_query": query,
                        "branch_provider_count": len(result.providers or []),
                    },
                )
        except Exception as exc:
            LOGGER.debug("analytics insert_branch_candidates failed: %s", exc)
    return merged
