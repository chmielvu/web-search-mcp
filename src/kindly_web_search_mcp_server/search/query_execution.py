"""Canonical multi-provider search orchestration with OpenTelemetry instrumentation."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from opentelemetry import trace

from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import add_results_to_span, get_search_total_metric, get_tracer
from ..utils.async_helpers import gather_with_deadline, task_completed_successfully
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from .budget import ProviderBudget
from .errors import WebSearchProviderError
from .intents import SearchIntent
from .merge import merge_search_results
from .options import SearchOptions
from .provider_config import (
    ProviderConfig,
    ProviderGroup,
    resolve_provider_configs,
    resolve_providers_for_search,
    select_serp_paid_configs,
)
from .provider_execution import _search_single_provider
from .provider_options import ProviderOptionBundle
from .provider_plan import ProviderExecutionPlan

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")

# Global SERP semaphore — shared across all concurrent queries so that
# SERP_SEMAPHORE_LIMIT is a hard ceiling on paid API concurrency,
# not per-branch. Re-created when settings change (rare at runtime).
_serp_semaphore: asyncio.Semaphore | None = None


def _get_serp_semaphore() -> asyncio.Semaphore:
    global _serp_semaphore
    if _serp_semaphore is None or _serp_semaphore._value != settings.serp_semaphore_limit:
        _serp_semaphore = asyncio.Semaphore(settings.serp_semaphore_limit)
    return _serp_semaphore


async def search_single_query(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    intent: SearchIntent = "general",
    search_options: SearchOptions | None = None,
    provider_plan: ProviderExecutionPlan | None = None,
    provider_options_by_name: dict[str, ProviderOptionBundle] | None = None,
    run_key: str | None = None,
) -> list[WebSearchResult]:
    """Search with full OpenTelemetry instrumentation."""
    start_time = time.time()

    with tracer.start_as_current_span(
        "web_search",
        kind=trace.SpanKind.SERVER,
        attributes={
            "query": query[:200],
            "num_results_requested": num_results,
            "intent": intent,
        },
    ) as span:
        budget = ProviderBudget()
        active_configs = (
            resolve_provider_configs(provider_plan.provider_names, intent=intent)
            if provider_plan is not None
            else resolve_providers_for_search(intent)
        )
        resolved_provider_options = (
            provider_options_by_name
            if provider_options_by_name is not None
            else provider_plan.options.bundles
            if provider_plan is not None
            else {}
        )

        if not active_configs:
            span.set_attribute("error", "No providers available")
            raise WebSearchProviderError(
                "No search providers available. Configure SEARXNG_BASE_URL, "
                "or specify providers explicitly."
            )

        span.set_attribute("active_providers", [c.name for c in active_configs])

        if diagnostics:
            diagnostics.emit(
                "search.provider_select",
                "Active providers for search",
                {
                    "query": query,
                    "num_results": num_results,
                    "active_providers": [c.name for c in active_configs],
                },
            )

        async def _provider_call(
            config: ProviderConfig,
            client: httpx.AsyncClient,
        ) -> list[WebSearchResult]:
            bundle = resolved_provider_options.get(config.name)
            provider_search_options = (
                bundle.search_options if bundle and bundle.search_options is not None else search_options
            )
            provider_arguments = bundle.arguments if bundle is not None else None
            return await _search_single_provider(
                config.name,
                config.search_fn,
                query,
                num_results,
                client,
                provider_search_options,
                budget,
                provider_arguments,
                run_key=run_key,
            )

        deadline = settings.provider_group_deadline_seconds

        async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
            all_results: list[list[WebSearchResult]] = []
            provider_names: list[str] = []

            # Group by ProviderGroup for dispatch
            free_configs = [c for c in active_configs if c.group == ProviderGroup.free]
            serp_paid_configs = [c for c in active_configs if c.group == ProviderGroup.serp_paid]
            other_configs = [c for c in active_configs if c.group == ProviderGroup.other]
            serp_paid_configs = select_serp_paid_configs(serp_paid_configs)

            # free group: fire all concurrently with deadline
            if free_configs:
                free_tasks = [
                    asyncio.create_task(_provider_call(c, client), name=c.name)
                    for c in free_configs
                ]
                _, errors = await gather_with_deadline(
                    *free_tasks, deadline_seconds=deadline,
                )
                for err in errors:
                    LOGGER.warning("Provider task in free group: %s", err)
                # Map completed results back to provider names via task names
                for task in free_tasks:
                    if task_completed_successfully(task):
                        all_results.append(task.result())
                        provider_names.append(task.get_name())

            # serp_paid group: fire the selected round-robin subset, gated by the
            # global SERP semaphore, with deadline
            if serp_paid_configs:
                semaphore = _get_serp_semaphore()

                async def _search_with_semaphore(
                    config: ProviderConfig,
                ) -> list[WebSearchResult]:
                    async with semaphore:
                        return await _provider_call(config, client)

                paid_tasks = [
                    asyncio.create_task(
                        _search_with_semaphore(c), name=c.name,
                    )
                    for c in serp_paid_configs
                ]
                _, errors = await gather_with_deadline(
                    *paid_tasks, deadline_seconds=deadline,
                )
                for err in errors:
                    LOGGER.warning("Provider task in serp_paid group: %s", err)
                for task in paid_tasks:
                    if task_completed_successfully(task):
                        all_results.append(task.result())
                        provider_names.append(task.get_name())

            # other group: fire all concurrently with deadline
            if other_configs:
                other_tasks = [
                    asyncio.create_task(_provider_call(c, client), name=c.name)
                    for c in other_configs
                ]
                _, errors = await gather_with_deadline(
                    *other_tasks, deadline_seconds=deadline,
                )
                for err in errors:
                    LOGGER.warning("Provider task in other group: %s", err)
                for task in other_tasks:
                    if task_completed_successfully(task):
                        all_results.append(task.result())
                        provider_names.append(task.get_name())

            merged = merge_search_results(all_results) if all_results else []
            span.set_attribute("result_count", len(merged))
            span.set_attribute("providers_used", provider_names)
            add_results_to_span(span, merged, max_results=10)
            emit_observability_event(
                LOGGER,
                "search.single_query.response",
                query=query,
                num_results_requested=num_results,
                active_providers=[c.name for c in active_configs],
                providers_used=provider_names,
                merged_result_count=len(merged),
                results=merged[:num_results],
                run_key=run_key,
            )

            if diagnostics:
                diagnostics.emit(
                    "search.complete",
                    "Search completed",
                    {
                        "input_lists": len(all_results),
                        "output_count": len(merged),
                        "providers_used": provider_names,
                    },
                )

            return merged[:num_results]

        try:
            if http_client is not None:
                results = await _run(http_client)
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    results = await _run(client)

            total_duration = time.time() - start_time
            span.set_attribute("total_duration_ms", total_duration * 1000)
            get_search_total_metric().add(1)
            return results
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", str(exc)[:500])
            raise
