"""Bright Data SERP API provider: request builders, response parsing, and transport.

Serves the three catalog providers ``brightdata`` (Google), ``brightdata_bing``
and ``brightdata_yandex`` through one direct-REST path
(``POST https://api.brightdata.com/request``).  Google and Bing are parsed from
Bright Data's JSON envelopes; Yandex returns raw HTML and is parsed locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import quote_plus

import httpx

from ...settings import get_env_value, settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..types import AnswerKind, EngineCall, EngineFailure, QueryIntegrity, SearchHit, SourceKind
from .base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    RequestFn,
    _parse_retry_after,
    run_provider,
)

logger = logging.getLogger(__name__)

_REST_ENDPOINT = "https://api.brightdata.com/request"
_SUPPORTED_PROVIDERS = frozenset({"brightdata", "brightdata_bing", "brightdata_yandex"})
_PAGE_SIZE = 10
_MAX_PAGE_COUNT = 10
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_BRIGHTDATA_FRESHNESS_MAP: dict[str, str] = {
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
    "pd": "d",
    "pw": "w",
    "pm": "m",
    "py": "y",
}

TResponse = TypeVar("TResponse")


# ------------------------------------------------------------------
# Errors and zone/credential resolution.
# ------------------------------------------------------------------


class BrightDataError(ProviderRequestError):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "brightdata",
        status_code: int | None = None,
        response_meta: dict[str, object] | None = None,
    ) -> None:
        metadata_values = response_meta or {}
        retry_after_value = metadata_values.get("retry_after")
        retry_after = (
            _parse_retry_after(str(retry_after_value)) if retry_after_value is not None else None
        )
        super().__init__(
            message,
            metadata=ProviderRequestMetadata(
                provider=provider,
                http_status=status_code,
                result_class="error",
                error_summary=message[:500],
                response_meta=metadata_values,
                retry_after=retry_after,
                retryable=status_code in _RETRYABLE_STATUS_CODES,
            ),
        )
        self.status_code = status_code
        self.response_meta = metadata_values


class BrightDataConfigError(BrightDataError):
    pass


def get_brightdata_api_key() -> str:
    api_key = get_env_value("BRIGHTDATA_API_KEY", settings.brightdata_api_key).strip()
    if not api_key:
        raise BrightDataConfigError(
            "BRIGHTDATA_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def get_brightdata_zone() -> str:
    """Resolve an explicitly configured SERP zone.

    ``BRIGHTDATA_SERP_ZONE`` is the documented name.  ``BRIGHTDATA_ZONE`` is
    retained as a compatibility alias, but the historical ``sdk_serp``
    fallback is intentionally rejected because it can silently select the
    wrong product zone.  Set ``BRIGHTDATA_SERP_ZONE`` (not the settings
    default) when the account's SERP zone happens to be named ``sdk_serp``.
    """
    zone = get_env_value("BRIGHTDATA_SERP_ZONE", "").strip()
    if not zone:
        zone = get_env_value("BRIGHTDATA_ZONE", "").strip()
    if zone:
        return zone
    if not zone:
        configured_zone = getattr(settings, "brightdata_zone", "")
        if isinstance(configured_zone, str):
            zone = configured_zone.strip()
    if zone and zone != "sdk_serp":
        return zone
    if not zone or zone == "sdk_serp":
        raise BrightDataConfigError(
            "BRIGHTDATA_SERP_ZONE is not configured. Set it to the name of an "
            "existing Bright Data SERP zone. BRIGHTDATA_ZONE is accepted as "
            "a compatibility alias."
        )
    return zone


def resolve_payload_base() -> dict:
    zone = get_brightdata_zone()
    payload: dict = {
        "zone": zone,
        "format": "raw",
    }
    extra = settings.brightdata_payload_extra
    if extra:
        try:
            payload.update(json.loads(extra))
        except json.JSONDecodeError:
            logger.warning("BRIGHTDATA_PAYLOAD_EXTRA is not valid JSON, ignoring")
    return payload


# ------------------------------------------------------------------
# Target-URL builders for the per-engine Google/Bing/Yandex endpoints.
# ------------------------------------------------------------------


def brightdata_freshness_token(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return _BRIGHTDATA_FRESHNESS_MAP.get(normalized)


def build_google_url(
    query: str,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
    freshness: str | None = None,
    start: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.google.com/search?q={q}"
    if country:
        url += f"&gl={country}"
    if language:
        url += f"&hl={language}"
    if exact_match:
        url += "&nfpr=1"
    if start is not None and start > 0:
        url += f"&start={start}"
    url += "&brd_json=1"
    if search_type == "news":
        url += "&tbm=nws"
        token = brightdata_freshness_token(freshness)
        if token:
            url += f"&tbs=qdr:{token}"
    return url


def build_bing_url(
    query: str,
    country: str = "us",
    language: str = "en",
    first: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.bing.com/search?q={q}"
    if country:
        url += f"&cc={country}"
    if language:
        normalized_language = language.strip().replace("_", "-")
        if len(normalized_language) == 2 and country:
            normalized_language = f"{normalized_language.lower()}-{country.upper()}"
        url += f"&setLang={normalized_language}"
    if first is not None:
        url += f"&first={max(1, first)}"
    url += "&brd_json=1"
    return url


def build_yandex_url(
    query: str,
    region: str | None = "84",
    language: str = "en",
    page: int | None = None,
) -> str:
    q = quote_plus(query)
    url = f"https://www.yandex.com/search/?text={q}"
    if region:
        url += f"&lr={region}"
    if language:
        url += f"&lang={language}"
    if page is not None:
        url += f"&p={max(1, page)}"
    return url


def yandex_region_for_country(country: str | None) -> str | None:
    """Return only region mappings verified by the Bright Data docs.

    Bright Data documents numeric Yandex ``lr`` values rather than a complete
    country-code mapping.  Keep the known USA mapping for backwards
    compatibility and require callers to provide ``yandex_region`` for other
    locales instead of silently using USA.
    """
    if not country:
        return None
    return {"us": "84"}.get(country.strip().lower())


# ------------------------------------------------------------------
# Response parsing.
# ------------------------------------------------------------------


def detect_upstream_error(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    status_code = data.get("status_code")
    if not isinstance(status_code, int) or status_code < 400:
        return None
    headers = data.get("headers")
    msg = ""
    if isinstance(headers, dict):
        msg = (
            headers.get("x-brd-error")
            or headers.get("x-brd-err-msg")
            or headers.get("proxy-status")
            or ""
        )
    body = data.get("body")
    if isinstance(body, str) and body.strip():
        body = body.strip()[:240]
        return f"BrightData upstream {status_code}: {msg or body}"
    return f"BrightData upstream {status_code}: {msg or 'unknown error'}"


def _upstream_response_metadata(data: dict) -> dict[str, object]:
    headers = data.get("headers")
    if not isinstance(headers, dict):
        return {}
    response_meta: dict[str, object] = {}
    for key in (
        "retry-after",
        "x-brd-error-code",
        "x-brd-err-code",
        "x-brd-error",
        "x-brd-err-msg",
        "x-brd-rate-limit",
        "x-brd-rate-limit-period-ms",
        "proxy-status",
    ):
        value = headers.get(key)
        if value:
            response_meta[key.replace("-", "_")] = str(value)[:500]
    return response_meta


# ------------------------------------------------------------------
# Typed harvest helpers (Bright Data Full JSON schema, verified against
# https://docs.brightdata.com/products/serp-api/parsed-json-results).
# ------------------------------------------------------------------

_EXPANSION_LIMIT = 4

_FORUM_HOSTS = ("reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com")
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
_SOCIAL_HOSTS = (
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "linkedin.com",
    "medium.com",
)
_DOCS_HOSTS = ("github.com", "gitlab.com", "readthedocs.io", "readthedocs.org")


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _classify_source(source_name: object, host: str) -> SourceKind | None:
    """Classify the engine's source string/host into a coarse source kind."""
    name = source_name.strip().lower() if isinstance(source_name, str) else ""
    if any(_host_matches(host, domain) for domain in _FORUM_HOSTS) or "reddit" in name:
        return "forum"
    if any(_host_matches(host, domain) for domain in _VIDEO_HOSTS) or "youtube" in name:
        return "video"
    if any(_host_matches(host, domain) for domain in _SOCIAL_HOSTS):
        return "social"
    if any(_host_matches(host, domain) for domain in _DOCS_HOSTS) or name.startswith(
        ("github", "gitlab", "developer", "docs")
    ):
        return "docs"
    return None


def _display_host(display_link: object) -> str | None:
    """Resolve the hostname of a displayed URL such as ``example.com/path``."""
    if not isinstance(display_link, str) or not display_link.strip():
        return None
    text = display_link.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    token = text.split("\u203a")[0].split("\u00b7")[0].split("/")[0].strip()
    return extract_domain_from_url(token)


def _hit_domain(item: dict, link: str) -> str | None:
    """Resolve the row domain: the displayed URL's host first, link host second."""
    return _display_host(item.get("display_link")) or extract_domain_from_url(link)


def _usable_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _organic_hits(items: list, *, adapter: str, limit: int) -> list[SearchHit]:
    """Build page hits from Bright Data organic rows."""
    hits: list[SearchHit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name")
        link = item.get("link") or item.get("url")
        if not (
            isinstance(title, str) and title.strip() and isinstance(link, str) and link.strip()
        ):
            continue
        link = link.strip()
        domain = _hit_domain(item, link)
        if not domain:
            continue
        engine_rank = _strict_int(item.get("global_rank"))
        if engine_rank is None:
            engine_rank = _strict_int(item.get("rank"))
        source = item.get("source")
        published = _usable_text(item.get("last_modified_date")) or _usable_text(item.get("date"))
        highlights = item.get("snippet_highlighted_words")
        hits.append(
            SearchHit(
                title=title.strip(),
                url=link,
                snippet=str(item.get("description") or item.get("snippet") or "").strip(),
                domain=domain,
                adapter=adapter,
                engine_rank=engine_rank,
                source_name=_usable_text(source),
                source_kind=_classify_source(source, domain),
                published=published,
                highlights=(
                    tuple(
                        part.strip()
                        for part in highlights
                        if isinstance(part, str) and part.strip()
                    )
                    if isinstance(highlights, list)
                    else ()
                ),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _news_hits(data: dict, *, adapter: str, limit: int) -> list[SearchHit]:
    """Build page hits from Bright Data news rows."""
    hits: list[SearchHit] = []
    news = data.get("news", [])
    if not isinstance(news, list):
        return hits
    for item in news:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        link = item.get("link")
        if not (
            isinstance(title, str) and title.strip() and isinstance(link, str) and link.strip()
        ):
            continue
        link = link.strip()
        domain = _hit_domain(item, link)
        if not domain:
            continue
        engine_rank = _strict_int(item.get("global_rank"))
        hits.append(
            SearchHit(
                title=title.strip(),
                url=link,
                snippet=str(item.get("description") or "").strip(),
                domain=domain,
                adapter=adapter,
                engine_rank=engine_rank,
                source_name=_usable_text(item.get("source")),
                source_kind="news",
                published=_usable_text(item.get("date")),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _answer_hits(data: dict, *, adapter: str) -> list[SearchHit]:
    """Harvest answer-tier rows: featured snippet, PAA answers, knowledge facts, forum threads.

    Entries without a usable URL are dropped: a hit without a URL cannot be
    cited or fetched.
    """
    hits: list[SearchHit] = []

    def _row(
        title: object,
        url: object,
        snippet: object,
        kind: AnswerKind,
        *,
        source: object = None,
        display: object = None,
        rank: object = None,
    ) -> None:
        if not (isinstance(title, str) and title.strip() and isinstance(url, str) and url.strip()):
            return
        domain = _display_host(display) or extract_domain_from_url(url.strip())
        if not domain:
            return
        engine_rank = _strict_int(rank)
        hits.append(
            SearchHit(
                title=title.strip(),
                url=url.strip(),
                snippet=str(snippet or "").strip(),
                domain=domain,
                adapter=adapter,
                engine_rank=engine_rank,
                source_name=_usable_text(source),
                source_kind=_classify_source(source, domain),
                answer_kind=kind,
            )
        )

    for item in data.get("featured_snippets") or []:
        if not isinstance(item, dict):
            continue
        _row(
            item.get("title") or item.get("value"),
            item.get("link"),
            item.get("description") or item.get("value"),
            "featured_snippet",
            source=item.get("source"),
            display=item.get("display_link"),
            rank=item.get("global_rank"),
        )
    for item in data.get("people_also_ask") or []:
        if not isinstance(item, dict):
            continue
        _row(
            item.get("title") or item.get("question"),
            item.get("link"),
            item.get("answer") or item.get("question"),
            "paa",
            display=item.get("display_link"),
            rank=item.get("global_rank"),
        )
    knowledge = data.get("knowledge")
    if isinstance(knowledge, dict):
        knowledge_link = knowledge.get("description_link") or knowledge.get("link")
        if isinstance(knowledge_link, str) and knowledge_link.strip():
            _row(
                knowledge.get("title"),
                knowledge_link,
                knowledge.get("description"),
                "knowledge",
                source=knowledge.get("description_source"),
            )
        facts = knowledge.get("facts")
        if isinstance(facts, list):
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                fact_title = fact.get("predicate") or fact.get("title") or knowledge.get("title")
                _row(fact_title, fact.get("link"), fact.get("value"), "knowledge")
    forums = data.get("forums")
    if isinstance(forums, dict):
        for item in forums.get("items") or []:
            if not isinstance(item, dict):
                continue
            answers = item.get("answers")
            top_answer = ""
            if isinstance(answers, list):
                answer = next(
                    (
                        candidate
                        for candidate in answers
                        if isinstance(candidate, dict) and candidate.get("is_top_answer") is True
                    ),
                    None,
                )
                if answer is None:
                    answer = next(
                        (candidate for candidate in answers if isinstance(candidate, dict)),
                        None,
                    )
                if answer is not None:
                    top_answer = str(answer.get("text") or "")
            _row(
                item.get("title"),
                item.get("link"),
                top_answer,
                "forum",
                source=item.get("source"),
            )
    return hits


def _expansion_seeds(data: dict) -> tuple[str, ...]:
    """Harvest expansion seeds: related searches, chips, and PAA questions."""
    seeds: list[str] = []
    for section in (data.get("related") or [], data.get("chips") or []):
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, dict):
                continue
            text = _usable_text(item.get("text")) or _usable_text(item.get("label"))
            if text and text not in seeds:
                seeds.append(text)
            if len(seeds) >= _EXPANSION_LIMIT:
                return tuple(seeds)
    paa = data.get("people_also_ask")
    if isinstance(paa, list):
        for item in paa:
            if not isinstance(item, dict):
                continue
            question = _usable_text(item.get("question"))
            if question and question not in seeds:
                seeds.append(question)
            if len(seeds) >= _EXPANSION_LIMIT:
                break
    return tuple(seeds)


def _query_integrity(data: dict, sent_query: str) -> QueryIntegrity | None:
    """Compare the engine's detected query against the query we sent.

    Per the Bright Data debugging docs, a ``detected_query`` difference is a
    genuine spelling correction when a ``spelling`` object is present and a
    truncation (cloaked query) when it is absent.
    """
    general = data.get("general")
    if not isinstance(general, dict):
        return None
    effective_query = _usable_text(general.get("query")) or sent_query
    detected = _usable_text(general.get("detected_query"))
    spelling_value = data.get("spelling")
    spelling = spelling_value if isinstance(spelling_value, dict) else {}
    has_spelling = bool(spelling)
    spelling_text: str | None = None
    if has_spelling:
        for key in ("auto_corrected_text", "suggested_text"):
            spelling_text = _usable_text(spelling.get(key))
            if spelling_text:
                break
    results_cnt = general.get("results_cnt")
    return QueryIntegrity(
        sent_query=sent_query,
        detected_query=detected,
        truncated=bool(detected and detected != effective_query and not has_spelling),
        spelling=spelling_text,
        result_count=_strict_int(results_cnt),
    )


def parse_brightdata_response(
    data: dict, search_type: str, num_results: int, *, adapter: str, sent_query: str
) -> EngineCall:
    """Parse one Bright Data Google/Bing JSON payload into a typed engine call."""
    upstream = detect_upstream_error(data)
    if upstream:
        status_code = data.get("status_code")
        raise BrightDataError(
            upstream,
            provider=adapter,
            status_code=status_code if isinstance(status_code, int) else None,
            response_meta=_upstream_response_metadata(data),
        )

    hits: list[SearchHit] = _answer_hits(data, adapter=adapter)
    if search_type == "news":
        hits.extend(_news_hits(data, adapter=adapter, limit=num_results))
    organic = data.get("organic", [])
    if not isinstance(organic, list) or not organic:
        web_pages = data.get("webPages")
        organic = web_pages.get("value", []) if isinstance(web_pages, dict) else []
    if isinstance(organic, list):
        hits.extend(_organic_hits(organic, adapter=adapter, limit=num_results))

    ordered_hits = [
        hit
        for _, hit in sorted(
            enumerate(hits),
            key=lambda indexed: (
                indexed[1].engine_rank is None,
                indexed[1].engine_rank if indexed[1].engine_rank is not None else 0,
                indexed[0],
            ),
        )
    ]

    return EngineCall(
        adapter=adapter,
        query=sent_query,
        hits=tuple(ordered_hits[:num_results]),
        expansion=_expansion_seeds(data),
        integrity=_query_integrity(data, sent_query),
    )


# ------------------------------------------------------------------
# Bounded page-by-page transport.
# ------------------------------------------------------------------

# x-brd-error-code values verified against the SERP API error catalog
# (https://docs.brightdata.com/products/serp-api/debugging).
_RATE_LIMIT_CODES = frozenset(
    {
        "failed_query_rejected",
        "repeat_query_rejected",
        "sr_rate_limit",
        "bucket_rate_limit",
        "client_10110",
    }
)
_CHALLENGE_CODES = frozenset({"verifying", "no_ready_cookies", "expect_element", "captcha"})
_CHALLENGE_MARKERS = ("captcha", "no_ready_cookies", "expect_element", "challenge page")
# Per-query rejections and challenge pages must not be retried for at least
# 15 seconds per the docs; the missing header falls back to that floor.
_MIN_QUERY_RETRY_SECONDS = 15.0


def _engine_failure(exc: ProviderRequestError) -> EngineFailure:
    """Classify a provider request failure into a typed engine failure."""
    metadata = exc.metadata
    status = metadata.http_status if metadata else None
    response_meta = metadata.response_meta if metadata else {}
    code_value = response_meta.get("x_brd_error_code") or response_meta.get("x_brd_err_code")
    code = str(code_value).strip() if code_value else None
    message = (metadata.error_summary if metadata else None) or str(exc)
    retry_after = metadata.retry_after if metadata else None
    if code in _RATE_LIMIT_CODES or status == 429:
        return EngineFailure(
            kind="rate_limited",
            message=message,
            code=code,
            retryable=True,
            retry_after=retry_after if retry_after is not None else _MIN_QUERY_RETRY_SECONDS,
        )
    if code in _CHALLENGE_CODES or any(marker in message.lower() for marker in _CHALLENGE_MARKERS):
        retryable = code == "verifying"
        return EngineFailure(
            kind="bot_challenge",
            message=message,
            code=code,
            retryable=retryable,
            retry_after=_MIN_QUERY_RETRY_SECONDS if retryable else None,
        )
    if code == "unexpected_q":
        return EngineFailure(kind="query_truncated", message=message, code=code)
    if metadata is not None and metadata.error_type == "timeout":
        return EngineFailure(kind="timeout", message=message, code=code)
    if isinstance(status, int) and status in {401, 403, 407}:
        return EngineFailure(kind="permission_denied", message=message, code=code)
    if isinstance(status, int) and status >= 500:
        return EngineFailure(kind="upstream_5xx", message=message, code=code)
    if isinstance(status, int) and status >= 400:
        return EngineFailure(kind="upstream_4xx", message=message, code=code)
    return EngineFailure(kind="parse_error", message=message, code=code)


def _empty_call(provider_name: str, query: str, failure: EngineFailure | None = None) -> EngineCall:
    return EngineCall(adapter=provider_name, query=query, failure=failure)


def _retry_delay(error: ProviderRequestError, remaining_seconds: float) -> float | None:
    """Return one small retry delay when the remaining request budget allows it."""
    raw_delay = getattr(error.metadata, "response_meta", {}).get("retry_after")
    try:
        delay = float(raw_delay) if raw_delay is not None else 0.1
    except (TypeError, ValueError):
        delay = 0.1
    if delay < 0:
        return None
    delay = min(delay, 2.0)
    return delay if delay < remaining_seconds else None


async def _run_page[TResponse](
    provider_name: str,
    query: str,
    page_limit: int,
    *,
    page_index: int,
    request_factory: Callable[[int, float], RequestFn[TResponse]],
    parse_response: Callable[[TResponse], EngineCall],
    http_client: httpx.AsyncClient | None,
    deadline: float,
) -> EngineCall:
    """Run one page and allow at most one budget-aware transient retry.

    A page that still fails after its retry budget returns a typed failed
    call instead of raising, so earlier pages' hits are not discarded.
    """
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _empty_call(
                provider_name,
                query,
                EngineFailure(
                    kind="budget_exhausted",
                    message="retrieve budget exhausted before page fetch",
                ),
            )
        try:
            return await run_provider(
                provider_name=provider_name,
                query=query,
                num_results=page_limit,
                request=request_factory(page_index, remaining),
                parse_response=parse_response,
                http_client=http_client,
                timeout_seconds=remaining,
            )
        except ProviderRequestError as exc:
            status = getattr(exc.metadata, "http_status", None)
            if attempt or status not in _RETRYABLE_STATUS_CODES:
                return _empty_call(provider_name, query, _engine_failure(exc))
            remaining = deadline - time.monotonic()
            delay = _retry_delay(exc, remaining)
            if delay is None:
                return _empty_call(provider_name, query, _engine_failure(exc))
            await asyncio.sleep(delay)
    return _empty_call(provider_name, query)


async def _run_paginated[TResponse](
    provider_name: str,
    query: str,
    num_results: int,
    *,
    request_factory: Callable[[int, float], RequestFn[TResponse]],
    parse_response: Callable[[TResponse], EngineCall],
    http_client: httpx.AsyncClient | None,
    timeout_seconds: float,
) -> EngineCall:
    """Fetch only the bounded pages needed to satisfy ``num_results``."""
    if http_client is None:
        # Reuse one connection pool across pages for direct callers. The
        # normal search path already injects the shared run-level client.
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as owned_client:
            return await _run_paginated(
                provider_name,
                query,
                num_results,
                request_factory=request_factory,
                parse_response=parse_response,
                http_client=owned_client,
                timeout_seconds=timeout_seconds,
            )

    page_limit = min(_PAGE_SIZE, num_results)
    page_count = min(_MAX_PAGE_COUNT, max(1, math.ceil(num_results / _PAGE_SIZE)))
    deadline = time.monotonic() + timeout_seconds
    hits: list[SearchHit] = []
    seen_links: set[str] = set()
    expansion: list[str] = []
    integrity: QueryIntegrity | None = None
    failure: EngineFailure | None = None

    for page_index in range(page_count):
        page = await _run_page(
            provider_name,
            query,
            page_limit,
            page_index=page_index,
            request_factory=request_factory,
            parse_response=parse_response,
            http_client=http_client,
            deadline=deadline,
        )
        if integrity is None and page.integrity is not None:
            integrity = page.integrity
        added = 0
        for hit in page.hits:
            key = hit.url.strip().rstrip("/").casefold()
            if key in seen_links:
                continue
            seen_links.add(key)
            hits.append(hit)
            added += 1
        for seed in page.expansion:
            if seed not in expansion and len(expansion) < _EXPANSION_LIMIT:
                expansion.append(seed)
        if failure is None and page.failure is not None:
            failure = page.failure
        if page.failure is not None and not page.hits:
            break
        if len(page.hits) < page_limit or added == 0:
            break
        if deadline - time.monotonic() <= 0:
            break
    return EngineCall(
        adapter=provider_name,
        query=query,
        hits=tuple(hits[:num_results]),
        expansion=tuple(expansion),
        integrity=integrity,
        failure=failure,
    )


# ------------------------------------------------------------------
# Provider entry points.
# ------------------------------------------------------------------


async def search_brightdata(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    country: str = "us",
    language: str = "en",
    search_type: str = "web",
    exact_match: bool = True,
    freshness: str | None = None,
    provider_name: str = "brightdata",
    yandex_region: str | None = None,
) -> EngineCall:
    if not query.strip() or num_results < 1:
        return EngineCall(adapter=provider_name, query=query)
    if provider_name not in _SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported Bright Data provider: {provider_name}")

    api_key = get_brightdata_api_key()
    payload_base = resolve_payload_base()
    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if provider_name == "brightdata_bing":
        return await _search_bing(
            query,
            num_results,
            http_client,
            payload_base,
            req_headers,
            country,
            language,
        )

    if provider_name == "brightdata_yandex":
        return await _search_yandex(
            query,
            num_results=num_results,
            http_client=http_client,
            payload_base=payload_base,
            req_headers=req_headers,
            yandex_region=yandex_region or yandex_region_for_country(country),
            language=language,
        )

    google_timeout = max(30.0, float(settings.search_retrieve_budget_seconds))
    page_limit = min(_PAGE_SIZE, num_results)

    def _google_request_factory(page_index: int, request_timeout: float) -> RequestFn[dict]:
        async def _request(client: httpx.AsyncClient) -> dict:
            google_url = build_google_url(
                query,
                country,
                language,
                search_type,
                exact_match,
                freshness,
                start=page_index * _PAGE_SIZE,
            )
            body = {
                **payload_base,
                "url": google_url,
                # Per the SERP API docs, API requests enable mismatch delivery
                # through the body rather than a target-URL parameter; with
                # this on, truncated/corrected queries arrive as data and the
                # parse validates them itself via general/spelling fields.
                "data_options": {"return_mismatch": True},
            }
            response = await client.post(
                _REST_ENDPOINT,
                json=body,
                headers=req_headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError(
                    "BrightData response was not valid JSON.", provider=provider_name
                ) from exc
            if not isinstance(data, dict):
                raise BrightDataError(
                    "BrightData response was not a JSON object.", provider=provider_name
                )
            return data

        return _request

    def _google_parse(data: dict) -> EngineCall:
        return parse_brightdata_response(
            data, search_type, page_limit, adapter=provider_name, sent_query=query
        )

    return await _run_paginated(
        provider_name,
        query,
        num_results,
        request_factory=_google_request_factory,
        parse_response=_google_parse,
        http_client=http_client,
        timeout_seconds=google_timeout,
    )


async def _search_bing(
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    payload_base: dict,
    headers: dict,
    country: str,
    language: str,
) -> EngineCall:
    bing_timeout = settings.search_retrieve_budget_seconds
    page_limit = min(_PAGE_SIZE, num_results)

    def _request_factory(page_index: int, request_timeout: float) -> RequestFn[dict]:
        async def _request(client: httpx.AsyncClient) -> dict:
            url = build_bing_url(
                query,
                country,
                language,
                first=1 + page_index * _PAGE_SIZE,
            )
            body = {**payload_base, "url": url}
            response = await client.post(
                _REST_ENDPOINT,
                json=body,
                headers=headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise BrightDataError(
                    "BrightData Bing response was not valid JSON.",
                    provider="brightdata_bing",
                ) from exc
            if not isinstance(data, dict):
                raise BrightDataError(
                    "BrightData Bing response was not a JSON object.",
                    provider="brightdata_bing",
                )
            return data

        return _request

    def _parse(data: dict) -> EngineCall:
        return parse_brightdata_response(
            data, "web", page_limit, adapter="brightdata_bing", sent_query=query
        )

    return await _run_paginated(
        provider_name="brightdata_bing",
        query=query,
        num_results=num_results,
        request_factory=_request_factory,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=bing_timeout,
    )


def parse_yandex_html_response(
    html: str,
    num_results: int,
    *,
    adapter: str = "brightdata_yandex",
) -> EngineCall:
    """Parse organic results from a raw Yandex SERP response."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    hits: list[SearchHit] = []
    for item in soup.select("li.serp-item, ul#search-result > li"):
        classes = item.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        if "serp-item_type_ad" in classes or item.select_one("[class*='AdvLabel']"):
            continue
        link_tag = item.select_one("a.OrganicTitle-Link[href], h2 a[href], a.Link[href]")
        if link_tag is None:
            continue
        link = str(link_tag.get("href", "")).strip()
        if not link.startswith(("http://", "https://")):
            continue
        title = link_tag.get_text(" ", strip=True)
        if not title:
            continue
        snippet_tag = item.select_one(
            ".OrganicText, .TextContainer, .organic__text, .Organic-ContentWrapper"
        )
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""
        domain = extract_domain_from_url(link)
        if not domain:
            continue
        hits.append(
            SearchHit(
                title=title,
                url=link,
                snippet=snippet,
                domain=domain,
                adapter=adapter,
            )
        )
        if len(hits) >= num_results:
            break
    return EngineCall(adapter=adapter, query="", hits=tuple(hits))


async def _search_yandex(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None,
    payload_base: dict,
    req_headers: dict,
    yandex_region: str | None,
    language: str,
) -> EngineCall:
    """Fetch Yandex SERP via BrightData and parse the raw HTML response."""
    yandex_timeout = settings.search_retrieve_budget_seconds
    page_limit = min(_PAGE_SIZE, num_results)

    def _request_factory(page_index: int, request_timeout: float) -> RequestFn[str]:
        async def _request(client: httpx.AsyncClient) -> str:
            yandex_url = build_yandex_url(
                query,
                yandex_region,
                language,
                page=page_index + 1 if num_results > _PAGE_SIZE else None,
            )
            body = {**payload_base, "url": yandex_url}
            response = await client.post(
                _REST_ENDPOINT,
                json=body,
                headers=req_headers,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            return response.text

        return _request

    def _parse(html: str) -> EngineCall:
        return parse_yandex_html_response(html, page_limit, adapter="brightdata_yandex")

    return await _run_paginated(
        provider_name="brightdata_yandex",
        query=query,
        num_results=num_results,
        request_factory=_request_factory,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=yandex_timeout,
    )
