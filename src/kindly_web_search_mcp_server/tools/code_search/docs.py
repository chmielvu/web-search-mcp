"""Documentation providers selected from request fields by the backend."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

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
_DEEPWIKI_URL = "https://mcp.deepwiki.com/mcp"
_CONTEXT7_URL = "https://context7.com/api/v1"


def _diag(
    provider: str,
    message: str,
    *,
    query: str | None = None,
    outcome: str = "error",
    failure_kind: str = "provider",
) -> Diagnostic:
    return Diagnostic(
        provider=provider,
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=failure_kind,  # type: ignore[arg-type]
        query=query,
    )


def _content_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if isinstance(text, str) and text.strip():
            values.append(text.strip())
    return "\n".join(values)


async def search_deepwiki(plan: QueryPlan, request: CodeSearchRequest) -> ProviderResponse:
    repo_name = (request.repo_name or plan.repository_hint or "").strip()
    if not repo_name:
        return ProviderResponse(
            provider="deepwiki",
            diagnostics=[
                _diag("deepwiki", "repo_name is required for DeepWiki", failure_kind="validation")
            ],
        )
    question_parts = [plan.search_text or request.query]
    if request.topic:
        question_parts.append(f"Focus on {request.topic}.")
    question = " ".join(item for item in question_parts if item).strip()
    try:
        async with streamable_http_client(_DEEPWIKI_URL) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "ask_question",
                    arguments={"repoName": repo_name, "question": question},
                )
    except Exception as exc:
        LOGGER.warning("DeepWiki MCP request failed: %s", type(exc).__name__)
        return ProviderResponse(
            provider="deepwiki",
            diagnostics=[
                _diag(
                    "deepwiki",
                    f"DeepWiki MCP request failed ({type(exc).__name__})",
                    query=question,
                    failure_kind="network",
                )
            ],
            request_count=1,
        )
    if getattr(result, "isError", False) or (isinstance(result, dict) and result.get("isError")):
        return ProviderResponse(
            provider="deepwiki",
            diagnostics=[_diag("deepwiki", "DeepWiki returned an MCP error", query=question)],
            request_count=1,
        )
    answer = _content_text(result)
    if not answer:
        return ProviderResponse(
            provider="deepwiki",
            diagnostics=[
                _diag(
                    "deepwiki",
                    "DeepWiki returned an empty answer",
                    query=question,
                    outcome="no_hit",
                )
            ],
            request_count=1,
        )
    return ProviderResponse(
        provider="deepwiki",
        hits=[
            CodeSearchHit(
                repository=repo_name,
                url=f"https://deepwiki.com/{repo_name}",
                provider="deepwiki",
                query_variant=question,
                search_rank=1,
                result_kind="documentation",
                location=build_location_metadata(
                    repository=repo_name,
                    path=None,
                    url=f"https://deepwiki.com/{repo_name}",
                    match_data_available=False,
                ),
                title=f"DeepWiki: {repo_name}",
                snippet=answer[:50_000],
                fragments=[TextFragment(text=answer[:20_000])],
                source_metadata={"repo_name": repo_name},
            )
        ],
        request_count=1,
    )


def _context7_headers() -> dict[str, str]:
    headers = {"Accept": "application/json, text/plain", "User-Agent": "web-search-mcp/code-search"}
    token = os.environ.get("CONTEXT7_API_KEY", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Context7-API-Key"] = token
    return headers


def _payload_content(payload: Any) -> tuple[str, str | None]:
    if isinstance(payload, str):
        return payload, None
    if not isinstance(payload, dict):
        return "", None
    content = payload.get("content") or payload.get("text") or payload.get("documentation")
    title = payload.get("title") or payload.get("name")
    return (content if isinstance(content, str) else ""), (
        title if isinstance(title, str) else None
    )


def _library_id(payload: Any, requested: str | None) -> str | None:
    candidates: Any = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list) or not candidates:
        return None
    requested_normalized = (requested or "").strip().casefold()
    requested_leaf = requested_normalized.strip("/").rsplit("/", 1)[-1]

    def candidate_id(item: dict[str, Any]) -> str | None:
        for key in ("id", "libraryId", "library_id", "libraryID"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    ranked: list[tuple[float, str]] = []
    for item in candidates:
        if not isinstance(item, dict) or not (identifier := candidate_id(item)):
            continue
        normalized_id = identifier.casefold()
        if requested_normalized and normalized_id == requested_normalized:
            return identifier
        title = str(item.get("title") or item.get("name") or "").casefold()
        leaf = normalized_id.strip("/").rsplit("/", 1)[-1]
        score = 0.0
        if requested_leaf and leaf == requested_leaf:
            score += 500.0
        if requested_leaf and title == requested_leaf:
            score += 400.0
        if item.get("verified"):
            score += 100.0
        score += float(item.get("trustScore") or 0.0) * 10.0
        score += float(item.get("benchmarkScore") or 0.0)
        score += math.log1p(max(0, int(item.get("stars") or 0)))
        score += math.log1p(max(0, int(item.get("totalSnippets") or 0)))
        ranked.append((score, identifier))
    return max(ranked, default=(0.0, None))[1]


async def search_context7(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    library_hint = (request.library_name or plan.library_hint or "").strip()
    query = " ".join(
        item for item in (plan.search_text or request.query, library_hint) if item
    ).strip()
    if not query:
        return ProviderResponse(provider="context7")
    headers = _context7_headers()
    try:
        search_response = await http_client.get(
            f"{_CONTEXT7_URL}/search",
            params={"query": query},
            headers=headers,
            timeout=settings.search_retrieve_budget_seconds,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    f"Context7 search request failed ({type(exc).__name__})",
                    query=query,
                    failure_kind="network",
                )
            ],
            request_count=1,
        )
    if search_response.status_code != 200:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    f"Context7 search returned HTTP {search_response.status_code}",
                    query=query,
                )
            ],
            request_count=1,
        )
    try:
        search_payload = search_response.json()
    except ValueError:
        return ProviderResponse(
            provider="context7",
            diagnostics=[_diag("context7", "Context7 search returned invalid JSON", query=query)],
            request_count=1,
        )
    provider_library_id = _library_id(search_payload, library_hint)
    selected_id = provider_library_id.strip().strip("/") if provider_library_id else None
    if not selected_id:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    "Context7 search returned no library ID",
                    query=query,
                    outcome="no_hit",
                )
            ],
            request_count=1,
        )
    params: dict[str, str] = {"type": "txt", "tokens": "4000"}
    if request.topic:
        params["topic"] = request.topic
    try:
        fetch_response = await http_client.get(
            f"{_CONTEXT7_URL}/{quote(selected_id, safe='/')}",
            params=params,
            headers=headers,
            timeout=settings.search_retrieve_budget_seconds,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    f"Context7 fetch request failed ({type(exc).__name__})",
                    query=query,
                    failure_kind="network",
                )
            ],
            request_count=2,
        )
    if fetch_response.status_code != 200:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    f"Context7 fetch returned HTTP {fetch_response.status_code}",
                    query=query,
                )
            ],
            request_count=2,
        )
    try:
        fetched_payload = fetch_response.json()
    except ValueError:
        fetched_payload = fetch_response.text
    content, title = _payload_content(fetched_payload)
    if not content and isinstance(fetch_response.text, str):
        content, title = _payload_content(fetch_response.text)
    if not content:
        return ProviderResponse(
            provider="context7",
            diagnostics=[
                _diag(
                    "context7",
                    "Context7 returned an empty documentation body",
                    query=query,
                    outcome="no_hit",
                )
            ],
            request_count=2,
        )
    return ProviderResponse(
        provider="context7",
        hits=[
            CodeSearchHit(
                repository=selected_id,
                url=f"https://context7.com/{selected_id}",
                provider="context7",
                query_variant=query,
                search_rank=1,
                result_kind="documentation",
                location=build_location_metadata(
                    repository=selected_id,
                    path=None,
                    url=f"https://context7.com/{selected_id}",
                    match_data_available=False,
                ),
                title=title or f"Context7: {selected_id}",
                snippet=content[:50_000],
                fragments=[TextFragment(text=content[:20_000])],
                source_metadata={
                    "library_id": provider_library_id,
                    "topic": request.topic,
                    "resolution_source": plan.resolution_source,
                },
            )
        ],
        request_count=2,
        metadata={"library_id": provider_library_id, "repository": selected_id},
    )


async def search_docs(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> list[ProviderResponse]:
    """Run only documentation adapters for which the request has usable identity."""
    operations: list[tuple[str, Any]] = []

    if request.repo_name or plan.repository_hint:
        operations.append(("deepwiki", search_deepwiki(plan, request)))
    if request.library_name or plan.library_hint:
        operations.append(("context7", search_context7(plan, request, http_client=http_client)))
    if not operations:
        return []
    results = await asyncio.gather(
        *(operation for _, operation in operations), return_exceptions=True
    )
    responses: list[ProviderResponse] = []
    for (provider, _), result in zip(operations, results, strict=True):
        if isinstance(result, BaseException):
            responses.append(
                ProviderResponse(
                    provider=provider,
                    diagnostics=[
                        _diag(
                            provider,
                            f"{provider} branch failed: {type(result).__name__}",
                            failure_kind="provider",
                        )
                    ],
                )
            )
        else:
            responses.append(result)
    return responses
