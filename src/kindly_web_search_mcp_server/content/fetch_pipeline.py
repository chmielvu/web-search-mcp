"""Single-source content acquisition + selection → finalize_artifact.

Pipeline flow:

  normalized identity
      │    ↓
  cache candidate lookup  (versioned key includes policy + mode)
      │    ↓ miss
  resolver registry       (REGISTRY in content/resolver_registry.py)
      │    ↓ first acquisition target
  DOM-routed Jina candidate (preflight classify → per-route engine;
                             skipped on ``route == browser``)
      │    ↓ no usable candidate
  Crawl4AI ``POST /crawl`` (agent-ready fit Markdown; runs on browser-route too)
      │    ↓ no usable candidate
  Camoufox (skipped when preflight already saw challenge evidence)
      │    ↓ no usable candidate
  Bright Data Web Unlocker (always on when configured)
      │    ↓ no usable candidate
  Wayback archive
      │    ↓
  MarkdownProcessor → Candidate for every path
      │    ↓
  selection: usable > error/challenge, requested scope > narrower,
             accepted > rejected, complete > truncated,
             higher measured quality wins, earlier index breaks ties
      │    ↓
  finalize_artifact(input_url, selected_candidate, options=...)

There is no local HTML rung, no Trafilatura / markdownify ladder,
no regex fallback, and no scattered ``replace(artifact, ...)`` rewrite. The
only finalization path runs through ``finalize_artifact``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from ..settings import settings
from ..telemetry import record_content_error
from ..utils.url_canonicalize import canonicalize_url
from .constructor import ContentArtifact, ContentError, finalize_artifact
from .dom_detector import RouteDecision, analyze_html
from .http_utils import SafeFetchError, safe_fetch_url
from .jina_reader import JinaReaderError
from .jina_reader import fetch_raw_document as fetch_jina_raw_document
from .markdown_processor import MarkdownProcessor
from .models import (
    AcquisitionError,
    Candidate,
    Diagnostic,
    FetchContext,
    FetchOptions,
    ParsedURL,
    ProcessedMarkdown,
    ProcessingMode,
    QualityReport,
    RawDocument,
    ResolverSpec,
    ResolverTarget,
    TextDocument,
)
from .remote_clients import (
    CamoufoxClientError,
    Crawl4AIClientError,
    UnlockerClientError,
    crawl4ai_browser_params,
    crawl4ai_markdown_params,
    extract_crawl_markdown_candidates,
    get_camoufox_client,
    get_crawl4ai_client,
    get_unlocker_client,
)
from .renderers import render_raw_document
from .resolver_registry import REGISTRY
from .resolvers.files import DOC_EXTENSIONS
from .resolvers.wayback import fetch_wayback_raw

LOGGER = logging.getLogger(__name__)

# Re-export the canonical contracts declared in :mod:`content.models` so
# callers that historically imported them from this module keep resolving.
# The single source of truth is ``content.models``; do not redefine here.
__all__ = (
    "ContentArtifact",
    "ContentError",
    "FetchContext",
    "FetchOptions",
    "MarkdownProcessor",
    "ProcessingMode",
    "RawDocument",
    "StageAttempt",
    "evaluate_candidate",
    "fetch_content_artifact",
    "fetch_deadline_seconds",
    "finalize_artifact",
    "raw_document_from_crawl_item",
    "select_candidate",
)

FETCH_DEADLINE_FLOOR_SECONDS = 60.0
FETCH_UNLOCKER_BUDGET_SECONDS = 240.0


def fetch_deadline_seconds() -> float:
    """Wall-clock budget for one fetch, including Web Unlocker when configured.

    FastMCP ``fetch`` / ``crawl_web`` catalog timeouts must stay at least this
    large or the tool wrapper kills Unlocker before it returns.
    """
    budget = max(FETCH_DEADLINE_FLOOR_SECONDS, settings.web_fetch_timeout_seconds)
    if get_unlocker_client() is not None:
        budget = max(budget, FETCH_UNLOCKER_BUDGET_SECONDS)
    return budget


# ------------------------------------------------------------------
# Versioned cache key — policy + mode baked in so older cache entries
# (no fingerprint, wrong mode) cannot match a fresh request.
# ------------------------------------------------------------------
_CACHE_KEY_TEMPLATE: Final[str] = "candidate-v1:{policy}:{mode}:{normalized}"


@dataclass(frozen=True, slots=True)
class StageAttempt:
    """One recorded probe, regardless of whether it succeeded."""

    label: str
    backend: str
    outcome: str
    error_code: str | None
    latency_ms: float | None
    selection_reason: str | None = None
    chars_kept: int | None = None
    quality_score: float | None = None

    _ANALYTICS_OUTCOMES: Final[frozenset[str]] = frozenset(
        {"success", "partial", "blocked", "unsupported", "error", "skipped"}
    )

    def _analytics_outcome(self) -> str:
        """Map an internal attempt outcome into the analytics CHECK domain."""
        if self.outcome in {"success", "partial", "blocked", "unsupported", "error", "skipped"}:
            return self.outcome
        if self.outcome in {"accepted", "selected", "hit"}:
            return "success"
        if self.outcome == "rejected":
            return "partial"
        return "error"

    def to_dict(self, *, item_index: int, normalized_url: str) -> dict[str, Any]:
        return {
            "item_index": item_index,
            "stage": self.label,
            "stage_order": _STAGE_ORDER.get(self.label, 0),
            "outcome": self._analytics_outcome(),
            "normalized_url": normalized_url,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "selection_reason": self.selection_reason,
            "backend": self.backend,
            "attempt_count": 1,
            "chars_kept": self.chars_kept,
            "quality_score": self.quality_score,
            "skipped_reason": None,
        }


_STAGE_ORDER: Final[dict[str, int]] = {
    "cache": 0,
    "resolver_registry": 1,
    "jina_generic": 2,
    "crawl4ai_remote": 3,
    "browser_optional": 4,
    "unlocker_remote": 5,
    "archive_optional": 6,
    "llms_txt": 7,
    "selection": 8,
}


# ------------------------------------------------------------------
# Stage attempts buffer (one semantic attempt per attempted path).
# ------------------------------------------------------------------


class _AttemptLog:
    """Fire-and-forget attempt recorder shared by every stage."""

    def __init__(self, sink: list[dict[str, Any]] | None, *, normalized_url: str) -> None:
        self._sink = sink
        self._normalized_url = normalized_url
        self._order = 0

    def record(self, attempt: StageAttempt) -> None:
        if self._sink is None:
            return
        self._order += 1
        payload = attempt.to_dict(item_index=self._order, normalized_url=self._normalized_url)
        self._sink.append(payload)

    def record_outcome(
        self,
        *,
        label: str,
        backend: str,
        outcome: str,
        error_code: str | None = None,
        selection_reason: str | None = None,
        candidate: Candidate | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """Record one attempt, deriving measured facts from ``candidate`` when given."""
        chars = len(candidate.processed.markdown) if candidate is not None else None
        score = candidate.processed.quality.score if candidate is not None else None
        self.record(
            StageAttempt(
                label=label,
                backend=backend,
                outcome=outcome,
                error_code=error_code,
                latency_ms=latency_ms,
                selection_reason=selection_reason,
                chars_kept=chars,
                quality_score=score,
            )
        )


# ------------------------------------------------------------------
# Acquisition: every backend returns a RawDocument. Resolvers raise
# ``AcquisitionError`` on failure. The orchestrator wraps each result
# through MarkdownProcessor and records one attempt per path.
# ------------------------------------------------------------------


def _acquisition_error_to_failure(
    exc: BaseException,
    *,
    fallback_code: str,
) -> ContentError:
    """Translate any acquisition failure into a stable ContentError."""
    if isinstance(exc, AcquisitionError):
        return exc.error
    return ContentError(code=fallback_code, message=str(exc)[:300])


def _empty_failure_document(
    *,
    input_url: str,
    fetched_url: str | None,
    backend: str,
) -> RawDocument:
    """A RawDocument carrying no body — used for failure candidates."""
    return RawDocument(
        input_url=input_url,
        fetched_url=fetched_url or input_url,
        source_type="web",
        fetch_backend=backend,
        body=TextDocument(text=""),
        content_type=None,
        title=None,
        metadata={},
        links=(),
        diagnostics=(),
        http_status=None,
        response_headers={},
        complete=False,
        scope="full",
        bytes_downloaded=0,
        redirect_count=0,
    )


async def _acquire_via_resolver(
    spec: ResolverSpec,
    target: ResolverTarget,
    ctx: FetchContext,
) -> RawDocument:
    """Invoke a registered resolver inside the shared timeout surface."""
    return await spec.fetch(target, ctx)


async def _decide_jina_route(url: str, ctx: FetchContext) -> RouteDecision:
    """Classify the target with a bounded HTML preflight before any Jina call.

    Preflight failures fall back to the fast ``agent`` route instead of
    failing the stage; non-HTML targets skip signal extraction the same way.
    """
    try:
        remaining = ctx.timeout(maximum=8.0)
        preflight_timeout = max(1.0, min(8.0, remaining / 3))
    except TimeoutError:
        return analyze_html("", status=200)
    try:
        fetched = await safe_fetch_url(
            url,
            timeout_seconds=preflight_timeout,
            max_response_bytes=1_500_000,
        )
    except (TimeoutError, SafeFetchError, httpx.HTTPError, OSError, ValueError) as exc:
        LOGGER.debug("Jina preflight unavailable for %s: %s", url, exc)
        return analyze_html("", status=200)
    content_type = (fetched.content_type or "").split(";")[0].strip().lower()
    if (
        fetched.is_pdf
        or fetched.doc_type
        or content_type
        not in (
            "",
            "text/html",
            "application/xhtml+xml",
        )
    ):
        return analyze_html("", status=200)
    return analyze_html(fetched.text or "", status=fetched.status_code)


async def _acquire_via_jina(
    url: str,
    *,
    ctx: FetchContext,
    decision: RouteDecision,
    timeout_seconds: float | None = None,
) -> RawDocument:
    """DOM-routed Jina Reader profile → RawDocument."""
    return await fetch_jina_raw_document(
        url,
        ctx=ctx,
        decision=decision,
        timeout_seconds=timeout_seconds,
    )


def raw_document_from_crawl_item(
    url: str,
    item: Mapping[str, Any],
    markdown: str,
    *,
    variant: str,
    links: tuple[dict[str, Any], ...] = (),
) -> RawDocument:
    """Build one neutral Markdown document from a Crawl4AI ``/crawl`` result."""
    metadata = item.get("metadata")
    metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
    metadata_dict["crawl_variant"] = variant
    metadata_dict["extraction_method"] = "crawl4ai_crawl"
    fetched_url = item.get("redirected_url") or item.get("url") or item.get("source_url") or url
    status_code = item.get("status_code") or item.get("status")
    http_status = (
        status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None
    )
    complete_value = item.get("success")
    complete = complete_value if isinstance(complete_value, bool) else None
    title = item.get("title")
    if not title and isinstance(metadata, Mapping):
        meta_title = metadata.get("title")
        title = meta_title if isinstance(meta_title, str) else None
    return RawDocument(
        input_url=url,
        fetched_url=str(fetched_url),
        source_type="html",
        fetch_backend="crawl4ai_remote",
        body=TextDocument(text=markdown, format="markdown"),
        content_type="text/markdown",
        title=str(title) if title else None,
        metadata=metadata_dict,
        links=links,
        http_status=http_status,
        complete=complete,
        scope="full",
        bytes_downloaded=len(markdown.encode("utf-8")),
    )


def _crawl4ai_target_elements(decision: RouteDecision | None) -> list[str] | None:
    """Choose Crawl4AI ``target_elements`` from DOM evidence, or omit them."""
    if decision is None:
        return None
    if decision.target_selector:
        return [decision.target_selector]
    if decision.signals.has_article:
        return ["article"]
    return None


async def _acquire_via_crawl4ai(
    url: str,
    *,
    ctx: FetchContext,
    decision: RouteDecision | None = None,
) -> list[RawDocument]:
    """Crawl4AI ``POST /crawl`` agent-ready Markdown → one RawDocument per variant."""
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIClientError("Crawl4AI client not configured", retryable=False)
    remaining = ctx.timeout(maximum=settings.crawl4ai_timeout_seconds)
    items = await client.crawl(
        [url],
        browser_params=crawl4ai_browser_params(),
        crawler_params=crawl4ai_markdown_params(
            target_elements=_crawl4ai_target_elements(decision),
            wait_for=decision.wait_for_selector if decision is not None else None,
        ),
        timeout=remaining,
    )
    if not items:
        raise Crawl4AIClientError("Crawl4AI /crawl returned no results", retryable=True)
    item = items[0]
    if item.get("success") is False:
        message = str(item.get("error") or item.get("message") or "Crawl4AI failed")
        raise Crawl4AIClientError(message, retryable=False)
    entries = extract_crawl_markdown_candidates(item)
    if not entries:
        raise Crawl4AIClientError("Crawl4AI returned no Markdown", retryable=False)
    documents: list[RawDocument] = []
    for entry in entries:
        markdown = entry["markdown"]
        if len(markdown.encode("utf-8")) > ctx.max_response_bytes:
            raise Crawl4AIClientError(
                f"Crawl4AI response exceeds {ctx.max_response_bytes} byte cap",
                retryable=False,
            )
        documents.append(
            raw_document_from_crawl_item(url, item, markdown, variant=entry["variant"])
        )
    return documents


async def _acquire_via_camoufox(url: str, *, ctx: FetchContext) -> RawDocument:
    """Camoufox stealth-Firefox HTML → RawDocument.

    Camoufox returns raw HTML, not markdown. The MarkdownProcessor
    evaluator runs the sanitize / shape pass; the body keeps ``format="html"``
    so callers know the body still needs sanitizing. Origin HTTP status is
    copied when the sidecar exposes it; it is left unset rather than forged.
    """
    client = get_camoufox_client()
    if client is None:
        raise CamoufoxClientError("Camoufox client not configured", retryable=False)
    remaining = ctx.timeout(maximum=settings.camoufox_timeout_seconds)
    result = await client.fetch_html(url, max_bytes=ctx.max_response_bytes, timeout=remaining)
    return RawDocument(
        input_url=url,
        fetched_url=result.fetched_url or url,
        source_type="html",
        fetch_backend="camoufox_remote",
        body=TextDocument(text=result.html, format="html"),
        content_type="text/html",
        title=None,
        metadata={"extraction_method": "camoufox_remote"},
        links=(),
        diagnostics=(),
        http_status=result.http_status,
        response_headers=result.response_headers,
        complete=True,
        scope="full",
        bytes_downloaded=len(result.html.encode("utf-8")),
        redirect_count=0,
    )


async def _acquire_via_unlocker(url: str, *, ctx: FetchContext) -> RawDocument:
    """Bright Data Web Unlocker Markdown → RawDocument."""
    client = get_unlocker_client()
    if client is None:
        raise UnlockerClientError("Web Unlocker client not configured", retryable=False)
    remaining = ctx.timeout(maximum=settings.brightdata_unlocker_timeout_seconds)
    result = await client.fetch_markdown(url, timeout=remaining)
    if len(result.markdown.encode("utf-8")) > ctx.max_response_bytes:
        raise UnlockerClientError(
            f"Web Unlocker response exceeds {ctx.max_response_bytes} byte cap",
            retryable=False,
        )
    return RawDocument(
        input_url=url,
        fetched_url=url,
        source_type="html",
        fetch_backend="brightdata_unlocker",
        body=TextDocument(text=result.markdown, format="markdown"),
        content_type="text/markdown",
        title=None,
        metadata={"extraction_method": "brightdata_unlocker"},
        links=(),
        diagnostics=(),
        http_status=result.http_status,
        response_headers=result.response_headers,
        complete=True,
        scope="full",
        bytes_downloaded=len(result.markdown.encode("utf-8")),
        redirect_count=0,
    )


# ------------------------------------------------------------------
# Wayback archive — always-on fallback when nothing was accepted yet.
# ------------------------------------------------------------------


async def _acquire_via_wayback(url: str, *, ctx: FetchContext) -> RawDocument | None:
    """Return a Wayback RawDocument or None when no snapshot exists."""
    try:
        return await fetch_wayback_raw(
            ResolverTarget(url=url, kind="wayback", values={"url": url}), ctx
        )
    except AcquisitionError:
        return None


# ------------------------------------------------------------------
# Cache candidate lookup. The page cache is keyed by policy+mode; the
# cache returns a RawDocument assembled from ``page_content`` and the
# ``extraction_method`` so the rest of the pipeline treats a hit like
# any other acquisition.
# ------------------------------------------------------------------


def _cache_key(normalized_url: str, options: FetchOptions) -> str:
    return _CACHE_KEY_TEMPLATE.format(
        policy=options.cache_fingerprint().split(":", 1)[0],
        mode=options.processing_mode,
        normalized=normalized_url,
    )


async def _cache_candidate_lookup(
    canonical_url: str,
    *,
    options: FetchOptions,
) -> Candidate | None:
    """Return a cached Candidate when the versioned key matches."""
    try:
        from ..cache import get_page_cache

        cache = get_page_cache()
    except (ImportError, OSError, RuntimeError) as exc:
        LOGGER.warning("Page cache unavailable for %s: %s", canonical_url, exc)
        return None
    try:
        key = _cache_key(canonical_url, options)
        entry = await cache.alookup(key)
    except (TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
        LOGGER.warning("Page cache lookup failed for %s: %s", canonical_url, exc)
        return None
    if not entry:
        return None
    backend = str(entry.get("extraction_method", "cached"))
    markdown = entry.get("page_content") or ""
    document = RawDocument(
        input_url=canonical_url,
        fetched_url=canonical_url,
        source_type="cached",
        fetch_backend=backend,
        body=TextDocument(text=markdown),
        content_type="text/markdown",
        title=None,
        metadata={"cache": True, "extraction_method": backend},
        links=(),
        diagnostics=(),
        http_status=200,
        response_headers={},
        complete=True,
        scope="full",
        bytes_downloaded=len(markdown.encode("utf-8")),
        redirect_count=0,
    )
    processor = MarkdownProcessor()
    rendered = render_raw_document(document)
    processed = await processor.process(rendered.markdown, options.processing_mode)
    return Candidate(
        document=document,
        processed=_with_originating_diagnostics(processed, document, rendered.links),
        attempt_index=0,
    )


# ------------------------------------------------------------------
# Candidate construction. Every completed path runs through
# MarkdownProcessor.process(...) once; the resulting ProcessedMarkdown is
# paired with the originating RawDocument to form one Candidate. A
# failure candidate carries an empty ProcessedMarkdown with the failure
# diagnostic attached.
# ------------------------------------------------------------------


def _with_originating_diagnostics(
    processed: ProcessedMarkdown,
    document: RawDocument,
    rendered_links: tuple[dict[str, Any], ...] = (),
) -> ProcessedMarkdown:
    """Augment processor diagnostics and links with acquisition evidence."""
    extras = tuple(document.diagnostics)
    links = processed.links or rendered_links
    if not extras and links == processed.links:
        return processed
    return ProcessedMarkdown(
        markdown=processed.markdown,
        quality=processed.quality,
        diagnostics=processed.diagnostics + extras,
        transforms=processed.transforms,
        links=links,
        structure=processed.structure,
    )


async def evaluate_candidate(
    document: RawDocument,
    *,
    attempt_index: int,
    options: FetchOptions,
) -> Candidate:
    processor = MarkdownProcessor()
    rendered = render_raw_document(document)
    processed = await processor.process(rendered.markdown, options.processing_mode)
    return Candidate(
        document=document,
        processed=_with_originating_diagnostics(processed, document, rendered.links),
        attempt_index=attempt_index,
    )


def _origin_access_flags(document: RawDocument) -> tuple[str, ...]:
    """Return blocking flags derived from the origin HTTP status alone.

    The MarkdownProcessor only sees rendered text; origin transport facts
    (401/403/429/5xx) are enforced here so no challenge page that happens to
    parse as Markdown can be accepted. Successful content from an accepted
    response keeps complete=None semantics untouched.
    """
    status = document.http_status
    if status is None or status < 400:
        return ()
    if status in {401, 403}:
        return ("login_wall",)
    if status == 429:
        return ("http_429",)
    if status >= 500:
        return (f"http_{status}",)
    return ("error_page",)


def _failure_candidate(
    document: RawDocument,
    *,
    attempt_index: int,
    error: ContentError,
) -> Candidate:
    """Build a Candidate whose body was never acquired.

    Emits a QualityReport with ``accepted=False``, the first flag set to
    the failure code, and preserves all other fields as zero. The
    ``failure`` record rides on the Candidate so the finalizer can map
    ``ContentError.status`` without reclassification.
    """
    report = QualityReport(
        accepted=False,
        score=0.0,
        word_count=0,
        heading_count=0,
        code_blocks=0,
        tables=0,
        duplicate_ratio=0.0,
        boilerplate_hits=0,
        malformed_tables=0,
        fence_errors=0,
        flags=(error.code,),
    )
    diagnostic = Diagnostic(
        code=error.code,
        message=error.message,
        severity="error",
        source="acquisition",
    )
    processed = ProcessedMarkdown(
        markdown="",
        quality=report,
        diagnostics=(diagnostic,),
        transforms=("acquisition_failed",),
        links=(),
    )
    return Candidate(
        document=document,
        processed=processed,
        attempt_index=attempt_index,
        failure=error,
    )


# ------------------------------------------------------------------
# Selection. The orchestrator's only ordering step.
# ------------------------------------------------------------------


_REQUESTED_SCOPE_RANK: Final[dict[str, int]] = {"full": 3, "metadata": 2, "excerpt": 1, "sample": 0}


def _rank_candidate(candidate: Candidate) -> tuple[int, int, int, int, float, int]:
    """Return the comparison key for ``max(candidates, key=...)``.

    Ordering — a failure, an origin-access flag (401/403/429/5xx), or a
    blocking flag (paywall, bot, error, garbled, empty) disqualifies a
    candidate outright. Between usable candidates: accepted beats rejected,
    requested scope wins, complete coverage wins, then measured quality,
    then earlier index. Backend identity and length never enter the
    tie-break.
    """
    quality = candidate.processed.quality
    flags = set(quality.flags) | set(_origin_access_flags(candidate.document))
    blocking = {
        "access_blocked",
        "login_wall",
        "paywall",
        "bot_challenge",
        "error",
        "error_page",
        "garbled_content",
        "empty_content",
        "incomplete",
    }
    http_5_blocked = any(flag.startswith("http_5") for flag in flags)
    blocked = candidate.failure is not None
    blocked = blocked or bool(flags.intersection(blocking)) or "http_429" in flags or http_5_blocked
    usable = 1 if quality.accepted and not blocked else 0
    scope_rank = _REQUESTED_SCOPE_RANK.get(candidate.document.scope, 0)
    complete = 1 if candidate.document.complete is not False else 0
    score = round(quality.score, 6)
    return (0 if blocked else 1, usable, scope_rank, complete, score, -candidate.attempt_index)


def select_candidate(candidates: list[Candidate]) -> Candidate | None:
    """Return the best Candidate or ``None`` when the list is empty."""
    if not candidates:
        return None
    return max(candidates, key=_rank_candidate)


def _candidate_blocked(candidate: Candidate) -> bool:
    """Expose the disqualification half of the ranking key."""
    return _rank_candidate(candidate)[0] == 0


def _has_usable_candidate(candidates: list[Candidate]) -> bool:
    """True when selection would keep an accepted, unblocked candidate."""
    best = select_candidate(candidates)
    return best is not None and best.processed.quality.accepted and not _candidate_blocked(best)


# ------------------------------------------------------------------
# Public entry. PipelineSlice owns this surface; tools/CLI call it.
# ------------------------------------------------------------------


async def fetch_content_artifact(
    input_url: str,
    *,
    fetch_options: FetchOptions | None = None,
    registry: tuple[ResolverSpec, ...] | None = None,
    http_client: httpx.AsyncClient | None = None,
    deadline: float | None = None,
    stage_attempts: list | None = None,
    skip_stages: frozenset[str] = frozenset(),
) -> ContentArtifact:
    """Run the orchestration: cache → registry → generic → finalize.

    Every completed path flows through :class:`MarkdownProcessor` and
    contributes one entry to the attempt log. Selection chooses the best
    Candidate and one call into :func:`finalize_artifact` produces the
    artifact. Generic stages after the first usable candidate are skipped.
    """
    options = fetch_options or FetchOptions()
    normalized = canonicalize_url(input_url)
    attempts = _AttemptLog(stage_attempts, normalized_url=normalized)
    candidates: list[Candidate] = []
    skipped = skip_stages

    if deadline is None:
        deadline = time.monotonic() + fetch_deadline_seconds()
    owns_client = http_client is None
    if owns_client:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.web_fetch_timeout_seconds),
            follow_redirects=True,
        )
    ctx = FetchContext(
        http_client=http_client,
        deadline=deadline,
        max_response_bytes=options.max_response_bytes,
        processing_mode=options.processing_mode,
    )

    try:
        cached = await _cache_candidate_lookup(normalized, options=options)
        if cached is not None:
            candidates.append(cached)
            attempts.record_outcome(
                label="cache",
                backend=cached.document.fetch_backend,
                outcome="hit",
            )

        registry_iterable = registry if registry is not None else REGISTRY
        parsed = ParsedURL.parse(input_url)
        for spec in registry_iterable:
            started = time.perf_counter()
            try:
                target = spec.match(parsed)
            except (TypeError, ValueError, AttributeError) as exc:
                LOGGER.debug("Resolver %s.match failed: %s", spec.name, exc)
                continue
            if target is None:
                continue
            try:
                document = await _acquire_via_resolver(spec, target, ctx)
            except AcquisitionError as exc:
                LOGGER.debug("Resolver %s acquisition failed: %s", spec.name, exc)
                failure = _acquisition_error_to_failure(exc, fallback_code=f"{spec.name}_failed")
                attempts.record_outcome(
                    label="resolver_registry",
                    backend=spec.name,
                    outcome="error",
                    error_code=failure.code,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                failure_doc = _empty_failure_document(
                    input_url=input_url,
                    fetched_url=target.url,
                    backend=spec.name,
                )
                candidates.append(
                    _failure_candidate(
                        failure_doc,
                        attempt_index=len(candidates),
                        error=failure,
                    )
                )
                if not target.allow_generic:
                    break
                continue
            except (TimeoutError, httpx.HTTPError) as exc:
                LOGGER.debug("Resolver %s raised: %s", spec.name, exc)
                attempts.record_outcome(
                    label="resolver_registry",
                    backend=spec.name,
                    outcome="error",
                    error_code=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                continue
            candidate = await evaluate_candidate(
                document, attempt_index=len(candidates), options=options
            )
            candidates.append(candidate)
            flags = candidate.processed.quality.flags
            outcome = (
                "accepted"
                if candidate.processed.quality.accepted
                else (flags[0] if flags else "rejected")
            )
            attempts.record_outcome(
                label="resolver_registry",
                backend=spec.name,
                outcome=outcome,
                candidate=candidate,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            if not target.allow_generic:
                break
            if candidate.processed.quality.accepted and candidate.document.complete is not False:
                break

        decision: RouteDecision | None = None
        if not _has_usable_candidate(candidates):
            started = time.perf_counter()
            decision = await _decide_jina_route(input_url, ctx)
            if decision.route == "browser":
                LOGGER.debug("Jina stage skipped: DOM route is browser (%s)", decision.reasons)
                attempts.record_outcome(
                    label="jina_generic",
                    backend="jina_reader",
                    outcome="skipped",
                    selection_reason=f"dom_route:browser:{' '.join(decision.reasons)}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                try:
                    jina_document = await _acquire_via_jina(
                        input_url,
                        ctx=ctx,
                        decision=decision,
                    )
                    jina_candidate = await evaluate_candidate(
                        jina_document, attempt_index=len(candidates), options=options
                    )
                    candidates.append(jina_candidate)
                    attempts.record_outcome(
                        label="jina_generic",
                        backend="jina_reader",
                        outcome=(
                            "accepted" if jina_candidate.processed.quality.accepted else "rejected"
                        ),
                        selection_reason=f"dom_route:{decision.route}",
                        candidate=jina_candidate,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                except (TimeoutError, JinaReaderError, httpx.HTTPError) as exc:
                    LOGGER.debug("Jina generic failed: %s", exc)
                    failure = _acquisition_error_to_failure(exc, fallback_code="jina_unavailable")
                    attempts.record_outcome(
                        label="jina_generic",
                        backend="jina_reader",
                        outcome="error",
                        error_code=failure.code,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )

        if (
            not _has_usable_candidate(candidates)
            and "crawl4ai_remote" not in skipped
            and not _is_binary_target(input_url)
            and get_crawl4ai_client() is not None
        ):
            started = time.perf_counter()
            try:
                crawl_documents = await _acquire_via_crawl4ai(input_url, ctx=ctx, decision=decision)
            except Crawl4AIClientError as exc:
                LOGGER.warning("Crawl4AI remote failed for %s: %s", input_url, exc)
                record_content_error(
                    stage="crawl4ai_remote", url=input_url, error_type="crawl4ai_failed"
                )
                attempts.record_outcome(
                    label="crawl4ai_remote",
                    backend="crawl4ai_remote",
                    outcome="error",
                    error_code=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            except (TimeoutError, httpx.HTTPError) as exc:
                LOGGER.debug("Crawl4AI raised: %s", exc)
                attempts.record_outcome(
                    label="crawl4ai_remote",
                    backend="crawl4ai_remote",
                    outcome="error",
                    error_code=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                batch_start = len(candidates)
                for crawl_document in crawl_documents:
                    crawl_candidate = await evaluate_candidate(
                        crawl_document, attempt_index=len(candidates), options=options
                    )
                    candidates.append(crawl_candidate)
                batch = candidates[batch_start:]
                if batch:
                    accepted_batch = [item for item in batch if item.processed.quality.accepted]
                    logged = accepted_batch[-1] if accepted_batch else batch[-1]
                    attempts.record_outcome(
                        label="crawl4ai_remote",
                        backend="crawl4ai_remote",
                        outcome=("accepted" if logged.processed.quality.accepted else "rejected"),
                        candidate=logged,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )

        challenge = bool(decision is not None and decision.signals.challenge_evidence)
        if (
            not _has_usable_candidate(candidates)
            and not _is_binary_target(input_url)
            and get_camoufox_client() is not None
        ):
            started = time.perf_counter()
            if challenge:
                attempts.record_outcome(
                    label="browser_optional",
                    backend="camoufox_remote",
                    outcome="skipped",
                    selection_reason="challenge_evidence",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                try:
                    browser_document = await _acquire_via_camoufox(input_url, ctx=ctx)
                except CamoufoxClientError as exc:
                    LOGGER.warning("Browser fallback failed for %s: %s", input_url, exc)
                    attempts.record_outcome(
                        label="browser_optional",
                        backend="camoufox_remote",
                        outcome="error",
                        error_code=type(exc).__name__,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                except (TimeoutError, httpx.HTTPError) as exc:
                    LOGGER.debug("Browser fallback raised: %s", exc)
                    attempts.record_outcome(
                        label="browser_optional",
                        backend="camoufox_remote",
                        outcome="error",
                        error_code=type(exc).__name__,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                else:
                    browser_candidate = await evaluate_candidate(
                        browser_document, attempt_index=len(candidates), options=options
                    )
                    candidates.append(browser_candidate)
                    attempts.record_outcome(
                        label="browser_optional",
                        backend="camoufox_remote",
                        outcome=(
                            "accepted"
                            if browser_candidate.processed.quality.accepted
                            else "rejected"
                        ),
                        candidate=browser_candidate,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )

        if (
            not _has_usable_candidate(candidates)
            and not _is_binary_target(input_url)
            and get_unlocker_client() is not None
        ):
            started = time.perf_counter()
            try:
                unlocker_document = await _acquire_via_unlocker(input_url, ctx=ctx)
            except UnlockerClientError as exc:
                LOGGER.warning("Web Unlocker failed for %s: %s", input_url, exc)
                attempts.record_outcome(
                    label="unlocker_remote",
                    backend="brightdata_unlocker",
                    outcome="error",
                    error_code=exc.error_code or type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            except (TimeoutError, httpx.HTTPError) as exc:
                LOGGER.debug("Web Unlocker raised: %s", exc)
                attempts.record_outcome(
                    label="unlocker_remote",
                    backend="brightdata_unlocker",
                    outcome="error",
                    error_code=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                unlocker_candidate = await evaluate_candidate(
                    unlocker_document, attempt_index=len(candidates), options=options
                )
                candidates.append(unlocker_candidate)
                attempts.record_outcome(
                    label="unlocker_remote",
                    backend="brightdata_unlocker",
                    outcome=(
                        "accepted" if unlocker_candidate.processed.quality.accepted else "rejected"
                    ),
                    candidate=unlocker_candidate,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )

        if not _has_usable_candidate(candidates):
            started = time.perf_counter()
            try:
                archive_document = await _acquire_via_wayback(input_url, ctx=ctx)
            except (TimeoutError, httpx.HTTPError, AcquisitionError) as exc:
                LOGGER.debug("Archive fallback raised: %s", exc)
                attempts.record_outcome(
                    label="archive_optional",
                    backend="wayback_archive",
                    outcome="error",
                    error_code=type(exc).__name__,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                if archive_document is not None:
                    archive_candidate = await evaluate_candidate(
                        archive_document, attempt_index=len(candidates), options=options
                    )
                    candidates.append(archive_candidate)
                    attempts.record_outcome(
                        label="archive_optional",
                        backend="wayback_archive",
                        outcome=(
                            "accepted"
                            if archive_candidate.processed.quality.accepted
                            else "rejected"
                        ),
                        candidate=archive_candidate,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )

        selected = select_candidate(candidates)
        if selected is not None:
            attempts.record_outcome(
                label="selection",
                backend=selected.document.fetch_backend,
                outcome="selected",
                selection_reason=(
                    f"selected:usable={selected.processed.quality.accepted} "
                    f"scope={selected.document.scope} "
                    f"complete={selected.document.complete} "
                    f"score={round(selected.processed.quality.score, 4)} "
                    f"chars_kept={len(selected.processed.markdown)}"
                ),
                candidate=selected,
            )

        if selected is None:
            failure = ContentError(
                "acquisition_failed",
                "No acquisition path returned content.",
                status="error",
            )
            return await finalize_artifact(
                input_url,
                None,
                options=options,
                failure=failure,
                failure_status="error",
                extra_diagnostics=(
                    Diagnostic(
                        "no_candidate",
                        "No acquired document passed selection.",
                        severity="error",
                        source="pipeline",
                    ),
                ),
            )
        return await finalize_artifact(
            input_url,
            selected,
            options=options,
            extra_diagnostics=(),
        )
    finally:
        if owns_client and http_client is not None:
            await http_client.aclose()


# ------------------------------------------------------------------
# Small URL-shape helpers.
# ------------------------------------------------------------------


def _is_binary_target(url: str) -> bool:
    """Return True when the path clearly targets a non-HTML document."""
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS if ext != ".csv")
