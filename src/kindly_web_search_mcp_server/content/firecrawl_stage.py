"""Firecrawl Cloud batch scrape stage for batch_get_content.

Firecrawl is the first backend for batch_get_content. Returns None when
Firecrawl is unavailable or the batch call fails, so the orchestrator can
fall back to Crawl4AI for the whole batch. Returns a dict (possibly empty)
of {url: ContentArtifact} on success.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..search.normalize import canonicalize_url
from ..settings import settings
from .artifact import ContentArtifact, ContentError
from .options import FetchOptions

if TYPE_CHECKING:
    from firecrawl.v2.client_async import AsyncFirecrawlClient

LOGGER = logging.getLogger(__name__)

_client: AsyncFirecrawlClient | None = None


def get_firecrawl_client() -> AsyncFirecrawlClient | None:
    """Get or create the singleton Firecrawl async client. None if no API key."""
    global _client
    if not settings.firecrawl_api_key:
        return None
    if _client is None:
        from firecrawl.v2.client_async import AsyncFirecrawlClient

        _client = AsyncFirecrawlClient(
            api_key=settings.firecrawl_api_key,
            api_url=settings.firecrawl_api_url,
            timeout=settings.firecrawl_timeout_seconds,
        )
        LOGGER.info("Firecrawl client initialized: %s", settings.firecrawl_api_url)
    return _client


async def close_firecrawl_client() -> None:
    """Close the singleton Firecrawl client on shutdown."""
    global _client
    if _client is None:
        return
    try:
        http_client = getattr(_client, "async_http_client", None)
        if http_client is not None:
            close_method = getattr(http_client, "aclose", None) or getattr(
                http_client, "close", None
            )
            if close_method is not None:
                result = close_method()
                if hasattr(result, "__await__"):
                    await result
    except Exception as exc:
        LOGGER.warning("Failed to close Firecrawl client: %s", exc)
    finally:
        _client = None


def _resolve_input_url(
    fetched_url: str | None,
    urls: list[str],
    input_by_normalized: dict[str, str],
) -> str | None:
    if not fetched_url:
        return None
    normalized = canonicalize_url(fetched_url)
    if normalized in input_by_normalized:
        return input_by_normalized[normalized]
    for url in urls:
        if url == fetched_url or canonicalize_url(url) == normalized:
            return url
    return None


def _doc_links(doc: Any, options: FetchOptions) -> list[dict[str, Any]] | None:
    if not options.include_links:
        return None
    raw_links: list[Any] = getattr(doc, "links", None) or []
    if not raw_links:
        return None
    normalized: list[dict[str, Any]] = []
    for link in raw_links[: options.max_links]:
        normalized.append(link if isinstance(link, dict) else {"href": str(link), "text": ""})
    return normalized or None


async def run_firecrawl_batch(
    urls: list[str],
    *,
    options: FetchOptions | None = None,
    batch_params: Any | None = None,
) -> dict[str, ContentArtifact] | None:
    """Run Firecrawl batch_scrape. None = unavailable/failed; dict = success."""
    del batch_params
    client = get_firecrawl_client()
    if client is None:
        return None

    options = options or FetchOptions()
    formats = ["markdown"] + (["links"] if options.include_links else [])

    try:
        result = await client.batch_scrape(
            urls,
            formats=formats,
            poll_interval=settings.firecrawl_poll_interval_seconds,
            timeout=settings.firecrawl_max_poll_seconds,
            only_main_content=True,
            ignore_invalid_urls=True,
        )
    except Exception as exc:
        LOGGER.warning("Firecrawl batch scrape failed: %s", exc)
        return None

    artifacts: dict[str, ContentArtifact] = {}
    data = getattr(result, "data", None) or []
    input_by_normalized = {canonicalize_url(url): url for url in urls}

    for doc in data:
        meta = getattr(doc, "metadata_dict", None)
        if not isinstance(meta, dict):
            meta = {}
        fetched_url = meta.get("source_url") or meta.get("url")
        input_url = _resolve_input_url(fetched_url, urls, input_by_normalized)
        if input_url is None:
            continue

        markdown = getattr(doc, "markdown", "") or ""
        has_error = not markdown.strip()

        artifacts[input_url] = ContentArtifact(
            input_url=input_url,
            normalized_url=canonicalize_url(input_url),
            fetched_url=fetched_url,
            status="error" if has_error else "success",
            source_type="html",
            fetch_backend="firecrawl_cloud",
            content_type="text/markdown",
            markdown=markdown,
            metadata=meta,
            links=_doc_links(doc, options),
            error=ContentError(
                code="firecrawl_empty",
                message="Firecrawl returned empty markdown for this URL.",
                retryable=True,
            )
            if has_error
            else None,
        )

    return artifacts
