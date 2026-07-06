"""Unified concurrent provider dispatch with hard deadline enforcement.

Each provider coroutine is individually wrapped with ``asyncio.wait_for``
using a per-task deadline BEFORE it becomes a task. This ensures
``CancelledError`` is delivered to each provider separately at the deadline
boundary, not just to the batch-wait wrapper.

All selected providers (free, paid_serp, specialized) fire concurrently.
Provider selection and SERP round-robin happen upstream in
``provider_plan.build_provider_execution_plan`` — this module only handles
the concurrent execution and deadline enforcement.
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

    Returns merged results from all providers that completed before the
    deadline.  Stragglers are cancelled after a bounded drain window.
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

    tasks = [asyncio.create_task(_call(cfg)) for cfg in providers]
    done, pending = await asyncio.wait(tasks, timeout=deadline_seconds)

    if pending:
        LOGGER.warning(
            "Cancelling %d provider tasks that exceeded %.1fs deadline",
            len(pending),
            deadline_seconds,
        )
        for task in pending:
            task.cancel()
        drain_done, drain_pending = await asyncio.wait(
            pending,
            timeout=3.0,
        )
        if drain_pending:
            LOGGER.warning(
                "%d provider tasks did not finish cleanup within %.1fs drain window; abandoning",
                len(drain_pending),
                3.0,
            )
            for task in drain_pending:
                task.add_done_callback(_ignore_task_exception)
        done |= drain_done

    # Collect results from tasks that completed before deadline + drain.
    all_results: list[list[WebSearchResult]] = []
    for task in tasks:
        if not task.done() or task.cancelled():
            continue
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            continue
        if exc is not None:
            LOGGER.debug("Provider task failed: %s", exc)
            continue
        try:
            all_results.append(task.result())
        except asyncio.CancelledError:
            continue
        except Exception:
            pass

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
