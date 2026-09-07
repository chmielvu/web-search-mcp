"""Tier-2 generic extraction stages — one directly-callable async function per backend.

No orchestration here; fetch_pipeline.py owns availability ordering.

Stages:
  - _fetch_via_jina      : Jina Reader (free, no API key)
  - _fetch_via_local     : BS4+markdownify (offline, pure HTTP)
  - _fetch_via_crawl4ai  : Crawl4AI remote POST /md (cloud markdown)
  - _fetch_via_camoufox  : Camoufox sidecar (stealth-Firefox, returns raw HTML -> markdown)
"""

from __future__ import annotations

import asyncio
import logging
import httpx
from typing import Any, Awaitable, Callable, TypeVar
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

from .artifact import ContentArtifact, ContentError
from .resolvers.document import fetch_document_markdown
from .jina_reader import JinaReaderError, fetch_with_jina_reader
from ..utils.content_classify import chrome_ratio, classify_markdown
from ..utils.text_clean import (
    parse_jina_frontmatter,
    strip_boilerplate,
    strip_jina_frontmatter,
)
from .html_extract import extract_html_as_markdown
from .remote_clients import (
    CamoufoxClientError,
    Crawl4AIClientError,
    get_camoufox_client,
    get_crawl4ai_client,
)
from .safe_fetch import SafeFetchError, safe_fetch_url
from .typed_content import (
    SUPPORTED_TYPED_FORMATS,
    detect_content_format,
    relabel_typed_artifact,
    render_typed_content,
)
from ..utils.url_canonicalize import canonicalize_url
from ..settings import settings
from ..telemetry import record_content_resolution

_CRAWL4AI_SEMAPHORE = asyncio.Semaphore(max(1, settings.web_fetch_workers))
_CAMOUFOX_SEMAPHORE = asyncio.Semaphore(1)
LOGGER = logging.getLogger(__name__)


def _soup(html: str):
    if BeautifulSoup is None:
        return None
    return BeautifulSoup(html or "", "html.parser")


def _safe_domain(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def _stage_extract_metadata(
    html: str, *, page_url: str, fetched_url: str | None = None
) -> dict[str, str]:
    soup = _soup(html)
    metadata: dict[str, str] = {
        "fetched_url": fetched_url or page_url,
        "domain": _safe_domain(fetched_url or page_url) or "",
    }
    if soup is None:
        return {key: value for key, value in metadata.items() if value}

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if title:
        metadata["title"] = title

    def _meta(*, name: str | None = None, property: str | None = None) -> str:
        attrs: dict[str, str] = {}
        if name:
            attrs["name"] = name
        if property:
            attrs["property"] = property
        tag = soup.find("meta", attrs=attrs)  # type: ignore[call-overload]
        content = tag.get("content") if tag else None
        return content.strip() if isinstance(content, str) and content.strip() else ""

    for key, value in (
        ("description", _meta(name="description") or _meta(property="og:description")),
        ("site_name", _meta(property="og:site_name") or _meta(name="application-name")),
    ):
        if value:
            metadata[key] = value

    canonical = ""
    link = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})  # type: ignore[call-overload]
    if link:
        href = link.get("href")
        canonical = href.strip() if isinstance(href, str) and href.strip() else ""
    if canonical:
        metadata["canonical_url"] = canonical

    html_tag = soup.find("html")
    if html_tag:
        lang = html_tag.get("lang")
        if isinstance(lang, str) and lang.strip():
            metadata["language"] = lang.strip()

    return metadata


def _stage_extract_links(
    html: str,
    *,
    base_url: str,
    max_links: int = 25,
    include_external: bool = True,
    same_domain_only: bool = False,
) -> list[dict[str, str | bool]]:
    soup = _soup(html)
    if soup is None:
        return []

    base_domain = _safe_domain(base_url)
    links: list[dict[str, str | bool]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            continue

        domain = parsed.netloc.lower() or ""
        internal = bool(base_domain and domain == base_domain)
        if same_domain_only or not include_external:
            if not internal:
                continue

        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen:
            continue
        seen.add(normalized_url)

        text = anchor.get_text(" ", strip=True) or normalized_url
        links.append(
            {
                "url": normalized_url,
                "text": text,
                "domain": domain,
                "internal": internal,
            }
        )
        if len(links) >= max_links:
            break

    return links


T = TypeVar("T")


async def _stage_retry(
    label: str,
    coro_factory: Callable[[], Awaitable[T]],
    *,
    retries: int = 1,
    base_delay: float = 1.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> T:
    """Retry a stage coroutine on transient failures with exponential backoff.

    Defaults to retrying on httpx transport/server errors. Callers can override
    ``retryable_exceptions`` for stage-specific semantics (e.g. Crawl4AIClientError).
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        except Exception as exc:
            if retryable_exceptions is not None and isinstance(exc, retryable_exceptions):
                if getattr(exc, "retryable", True) is False:
                    raise
                last_exc = exc
            else:
                raise
        if attempt < retries:
            delay = base_delay * (2**attempt)
            LOGGER.warning(
                "%s retry %d/%d after %.1fs: %s", label, attempt + 1, retries, delay, last_exc
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ------------------------------------------------------------------
# Stage 1: Jina Reader
# ------------------------------------------------------------------


async def _fetch_via_jina(
    url: str,
    *,
    max_response_bytes: int,
    include_links: bool,
    timeout_seconds: float | None = None,
) -> ContentArtifact | None:
    """Fetch via Jina Reader (free, no API key).

    Returns ``None`` on transport failure (unavailable). Returns a
    ``ContentArtifact`` on success or low-quality response.
    """
    try:
        if timeout_seconds is not None and timeout_seconds < 1.0:
            LOGGER.debug("Jina stage skipped: remaining budget too small (%s)", timeout_seconds)
            return None
        jina_markdown = await _stage_retry(
            "jina_reader",
            lambda: fetch_with_jina_reader(
                url,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else 25.0,
            ),
        )
    except (
        JinaReaderError,
        httpx.TimeoutException,
        httpx.RequestError,
        httpx.HTTPStatusError,
    ) as exc:
        LOGGER.debug("Jina Reader failed: %s", exc)
        return None

    envelope = parse_jina_frontmatter(jina_markdown)
    jina_warning = envelope.get("warning", "")
    pre_chrome = chrome_ratio(jina_markdown)
    jina_markdown = strip_boilerplate(jina_markdown)
    cls = classify_markdown(jina_markdown)
    word_count = len(jina_markdown.split())
    if jina_warning:
        status = "blocked"
        error = ContentError(code="access_blocked:jina_warning", message=jina_warning)
    elif cls.status == "success" and (pre_chrome > 0.5 or word_count < 80):
        status = "partial"
        error = ContentError(code="chrome_boilerplate", message="chrome_boilerplate")
    else:
        status = cls.status
        error = (
            None
            if cls.status == "success"
            else ContentError(
                code=cls.reason or "jina_low_quality", message=cls.reason or "jina_low_quality"
            )
        )
    record_content_resolution(
        stage="jina_reader",
        url=url,
        success=status == "success",
        word_count=word_count,
        extraction_method="jina_reader",
    )
    artifact = ContentArtifact(
        input_url=url,
        normalized_url=canonicalize_url(url),
        fetched_url=url,
        status=status,
        source_type="html",
        fetch_backend="jina_reader",
        content_type="text/markdown",
        markdown=jina_markdown,
        word_count=word_count,
        quality_score=0.9 if status == "success" else 0.5,
        error=error,
    )
    return relabel_typed_artifact(artifact)


# ------------------------------------------------------------------
async def _fetch_via_local(
    url: str,
    *,
    max_response_bytes: int,
    include_links: bool,
    timeout_seconds: float | None = None,
) -> ContentArtifact:
    """Fetch via local BS4+markdownify (offline, pure HTTP).

    Always returns a ``ContentArtifact`` (never raises).
    """
    canonical = canonicalize_url(url)
    if timeout_seconds is not None and timeout_seconds < 1.0:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="safe_http",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(
                code="stage_timeout_exceeded",
                message="Remaining pipeline budget too small for local fetch",
                retryable=True,
            ),
        )

    try:
        fetched = await _stage_retry(
            "safe_fetch",
            lambda: safe_fetch_url(
                url,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else 20.0,
                max_response_bytes=max_response_bytes,
            ),
        )
    except SafeFetchError as exc:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="blocked" if exc.code.startswith("private") else "error",
            source_type="web",
            fetch_backend="safe_http",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=None,
            status="error",
            source_type="web",
            fetch_backend="fallback_failed",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code="fallback_fetch_failed", message=str(exc), retryable=True),
        )
    typed_text = strip_jina_frontmatter(fetched.text or "")
    typed_format = detect_content_format(url, fetched.content_type, typed_text)
    if typed_format is not None and typed_format in SUPPORTED_TYPED_FORMATS:
        typed_markdown, typed_metadata, typed_links = render_typed_content(
            typed_format,
            typed_text or fetched.body.decode("utf-8", errors="replace"),
            fetched.fetched_url or url,
        )
        typed_cls = classify_markdown(typed_markdown)
        typed_status = (
            "success"
            if typed_markdown.strip() and typed_cls.status in ("success", "partial")
            else typed_cls.status
        )
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=fetched.fetched_url,
            status=typed_status,
            source_type=typed_format,
            fetch_backend="typed_content",
            content_type=fetched.content_type,
            markdown=typed_markdown,
            metadata=typed_metadata,
            links=typed_links if include_links else None,
            word_count=len(typed_markdown.split()),
            quality_score=1.0 if typed_status == "success" else 0.4,
            error=None
            if typed_status == "success"
            else ContentError(
                code=typed_cls.reason or "typed_content_partial",
                message=typed_cls.reason or "partial typed content",
            ),
        )

    # Handle Documents & PDFs
    if fetched.doc_type:
        doc_artifact = await fetch_document_markdown(url, max_response_bytes=max_response_bytes)
        if doc_artifact.status == "success":
            return doc_artifact
    html = fetched.text

    metadata = _stage_extract_metadata(html, page_url=url, fetched_url=fetched.fetched_url)

    links: list[dict[str, Any]] | None = None
    if include_links:
        links = _stage_extract_links(
            html,
            base_url=fetched.fetched_url or url,
            include_external=True,
            same_domain_only=False,
        )

    markdown = extract_html_as_markdown(html, url=fetched.fetched_url)
    cls = classify_markdown(
        markdown,
        http_status=fetched.status_code if fetched.status_code != 200 else None,
        headers=fetched.response_headers,
    )

    if cls.status in ("blocked", "error"):
        return ContentArtifact(
            input_url=url,
            normalized_url=canonical,
            fetched_url=fetched.fetched_url,
            status=cls.status,
            source_type="html",
            fetch_backend="local",
            content_type=fetched.content_type,
            markdown=markdown,
            metadata=metadata,
            links=links,
            word_count=len(markdown.split()),
            quality_score=0.2,
            error=ContentError(
                code=cls.reason or "blocked", message=cls.reason or "blocked", retryable=False
            ),
        )

    return ContentArtifact(
        input_url=url,
        normalized_url=canonical,
        fetched_url=fetched.fetched_url,
        status=cls.status,
        source_type="html",
        fetch_backend="local",
        content_type=fetched.content_type,
        markdown=markdown,
        metadata=metadata,
        links=links,
        word_count=len(markdown.split()),
        quality_score=1.0 if cls.status == "success" else 0.4,
        error=None
        if cls.status == "success"
        else ContentError(code=cls.reason or "partial", message=cls.reason or "partial"),
    )


# ------------------------------------------------------------------
# Stage 3: Crawl4AI remote (POST /md, non-browser)
# ------------------------------------------------------------------


async def _fetch_via_crawl4ai(
    url: str,
    *,
    max_response_bytes: int,
    include_links: bool,
    timeout_seconds: float | None = None,
) -> ContentArtifact:
    """Fetch via Crawl4AI remote POST /md (non-browser cloud markdown).

    Raises ``Crawl4AIClientError`` on transport failure (unavailable).
    Returns a ``ContentArtifact`` on success or low-quality response.
    """
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIClientError("Crawl4AI client not configured", retryable=False)

    async with _CRAWL4AI_SEMAPHORE:
        markdown = await _stage_retry(
            "crawl4ai_remote",
            lambda: client.fetch_markdown(url, mode="fit"),
            retryable_exceptions=(Crawl4AIClientError,),
        )
    if len(markdown.encode("utf-8")) > max_response_bytes:
        raise Crawl4AIClientError(
            f"Crawl4AI response exceeds {max_response_bytes} byte cap",
            retryable=False,
        )
    cls = classify_markdown(markdown)
    word_count = len(markdown.split())
    record_content_resolution(
        stage="crawl4ai_remote",
        url=url,
        success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")),
        word_count=word_count,
        extraction_method="crawl4ai_md",
    )
    return relabel_typed_artifact(
        ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status=cls.status,
            source_type="html",
            fetch_backend="crawl4ai_remote",
            content_type="text/markdown",
            markdown=markdown,
            metadata=None,
            links=None,
            word_count=word_count,
            quality_score=1.0 if cls.status == "success" else 0.6,
            error=None
            if cls.status == "success"
            else ContentError(
                code=cls.reason or "crawl4ai_low_quality",
                message=cls.reason or "crawl4ai_low_quality",
            ),
        )
    )


async def _fetch_via_camoufox(
    url: str,
    *,
    max_response_bytes: int,
    include_links: bool,
    timeout_seconds: float | None = None,
) -> ContentArtifact:
    """Fetch via Camoufox sidecar: raw HTML -> markdown + metadata + links.

    Camoufox returns raw HTML (POST /content), NOT markdown — pipe through
    extract_html_as_markdown. Do NOT swap for Crawl4AIClient.fetch_markdown.

    Raises ``CamoufoxClientError`` on transport failure (unavailable).
    Returns a ``ContentArtifact`` on success or low-quality response.
    """
    client = get_camoufox_client()
    if client is None:
        raise CamoufoxClientError("Camoufox client not configured", retryable=False)
    async with _CAMOUFOX_SEMAPHORE:
        html = await client.fetch_html(url, max_bytes=max_response_bytes)

    markdown = extract_html_as_markdown(html, url=url)
    cls = classify_markdown(markdown)
    word_count = len(markdown.split())

    metadata = _stage_extract_metadata(html, page_url=url)
    links = _stage_extract_links(html, base_url=url) if include_links else None

    record_content_resolution(
        stage="camoufox_remote",
        url=url,
        success=cls.status == "success",
        size_bytes=len(markdown.encode("utf-8")),
        word_count=word_count,
        extraction_method="camoufox_remote",
    )
    return relabel_typed_artifact(
        ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status=cls.status,
            source_type="html",
            fetch_backend="camoufox_remote",
            content_type="text/markdown",
            markdown=markdown,
            metadata=metadata,
            links=links,
            word_count=word_count,
            quality_score=1.0 if cls.status == "success" else 0.4,
            error=None
            if cls.status == "success"
            else ContentError(
                code=cls.reason or "camoufox_low_quality",
                message=cls.reason or "camoufox_low_quality",
            ),
        )
    )
