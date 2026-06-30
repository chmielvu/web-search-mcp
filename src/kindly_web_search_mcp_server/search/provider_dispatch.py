"""Unified concurrent provider dispatch.

All selected providers (free, paid_serp, specialized) fire concurrently in a single
asyncio.wait() call. The deadline applies to the whole batch.

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
from ..utils.async_helpers import (
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    task_completed_successfully,
)
from .merge import merge_search_results
from .options import SearchOptions
from .provider_config import ProviderConfig, ProviderGroup
from .provider_execution import _search_single_provider
from .provider_options import ProviderOptionBundle

LOGGER = logging.getLogger(__name__)

# Global SERP semaphore — shared across all concurrent queries so that
# serp_semaphore_limit is a hard ceiling on paid API concurrency.
_serp_semaphore: asyncio.Semaphore | None = None


def _get_serp_semaphore() -> asyncio.Semaphore:
    from ..settings import settings  # noqa: PLC0415

    global _serp_semaphore
    if _serp_semaphore is None or _serp_semaphore._value != settings.serp_semaphore_limit:
        _serp_semaphore = asyncio.Semaphore(settings.serp_semaphore_limit)
    return _serp_semaphore


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

    Providers are already filtered and round-robin selected by the caller
    (``build_provider_execution_plan``).  This function only handles the
    concurrent dispatch and deadline logic.

    SERP-paid providers are gated by the global SERP semaphore inside their
    individual task, so they count against the semaphore but still run
    concurrently with free/other providers.

    Returns merged results from all providers that completed before the
    deadline.  Stragglers are cancelled and their results discarded.
    """
    if not providers:
        return []

    semaphore = _get_serp_semaphore()

    async def _call(cfg: ProviderConfig) -> list[WebSearchResult]:
        bundle = (provider_options_by_name or {}).get(cfg.name)
        provider_search_options = (
            bundle.search_options if bundle and bundle.search_options is not None else search_options
        )
        provider_arguments = bundle.arguments if bundle is not None else None

        # SERP-paid providers are semaphore-gated; free/specialized providers are not.
        if cfg.group == ProviderGroup.paid_serp:
            async with semaphore:
                return await _search_single_provider(
                    cfg.name,
                    cfg.search_fn,
                    query,
                    num_results,
                    http_client,
                    provider_search_options,
                    None,  # budget — handled upstream
                    provider_arguments,
                    run_key=run_key,
                    branch_index=branch_index,
                    branch_attempt_id=branch_attempt_id,
                    tool_call_id=tool_call_id,
                )
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
        )

    # Create all tasks at once — they all fire concurrently.
    tasks = [
        asyncio.create_task(_call(cfg), name=f"provider-{cfg.name}")
        for cfg in providers
    ]

    done, pending = await asyncio.wait(tasks, timeout=deadline_seconds)

    # Collect results from fast providers first (before any drain) so
    # that they survive even if the caller's own deadline fires.
    all_results: list[list[WebSearchResult]] = []
    for t in done:
        if t.cancelled():
            continue
        if not task_completed_successfully(t):
            exc = t.exception()
            LOGGER.debug("Provider %s failed: %s", t.get_name(), exc)
            continue
        all_results.append(t.result())

    # Cancel stragglers in the background — don't block result return.
    if pending:
        LOGGER.warning(
            "%d of %d providers exceeded %.1fs deadline, cancelling",
            len(pending),
            len(tasks),
            deadline_seconds,
        )
        for t in pending:
            t.cancel()

        async def _drain() -> None:
            await asyncio.wait(pending, timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS)

        asyncio.create_task(_drain(), name="provider-drain")

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
