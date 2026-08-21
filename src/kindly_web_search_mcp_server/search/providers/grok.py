"""Native xAI Grok search through the Responses API.

This module deliberately calls xAI directly instead of routing search through
OpenRouter. xAI's Responses API owns the server-side ``web_search`` and
``x_search`` tools, returns a complete citation list, and exposes billable tool
usage separately from model token usage. Keeping this adapter direct avoids an
extra routing layer, preserves X-search semantics, and makes cost accounting
observable.

Vertex AI can serve Grok Responses models, but Google's current managed Grok
Responses documentation does not expose xAI's native server-side search tools.
Selecting Vertex for this search adapter therefore fails explicitly instead of
returning an answer that was not grounded by web/X search.

The adapter has two entry points:

* ``search_grok_xai`` is the light provider used by the RRF search pipeline.
* ``grok_search`` is the synthesized-answer MCP/CLI tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from ...models import WebSearchResult
from ...prompts.provider_grok import build_provider_grok_prompt
from ...settings import settings
from ...telemetry.spans import create_llm_operation_span
from ...telemetry.span_enhancements import set_span_error, set_span_success
from ...telemetry.usage import extract_llm_usage
from .base import ProviderRequestError, run_provider

__all__ = [
    "GrokBackendCapabilityError",
    "GrokProviderConfigError",
    "GrokProviderError",
    "GrokSearchResult",
    "grok_search",
    "search_grok_xai",
]

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.5"
REQUEST_TIMEOUT = 60.0
MAX_RESULTS = 10
MAX_DOMAIN_FILTERS = 5

_NATIVE_SEARCH_GUIDANCE = """
Use both native search tools when they can improve freshness: web_search for
web sources and x_search for public X posts. Prefer the narrowest useful set of
search calls, cite every factual claim, and return no more than the requested
number of source links. Do not claim that a source was consulted unless it is
present in the response citations.
""".strip()


class GrokProviderError(ProviderRequestError):
    """Base error for direct xAI Grok requests."""


class GrokProviderConfigError(ValueError):
    """Raised when the direct Grok backend is not configured."""


class GrokBackendCapabilityError(GrokProviderConfigError):
    """Raised when a configured backend cannot provide native search."""


@dataclass
class GrokSearchResult:
    """Synthesized answer and diagnostics returned by ``grok_search``."""

    query: str
    answer: str
    citations: list[dict[str, str]]
    model: str
    search_queries_used: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    backend: str = "xai"
    web_search_calls: int = 0
    x_search_calls: int = 0
    sources_used: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def model_used(self) -> str:
        return self.model

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Provide the Pydantic-like dump expected by the CLI service."""
        value: dict[str, Any] = {
            "query": self.query,
            "answer": self.answer,
            "citations": self.citations,
            "model": self.model,
            "model_used": self.model_used,
            "search_queries_used": self.search_queries_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
            "backend": self.backend,
            "web_search_calls": self.web_search_calls,
            "x_search_calls": self.x_search_calls,
            "sources_used": self.sources_used,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }
        if exclude_none:
            return {key: item for key, item in value.items() if item is not None}
        return value


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _clean_filter_values(values: list[str] | None, *, name: str) -> list[str] | None:
    if values is None:
        return None
    cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    if len(cleaned) > MAX_DOMAIN_FILTERS:
        raise ValueError(f"{name} supports at most {MAX_DOMAIN_FILTERS} domains")
    return cleaned or None


def _build_search_tools(
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build xAI native tools, applying filters only to web search.

    xAI treats allowed and excluded web domains as mutually exclusive. X
    search has separate handle/date filters, which are intentionally not
    exposed by the current MCP contract; callers can still search X freely.
    """
    allowed = _clean_filter_values(allowed_domains, name="allowed_domains")
    excluded = _clean_filter_values(excluded_domains, name="excluded_domains")
    if allowed and excluded:
        raise ValueError("allowed_domains and excluded_domains are mutually exclusive")

    web_search: dict[str, Any] = {"type": "web_search"}
    if allowed:
        web_search["filters"] = {"allowed_domains": allowed}
    elif excluded:
        web_search["filters"] = {"excluded_domains": excluded}
    return [web_search, {"type": "x_search"}]


def _resolved_backend() -> str:
    backend = str(getattr(settings, "grok_backend", "xai") or "xai").strip().lower()
    if backend not in {"auto", "xai", "vertex"}:
        raise GrokProviderConfigError("GROK_BACKEND must be one of: auto, xai, vertex")
    if backend == "auto":
        return "xai" if getattr(settings, "grok_xai_api_key", "").strip() else "vertex"
    return backend


def _resolved_model(model: str | None = None) -> str:
    value = str(model or getattr(settings, "grok_model", "") or DEFAULT_MODEL).strip()
    return value.removeprefix("x-ai/") or DEFAULT_MODEL


def _check_grok_configured() -> str:
    """Validate that native search can run and return its backend name."""
    backend = _resolved_backend()
    if backend == "vertex":
        raise GrokBackendCapabilityError(
            "Vertex AI's managed Grok Responses endpoint does not expose xAI's "
            "native web_search or x_search tools. Set GROK_BACKEND=xai and "
            "configure XAI_API_KEY for grounded web/X search."
        )
    api_key = getattr(settings, "grok_xai_api_key", "").strip()
    if not api_key:
        raise GrokProviderConfigError(
            "XAI_API_KEY is not set. Configure it for direct native Grok web/X search."
        )
    return backend


def _get_headers() -> dict[str, str]:
    api_key = getattr(settings, "grok_xai_api_key", "").strip()
    if not api_key:
        raise GrokProviderConfigError(
            "XAI_API_KEY is not set. Configure it for direct native Grok web/X search."
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _responses_url() -> str:
    base_url = str(getattr(settings, "grok_xai_base_url", "") or "").strip()
    return f"{(base_url or XAI_RESPONSES_URL.removesuffix('/responses')).rstrip('/')}/responses"


def _build_prompts(query: str, research_goal: str | None) -> tuple[str, str]:
    system_prompt, user_prompt = build_provider_grok_prompt(
        query=query,
        research_goal=research_goal,
        provider_name="grok",
    )
    return f"{system_prompt}\n\n{_NATIVE_SEARCH_GUIDANCE}", user_prompt


def _build_responses_payload(
    *,
    query: str,
    research_goal: str | None,
    model: str,
    num_results: int,
    allowed_domains: list[str] | None,
    excluded_domains: list[str] | None,
) -> dict[str, Any]:
    system_prompt, user_prompt = _build_prompts(query, research_goal)
    return {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": _build_search_tools(allowed_domains, excluded_domains),
        "max_output_tokens": max(256, min(num_results * 300, 2400)),
        "max_turns": max(1, min(int(getattr(settings, "grok_max_turns", 3) or 3), 5)),
        "store": bool(getattr(settings, "grok_store", False)),
    }


async def _post_responses(
    client: httpx.AsyncClient,
    payload: Mapping[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    response = await client.post(
        _responses_url(),
        headers=_get_headers(),
        json=dict(payload),
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise GrokProviderError("xAI Responses response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise GrokProviderError("xAI Responses response was not a JSON object")
    return data


def _extract_output_text(data: Mapping[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str):
        return direct.strip()

    chunks: list[str] = []
    output = data.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        item_mapping = _as_mapping(item)
        if not item_mapping or item_mapping.get("type") != "message":
            continue
        content = item_mapping.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            part_mapping = _as_mapping(part)
            if not part_mapping:
                continue
            text = part_mapping.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()


def _citation_from_value(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value.strip():
        return {"url": value.strip(), "title": "", "snippet": ""}
    mapping = _as_mapping(value)
    if not mapping:
        return None
    nested = _as_mapping(mapping.get("url_citation"))
    if nested:
        mapping = nested
    url = mapping.get("url") or mapping.get("link") or mapping.get("source_url")
    if not isinstance(url, str) or not url.strip():
        return None
    title = mapping.get("title") or mapping.get("name") or ""
    snippet = mapping.get("snippet") or mapping.get("text") or mapping.get("description") or ""
    return {
        "url": url.strip(),
        "title": title.strip() if isinstance(title, str) else "",
        "snippet": snippet.strip() if isinstance(snippet, str) else "",
    }


def _extract_citations(data: Mapping[str, Any]) -> list[dict[str, str]]:
    """Normalize top-level citations and Responses output annotations."""
    citations: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        citation = _citation_from_value(value)
        if not citation or citation["url"] in seen:
            return
        seen.add(citation["url"])
        if not citation["title"]:
            citation["title"] = citation["url"]
        if not citation["snippet"]:
            citation["snippet"] = citation["title"]
        citations.append(citation)

    top_level = data.get("citations")
    if isinstance(top_level, list):
        for item in top_level:
            add(item)

    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            item_mapping = _as_mapping(item)
            if not item_mapping:
                continue
            for key in ("citations", "sources"):
                values = item_mapping.get(key)
                if isinstance(values, list):
                    for value in values:
                        add(value)
            action = _as_mapping(item_mapping.get("action"))
            if action:
                values = action.get("sources")
                if isinstance(values, list):
                    for value in values:
                        add(value)
            content = item_mapping.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                part_mapping = _as_mapping(part)
                if not part_mapping:
                    continue
                annotations = part_mapping.get("annotations")
                if isinstance(annotations, list):
                    for annotation in annotations:
                        add(annotation)
    return citations


def _tool_usage(data: Mapping[str, Any], citations_count: int) -> dict[str, int]:
    usage = _as_mapping(data.get("usage")) or {}
    server_usage: dict[str, Any] = {}
    for key in ("server_side_tool_usage", "server_side_tool_usage_details"):
        values = _as_mapping(usage.get(key))
        if values:
            server_usage.update(values)

    def count(*keys: str) -> int:
        for key in keys:
            value = _int_value(server_usage.get(key))
            if value is not None:
                return max(0, value)
        return 0

    web_calls = count("web_search_calls", "SERVER_SIDE_TOOL_WEB_SEARCH")
    x_calls = count("x_search_calls", "SERVER_SIDE_TOOL_X_SEARCH")

    output_web_calls = 0
    output_x_calls = 0
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            item_type = _as_mapping(item)
            if not item_type:
                continue
            if item_type.get("type") == "web_search_call":
                output_web_calls += 1
            elif item_type.get("type") == "x_search_call":
                output_x_calls += 1
    if web_calls == 0:
        web_calls = output_web_calls
    if x_calls == 0:
        x_calls = output_x_calls
    sources = _int_value(usage.get("num_sources_used"))
    if sources is None:
        sources = citations_count
    return {
        "web_search_calls": web_calls,
        "x_search_calls": x_calls,
        "search_queries_used": web_calls + x_calls,
        "sources_used": max(0, sources),
    }


def _token_fields(data: Mapping[str, Any]) -> dict[str, int | None]:
    usage = _as_mapping(data.get("usage")) or {}
    usage_model = {"usage": usage}
    token_usage = extract_llm_usage(usage_model)
    input_details = _as_mapping(usage.get("input_tokens_details")) or {}
    output_details = _as_mapping(usage.get("output_tokens_details")) or {}
    return {
        "input_tokens": token_usage.input_tokens if token_usage else None,
        "output_tokens": token_usage.output_tokens if token_usage else None,
        "total_tokens": token_usage.total_tokens if token_usage else None,
        "cached_input_tokens": _int_value(input_details.get("cached_tokens")),
        "reasoning_tokens": _int_value(output_details.get("reasoning_tokens")),
    }


def _results_from_response(data: Mapping[str, Any], num_results: int) -> list[WebSearchResult]:
    return [
        WebSearchResult(
            title=citation["title"],
            link=citation["url"],
            snippet=citation["snippet"],
        )
        for citation in _extract_citations(data)[:num_results]
    ]


async def search_grok_xai(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Run native xAI web/X search and return normalized RRF results."""
    if not query or not query.strip() or num_results < 1:
        return []

    backend = _check_grok_configured()
    model = _resolved_model()
    timeout = float(getattr(settings, "grok_timeout_seconds", REQUEST_TIMEOUT) or REQUEST_TIMEOUT)
    payload = _build_responses_payload(
        query=query.strip(),
        research_goal=None,
        model=model,
        num_results=max(1, min(num_results, MAX_RESULTS)),
        allowed_domains=None,
        excluded_domains=None,
    )

    async def request(client: httpx.AsyncClient) -> dict[str, Any]:
        return await _post_responses(client, payload, timeout=timeout)

    def parse_response(data: dict[str, Any]) -> list[WebSearchResult]:
        return _results_from_response(data, max(1, min(num_results, MAX_RESULTS)))

    with create_llm_operation_span(
        "search",
        system="xai",
        attributes={
            "llm.model_name": model,
            "search.backend": backend,
            "search.query": query[:500],
            "search.num_results_requested": num_results,
            "search.native_tools": "web_search,x_search",
        },
    ) as span:
        try:
            results = await run_provider(
                "grok_xai",
                query,
                num_results,
                request=request,
                parse_response=parse_response,
                http_client=http_client,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            set_span_error(span, exc)
            raise
        span.set_attribute("search.source_count", len(results))
        set_span_success(span, result_count=len(results))
        return results


async def grok_search(
    query: str,
    research_goal: str,
    *,
    model: str | None = None,
    num_results: int = 5,
    allowed_domains: list[str] | None = None,
    excluded_domains: list[str] | None = None,
    timeout: float | None = None,
) -> GrokSearchResult:
    """Synthesize a direct xAI web+X search response with citations.

    ``GROK_BACKEND=xai`` is required because Vertex's managed Grok endpoint
    currently documents Responses text generation, function calling, and
    structured output, but not xAI's server-side ``web_search``/``x_search``.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")

    backend = _check_grok_configured()
    resolved_model = _resolved_model(model)
    resolved_timeout = timeout or getattr(settings, "grok_timeout_seconds", REQUEST_TIMEOUT)
    max_results = max(1, min(num_results, MAX_RESULTS))
    payload = _build_responses_payload(
        query=query.strip(),
        research_goal=research_goal,
        model=resolved_model,
        num_results=max_results,
        allowed_domains=allowed_domains,
        excluded_domains=excluded_domains,
    )

    with create_llm_operation_span(
        "responses",
        system="xai",
        attributes={
            "llm.model_name": resolved_model,
            "search.backend": backend,
            "search.query": query[:500],
            "search.research_goal": research_goal[:500],
            "search.num_results_requested": max_results,
            "search.native_tools": "web_search,x_search",
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=resolved_timeout) as client:
                data = await _post_responses(client, payload, timeout=resolved_timeout)
            answer = _extract_output_text(data)
            citations = _extract_citations(data)
            usage = _tool_usage(data, len(citations))
            tokens = _token_fields(data)
            span.set_attribute("search.citation_count", len(citations))
            span.set_attribute("search.answer_chars", len(answer))
            span.set_attribute("search.web_search_calls", usage["web_search_calls"])
            span.set_attribute("search.x_search_calls", usage["x_search_calls"])
            span.set_attribute("search.sources_used", usage["sources_used"])
            for field, attribute in (
                ("input_tokens", "llm.token_count.prompt"),
                ("output_tokens", "llm.token_count.completion"),
                ("total_tokens", "llm.token_count.total"),
                ("cached_input_tokens", "llm.token_count.cached"),
                ("reasoning_tokens", "llm.token_count.reasoning"),
            ):
                value = tokens[field]
                if value is not None:
                    span.set_attribute(attribute, value)
            set_span_success(span, result_count=len(citations))
            error_msg = None
            if not answer and not citations:
                error_msg = "No answer or citations returned by Grok"
            return GrokSearchResult(
                query=query,
                answer=answer,
                citations=citations[:max_results],
                model=str(data.get("model") or resolved_model),
                search_queries_used=usage["search_queries_used"],
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                error=error_msg,
                backend=backend,
                web_search_calls=usage["web_search_calls"],
                x_search_calls=usage["x_search_calls"],
                sources_used=usage["sources_used"],
                cached_input_tokens=tokens["cached_input_tokens"],
                reasoning_tokens=tokens["reasoning_tokens"],
                total_tokens=tokens["total_tokens"],
            )
        except httpx.TimeoutException as exc:
            set_span_error(span, exc)
            raise httpx.HTTPError(f"Grok search timed out after {resolved_timeout}s") from exc
        except Exception as exc:
            set_span_error(span, exc)
            raise
