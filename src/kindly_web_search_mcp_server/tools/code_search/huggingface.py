"""Semantic Hugging Face Hub search adapter for ``code_search``."""

from __future__ import annotations
import asyncio
import os
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

from ...settings import settings
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    ProviderResponse,
)
from .query import QueryPlan

_PROVIDER = "huggingface"
_ASSET_TYPES = ("datasets", "models")
_MAX_QUERY_CHARS = 200
_MAX_RESULTS = 100
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _failure_kind(status_code: int) -> str:
    if status_code == 401:
        return "auth"
    if status_code == 403 or status_code == 429:
        return "rate_limit"
    if status_code == 404:
        return "not_found"
    if status_code in {408, 500, 502, 503, 504}:
        return "network"
    return "provider"


def _diagnostic(
    message: str,
    *,
    outcome: str = "error",
    failure_kind: str = "provider",
    status_code: int | None = None,
    retry_after_seconds: float | None = None,
    query: str | None = None,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    return Diagnostic(
        provider=_PROVIDER,
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=failure_kind,  # type: ignore[arg-type]
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
        query=query,
        details=details or {},
    )


def _reserve_rate_slot() -> float:
    """Reserve a request start without assuming the public API exposes limits."""

    global _LAST_REQUEST_AT
    minimum_interval = max(0.0, settings.huggingface_semantic_search_min_interval_seconds)
    with _RATE_LOCK:
        now = time.monotonic()
        wait_for = max(0.0, minimum_interval - (now - _LAST_REQUEST_AT))
        _LAST_REQUEST_AT = now + wait_for
        return wait_for


async def _wait_for_rate_slot() -> None:
    wait_for = _reserve_rate_slot()
    if wait_for:
        await asyncio.sleep(wait_for)


def _query(plan: QueryPlan) -> str:
    return plan.search_text.strip()


def _params(request: CodeSearchRequest, query: str, asset_type: str, k: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "query": query,
        "k": max(1, min(_MAX_RESULTS, k)),
        "sort_by": request.huggingface_sort_by,
        "min_likes": request.huggingface_min_likes,
        "min_downloads": request.huggingface_min_downloads,
    }
    for key, value in (
        ("task", request.huggingface_task),
        ("license", request.huggingface_license),
        ("language", request.huggingface_language),
        ("modified_after", request.huggingface_modified_after),
    ):
        if value:
            params[key] = value
    if asset_type == "models":
        if request.huggingface_min_param_count > 0:
            params["min_param_count"] = request.huggingface_min_param_count
        if request.huggingface_max_param_count is not None:
            params["max_param_count"] = request.huggingface_max_param_count
        if request.huggingface_hybrid:
            params["hybrid"] = "true"
    elif request.huggingface_hybrid:
        params["hybrid"] = "true"
    return params


def _asset_url(asset_type: str, asset_id: str) -> str:
    return f"https://huggingface.co/{asset_type}/{quote(asset_id, safe='/')}"


def _hit(
    item: dict[str, Any],
    *,
    asset_type: str,
    query: str,
    rank: int,
    request: CodeSearchRequest,
) -> CodeSearchHit | None:
    id_key = "model_id" if asset_type == "models" else "dataset_id"
    asset_id = item.get(id_key)
    if not isinstance(asset_id, str) or not asset_id.strip():
        return None
    asset_id = asset_id.strip()
    summary = item.get("summary") if isinstance(item.get("summary"), str) else ""
    similarity = item.get("similarity")
    semantic_score = float(similarity) if isinstance(similarity, (int, float)) else None
    last_modified = item.get("last_modified")
    metadata = {
        "asset_type": asset_type[:-1],
        "asset_id": asset_id,
        "semantic_score": semantic_score,
        "score_semantics": (
            "not_semantic_trending"
            if request.huggingface_sort_by == "trending"
            else "provider_similarity"
        ),
        "ranking_mode": "hybrid" if request.huggingface_hybrid else request.huggingface_sort_by,
        "api_rank": rank,
        "likes": item.get("likes") if isinstance(item.get("likes"), int) else 0,
        "downloads": item.get("downloads") if isinstance(item.get("downloads"), int) else 0,
        "task": item.get("task"),
        "license": item.get("license"),
        "language": item.get("language"),
        "last_modified": last_modified,
        "repository_kind": "huggingface_hub_asset",
        "match_data_available": False,
    }
    if asset_type == "models":
        metadata["param_count"] = (
            item.get("param_count") if isinstance(item.get("param_count"), int) else None
        )
    return CodeSearchHit(
        result_kind="repository",
        repository=asset_id,
        url=_asset_url(asset_type, asset_id),
        provider=_PROVIDER,
        query_variant=query,
        search_rank=rank,
        title=asset_id,
        snippet=summary,
        published_date=last_modified if isinstance(last_modified, str) else None,
        score=semantic_score,
        score_components={"semantic_score": semantic_score, "api_rank": rank},
        source_metadata=metadata,
    )


async def _search_type(
    client: httpx.AsyncClient,
    plan: QueryPlan,
    request: CodeSearchRequest,
    asset_type: str,
    k: int,
) -> ProviderResponse:
    query = _query(plan)
    await _wait_for_rate_slot()
    endpoint = f"{settings.huggingface_semantic_search_url.rstrip('/')}/search/{asset_type}"
    try:
        headers = {"Accept": "application/json", "User-Agent": "web-search-mcp/code-search"}
        hf_token = os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"
        response = await client.get(
            endpoint,
            params=_params(request, query, asset_type, k),
            timeout=settings.huggingface_semantic_search_timeout_seconds,
            headers=headers,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return ProviderResponse(
            provider=_PROVIDER,
            diagnostics=[
                _diagnostic(
                    f"Hugging Face semantic search failed ({type(exc).__name__})",
                    outcome="partial",
                    failure_kind="network",
                    query=query,
                    details={"asset_type": asset_type},
                )
            ],
            request_count=1,
        )

    if response.status_code != 200:
        kind = _failure_kind(response.status_code)
        outcome = "partial" if kind in {"network", "rate_limit"} else "error"
        return ProviderResponse(
            provider=_PROVIDER,
            diagnostics=[
                _diagnostic(
                    f"Hugging Face semantic search returned HTTP {response.status_code}",
                    outcome=outcome,
                    failure_kind=kind,
                    status_code=response.status_code,
                    retry_after_seconds=_retry_after(response),
                    query=query,
                    details={"asset_type": asset_type},
                )
            ],
            request_count=1,
        )

    try:
        payload = response.json()
    except ValueError:
        return ProviderResponse(
            provider=_PROVIDER,
            diagnostics=[
                _diagnostic(
                    "Hugging Face semantic search returned invalid JSON",
                    outcome="partial",
                    failure_kind="provider",
                    query=query,
                    details={"asset_type": asset_type},
                )
            ],
            request_count=1,
        )
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return ProviderResponse(
            provider=_PROVIDER,
            diagnostics=[
                _diagnostic(
                    "Hugging Face semantic search returned no result list",
                    outcome="partial",
                    failure_kind="provider",
                    query=query,
                    details={"asset_type": asset_type},
                )
            ],
            request_count=1,
        )

    hits: list[CodeSearchHit] = []
    invalid_count = 0
    for rank, item in enumerate(raw_results[:k], 1):
        if not isinstance(item, dict):
            invalid_count += 1
            continue
        hit = _hit(item, asset_type=asset_type, query=query, rank=rank, request=request)
        if hit is None:
            invalid_count += 1
            continue
        hits.append(hit)

    diagnostics: list[Diagnostic] = []
    if invalid_count:
        diagnostics.append(
            _diagnostic(
                f"Skipped {invalid_count} malformed Hugging Face result(s)",
                outcome="partial",
                failure_kind="provider",
                query=query,
                details={"asset_type": asset_type, "invalid_count": invalid_count},
            )
        )
    return ProviderResponse(
        provider=_PROVIDER,
        hits=hits,
        diagnostics=diagnostics,
        request_count=1,
        metadata={
            "asset_type": asset_type,
            "compiled_queries": [f"{asset_type}:{query}"],
            "endpoint": endpoint,
            "result_count": len(hits),
        },
    )


async def search_huggingface(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    """Search model and/or dataset cards through the public semantic Hub API."""

    query = _query(plan)
    if not 3 <= len(query) <= _MAX_QUERY_CHARS:
        return ProviderResponse(
            provider=_PROVIDER,
            diagnostics=[
                _diagnostic(
                    "Hugging Face semantic search requires a 3-200 character query",
                    outcome="error",
                    failure_kind="validation",
                    query=query,
                    details={"query_length": len(query), "min_chars": 3, "max_chars": 200},
                )
            ],
        )

    asset_types = (
        _ASSET_TYPES if request.huggingface_type == "both" else (request.huggingface_type,)
    )
    per_type_k = (
        max(1, (request.max_results + len(asset_types) - 1) // len(asset_types))
        if len(asset_types) > 1
        else request.max_results
    )
    responses = await asyncio.gather(
        *(
            _search_type(http_client, plan, request, asset_type, per_type_k)
            for asset_type in asset_types
        ),
        return_exceptions=True,
    )
    hits: list[CodeSearchHit] = []
    diagnostics: list[Diagnostic] = []
    request_count = 0
    compiled_queries: list[str] = []
    for asset_type, response in zip(asset_types, responses, strict=True):
        if isinstance(response, BaseException):
            diagnostics.append(
                _diagnostic(
                    f"Hugging Face {asset_type} branch failed ({type(response).__name__})",
                    outcome="partial",
                    failure_kind="provider",
                    query=query,
                    details={"asset_type": asset_type},
                )
            )
            continue
        hits.extend(response.hits)
        diagnostics.extend(response.diagnostics)
        request_count += response.request_count
        compiled_queries.extend(response.metadata.get("compiled_queries", []))

    if request.huggingface_hybrid:
        hits.sort(key=lambda hit: (hit.search_rank or 10_000, -(hit.score or 0.0), hit.url))
    else:
        hits.sort(key=lambda hit: (-(hit.score or 0.0), hit.search_rank or 10_000, hit.url))
    return ProviderResponse(
        provider=_PROVIDER,
        hits=hits[: request.max_results],
        diagnostics=diagnostics,
        request_count=request_count,
        metadata={
            "compiled_queries": compiled_queries,
            "asset_types": list(asset_types),
            "ranking_mode": "hybrid" if request.huggingface_hybrid else request.huggingface_sort_by,
        },
    )


__all__ = ["search_huggingface"]
