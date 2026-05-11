"""Instrumented search module with OpenTelemetry telemetry.

This module wraps the standard search functions with telemetry tracking.
Import this instead of the regular search module to get Grafana Cloud visibility.

USAGE:
    # In server.py, replace:
    from .search import search_single_query

    # With:
    from .search_instrumented import search_single_query

    # And initialize telemetry at startup:
    from .telemetry import init_telemetry
    init_telemetry()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from opentelemetry import trace

from .models import WebSearchResult
from .search import (
    ProviderBudget,
    ProviderConfig,
    WebSearchProviderError,
    _search_single_provider as _original_search_single_provider,
    resolve_providers_for_search,
)
from .search.merge import merge_search_results
from .telemetry import (
    get_tracer,
    record_provider_call,
    record_merge,
    add_results_to_span,
)
from .utils.diagnostics import Diagnostics

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")


async def _search_single_provider_instrumented(
    provider_name: str,
    provider_fn: Any,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
    budget: ProviderBudget | None = None,
) -> list[WebSearchResult]:
    """Search a single provider with telemetry tracking.

    Creates a span for each provider call and records:
    - Duration (histogram metric)
    - Result count (counter metric)
    - Status (success/error)
    - Actual result titles/links as span events (VISIBLE IN GRAFANA)
    """
    start_time = time.time()

    with tracer.start_as_current_span(
        f"provider.{provider_name}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            "provider": provider_name,
            "query": query[:200],
            "num_results_requested": num_results,
        },
    ) as span:
        try:
            results = await _original_search_single_provider(
                provider_name, provider_fn, query, num_results, http_client, budget
            )

            duration = time.time() - start_time

            # Record metrics
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=len(results),
                status="success",
            )

            # Add span attributes
            span.set_attribute("result_count", len(results))
            span.set_attribute("duration_ms", duration * 1000)
            span.set_attribute("status", "success")

            # ADD ACTUAL RESULTS TO SPAN - THIS IS WHAT YOU WANT TO SEE IN GRAFANA
            add_results_to_span(span, results, max_results=5)

            LOGGER.debug(f"Provider {provider_name}: {len(results)} results in {duration*1000:.1f}ms")
            return results

        except Exception as e:
            duration = time.time() - start_time

            # Record metrics
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=0,
                status="error",
                error_type=type(e).__name__,
            )

            # Add span attributes
            span.set_attribute("status", "error")
            span.set_attribute("error_type", type(e).__name__)
            span.set_attribute("error_message", str(e)[:500])
            span.record_exception(e)

            LOGGER.warning(f"Provider {provider_name} failed: {type(e).__name__}: {e}")
            return []


async def search_single_query(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
) -> list[WebSearchResult]:
    """Search with full OpenTelemetry instrumentation.

    Creates spans and metrics for:
    - Overall search operation
    - Each provider call
    - RRF merge operation

    Results are visible in Grafana Cloud trace view.
    """
    start_time = time.time()

    with tracer.start_as_current_span(
        "web_search",
        kind=trace.SpanKind.SERVER,
        attributes={
            "query": query[:200],
            "num_results_requested": num_results,
            "providers_requested": str(providers or []),
        },
    ) as span:
        budget = ProviderBudget()
        active_configs = resolve_providers_for_search(providers)

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

        async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
            all_results: list[list[WebSearchResult]] = []
            provider_names: list[str] = []

            free_providers = [c for c in active_configs if c.is_free]
            paid_providers = [c for c in active_configs if not c.is_free]

            # Tier 1: Free providers concurrently
            if free_providers:
                free_tasks = [
                    _search_single_provider_instrumented(
                        c.name, c.search_fn, query, num_results, client, budget
                    )
                    for c in free_providers
                ]
                free_results = await asyncio.gather(*free_tasks, return_exceptions=True)
                for i, r in enumerate(free_results):
                    if isinstance(r, list):
                        all_results.append(r)
                        provider_names.append(free_providers[i].name)

            # Tier 2: Paid providers with semaphore
            if paid_providers:
                semaphore = asyncio.Semaphore(max(2, len(paid_providers)))

                async def _search_with_semaphore(config: ProviderConfig) -> list[WebSearchResult]:
                    async with semaphore:
                        return await _search_single_provider_instrumented(
                            config.name, config.search_fn, query, num_results, client, budget
                        )

                paid_results = await asyncio.gather(
                    *[_search_with_semaphore(c) for c in paid_providers],
                    return_exceptions=True,
                )
                for i, r in enumerate(paid_results):
                    if isinstance(r, list):
                        all_results.append(r)
                        provider_names.append(paid_providers[i].name)

            # RRF merge with telemetry
            merge_start = time.time()
            with tracer.start_as_current_span(
                "rrf_merge",
                kind=trace.SpanKind.INTERNAL,
                attributes={
                    "input_lists": len(all_results),
                    "total_input_results": sum(len(r) for r in all_results),
                },
            ) as merge_span:
                merged = merge_search_results(all_results) if len(all_results) > 1 else (
                    all_results[0] if all_results else []
                )
                merge_duration = time.time() - merge_start

                merge_span.set_attribute("output_count", len(merged))
                merge_span.set_attribute("duration_ms", merge_duration * 1000)

                record_merge(merge_duration, len(all_results), len(merged))

            # Add final results to main span
            span.set_attribute("result_count", len(merged))
            span.set_attribute("providers_used", provider_names)
            add_results_to_span(span, merged, max_results=10)

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

            # Increment search total counter
            from .telemetry import get_search_total_metric
            get_search_total_metric().add(1)

            return results

        except Exception as e:
            span.record_exception(e)
            span.set_attribute("error", str(e)[:500])
            raise