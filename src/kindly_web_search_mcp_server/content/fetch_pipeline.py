"""Single-source content acquisition + selection → finalize_artifact.

Pipeline flow:

  normalized identity
      │    ↓
  cache candidate lookup  (versioned key includes policy + mode)
      │    ↓ miss
  resolver registry       (REGISTRY in content/resolver_registry.py)
      │    ↓ first acquisition target
  DOM-routed Jina candidate (preflight classify → per-route engine)
      │    ↓ rejected
  Crawl4AI candidate      (runs after a rejected/failed generic attempt)
      │    ↓ no acceptance yet
  browser (Camoufox) / Wayback archive fallbacks — always on when the
  corresponding client is configured
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

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from ..settings import settings
from ..telemetry import record_content_error
from ..utils.url_canonicalize import canonicalize_url
from .constructor import ContentArtifact, ContentError, finalize_artifact
from .markdown_processor import MarkdownProcessor
from .models import (
    AcquisitionError,
    Candidate,
    Diagnostic,
    FetchContext,
    FetchOptions,
    ParsedURL,
    ProcessingMode,
    ProcessedMarkdown,
    QualityReport,
    RawDocument,
    ResolverSpec,
    ResolverTarget,
    TextDocument,
)
from .remote_clients import (
    CamoufoxClientError,
    Crawl4AIClientError,
    get_camoufox_client,
    get_crawl4ai_client,
)
from .resolvers.wayback import fetch_wayback_raw
from .renderers import render_raw_document
from .resolvers.files import DOC_EXTENSIONS
from .resolver_registry import REGISTRY
from .dom_detector import RouteDecision, analyze_html
from .http_utils import safe_fetch_url
from .jina_reader import (
    JinaReaderError,
    fetch_raw_document as fetch_jina_raw_document,
)

LOGGER = logging.getLogger(__name__)

# Re-export the canonical contracts declared in :mod:`content.models` so
# legacy `from ..content.fetch_pipeline import FetchOptions` paths keep
# resolving. The single source of truth is ``content.models``; do not
# redefine here.
__all__ = (
    "ContentArtifact",
    "ContentError",
    "FetchContext",
    "FetchOptions",
    "MarkdownProcessor",
    "ProcessingMode",
    "RawDocument",
    "StageAttempt",
    "fetch_content_artifact",
    "finalize_artifact",
    "select_candidate",
)


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
    "archive_optional": 5,
    "llms_txt": 6,
    "selection": 7,
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
                latency_ms=None,
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
    except Exception as exc:
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


async def _acquire_via_crawl4ai(url: str, *, ctx: FetchContext) -> RawDocument:
    """Crawl4AI remote non-browser markdown → RawDocument."""
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIClientError("Crawl4AI client not configured", retryable=False)
    markdown = await client.fetch_markdown(url, mode="fit")
    if len(markdown.encode("utf-8")) > ctx.max_response_bytes:
        raise Crawl4AIClientError(
            f"Crawl4AI response exceeds {ctx.max_response_bytes} byte cap",
            retryable=False,
        )
    return RawDocument(
        input_url=url,
        fetched_url=url,
        source_type="html",
        fetch_backend="crawl4ai_remote",
        body=TextDocument(text=markdown),
        content_type="text/markdown",
        title=None,
        metadata={"extraction_method": "crawl4ai_md"},
        links=(),
        diagnostics=(),
        http_status=200,
        response_headers={},
        complete=True,
        scope="full",
        bytes_downloaded=len(markdown.encode("utf-8")),
        redirect_count=0,
    )


async def _acquire_via_camoufox(url: str, *, ctx: FetchContext) -> RawDocument:
    """Camoufox stealth-Firefox HTML → RawDocument.

    Camoufox returns raw HTML, not markdown. The MarkdownProcessor
    evaluator runs the sanitize / shape pass; the body keeps ``format="html"``
    so callers know the body still needs sanitizing.
    """
    client = get_camoufox_client()
    if client is None:
        raise CamoufoxClientError("Camoufox client not configured", retryable=False)
    html = await client.fetch_html(url, max_bytes=ctx.max_response_bytes)
    return RawDocument(
        input_url=url,
        fetched_url=url,
        source_type="html",
        fetch_backend="camoufox_remote",
        body=TextDocument(text=html, format="html"),
        content_type="text/html",
        title=None,
        metadata={"extraction_method": "camoufox_remote"},
        links=(),
        diagnostics=(),
        http_status=200,
        response_headers={},
        complete=True,
        scope="full",
        bytes_downloaded=len(html.encode("utf-8")),
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
    except Exception:
        return None
    try:
        key = _cache_key(canonical_url, options)
        entry = await cache.alookup(key)
    except Exception as exc:  # pragma: no cover - cache isolation
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
    )


async def _evaluate_candidate(
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
        "skip_mdformat",
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
) -> ContentArtifact:
    """Run the orchestration: cache → registry → generic → finalize.

    Every completed path (cache hit, resolver, Jina, Crawl4AI,
    browser/archive fallbacks) flows through :class:`MarkdownProcessor` and
    contributes one entry to the attempt log. Selection chooses the best
    Candidate and one call into :func:`finalize_artifact` produces the
    artifact.
    """
    options = fetch_options or FetchOptions()
    normalized = canonicalize_url(input_url)
    attempts = _AttemptLog(stage_attempts, normalized_url=normalized)
    candidates: list[Candidate] = []

    if deadline is None:
        deadline = time.monotonic() + max(60.0, settings.web_fetch_timeout_seconds)
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
        # 1. cache (versioned key includes policy + mode)
        cached = await _cache_candidate_lookup(normalized, options=options)
        if cached is not None:
            candidates.append(cached)
            attempts.record_outcome(
                label="cache",
                backend=cached.document.fetch_backend,
                outcome="hit",
            )

        # 2. resolver registry ordered by priority
        registry_iterable = registry if registry is not None else REGISTRY
        parsed = ParsedURL.parse(input_url)
        for spec in registry_iterable:
            try:
                target = spec.match(parsed)
            except Exception as exc:
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
            except Exception as exc:
                LOGGER.debug("Resolver %s raised: %s", spec.name, exc)
                attempts.record_outcome(
                    label="resolver_registry",
                    backend=spec.name,
                    outcome="error",
                    error_code=type(exc).__name__,
                )
                continue
            candidate = await _evaluate_candidate(
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
            )
            if not target.allow_generic:
                break
            if candidate.processed.quality.accepted and candidate.document.complete is not False:
                break

        # 3. DOM-routed Jina Reader — skipped when the registry already
        # produced an accepted candidate for this request.
        registry_accepted = any(
            candidate.processed.quality.accepted and candidate.document.fetch_backend != "cache"
            for candidate in candidates
        )
        jina_skipped = False
        if registry_accepted:
            jina_skipped = True
            attempts.record_outcome(
                label="jina_generic",
                backend="jina_reader",
                outcome="skipped",
                selection_reason="registry candidate accepted",
            )
        else:
            decision = await _decide_jina_route(input_url, ctx)
            if decision.route == "browser":
                # JS shells and challenges defer to the Camoufox stage.
                LOGGER.debug("Jina stage skipped: DOM route is browser (%s)", decision.reasons)
                attempts.record_outcome(
                    label="jina_generic",
                    backend="jina_reader",
                    outcome="skipped",
                    selection_reason=f"dom_route:browser:{' '.join(decision.reasons)}",
                )
            else:
                try:
                    jina_document = await _acquire_via_jina(
                        input_url,
                        ctx=ctx,
                        decision=decision,
                    )
                    jina_candidate = await _evaluate_candidate(
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
                    )
                except (JinaReaderError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                    LOGGER.debug("Jina generic failed: %s", exc)
                    failure = _acquisition_error_to_failure(exc, fallback_code="jina_unavailable")
                    attempts.record_outcome(
                        label="jina_generic",
                        backend="jina_reader",
                        outcome="error",
                        error_code=failure.code,
                    )

        # 4. Crawl4AI — only after a rejected or failed generic attempt; never
        # for binary targets, never after an accepted candidate.
        best_before = select_candidate(candidates)
        accepted_before = (
            best_before is not None
            and best_before.processed.quality.accepted
            and not _candidate_blocked(best_before)
        )
        if (
            not jina_skipped
            and not accepted_before
            and not _is_binary_target(input_url)
            and get_crawl4ai_client() is not None
        ):
            try:
                crawl_document = await _acquire_via_crawl4ai(input_url, ctx=ctx)
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
                )
            except Exception as exc:
                LOGGER.debug("Crawl4AI raised: %s", exc)
                attempts.record_outcome(
                    label="crawl4ai_remote",
                    backend="crawl4ai_remote",
                    outcome="error",
                    error_code=type(exc).__name__,
                )
            else:
                crawl_candidate = await _evaluate_candidate(
                    crawl_document, attempt_index=len(candidates), options=options
                )
                candidates.append(crawl_candidate)
                attempts.record_outcome(
                    label="crawl4ai_remote",
                    backend="crawl4ai_remote",
                    outcome=(
                        "accepted" if crawl_candidate.processed.quality.accepted else "rejected"
                    ),
                    candidate=crawl_candidate,
                )

        # 5. browser (Camoufox) fallback — always on when the client is configured
        try:
            _browser_timeout = ctx.timeout(maximum=35.0)
        except TimeoutError:
            _browser_timeout = 10.0
        if (
            not accepted_before
            and not _is_binary_target(input_url)
            and get_camoufox_client() is not None
        ):
            try:
                browser_document = await _acquire_via_camoufox(input_url, ctx=ctx)
            except CamoufoxClientError as exc:
                LOGGER.warning("Browser fallback failed for %s: %s", input_url, exc)
                attempts.record_outcome(
                    label="browser_optional",
                    backend="camoufox_remote",
                    outcome="error",
                    error_code=type(exc).__name__,
                )
            except Exception as exc:
                LOGGER.debug("Browser fallback raised: %s", exc)
                attempts.record_outcome(
                    label="browser_optional",
                    backend="camoufox_remote",
                    outcome="error",
                    error_code=type(exc).__name__,
                )
            else:
                browser_candidate = await _evaluate_candidate(
                    browser_document, attempt_index=len(candidates), options=options
                )
                candidates.append(browser_candidate)
                attempts.record_outcome(
                    label="browser_optional",
                    backend="camoufox_remote",
                    outcome=(
                        "accepted" if browser_candidate.processed.quality.accepted else "rejected"
                    ),
                    candidate=browser_candidate,
                )

        # 6. Wayback archive fallback — always on.
        if not accepted_before:
            try:
                archive_document = await _acquire_via_wayback(input_url, ctx=ctx)
            except Exception as exc:
                LOGGER.debug("Archive fallback raised: %s", exc)
                attempts.record_outcome(
                    label="archive_optional",
                    backend="wayback_archive",
                    outcome="error",
                    error_code=type(exc).__name__,
                )
            else:
                if archive_document is not None:
                    archive_candidate = await _evaluate_candidate(
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
