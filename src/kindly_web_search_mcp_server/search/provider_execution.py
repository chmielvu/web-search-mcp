"""Provider-level search execution with OpenTelemetry instrumentation."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import httpx
from opentelemetry import trace

from ..models import WebSearchResult
from ..telemetry import add_results_to_span, get_tracer, record_provider_call
from ..utils.observability import emit_observability_event
from .budget import ProviderBudget
from .circuit_breaker import CircuitBreaker
from .options import SearchOptions, build_search_query
from .provider_call import build_provider_call_kwargs
from .provider_health import get_provider_health

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")
_circuit_breaker = CircuitBreaker()


async def _search_single_provider(
    provider_name: str,
    provider_fn: Any,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
    search_options: SearchOptions | None = None,
    budget: ProviderBudget | None = None,
    provider_arguments: Mapping[str, object] | None = None,
) -> list[WebSearchResult]:
    """Search a single provider with circuit breaker, budget tracking, and spans."""
    if _circuit_breaker.is_open(provider_name):
        LOGGER.debug("Circuit breaker open for %s, skipping", provider_name)
        return []

    if budget is not None and not budget.can_spend(provider_name):
        LOGGER.debug("Budget exhausted for %s, skipping", provider_name)
        return []

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
            provider_query = build_search_query(query, search_options)
            provider_kwargs = build_provider_call_kwargs(
                provider_fn,
                search_options=search_options,
                provider_arguments=provider_arguments,
            )
            results = await provider_fn(
                provider_query,
                num_results=num_results,
                http_client=http_client,
                **provider_kwargs,
            )
            results = [
                result.model_copy(
                    update={
                        "providers": sorted({*(result.providers or []), provider_name}),
                    }
                )
                for result in results
            ]
            duration = time.time() - start_time

            _circuit_breaker.record_success(provider_name)
            if budget is not None:
                budget.record_call(provider_name, success=True)

            get_provider_health().mark_success(provider_name)
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=len(results),
                status_code=200,
            )
            span.set_attribute("result_count", len(results))
            span.set_attribute("duration_ms", duration * 1000)
            span.set_attribute("status", "success")
            add_results_to_span(span, results, max_results=5)
            emit_observability_event(
                LOGGER,
                "provider.search.result",
                provider_name=provider_name,
                query=query,
                num_results_requested=num_results,
                duration_ms=round(duration * 1000, 3),
                result_count=len(results),
                results=results,
            )
            LOGGER.debug(
                "Provider %s: %d results in %.1fms",
                provider_name,
                len(results),
                duration * 1000,
            )
            return results
        except Exception as exc:
            duration = time.time() - start_time

            _circuit_breaker.record_failure(provider_name)
            if budget is not None:
                budget.record_call(provider_name, success=False)

            get_provider_health().mark_failure(provider_name)
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=0,
                status_code=500,
                error_type=type(exc).__name__,
            )
            span.set_attribute("status", "error")
            span.set_attribute("error_type", type(exc).__name__)
            span.set_attribute("error_message", str(exc)[:500])
            span.record_exception(exc)
            emit_observability_event(
                LOGGER,
                "provider.search.error",
                level=logging.WARNING,
                provider_name=provider_name,
                query=query,
                num_results_requested=num_results,
                duration_ms=round(duration * 1000, 3),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            LOGGER.warning(
                "Provider %s failed: %s: %s", provider_name, type(exc).__name__, exc
            )
            return []
