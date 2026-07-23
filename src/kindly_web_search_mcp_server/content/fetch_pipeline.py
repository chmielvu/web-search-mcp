"""Content fetch pipeline — two-tier architecture.

Tier 1: Specialized resolvers (StackExchange, GitHub, Wikipedia, arXiv, Telegram) in content/resolvers/
Tier 2: Generic extraction stages (Jina -> Crawl4AI /md -> local BS4 -> Camoufox last-resort)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace

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


_content_tracer = trace.get_tracer("kindly_web_search_mcp_server.content.fetch_pipeline")

# Stage timeout budgets. Kept tight so later stages (Crawl4AI, local, Camoufox)
# get a fair shot before the outer tool-level timeout fires.
_STAGE_TIMEOUTS: dict[str, float] = {
    "jina": 25.0,
    "crawl4ai": 30.0,
    "local": 20.0,
    "camoufox": 35.0,
}

# Default total pipeline budget. Mirrors the tool-level default in tools._helpers.
_DEFAULT_TOTAL_TIMEOUT_SECONDS = 120.0


def _resolve_stage_timeout(
    stage: str,
    *,
    start_time: float,
    total_timeout: float = _DEFAULT_TOTAL_TIMEOUT_SECONDS,
) -> float:
    """Return the effective timeout for a pipeline stage.

    Uses the stage's default budget, capped by the remaining total pipeline
    budget. Always returns at least 1.0 second so a stage can fail fast.
    """
    remaining = total_timeout - (time.monotonic() - start_time)
    return max(1.0, min(_STAGE_TIMEOUTS[stage], remaining))


async def fetch_content_artifact(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch content for a URL using the two-tier pipeline.

    Tier 1 — Specialized resolvers (domain-specific, high quality):
      StackExchange, GitHub Issues, GitHub Discussions, Wikipedia, arXiv, Telegram

    Tier 2 — Generic extraction (any URL):
      Jina Reader -> Crawl4AI remote POST /md -> local BS4 (conditional) -> Camoufox last-resort
    """
    # GitHub blob URLs -> raw.githubusercontent.com so Jina/Crawl4AI fetch
    # raw content, not the GitHub HTML chrome page.
    url = _rewrite_github_blob_to_raw(url)

    with _content_tracer.start_as_current_span("content.fetch_pipeline") as span:
        span.set_attribute("content.url", url)

        options = fetch_options or FetchOptions()
        options.validate()

        # ----------------------------------------------------------
        # Tier 1: Specialized resolvers
        # ----------------------------------------------------------

        tier1 = await _resolve_tier1(url, options)
        if tier1 is not None:
            return tier1

        # ----------------------------------------------------------
        # Tier 2: Generic extraction
        # ----------------------------------------------------------
        start_time = time.monotonic()

        # Stage 1: Jina Reader
        jina_options = replace(
            options,
            stage_timeout_seconds=_resolve_stage_timeout("jina", start_time=start_time),
        )
        jina_artifact = await _fetch_via_jina(url, options=jina_options)
        if jina_artifact is not None and jina_artifact.status == "success":
            return jina_artifact
        jina_unavailable = jina_artifact is None

        # Stage 2: Crawl4AI cloud (POST /md, non-browser)
        c4a_artifact: ContentArtifact | None = None
        c4a_unavailable = False
        if get_crawl4ai_client() is not None:
            try:
                # Crawl4AIClient.fetch_markdown does not accept a per-call timeout;
                # pass the budget via options for future compatibility.
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
                c4a_unavailable = exc.retryable
        else:
            c4a_unavailable = True

        # Stage 3: local fallback — ONLY if BOTH upstreams unavailable
        local_artifact: ContentArtifact | None = None
        if jina_unavailable and c4a_unavailable:
            local_options = replace(
                options,
                stage_timeout_seconds=_resolve_stage_timeout("local", start_time=start_time),
            )
            local_artifact = await _fetch_via_local(url, options=local_options)
            if local_artifact.status == "success":
                return local_artifact

        # Stage 4: Camoufox (last-resort browser for hard sites)
        camoufox_artifact: ContentArtifact | None = None
        if get_camoufox_client() is not None:
            try:
                camoufox_artifact = await _fetch_via_camoufox(url, options)
                if camoufox_artifact.status == "success":
                    return camoufox_artifact
            except CamoufoxClientError as exc:
                LOGGER.warning("Camoufox failed for %s: %s", url, exc)
                record_content_error(stage="camoufox_remote", url=url, error_type="camoufox_failed")

        artifacts = (jina_artifact, c4a_artifact, local_artifact, camoufox_artifact)
        artifact = next((item for item in artifacts if item and item.status == "success"), None)
        if artifact is None:
            artifact = next((item for item in artifacts if item is not None), None)
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

        # Entity extraction hook: after clean markdown, before return to caller.
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
