"""Telemetry span helper functions."""
from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import urlparse

from opentelemetry import metrics, trace

from ..llm.phoenix_tracing import current_openinference_attributes
from .attributes import (
    CACHE_TYPE,
    CIRCUIT_STATE,
    CONTENT_STAGE,
    CONTENT_URL,
    GEN_AI_OPERATION_NAME,
    GEN_AI_TOOL_NAME,
    HTTP_REQUEST_METHOD,
    INPUT_MIME_TYPE,
    INPUT_VALUE,
    LLM_INVOCATION_PARAMETERS,
    LLM_MODEL_NAME,
    LLM_SYSTEM,
    MCP_METHOD_NAME,
    MCP_SERVER_NAME,
    MCP_SESSION_ID,
    OPENINFERENCE_SPAN_KIND,
    OPENINFERENCE_SPAN_KIND_CHAIN,
    OPENINFERENCE_SPAN_KIND_LLM,
    PROVIDER_NAME,
    RERANK_INPUT_COUNT,
    RERANK_MODEL,
    RERANK_STAGE,
    REWRITE_MODEL,
    REWRITE_POLICY,
    RPC_JSONRPC_VERSION,
    RPC_SYSTEM,
    SEARCH_MERGE_ALGORITHM,
    SEARCH_NUM_RESULTS_REQUESTED,
    SEARCH_PROVIDERS_REQUESTED,
    SEARCH_QUERY,
    SERVER_ADDRESS,
    SERVER_PORT,
    URL_FULL,
)

def get_tracer(name: str = "web-search-mcp") -> trace.Tracer:
    """Get tracer for manual span creation."""
    return trace.get_tracer(name)


def get_meter(name: str = "web-search-mcp") -> metrics.Meter:
    """Get meter for custom metrics."""
    return metrics.get_meter(name)
def create_search_span(
    query: str,
    num_results: int,
    providers_requested: list[str] | None,
) -> trace.Span:
    """Create span for search operation with semantic conventions.

    Use as context manager:
        with create_search_span(query, 10, ["searxng"]) as span:
            results = await search(...)
            span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, len(results))
    """
    tracer = get_tracer()
    return tracer.start_as_current_span(  # type: ignore[return-value]
        "web_search",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            OPENINFERENCE_SPAN_KIND: OPENINFERENCE_SPAN_KIND_CHAIN,
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
            SEARCH_PROVIDERS_REQUESTED: str(providers_requested or []),
            MCP_SERVER_NAME: "web-search-mcp",
            INPUT_VALUE: query[:500],
            INPUT_MIME_TYPE: "text/plain",
        },
    )


def create_provider_span(
    provider: str,
    query: str,
    num_results: int,
    url: str,
) -> trace.Span:
    """Create span for provider call with HTTP semantic conventions.

    Use as context manager:
        with create_provider_span("searxng", query, 10, "http://localhost:8080/search") as span:
            response = await httpx.get(url)
            span.set_attribute(HTTP_RESPONSE_STATUS_CODE, response.status_code)
            add_results_to_span(span, results)
    """
    tracer = get_tracer()

    # Parse URL for server.address and server.port

    parsed = urlparse(url)

    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"provider.{provider}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            OPENINFERENCE_SPAN_KIND: "RETRIEVER",
            PROVIDER_NAME: provider,
            HTTP_REQUEST_METHOD: "GET",
            URL_FULL: url[:500],
            SERVER_ADDRESS: parsed.hostname or "",
            SERVER_PORT: parsed.port or 80,
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
        },
    )


def create_llm_operation_span(
    operation: str,
    *,
    system: str,
    attributes: Mapping[str, Any] | None = None,
) -> trace.Span:
    """Create a client span for an LLM or AI-search operation.

    Use this for outbound model calls that should appear as first-class
    Langfuse observations rather than just transport-level HTTP spans.
    """
    tracer = get_tracer()
    span_attributes: dict[str, Any] = {
        **current_openinference_attributes(),
        OPENINFERENCE_SPAN_KIND: OPENINFERENCE_SPAN_KIND_LLM,
        LLM_SYSTEM: system,
        "llm.operation.name": operation,
    }
    if attributes:
        span_attributes.update(attributes)
        invocation_parameters = {
            key: value
            for key, value in attributes.items()
            if key
            not in {
                INPUT_VALUE,
                INPUT_MIME_TYPE,
                LLM_MODEL_NAME,
                LLM_SYSTEM,
                OPENINFERENCE_SPAN_KIND,
            }
        }
        if invocation_parameters:
            span_attributes[LLM_INVOCATION_PARAMETERS] = json.dumps(
                invocation_parameters,
                default=str,
                sort_keys=True,
            )
        if INPUT_VALUE not in span_attributes and "search.query" in attributes:
            span_attributes[INPUT_VALUE] = attributes["search.query"]
            span_attributes[INPUT_MIME_TYPE] = "text/plain"
    if LLM_MODEL_NAME not in span_attributes:
        raise ValueError(f"{operation} span requires {LLM_MODEL_NAME}")
    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"ai.{system}.{operation}",
        kind=trace.SpanKind.CLIENT,
        attributes=span_attributes,
    )


def create_chain_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> trace.Span:
    """Create a chain/root span for end-to-end orchestration."""
    tracer = get_tracer()
    span_attributes: dict[str, Any] = {
        **current_openinference_attributes(),
        OPENINFERENCE_SPAN_KIND: OPENINFERENCE_SPAN_KIND_CHAIN,
    }
    if attributes:
        span_attributes.update(attributes)
        if INPUT_VALUE not in span_attributes:
            query_like = attributes.get("search.query") or attributes.get("query")
            if query_like is not None:
                span_attributes[INPUT_VALUE] = str(query_like)[:500]
                span_attributes[INPUT_MIME_TYPE] = "text/plain"
    return tracer.start_as_current_span(  # type: ignore[return-value]
        name,
        kind=trace.SpanKind.SERVER,
        attributes=span_attributes,
    )


def create_mcp_tool_span(
    tool_name: str,
    method: str = "tools/call",
    session_id: str | None = None,
) -> trace.Span:
    """Create span for MCP tool invocation.

    Use at server entry point for each tool call.
    """
    tracer = get_tracer()
    attributes = {
        OPENINFERENCE_SPAN_KIND: "TOOL",
        MCP_METHOD_NAME: method,
        MCP_SERVER_NAME: "web-search-mcp",
        GEN_AI_TOOL_NAME: tool_name,
        GEN_AI_OPERATION_NAME: "execute_tool",
        RPC_SYSTEM: "jsonrpc",
        RPC_JSONRPC_VERSION: "2.0",
    }
    if session_id:
        attributes[MCP_SESSION_ID] = session_id

    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"{method} {tool_name}",
        kind=trace.SpanKind.SERVER,
        attributes=attributes,
    )


def create_content_span(
    stage: str,
    url: str,
) -> trace.Span:
    """Create span for content resolution stage."""
    tracer = get_tracer()


    parsed = urlparse(url)

    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"content.{stage}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            OPENINFERENCE_SPAN_KIND: "RETRIEVER",
            CONTENT_STAGE: stage,
            CONTENT_URL: url[:500],
            URL_FULL: url[:500],
            SERVER_ADDRESS: parsed.hostname or "",
            SERVER_PORT: parsed.port or 443 if parsed.scheme == "https" else 80,
        },
    )


def create_merge_span(input_lists: int, total_input: int) -> trace.Span:
    """Create span for RRF merge operation."""
    tracer = get_tracer()
    return tracer.start_as_current_span(  # type: ignore[return-value]
        "rrf_merge",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_MERGE_ALGORITHM: "rrf_k60",
            "merge.input_lists": input_lists,
            "merge.input_total": total_input,
        },
    )


def create_query_rewrite_span(
    query: str,
    policy: str,
) -> trace.Span:
    """Create span for query rewrite operation."""
    tracer = get_tracer()
    return tracer.start_as_current_span(  # type: ignore[return-value]
        "query.rewrite",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            OPENINFERENCE_SPAN_KIND: OPENINFERENCE_SPAN_KIND_CHAIN,
            SEARCH_QUERY: query[:500],
            REWRITE_POLICY: policy,
            REWRITE_MODEL: "cascade",
            MCP_SERVER_NAME: "web-search-mcp",
        },
    )


def create_rerank_span(
    stage: str,
    input_count: int,
) -> trace.Span:
    """Create span for reranking stage.

    Args:
        stage: "bi_encoder", "jina", or "diversity"
        input_count: Number of candidates to rerank
    """
    tracer = get_tracer()
    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"rerank.{stage}",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            OPENINFERENCE_SPAN_KIND: "RERANKER",
            RERANK_STAGE: stage,
            RERANK_INPUT_COUNT: input_count,
            RERANK_MODEL: "jina-reranker-v3" if stage == "jina" else "bi-encoder",
        },
    )


def create_circuit_breaker_span(
    provider: str,
    state: str,
) -> trace.Span:
    """Create span for circuit breaker state change."""
    tracer = get_tracer()
    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"circuit.{provider}",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            PROVIDER_NAME: provider,
            CIRCUIT_STATE: state,
            MCP_SERVER_NAME: "web-search-mcp",
        },
    )


def create_cache_span(
    cache_type: str,
    query: str | None = None,
    url: str | None = None,
) -> trace.Span:
    """Create span for cache operation."""
    tracer = get_tracer()
    attributes = {
        CACHE_TYPE: cache_type,
        MCP_SERVER_NAME: "web-search-mcp",
    }
    if query:
        attributes[SEARCH_QUERY] = query[:500]
    if url:
        attributes[URL_FULL] = url[:500]

    return tracer.start_as_current_span(  # type: ignore[return-value]
        f"cache.{cache_type}",
        kind=trace.SpanKind.INTERNAL,
        attributes=attributes,
    )

__all__ = [
    "create_cache_span",
    "create_chain_span",
    "create_circuit_breaker_span",
    "create_content_span",
    "create_llm_operation_span",
    "create_mcp_tool_span",
    "create_merge_span",
    "create_provider_span",
    "create_query_rewrite_span",
    "create_rerank_span",
    "create_search_span",
    "get_meter",
    "get_tracer",
]
