from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ...settings import settings
from ...utils.url_canonicalize import canonicalize_url, extract_domain_from_url
from ..filters import searxng_time_range
from ..options import SearchOptions
from ..types import EngineCall, SearchHit
from .base import (
    _RETRYABLE_HTTP_STATUSES,
    ProviderRequestError,
    ProviderRequestMetadata,
    _classify_http_status,
    _parse_retry_after,
    run_provider,
)


class SearxngError(ProviderRequestError):
    pass


class SearxngConfigError(SearxngError):
    pass


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SearxngRow:
    """Adapter-internal parse row carrying the engine-RRF working scores.

    SearXNG runs its own internal engine-level RRF and consensus bonus before
    the pipeline sees the hits, so the final combined score lives here and is
    dropped once the row order it produced is emitted as ``SearchHit`` rows.
    """

    title: str
    link: str
    snippet: str
    domain: str | None
    engines: tuple[str, ...] = ()
    score: float | None = None
    published: str | None = None


DEFAULT_SEARXNG_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _get_searxng_base_url() -> str:
    base_url = settings.searxng_base_url.strip()
    if not base_url:
        raise SearxngConfigError(
            "SEARXNG_BASE_URL is not set. Configure it as an environment variable in your IDE/run configuration."
        )

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise SearxngConfigError(f"SEARXNG_BASE_URL is not a valid URL: {base_url!r}")

    return base_url.rstrip("/")


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {}

    raw_extra = (settings.searxng_headers_json).strip()
    if raw_extra:
        try:
            parsed = json.loads(raw_extra)
        except json.JSONDecodeError as exc:
            raise SearxngConfigError("SEARXNG_HEADERS_JSON must be a JSON object string.") from exc

        if not isinstance(parsed, dict):
            raise SearxngConfigError("SEARXNG_HEADERS_JSON must be a JSON object string.")

        for key, value in parsed.items():
            if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
                headers[key] = value

    if "user-agent" not in {key.lower() for key in headers}:
        headers["User-Agent"] = settings.searxng_user_agent.strip() or DEFAULT_SEARXNG_USER_AGENT

    return headers


def _get_request_timeout_seconds() -> float | None:
    raw = settings.search_retrieve_budget_seconds
    if raw is None:
        return None
    return float(raw)


def _looks_like_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _engine_consensus_rrf_scores(
    results: list[_SearxngRow],
    k: int = 60,
) -> dict[str, float]:
    """Score URLs by RRF over SearXNG internal-engine rankings.

    Args:
        results: List of SearXNG parse rows (each carries its engine names).
        k: RRF constant (default 60).

    Returns:
        Dict mapping canonical URL to RRF score.
    """
    scores: dict[str, float] = {}

    results_by_engine: dict[str, list[_SearxngRow]] = {}
    for result in results:
        for engine in result.engines:
            results_by_engine.setdefault(engine, []).append(result)

    for _engine, engine_results in results_by_engine.items():
        for rank, result in enumerate(engine_results, start=1):
            canonical = canonicalize_url(result.link)
            scores[canonical] = scores.get(canonical, 0) + 1.0 / (k + rank)

    LOGGER.debug(
        "Engine-level RRF: %d engines, %d unique URLs scored",
        len(results_by_engine),
        len(scores),
    )
    return scores


def _apply_engine_consensus_bonus(
    results: list[_SearxngRow],
    bonus_per_engine: float = 0.05,
) -> list[_SearxngRow]:
    """Apply consensus bonus based on number of engines that returned each result.

    Args:
        results: List of SearXNG parse rows.
        bonus_per_engine: Bonus per additional engine (default 0.05).

    Returns:
        Rows with updated scores including consensus bonus.
    """
    boosted: list[_SearxngRow] = []
    for result in results:
        engine_count = len(result.engines)
        if engine_count > 1:
            consensus_bonus = bonus_per_engine * (engine_count - 1)
            current_score = result.score or 0.0
            result = dataclasses.replace(result, score=current_score + consensus_bonus)
            LOGGER.debug(
                "Consensus bonus: %s engines=%d bonus=%.3f",
                result.link,
                engine_count,
                consensus_bonus,
            )
        boosted.append(result)
    return boosted


async def search_searxng(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> EngineCall:
    """
    Query a SearXNG instance and return parsed results.

    SearXNG endpoint:
    - GET {SEARXNG_BASE_URL}/search
    - Params: q=<query>, format=json, plus optional params like language/categories/engines/time_range/safesearch.

    SearXNG docs: https://docs.searxng.org/dev/search_api.html
    """
    if not query.strip():
        return EngineCall(adapter="searxng", query=query)

    if num_results < 1:
        return EngineCall(adapter="searxng", query=query)

    base_url = _get_searxng_base_url()
    url = f"{base_url}/search"

    params: dict[str, Any] = {"q": query, "format": "json"}
    # SearXNG instance tuning comes from environment variables; the generic
    # locale fields refine language/region when the caller supplies them.
    # Temporal: SearXNG supports day/month/year only (no week); unsupported
    # buckets fall through to the pipeline post-filter.
    for env_key, param_key in (
        ("SEARXNG_LANGUAGE", "language"),
        ("SEARXNG_CATEGORIES", "categories"),
        ("SEARXNG_ENGINES", "engines"),
        ("SEARXNG_TIME_RANGE", "time_range"),
        ("SEARXNG_SAFESEARCH", "safesearch"),
    ):
        value = (os.environ.get(env_key) or "").strip()
        if value:
            params[param_key] = value
    if search_options is not None:
        if search_options.language:
            # Generic locale: SearXNG accepts BCP-47-ish codes ("pl", "pt-BR").
            if search_options.region:
                params["language"] = (
                    f"{search_options.language.lower()}-{search_options.region.upper()}"
                )
            else:
                params["language"] = search_options.language
        temporal_bucket = (
            search_options.temporal.bucket
            if search_options.temporal is not None and not search_options.temporal.is_empty
            else None
        )
        native_range = searxng_time_range(temporal_bucket)
        if native_range:
            params["time_range"] = native_range

    headers = _build_headers()
    timeout_seconds = _get_request_timeout_seconds()
    request_timeout = timeout_seconds if timeout_seconds is not None else 30.0

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(url, params=params, headers=headers, timeout=request_timeout)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"))
            if status == 403:
                raise SearxngError(
                    "SearXNG returned 403 Forbidden. JSON output may be disabled on the instance "
                    "(formats are configured in settings.yml; request uses format=json). "
                    "Fix: enable the 'json' format in the SearXNG instance configuration.",
                    metadata=ProviderRequestMetadata(
                        provider="searxng",
                        http_status=status,
                        result_class="error",
                        error_type="auth",
                        error_summary="SearXNG returned 403 Forbidden",
                        retry_after=retry_after,
                        retryable=False,
                    ),
                ) from exc
            if status == 429:
                raise SearxngError(
                    "SearXNG returned 429 Too Many Requests (rate limited).",
                    metadata=ProviderRequestMetadata(
                        provider="searxng",
                        http_status=status,
                        result_class="error",
                        error_type="rate_limit",
                        error_summary="SearXNG returned 429 Too Many Requests (rate limited)",
                        retry_after=retry_after,
                        retryable=True,
                    ),
                ) from exc
            raise SearxngError(
                f"SearXNG returned HTTP {status}.",
                metadata=ProviderRequestMetadata(
                    provider="searxng",
                    http_status=status,
                    result_class="error",
                    error_type=_classify_http_status(status),
                    error_summary=f"SearXNG returned HTTP {status}",
                    retry_after=retry_after,
                    retryable=status in _RETRYABLE_HTTP_STATUSES,
                ),
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise SearxngError("SearXNG response was not valid JSON.") from exc

        if not isinstance(data, dict):
            raise SearxngError("SearXNG response was not a JSON object.")
        return data

    def _parse_response(data: dict[str, Any]) -> EngineCall:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise SearxngError("SearXNG response missing `results` list.")

        if not raw_results:
            LOGGER.debug("SearXNG returned empty results list for query=%r", query)

        rows: list[_SearxngRow] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            link = item.get("url")
            snippet = item.get("content")

            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(link, str) or not link.strip() or not _looks_like_url(link):
                continue
            if not isinstance(snippet, str) or not snippet.strip():
                continue

            source_engines = item.get("engines")
            if isinstance(source_engines, list):
                engines = tuple(
                    str(engine).strip()
                    for engine in source_engines
                    if isinstance(engine, str) and engine.strip()
                )
            else:
                engines = ()

            raw_score = item.get("score")
            score = None
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                score = float(raw_score)

            published_date = item.get("publishedDate") or item.get("published_date")
            if not isinstance(published_date, str) or not published_date.strip():
                published_date = None

            rows.append(
                _SearxngRow(
                    title=title,
                    link=link,
                    snippet=snippet,
                    domain=extract_domain_from_url(link),
                    engines=engines,
                    score=score,
                    published=published_date,
                )
            )
            if len(rows) >= num_results:
                break

        if rows:
            # The in-adapter engine-RRF and consensus bonus are unchanged:
            # combined = 0.7 * engine_rrf + 0.3 * instance score, then order.
            engine_rrf_scores = _engine_consensus_rrf_scores(rows, k=60)
            rows = _apply_engine_consensus_bonus(rows, bonus_per_engine=0.05)

            for idx, row in enumerate(rows):
                canonical = canonicalize_url(row.link)
                rrf_score = engine_rrf_scores.get(canonical, 0.0)
                current_score = row.score or 0.0
                combined_score = 0.7 * rrf_score + 0.3 * current_score
                rows[idx] = dataclasses.replace(row, score=combined_score)

            rows = sorted(rows, key=lambda r: r.score or 0.0, reverse=True)

        hits = tuple(
            SearchHit(
                title=row.title,
                url=row.link,
                snippet=row.snippet,
                domain=row.domain or "",
                adapter="searxng",
                provider_score=row.score,
                published=row.published,
                source_engines=row.engines,
            )
            for row in rows
            if row.domain
        )
        return EngineCall(adapter="searxng", query=query, hits=hits)

    return await run_provider(
        "searxng",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse_response,
        http_client=http_client,
        timeout_seconds=request_timeout,
    )
