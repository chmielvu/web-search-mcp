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
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from .budget import ProviderBudget
from .errors import WebSearchProviderError
from .intents import SearchIntent
from .merge import merge_search_results
from .options import SearchOptions
from .provider_config import ProviderConfig, ProviderGroup, resolve_providers_for_search
from .provider_execution import _search_single_provider
from .provider_options import ProviderOptionBundle
from .provider_plan import ProviderExecutionPlan

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")


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
        active_configs = resolve_providers_for_search(intent)
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
            )

        async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
            all_results: list[list[WebSearchResult]] = []
            provider_names: list[str] = []

            # Group by ProviderGroup for dispatch
            free_configs = [c for c in active_configs if c.group == ProviderGroup.free]
            serp_paid_configs = [c for c in active_configs if c.group == ProviderGroup.serp_paid]
            other_configs = [c for c in active_configs if c.group == ProviderGroup.other]

            # free group: fire all concurrently (no semaphore)
            if free_configs:
                free_tasks = [_provider_call(c, client) for c in free_configs]
                free_results = await asyncio.gather(*free_tasks, return_exceptions=True)
                for config, result in zip(free_configs, free_results, strict=False):
                    if isinstance(result, BaseException):
                        LOGGER.warning(
                            "Provider task %s failed before returning results: %s",
                            config.name,
                            result,
                        )
                        continue
                    all_results.append(result)
                    provider_names.append(config.name)

            # serp_paid group: fire all, semaphore-gated
            if serp_paid_configs:
                semaphore = asyncio.Semaphore(settings.serp_semaphore_limit)

                async def _search_with_semaphore(
                    config: ProviderConfig,
                ) -> list[WebSearchResult]:
                    async with semaphore:
                        return await _provider_call(config, client)

                paid_tasks = [_search_with_semaphore(c) for c in serp_paid_configs]
                paid_results = asyncio.gather(*paid_tasks, return_exceptions=True)
                if hasattr(paid_results, "__await__"):
                    paid_results = await paid_results
                else:
                    for task in paid_tasks:
                        if hasattr(task, "close"):
                            task.close()
                for config, result in zip(serp_paid_configs, paid_results, strict=False):
                    if isinstance(result, BaseException):
                        LOGGER.warning(
                            "Provider task %s failed before returning results: %s",
                            config.name,
                            result,
                        )
                        continue
                    all_results.append(result)
                    provider_names.append(config.name)

            # other group: fire all concurrently (already filtered by intent via resolve_providers_for_search)
            if other_configs:
                other_tasks = [_provider_call(c, client) for c in other_configs]
                other_results = await asyncio.gather(*other_tasks, return_exceptions=True)
                for config, result in zip(other_configs, other_results, strict=False):
                    if isinstance(result, BaseException):
                        LOGGER.warning(
                            "Provider task %s failed before returning results: %s",
                            config.name,
                            result,
                        )
                        continue
                    all_results.append(result)
                    provider_names.append(config.name)

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