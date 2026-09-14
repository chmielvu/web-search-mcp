"""Wayback Machine resolver: snapshot discovery and archived-page fetch."""

from __future__ import annotations

import urllib.parse

from ..html_tools import html_to_markdown
from ..http_utils import SafeFetchError, safe_fetch_url
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
)
from ._bridge import _text_document


def match_wayback(parsed: ParsedURL) -> ResolverTarget | None:
    host = (parsed.parts.hostname or "").lower()
    if host in {"web.archive.org", "archive.org"}:
        return ResolverTarget(
            url=parsed.url,
            kind="wayback",
            values={"url": parsed.url},
            allow_generic=False,
        )
    return None


async def fetch_wayback_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire an archived snapshot, preserving the snapshot URL."""

    url = target.values.get("url") or target.url
    api_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url, safe='')}"
    try:
        response = await ctx.http_client.get(
            api_url,
            headers={"User-Agent": "kindly-web-search-mcp/1.0 (archive-resolver)"},
            timeout=ctx.timeout(15.0),
        )
    except Exception as exc:
        raise AcquisitionError(code="wayback_transport", message=str(exc)[:300]) from exc
    if response.status_code != 200:
        raise AcquisitionError(
            code="wayback_unavailable",
            message=f"Availability API returned HTTP {response.status_code}.",
            http_status=response.status_code,
        )
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(code="wayback_shape", message=str(exc)[:300]) from exc
    closest = (data.get("archived_snapshots", {}) or {}).get("closest", {}) or {}
    snapshot_url = closest.get("url")
    timestamp = closest.get("timestamp", "unknown")
    if not closest.get("available") or not snapshot_url:
        raise AcquisitionError(
            code="wayback_unavailable", message="No archived snapshot available."
        )
    try:
        fetched = await safe_fetch_url(
            snapshot_url,
            timeout_seconds=ctx.timeout(15.0),
            max_response_bytes=ctx.max_response_bytes,
        )
    except SafeFetchError as exc:
        raise AcquisitionError(code=exc.code, message=str(exc)) from exc
    if not fetched.text.strip():
        raise AcquisitionError(code="empty_content", message="Archived snapshot was empty.")
    header = (
        "# Archived Snapshot (Wayback Machine)\n"
        f"**Original URL:** {url}\n"
        f"**Snapshot Timestamp:** `{timestamp}`\n"
        f"**Archive Link:** {snapshot_url}\n\n---\n\n"
    )
    return _text_document(
        url,
        "wayback_archive",
        header + html_to_markdown(fetched.text, url=snapshot_url),
        fetched_url=snapshot_url,
        scope="excerpt",
        complete=False,
        extra=(
            Diagnostic(
                code="archived_snapshot",
                message="Content is an archived snapshot, not the live source.",
                source="wayback_archive",
                phase="acquire",
            ),
        ),
    )
