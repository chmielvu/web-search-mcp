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
from .search.options import SearchOptions
from .telemetry import (
    get_tracer,
    record_provider_call,
    add_results_to_span,
)
from .utils.diagnostics import Diagnostics
from .utils.observability import emit_observability_event, serialize_search_results

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")


async def _search_single_provider_instrumented(
    provider_name: str,
    provider_fn: Any,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
    search_options: SearchOptions | None = None,
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
            # Lazy import to avoid circular dependency.
            from .search.provider_health import get_provider_health  # noqa: PLC0415

            results = await _original_search_single_provider(
                provider_name,
                provider_fn,
                query,
                num_results,
                http_client,
                search_options,
                budget,
            )

            duration = time.time() - start_time

            # Mark provider as healthy
            get_provider_health().mark_success(provider_name)

            # Record metrics
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=len(results),
                status_code=200,
            )

            # Add span attributes
            span.set_attribute("result_count", len(results))
            span.set_attribute("duration_ms", duration * 1000)
            span.set_attribute("status", "success")

            # ADD ACTUAL RESULTS TO SPAN - THIS IS WHAT YOU WANT TO SEE IN GRAFANA
            add_results_to_span(span, results, max_results=5)
            emit_observability_event(
                LOGGER,
                "provider.search.result",
                provider_name=provider_name,
                query=query,
                num_results_requested=num_results,
                duration_ms=round(duration * 1000, 3),
                result_count=len(results),
                results=serialize_search_results(results, max_results=5),
            )

            LOGGER.debug(
                f"Provider {provider_name}: {len(results)} results in {duration * 1000:.1f}ms"
            )
            return results

        except Exception as e:
            duration = time.time() - start_time

            # Mark provider as failed (triggers cooldown)
            get_provider_health().mark_failure(provider_name)

            # Record metrics
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=0,
                status_code=500,
                error_type=type(e).__name__,
            )

            # Add span attributes
            span.set_attribute("status", "error")
            span.set_attribute("error_type", type(e).__name__)
            span.set_attribute("error_message", str(e)[:500])
            span.record_exception(e)
            emit_observability_event(
                LOGGER,
                "provider.search.error",
                level=logging.WARNING,
                provider_name=provider_name,
                query=query,
                num_results_requested=num_results,
                duration_ms=round(duration * 1000, 3),
                error_type=type(e).__name__,
                error_message=str(e),
            )

            LOGGER.warning(f"Provider {provider_name} failed: {type(e).__name__}: {e}")
            return []


async def search_single_query(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    search_options: SearchOptions | None = None,
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

            if free_providers:
                free_tasks = [
                    _search_single_provider_instrumented(
                        c.name,
                        c.search_fn,
                        query,
                        num_results,
                        client,
                        search_options,
                        budget,
                    )
                    for c in free_providers
                ]
                free_results = asyncio.gather(*free_tasks, return_exceptions=True)
                if hasattr(free_results, "__await__"):
                    free_results = await free_results
                else:
                    for task in free_tasks:
                        if hasattr(task, "close"):
                            task.close()
                for config, result in zip(free_providers, free_results, strict=False):
                    if isinstance(result, BaseException):
                        LOGGER.warning(
                            "Provider task %s failed before returning results: %s",
                            config.name,
                            result,
                        )
                        continue
                    all_results.append(result)
                    provider_names.append(config.name)

            if paid_providers:
                semaphore = asyncio.Semaphore(max(2, len(paid_providers)))

                async def _search_with_semaphore(
                    config: ProviderConfig,
                ) -> list[WebSearchResult]:
                    async with semaphore:
                        return await _search_single_provider_instrumented(
                            config.name,
                            config.search_fn,
                            query,
                            num_results,
                            client,
                            search_options,
                            budget,
                        )

                paid_tasks = [_search_with_semaphore(c) for c in paid_providers]
                paid_results = asyncio.gather(*paid_tasks, return_exceptions=True)
                if hasattr(paid_results, "__await__"):
                    paid_results = await paid_results
                else:
                    for task in paid_tasks:
                        if hasattr(task, "close"):
                            task.close()
                for config, result in zip(paid_providers, paid_results, strict=False):
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
                results=serialize_search_results(
                    merged[:num_results], max_results=num_results
                ),
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

            from .telemetry import get_search_total_metric

            get_search_total_metric().add(1)

            return results

        except Exception as e:
            span.record_exception(e)
            span.set_attribute("error", str(e)[:500])
            raise
