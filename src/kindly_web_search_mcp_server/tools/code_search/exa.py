"""Exa Code/Context adapter for implementation-oriented retrieval."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...settings import settings
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    ProviderResponse,
    TextFragment,
    build_location_metadata,
)
from .query import QueryPlan

LOGGER = logging.getLogger(__name__)
_EXA_CONTEXT_URL = "https://api.exa.ai/context"
_CONTEXT_MAX_QUERY_CHARS = 2_000
_CONTEXT_MAX_RESPONSE_CHARS = 50_000
_CONTEXT_TOKENS = 5_000
_CONTEXT_DEEP_TOKENS = 10_000
_SOURCE_URL = re.compile(r"https?://[^\s<>()\[\]`]+")


def _api_key() -> str | None:
    value = os.environ.get("EXA_API_KEY", "").strip()
    return value or None


def _response_details(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    details: dict[str, Any] = {}
    for source_key, target_key in (
        ("requestId", "request_id"),
        ("tag", "tag"),
        ("error", "error"),
    ):
        value = payload.get(source_key)
        if isinstance(value, str):
            details[target_key] = value[:500]
        elif isinstance(value, (bool, float, int)):
            details[target_key] = value
    return details


def _diagnostic(
    message: str,
    *,
    query: str | None = None,
    failure_kind: str = "provider",
    outcome: str = "error",
    response: httpx.Response | None = None,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    retry_after: float | None = None
    status = response.status_code if response is not None else None
    if response is not None:
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after:
            try:
                retry_after = float(raw_retry_after)
            except ValueError:
                retry_after = None
    kind = failure_kind
    if response is not None and failure_kind == "provider":
        if status == 401 or status == 403:
            kind = "auth"
        elif status == 402:
            kind = "budget"
        elif status == 404:
            kind = "not_found"
        elif status in {400, 422}:
            kind = "validation"
        elif status == 429:
            kind = "rate_limit"
        elif status in {408, 425, 500, 502, 503, 504}:
            kind = "network"
    return Diagnostic(
        provider="exa",
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=kind,  # type: ignore[arg-type]
        status_code=status,
        retry_after_seconds=retry_after,
        query=query,
        details=details or {},
    )


def _source_urls(value: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _SOURCE_URL.finditer(value):
        url = match.group(0).rstrip(".,;:!?)}]'\"")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:20]


def _github_identity(url: str) -> tuple[str | None, str | None, str | None]:
    parsed = urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None, None, None
    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) < 2:
        return None, None, None
    repository = "/".join(parts[:2])
    if len(parts) >= 5 and parts[2] in {"blob", "tree"}:
        return repository, "/".join(parts[4:]), parts[3]
    return repository, None, None


def _github_revision(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) >= 4 and parts[2] == "commit" and re.fullmatch(r"[0-9a-fA-F]{7,64}", parts[3]):
        return parts[3]
    return None


def _is_documentation_source(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").casefold()
    return hostname.startswith(("api.", "developer.", "developers.", "docs.")) or hostname.endswith(
        ".readthedocs.io"
    )


def _scope_values(plan: QueryPlan, request: CodeSearchRequest, key: str) -> tuple[str, ...]:
    explicit = {
        "repo": request.repositories,
        "language": (request.language,) if request.language else (),
        "path": (request.path,) if request.path else (),
        "filename": (request.filename,) if request.filename else (),
        "extension": (request.extension,) if request.extension else (),
    }[key]
    if explicit:
        return tuple(value for value in explicit if value)
    return tuple(value for name, value in plan.qualifiers if name == key and value)


def _context_query(plan: QueryPlan, request: CodeSearchRequest) -> str:
    """Build the Exa /context query, preferring the LLM-optimized semantic query.

    When the worker optimizer produced an ``exa_semantic_query`` on the plan,
    use it as the base — it rephrases the user intent as a self-contained
    semantic search for code examples. Fall back to the raw request query
    when no optimized query is available (fail-open).
    """

    base = (
        plan.exa_semantic_query.strip()
        or request.query.strip()
        or plan.original_query.strip()
    )
    constraints: list[str] = []
    repositories = _scope_values(plan, request, "repo")
    if repositories:
        constraints.append("Prefer GitHub source examples from: " + ", ".join(repositories))
    for key, label in (
        ("language", "programming language"),
        ("path", "repository path"),
        ("filename", "filename"),
        ("extension", "file extension"),
    ):
        values = _scope_values(plan, request, key)
        if values:
            constraints.append(f"Prefer {label}: {', '.join(values)}")
    if constraints:
        base += "\n\nRetrieval constraints: " + "; ".join(constraints)
    return base[:_CONTEXT_MAX_QUERY_CHARS].rstrip()


def _preferred_source_url(urls: list[str], repositories: tuple[str, ...]) -> str | None:
    normalized = {value.strip().strip('"').casefold() for value in repositories}
    for url in urls:
        repository = _github_identity(url)[0]
        if repository and repository.casefold() in normalized:
            return url
    if normalized:
        return None
    for url in urls:
        if _github_identity(url)[0]:
            return url
    for url in urls:
        if _is_documentation_source(url):
            return url
    return urls[0] if urls else None


def _context_hit(
    payload: dict[str, Any],
    *,
    query_variant: str,
    source_url: str,
) -> CodeSearchHit:
    context = payload["response"].strip()
    repository, path, ref = _github_identity(source_url)
    revision = _github_revision(source_url)
    source_urls = _source_urls(context)
    metadata = {
        "transport": "exa_context",
        "request_id": payload.get("requestId"),
        "response_query": payload.get("query"),
        "results_count": payload.get("resultsCount"),
        "output_tokens": payload.get("outputTokens"),
        "search_time": payload.get("searchTime"),
        "cost_dollars": payload.get("costDollars"),
        "source_urls": source_urls,
    }
    return CodeSearchHit(
        repository=repository,
        path=path,
        url=source_url,
        provider="exa",
        query_variant=query_variant,
        search_rank=1,
        result_kind="semantic_page",
        location=build_location_metadata(
            repository=repository,
            path=path,
            url=source_url,
            ref=ref,
            revision=revision,
            match_data_available=False,
        ),
        fragments=[
            TextFragment(
                text=context[:_CONTEXT_MAX_RESPONSE_CHARS],
                match_metadata={"source_urls": source_urls},
            )
        ],
        title="Exa Code context",
        snippet=context[:_CONTEXT_MAX_RESPONSE_CHARS],
        evidence_role="code_context_synthesis",
        source_metadata=metadata,
    )


async def _search_context(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    query_variant = request.query.strip() or (plan.variants or (plan.api_query,))[0]
    if not query_variant:
        return ProviderResponse(provider="exa")
    api_key = _api_key()
    if not api_key:
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    "EXA_API_KEY is required for Exa Code context",
                    query=query_variant,
                    failure_kind="auth",
                    outcome="partial",
                )
            ],
        )

    context_query = _context_query(plan, request)
    tokens_num = _CONTEXT_DEEP_TOKENS if request.deep else _CONTEXT_TOKENS
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "web-search-mcp/code-search",
    }
    try:
        response = await http_client.post(
            _EXA_CONTEXT_URL,
            headers=headers,
            json={"query": context_query, "tokensNum": tokens_num},
            timeout=settings.search_retrieve_budget_seconds,
        )
    except httpx.TimeoutException as exc:
        LOGGER.warning("Exa Code context timed out: %s", type(exc).__name__)
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    f"Exa Code context timed out ({type(exc).__name__})",
                    query=query_variant,
                    failure_kind="network",
                    outcome="partial",
                )
            ],
            request_count=1,
        )
    except httpx.HTTPError as exc:
        LOGGER.warning("Exa Code context failed: %s", type(exc).__name__)
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    f"Exa Code context request failed ({type(exc).__name__})",
                    query=query_variant,
                    failure_kind="network",
                    outcome="partial",
                )
            ],
            request_count=1,
        )

    if response.status_code != 200:
        outcome = (
            "partial"
            if response.status_code >= 500 or response.status_code in {408, 425, 429}
            else "error"
        )
        if response.status_code in {401, 403}:
            outcome = "partial"
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    f"Exa Code context returned HTTP {response.status_code}",
                    query=query_variant,
                    outcome=outcome,
                    response=response,
                    details=_response_details(response),
                )
            ],
            request_count=1,
        )

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    "Exa Code context returned invalid JSON",
                    query=query_variant,
                    outcome="partial",
                )
            ],
            request_count=1,
        )
    context = payload.get("response")
    if not isinstance(context, str) or not context.strip():
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    "Exa Code context returned no synthesized response",
                    query=query_variant,
                    outcome="partial",
                )
            ],
            request_count=1,
        )

    urls = _source_urls(context)
    repositories = _scope_values(plan, request, "repo")
    source_url = _preferred_source_url(urls, repositories)
    if source_url is None:
        scoped_message = (
            " matching the requested repository scope"
            if repositories
            else ""
        )
        return ProviderResponse(
            provider="exa",
            diagnostics=[
                _diagnostic(
                    "Exa Code context contained no source URL" + scoped_message,
                    query=query_variant,
                    outcome="partial",
                    details={
                        "source_urls": urls[:20],
                        "repositories": list(repositories)[:25],
                    },
                )
            ],
            request_count=1,
        )
    hit = _context_hit(payload, query_variant=query_variant, source_url=source_url)
    return ProviderResponse(
        provider="exa",
        hits=[hit],
        request_count=1,
        metadata={
            "compiled_queries": [context_query],
            "transport": "context",
            "tokens_num": tokens_num,
            "response_query": payload.get("query"),
            "source_urls": urls,
        },
    )


async def search_exa(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ProviderResponse:
    """Use Exa's documented Context/Code endpoint for code examples."""

    if http_client is not None:
        return await _search_context(plan, request, http_client=http_client)
    async with httpx.AsyncClient() as owned_client:
        return await _search_context(plan, request, http_client=owned_client)
