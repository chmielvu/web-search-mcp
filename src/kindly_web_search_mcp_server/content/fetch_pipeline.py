"""Content fetch pipeline — multi-tier architecture with active resilience.

Tier 1: Specialized resolvers (Documents, Raw Text, DOIs, PyPI, npm, HF, Crates.io, Discourse, StackExchange, GitHub, Reddit, Wikipedia, arXiv, YouTube, Telegram)
Tier 2: Generic extraction cascade (Jina Reader -> Local curl_cffi+Trafilatura -> Crawl4AI Remote -> Camoufox Stealth Browser)
Tier 3: Web Archive Fallback (Internet Archive Wayback Machine Availability API)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, replace
from hashlib import sha256
from urllib.parse import urlparse

from opentelemetry import trace

from ..settings import settings
from .artifact import ContentArtifact, ContentError
from .remote_clients import (
    CamoufoxClientError,
    Crawl4AIClientError,
    get_camoufox_client,
    get_crawl4ai_client,
)
from .resolvers.document import DOC_EXTENSIONS
from .resolvers.wayback import fetch_wayback_snapshot_markdown
from .specialized_pipeline import _resolve_tier1
from ..utils.url_canonicalize import canonicalize_url
from ..telemetry import record_content_error
from .stages import _fetch_via_camoufox, _fetch_via_crawl4ai, _fetch_via_jina, _fetch_via_local, _record_probe


@dataclass(frozen=True, slots=True)
class FetchOptions:
    max_response_bytes: int = 5 * 1024 * 1024

    def cache_fingerprint(self) -> str:
        return sha256(f"{self.max_response_bytes}".encode("utf-8")).hexdigest()[:16]


LOGGER = logging.getLogger(__name__)

# Rewrite github.com/<owner>/<repo>/blob/<ref>/<path> to raw.githubusercontent.com
# before Tier 1/Tier 2 dispatch so Jina/Crawl4AI fetch raw file content, not
# the GitHub HTML chrome page.
_GITHUB_BLOB_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")


def _rewrite_github_blob_to_raw(url: str) -> str:
    m = _GITHUB_BLOB_RE.match(url)
    if m:
        owner, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    return url


def _is_binary_target(url: str) -> bool:
    """Return True if URL clearly targets a binary document to prevent browser crashes."""
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS if ext != ".csv")


_content_tracer = trace.get_tracer("kindly_web_search_mcp_server.content.fetch_pipeline")

_STAGE_TIMEOUTS: dict[str, float] = {
    "jina": 25.0,
    "local": 20.0,
    "crawl4ai": 30.0,
    "camoufox": 35.0,
    "wayback": 15.0,
}

_DEFAULT_TOTAL_TIMEOUT_SECONDS = 120.0


def _resolve_stage_timeout(
    stage: str,
    *,
    start_time: float,
    total_timeout: float = _DEFAULT_TOTAL_TIMEOUT_SECONDS,
) -> float:
    """Return the effective timeout for a pipeline stage."""
    remaining = total_timeout - (time.monotonic() - start_time)
    return max(1.0, min(_STAGE_TIMEOUTS.get(stage, 20.0), remaining))


def _note_stage(
    attempts: list | None,
    *,
    stage: str,
    stage_order: int,
    outcome: str,
    normalized_url: str,
    error_code: str | None = None,
    latency_ms: float | None = None,
    attempt_count: int = 1,
    chars_kept: int | None = None,
    quality_score: float | None = None,
    skipped_reason: str | None = None,
) -> None:
    """Append one stage-attempt record (ids are stamped by the analytics layer)."""
    if attempts is None:
        return
    attempts.append(
        {
            "stage": stage,
            "stage_order": stage_order,
            "outcome": outcome,
            "normalized_url": normalized_url,
            "error_code": error_code,
            "latency_ms": latency_ms,
            "attempt_count": attempt_count,
            "chars_kept": chars_kept,
            "quality_score": quality_score,
            "skipped_reason": skipped_reason,
        }
    )


async def fetch_content_artifact(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
    stage_attempts: list | None = None,
) -> ContentArtifact:
    """Fetch content for a URL using the resilient multi-tier pipeline.

    When ``stage_attempts`` is a list, one attempt dict per pipeline stage
    (including skipped stages) is appended for analytics persistence.
    """
    # GitHub blob URLs -> raw.githubusercontent.com
    url = _rewrite_github_blob_to_raw(url)

    with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
        span.set_attribute("content.url", url)

        max_response_bytes = (fetch_options or FetchOptions()).max_response_bytes

        # ----------------------------------------------------------
        # Tier 1: Specialized resolvers
        # ----------------------------------------------------------
        _t0 = time.monotonic()
        tier1 = await _resolve_tier1(url, max_response_bytes=max_response_bytes)
        _t1 = time.monotonic()
        _note_stage(
            stage_attempts,
            stage=tier1.source_type if tier1 is not None else "tier1",
            stage_order=0,
            outcome=tier1.status if tier1 is not None else "skipped",
            normalized_url=tier1.normalized_url if tier1 is not None else canonicalize_url(url),
            error_code=tier1.error.code if tier1 is not None and tier1.error else None,
            latency_ms=(_t1 - _t0) * 1000.0,
            chars_kept=len(tier1.markdown) if tier1 is not None else None,
            quality_score=tier1.quality_score if tier1 is not None else None,
            skipped_reason="parser_no_match" if tier1 is None else None,
        )
        if tier1 is not None and tier1.status in ("success", "partial"):
            return tier1

        # ----------------------------------------------------------
        # Tier 2: Generic extraction cascade
        # ----------------------------------------------------------
        start_time = time.monotonic()
        is_binary = _is_binary_target(url)

        # Stage 1: Jina Reader
        _tries: list[int] = []
        _t0 = time.monotonic()
        jina_artifact = await _fetch_via_jina(
            url,
            max_response_bytes=max_response_bytes,
            include_links=False,
            timeout_seconds=_resolve_stage_timeout("jina", start_time=start_time),
            attempts_made=_tries,
        )
        _note_stage(
            stage_attempts,
            stage="jina_reader",
            stage_order=1,
            outcome=jina_artifact.status if jina_artifact is not None else "error",
            normalized_url=jina_artifact.normalized_url if jina_artifact is not None else canonicalize_url(url),
            error_code=jina_artifact.error.code if jina_artifact is not None and jina_artifact.error else ("jina_unavailable" if jina_artifact is None else None),
            latency_ms=(time.monotonic() - _t0) * 1000.0,
            attempt_count=_tries[0] if _tries else 1,
            chars_kept=len(jina_artifact.markdown) if jina_artifact is not None else None,
            quality_score=jina_artifact.quality_score if jina_artifact is not None else None,
        )
        if jina_artifact is not None and jina_artifact.status == "success":
            return jina_artifact

        # Stage 2: Local extraction (curl_cffi JA3/JA4 TLS impersonation + Trafilatura / BS4 / Doc converters)
        # Always executed when Jina is not a full success (fixes local stage isolation bug)
        _tries = []
        _t0 = time.monotonic()
        local_artifact = await _fetch_via_local(
            url,
            max_response_bytes=max_response_bytes,
            include_links=False,
            timeout_seconds=_resolve_stage_timeout("local", start_time=start_time),
            attempts_made=_tries,
        )
        _t1 = time.monotonic()
        _note_stage(
            stage_attempts,
            stage="local",
            stage_order=2,
            outcome=local_artifact.status,
            normalized_url=local_artifact.normalized_url,
            error_code=local_artifact.error.code if local_artifact.error else None,
            latency_ms=(_t1 - _t0) * 1000.0,
            attempt_count=_tries[0] if _tries else 1,
            chars_kept=len(local_artifact.markdown),
            quality_score=local_artifact.quality_score,
        )
        if local_artifact.status == "success":
            return local_artifact

        # Stage 3: Crawl4AI cloud (POST /md) - skipped for binary files to avoid headless crashes
        c4a_artifact: ContentArtifact | None = None
        if not is_binary and get_crawl4ai_client() is not None:
            _tries = []
            _t0 = time.monotonic()
            try:
                c4a_artifact = await _fetch_via_crawl4ai(
                    url,
                    max_response_bytes=max_response_bytes,
                    include_links=False,
                    timeout_seconds=_resolve_stage_timeout("crawl4ai", start_time=start_time),
                    attempts_made=_tries,
                )
            except Crawl4AIClientError as exc:
                _lat = (time.monotonic() - _t0) * 1000.0
                LOGGER.warning("Crawl4AI remote failed for %s: %s", url, exc)
                record_content_error(stage="crawl4ai_remote", url=url, error_type="crawl4ai_failed")
                _note_stage(
                    stage_attempts,
                    stage="crawl4ai_remote",
                    stage_order=3,
                    outcome="error",
                    normalized_url=canonicalize_url(url),
                    error_code=type(exc).__name__,
                    latency_ms=_lat,
                    attempt_count=_tries[0] if _tries else 1,
                )
                _record_probe("crawl4ai_remote", "error", _lat)
            else:
                _lat = (time.monotonic() - _t0) * 1000.0
                _note_stage(
                    stage_attempts,
                    stage="crawl4ai_remote",
                    stage_order=3,
                    outcome=c4a_artifact.status,
                    normalized_url=c4a_artifact.normalized_url,
                    error_code=c4a_artifact.error.code if c4a_artifact.error else None,
                    latency_ms=_lat,
                    attempt_count=_tries[0] if _tries else 1,
                    chars_kept=len(c4a_artifact.markdown),
                    quality_score=c4a_artifact.quality_score,
                )
                _record_probe("crawl4ai_remote", c4a_artifact.status, _lat)
                if c4a_artifact.status == "success":
                    return c4a_artifact
        else:
            _note_stage(
                stage_attempts,
                stage="crawl4ai_remote",
                stage_order=3,
                outcome="skipped",
                normalized_url=canonicalize_url(url),
                skipped_reason="binary_target" if is_binary else "client_unconfigured",
            )
            _record_probe("crawl4ai_remote", "skipped", None)

        # Stage 4: Camoufox (stealth browser sidecar) - skipped for binary files
        camoufox_artifact: ContentArtifact | None = None
        if not is_binary and get_camoufox_client() is not None:
            _t0 = time.monotonic()
            try:
                camoufox_artifact = await _fetch_via_camoufox(
                    url,
                    max_response_bytes=max_response_bytes,
                    include_links=False,
                )
            except CamoufoxClientError as exc:
                _lat = (time.monotonic() - _t0) * 1000.0
                LOGGER.warning("Camoufox failed for %s: %s", url, exc)
                record_content_error(stage="camoufox_remote", url=url, error_type="camoufox_failed")
                _note_stage(
                    stage_attempts,
                    stage="camoufox_remote",
                    stage_order=4,
                    outcome="error",
                    normalized_url=canonicalize_url(url),
                    error_code=type(exc).__name__,
                    latency_ms=_lat,
                )
                _record_probe("camoufox_remote", "error", _lat)
            else:
                _lat = (time.monotonic() - _t0) * 1000.0
                _note_stage(
                    stage_attempts,
                    stage="camoufox_remote",
                    stage_order=4,
                    outcome=camoufox_artifact.status,
                    normalized_url=camoufox_artifact.normalized_url,
                    error_code=camoufox_artifact.error.code if camoufox_artifact.error else None,
                    latency_ms=_lat,
                    chars_kept=len(camoufox_artifact.markdown),
                    quality_score=camoufox_artifact.quality_score,
                )
                _record_probe("camoufox_remote", camoufox_artifact.status, _lat)
                if camoufox_artifact.status == "success":
                    return camoufox_artifact
        else:
            _note_stage(
                stage_attempts,
                stage="camoufox_remote",
                stage_order=4,
                outcome="skipped",
                normalized_url=canonicalize_url(url),
                skipped_reason="binary_target" if is_binary else "client_unconfigured",
            )
            _record_probe("camoufox_remote", "skipped", None)

        # ----------------------------------------------------------
        # Tier 3: Web Archive Resilience Fallback (Wayback Machine)
        # ----------------------------------------------------------
        wayback_artifact: ContentArtifact | None = None
        _t0 = time.monotonic()
        wayback_artifact = await fetch_wayback_snapshot_markdown(
            url,
            max_response_bytes=max_response_bytes,
            timeout_seconds=_resolve_stage_timeout("wayback", start_time=start_time),
        )
        _note_stage(
            stage_attempts,
            stage="wayback_archive",
            stage_order=5,
            outcome=wayback_artifact.status if wayback_artifact is not None else "error",
            normalized_url=wayback_artifact.normalized_url if wayback_artifact is not None else canonicalize_url(url),
            error_code=wayback_artifact.error.code if wayback_artifact is not None and wayback_artifact.error else ("wayback_unavailable" if wayback_artifact is None else None),
            latency_ms=(time.monotonic() - _t0) * 1000.0,
            chars_kept=len(wayback_artifact.markdown) if wayback_artifact is not None else None,
            quality_score=wayback_artifact.quality_score if wayback_artifact is not None else None,
        )
        if wayback_artifact is not None and wayback_artifact.status in ("success", "partial"):
            return wayback_artifact

        # Evaluate best candidate from all attempted stages
        candidates = [
            item
            for item in (
                tier1,
                jina_artifact,
                local_artifact,
                c4a_artifact,
                camoufox_artifact,
                wayback_artifact,
            )
            if item is not None
        ]
        # Prefer artifact with status == 'partial' or highest quality_score or longest markdown
        artifact = max(
            candidates,
            key=lambda a: (
                1 if a.status in ("success", "partial") else 0,
                a.quality_score,
                len(a.markdown),
            ),
            default=None,
        )

        artifact = artifact or ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=None,
            status="blocked",
            source_type="web",
            fetch_backend="all_failed",
            content_type=None,
            markdown="",
            error=ContentError(code="all_stages_failed", message="All extraction stages failed"),
        )

        # Entity extraction hook
        if settings.entity_extraction_enabled and artifact.markdown:
            try:
                from ..ml.gliner_client import get_gliner_client
                from ..utils.observability import emit_observability_event

                ents = await get_gliner_client().extract_entities(artifact.markdown)
                if ents:
                    artifact = replace(artifact, entities=ents)
                emit_observability_event(
                    LOGGER,
                    "entity.content_extracted",
                    url=url,
                    count=len(ents or []),
                    backend=artifact.fetch_backend,
                )
            except Exception as exc:
                emit_observability_event(
                    LOGGER,
                    "entity.extraction.error",
                    url=url,
                    error=str(exc)[:300],
                    failure_mode="content_extract_failed",
                    component="fetch_pipeline",
                )
        return artifact
