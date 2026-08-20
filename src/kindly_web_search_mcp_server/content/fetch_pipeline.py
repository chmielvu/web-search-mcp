"""Content fetch pipeline — multi-tier architecture with active resilience.

Tier 1: Specialized resolvers (Documents, Raw Text, DOIs, PyPI, npm, HF, Crates.io, Discourse, StackExchange, GitHub, Reddit, Wikipedia, arXiv, YouTube, Telegram)
Tier 2: Generic extraction cascade (Jina Reader -> Local curl_cffi+Trafilatura -> Crawl4AI Remote -> Camoufox Stealth Browser)
Tier 3: Web Archive Fallback (Internet Archive Wayback Machine Availability API)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from urllib.parse import urlparse

from opentelemetry import trace

from ..settings import settings
from .artifact import ContentArtifact, ContentError
from .options import FetchOptions
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
from .stages import _fetch_via_camoufox, _fetch_via_crawl4ai, _fetch_via_jina, _fetch_via_local

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


async def fetch_content_artifact(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch content for a URL using the resilient multi-tier pipeline."""
    # GitHub blob URLs -> raw.githubusercontent.com
    url = _rewrite_github_blob_to_raw(url)

    with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
        span.set_attribute("content.url", url)

        options = fetch_options or FetchOptions()
        options.validate()

        # ----------------------------------------------------------
        # Tier 1: Specialized resolvers
        # ----------------------------------------------------------
        tier1 = await _resolve_tier1(url, options)
        if tier1 is not None and tier1.status in ("success", "partial"):
            return tier1

        # ----------------------------------------------------------
        # Tier 2: Generic extraction cascade
        # ----------------------------------------------------------
        start_time = time.monotonic()
        is_binary = _is_binary_target(url)

        # Stage 1: Jina Reader
        jina_options = replace(
            options,
            stage_timeout_seconds=_resolve_stage_timeout("jina", start_time=start_time),
        )
        jina_artifact = await _fetch_via_jina(url, options=jina_options)
        if jina_artifact is not None and jina_artifact.status == "success":
            return jina_artifact

        # Stage 2: Local extraction (curl_cffi JA3/JA4 TLS impersonation + Trafilatura / BS4 / Doc converters)
        # Always executed when Jina is not a full success (fixes local stage isolation bug)
        local_options = replace(
            options,
            stage_timeout_seconds=_resolve_stage_timeout("local", start_time=start_time),
        )
        local_artifact = await _fetch_via_local(url, options=local_options)
        if local_artifact.status == "success":
            return local_artifact

        # Stage 3: Crawl4AI cloud (POST /md) - skipped for binary files to avoid headless crashes
        c4a_artifact: ContentArtifact | None = None
        if not is_binary and get_crawl4ai_client() is not None:
            try:
                c4a_options = replace(
                    options,
                    stage_timeout_seconds=_resolve_stage_timeout("crawl4ai", start_time=start_time),
                )
                c4a_artifact = await _fetch_via_crawl4ai(url, c4a_options)
                if c4a_artifact.status == "success":
                    return c4a_artifact
            except Crawl4AIClientError as exc:
                LOGGER.warning("Crawl4AI remote failed for %s: %s", url, exc)
                record_content_error(stage="crawl4ai_remote", url=url, error_type="crawl4ai_failed")

        # Stage 4: Camoufox (stealth browser sidecar) - skipped for binary files
        camoufox_artifact: ContentArtifact | None = None
        if not is_binary and get_camoufox_client() is not None:
            try:
                camoufox_options = replace(
                    options,
                    stage_timeout_seconds=_resolve_stage_timeout("camoufox", start_time=start_time),
                )
                camoufox_artifact = await _fetch_via_camoufox(url, camoufox_options)
                if camoufox_artifact.status == "success":
                    return camoufox_artifact
            except CamoufoxClientError as exc:
                LOGGER.warning("Camoufox failed for %s: %s", url, exc)
                record_content_error(stage="camoufox_remote", url=url, error_type="camoufox_failed")

        # ----------------------------------------------------------
        # Tier 3: Web Archive Resilience Fallback (Wayback Machine)
        # ----------------------------------------------------------
        wayback_artifact: ContentArtifact | None = None
        wb_options = replace(
            options,
            stage_timeout_seconds=_resolve_stage_timeout("wayback", start_time=start_time),
        )
        wayback_artifact = await fetch_wayback_snapshot_markdown(url, fetch_options=wb_options)
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
                from ..search.entity_extractor import extract_entities
                from ..utils.observability import emit_observability_event

                ents = await extract_entities(artifact.markdown)
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
