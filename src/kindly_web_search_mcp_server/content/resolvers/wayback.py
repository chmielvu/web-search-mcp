"""Specialized resolver for Internet Archive (Wayback Machine) snapshots.

Used as a resilience fallback when a live URL returns 404, 410, or persistent blocks.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx

from ..artifact import ContentArtifact, ContentError
from ..extract import extract_content_as_markdown
from ..options import FetchOptions
from ..safe_fetch import safe_fetch_url
from ..sanitize import sanitize_markdown
from ..status_classifier import classify_markdown
from ...telemetry import record_content_error, record_content_resolution
from ...utils.url_canonicalize import canonicalize_url

LOGGER = logging.getLogger(__name__)


async def fetch_wayback_snapshot_markdown(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact | None:
    """Query Wayback Machine Availability API and fetch closest archived snapshot."""
    options = fetch_options or FetchOptions()
    api_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url, safe='')}"

    try:
        timeout_sec = options.stage_timeout_seconds or 15.0
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            headers = {"User-Agent": "kindly-web-search-mcp/1.0 (archive-resolver)"}
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None

            data: dict[str, Any] = resp.json()
            archived = data.get("archived_snapshots", {}).get("closest", {})
            if not archived.get("available") or not archived.get("url"):
                return None

            snapshot_url = archived["url"]
            timestamp = archived.get("timestamp", "unknown")

            # Fetch snapshot content
            fetched = await safe_fetch_url(snapshot_url, timeout_seconds=timeout_sec)
            html = fetched.text
            if not html.strip():
                return None

            raw_md = extract_content_as_markdown(html, url=snapshot_url)
            header = (
                f"# Archived Snapshot (Wayback Machine)\n"
                f"**Original URL:** {url}\n"
                f"**Snapshot Timestamp:** `{timestamp}`\n"
                f"**Archive Link:** {snapshot_url}\n\n---\n\n"
            )
            full_md = header + raw_md
            clean_md = sanitize_markdown(full_md)
            cls = classify_markdown(clean_md)
            word_count = len(clean_md.split())

            record_content_resolution(
                stage="wayback_archive",
                url=url,
                success=cls.status == "success",
                size_bytes=len(clean_md.encode("utf-8")),
                word_count=word_count,
                extraction_method="wayback_machine_api",
            )

            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=snapshot_url,
                status="success" if word_count >= 15 else cls.status,
                source_type="web_archive",
                fetch_backend="wayback_machine",
                content_type="text/markdown",
                markdown=clean_md,
                word_count=word_count,
                quality_score=0.9 if cls.status == "success" else 0.5,
                error=None
                if cls.status == "success"
                else ContentError(
                    code=cls.reason or "archive_partial",
                    message=cls.reason or "partial archive snapshot",
                ),
            )
    except Exception as exc:
        LOGGER.debug("Wayback snapshot fetch failed for %s: %s", url, exc)
        record_content_error(stage="wayback_archive", url=url, error_type=type(exc).__name__)
        return None
