"""Canonical multi-provider search orchestration with OpenTelemetry instrumentation."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from opentelemetry import trace

from ..models import WebSearchResult
from ..telemetry import add_results_to_span, get_search_total_metric, get_tracer
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from .budget import ProviderBudget
from .errors import WebSearchProviderError
from .merge import merge_search_results
from .options import SearchOptions
from .provider_execution import _search_single_provider
from .provider_config import ProviderConfig

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")


def _resolve_active_providers(providers: list[str] | None) -> list[ProviderConfig]:
    from . import resolve_providers_for_search as resolve_package_providers

    return resolve_package_providers(providers)


async def search_single_query(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    search_options: SearchOptions | None = None,
    provider_arguments: dict[str, dict[str, object]] | None = None,
) -> list[WebSearchResult]:
    """Search with full OpenTelemetry instrumentation."""
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
        active_configs = _resolve_active_providers(providers)

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
                    _search_single_provider(
                        c.name,
                        c.search_fn,
                        query,
                        num_results,
                        client,
                        search_options,
                        budget,
                        provider_arguments.get(c.name) if provider_arguments else None,
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
                        return await _search_single_provider(
                            config.name,
                            config.search_fn,
                            query,
                            num_results,
                            client,
                            search_options,
                            budget,
                            provider_arguments.get(config.name)
                            if provider_arguments
                            else None,
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
