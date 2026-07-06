"""Provider-level search execution with OpenTelemetry instrumentation."""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any, Mapping

import httpx
from opentelemetry import trace

from ..analytics.observability_store import _candidate_id, _canonical_result_id
from ..analytics.duckdb_store import insert_provider_calls as analytics_insert_provider_calls
from ..analytics.duckdb_store import (
    insert_provider_candidates as analytics_insert_provider_candidates,
)
from ..models import WebSearchResult
from ..telemetry import (
    INPUT_MIME_TYPE,
    INPUT_VALUE,
    OPENINFERENCE_SPAN_KIND,
    add_results_to_span,
    get_tracer,
    record_provider_call,
)
from ..utils.observability import emit_observability_event
from .budget import ProviderBudget
from .options import SearchOptions, build_search_query
from .provider_call import build_provider_call_kwargs
from .provider_health import get_provider_health

LOGGER = logging.getLogger(__name__)
tracer = get_tracer("web-search-mcp")


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After seconds from an HTTP exception, if present."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = getattr(response, "headers", {}).get("retry-after")
    if raw is None:
        return None
    try:
        return min(float(raw), 300.0)  # cap at 5 min
    except (ValueError, TypeError):
        return None


async def _search_single_provider(
    provider_name: str,
    provider_fn: Any,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
    search_options: SearchOptions | None = None,
    budget: ProviderBudget | None = None,
    provider_arguments: Mapping[str, object] | None = None,
    run_key: str | None = None,
    branch_index: int | None = None,
    branch_attempt_id: str | None = None,
    tool_call_id: str | None = None,
    cancel_token: Any | None = None,
) -> list[WebSearchResult]:
    """Search a single provider with unified health tracking, budget, and spans."""
    if not get_provider_health().is_healthy(provider_name):
        LOGGER.debug("Provider %s unhealthy (circuit open/cooldown), skipping", provider_name)
        return []

    if budget is not None and not budget.can_spend(provider_name):
        LOGGER.debug("Budget exhausted for %s, skipping", provider_name)
        return []

    start_time = time.time()
    with tracer.start_as_current_span(
        f"provider.{provider_name}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            OPENINFERENCE_SPAN_KIND: "RETRIEVER",
            "provider": provider_name,
            "query": query[:200],
            "num_results_requested": num_results,
            INPUT_VALUE: query[:200],
            INPUT_MIME_TYPE: "text/plain",
        },
    ) as span:
        try:
            provider_query = build_search_query(query, search_options)
            provider_kwargs = build_provider_call_kwargs(
                provider_fn,
                search_options=search_options,
                provider_arguments=provider_arguments,
            )
            call_kwargs: dict[str, Any] = {
                "num_results": num_results,
                "http_client": http_client,
                **provider_kwargs,
            }
            if cancel_token is not None:
                sig = inspect.signature(provider_fn)
                if "cancel_token" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    call_kwargs["cancel_token"] = cancel_token
            results = await provider_fn(provider_query, **call_kwargs)
            results = [
                result.model_copy(
                    update={
                        "providers": sorted({*(result.providers or []), provider_name}),
                    }
                )
                for result in results
            ]
            duration = time.time() - start_time

            if budget is not None:
                budget.record_call(provider_name, success=True)

            get_provider_health().mark_success(provider_name)
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=len(results),
                status_code=200,
            )
            # Best-effort dual-write: provider call to DuckDB analytics
            try:
                analytics_insert_provider_calls(
                    run_key=run_key or "",
                    provider=provider_name,
                    branch_index=branch_index,
                    branch_query=query,
                    num_results_requested=num_results,
                    num_results_returned=len(results),
                    duration_ms=round(duration * 1000, 3),
                    error_code=None,
                    error_message=None,
                    http_status=200,
                    payload_json={
                        "query": query,
                        "branch_attempt_id": branch_attempt_id,
                        "tool_call_id": tool_call_id,
                    },
                )
            except Exception as db_exc:
                LOGGER.debug("analytics insert_provider_calls failed: %s", db_exc)
            try:
                for rank, result in enumerate(results, start=1):
                    analytics_insert_provider_candidates(
                        run_key=run_key or "",
                        provider=provider_name,
                        branch_index=branch_index,
                        rank=rank,
                        title=result.title,
                        link=result.link,
                        snippet=result.snippet,
                        domain=result.domain or "",
                        score=result.score,
                        published_date=result.published_date,
                        payload_json={
                            "query": query,
                            "branch_query": query,
                            "branch_attempt_id": branch_attempt_id,
                            "tool_call_id": tool_call_id,
                            "candidate_id": _candidate_id(
                                result.link, result.title, result.snippet
                            ),
                            "canonical_result_id": _canonical_result_id(result.link),
                            "providers": result.providers or [],
                        },
                    )
            except Exception as db_exc:
                LOGGER.debug("analytics insert_provider_candidates failed: %s", db_exc)
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

            if budget is not None:
                budget.record_call(provider_name, success=False)

            is_rate_limit = (
                isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            )
            error_type = "rate_limit" if is_rate_limit else type(exc).__name__
            retry_after = _extract_retry_after(exc) if is_rate_limit else None
            get_provider_health().mark_failure_with_type(
                provider_name,
                error_type=error_type,
                retry_after_seconds=retry_after,
            )
            record_provider_call(
                provider=provider_name,
                duration_seconds=duration,
                result_count=0,
                status_code=500,
                error_type=type(exc).__name__,
            )
            # Best-effort dual-write: provider call to DuckDB analytics
            try:
                analytics_insert_provider_calls(
                    run_key=run_key or "",
                    provider=provider_name,
                    branch_index=branch_index,
                    branch_query=query,
                    num_results_requested=num_results,
                    num_results_returned=0,
                    duration_ms=round(duration * 1000, 3),
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:500],
                    http_status=500,
                    payload_json={
                        "query": query,
                        "branch_attempt_id": branch_attempt_id,
                        "tool_call_id": tool_call_id,
                    },
                )
            except Exception as db_exc:
                LOGGER.debug("analytics insert_provider_calls failed: %s", db_exc)
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
            LOGGER.warning("Provider %s failed: %s: %s", provider_name, type(exc).__name__, exc)
            return []
