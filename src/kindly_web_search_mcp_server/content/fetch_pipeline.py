"""Content fetch pipeline — two-tier architecture.

Tier 1: Specialized resolvers (StackExchange, GitHub, Wikipedia, arXiv, Telegram) in content/resolvers/
Tier 2: Generic extraction stages (Jina -> Crawl4AI /md -> local BS4 -> Camoufox last-resort)
"""

from __future__ import annotations

import logging
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
from ..search.normalize import canonicalize_url
from ..telemetry import record_content_error
from .stages import _fetch_via_camoufox, _fetch_via_crawl4ai, _fetch_via_jina, _fetch_via_local

LOGGER = logging.getLogger(__name__)

_content_tracer = trace.get_tracer("kindly_web_search_mcp_server.content.fetch_pipeline")


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

        # Stage 1: Jina Reader
        jina_artifact = await _fetch_via_jina(url, options=options)
        if jina_artifact is not None and jina_artifact.status == "success":
            return jina_artifact
        jina_unavailable = jina_artifact is None

        # Stage 2: Crawl4AI cloud (POST /md, non-browser)
        c4a_artifact: ContentArtifact | None = None
        c4a_unavailable = False
        if get_crawl4ai_client() is not None:
            try:
                c4a_artifact = await _fetch_via_crawl4ai(url, options)
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
            local_artifact = await _fetch_via_local(url, options=options)
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

        # Select best available artifact
        artifact: ContentArtifact = (
            jina_artifact
            or c4a_artifact
            or local_artifact
            or camoufox_artifact
            or ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=None,
                status="blocked",
                source_type="web",
                fetch_backend="all_failed",
                content_type=None,
                markdown="",
                error=ContentError(
                    code="all_stages_failed", message="All extraction stages failed"
                ),
            )
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
