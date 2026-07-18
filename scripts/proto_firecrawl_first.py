"""Prototype: Firecrawl batch_scrape as first backend, Crawl4AI fallback.

Proves the approach with LIVE API calls before touching the real codebase.
Requires FIRECRAWL_API_KEY in env. Crawl4AI fallback only runs if
CRAWL4AI_BASE_URL is set; otherwise it's skipped (prototype only).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, "src")

from firecrawl.v2.client_async import AsyncFirecrawlClient

from kindly_web_search_mcp_server.content.artifact import ContentArtifact, ContentError
from kindly_web_search_mcp_server.content.options import FetchOptions
from kindly_web_search_mcp_server.search.normalize import canonicalize_url
from kindly_web_search_mcp_server.settings import settings


@dataclass
class BatchParams:
    max_concurrency: int = 4
    per_item_char_length: int = 8000
    total_char_budget: int = 24000
    per_url_timeout_seconds: float = 120.0


def _resolve_input_url(fetched_url, urls, input_by_normalized):
    if not fetched_url:
        return None
    normalized = canonicalize_url(fetched_url)
    if normalized in input_by_normalized:
        return input_by_normalized[normalized]
    for u in urls:
        if u == fetched_url or canonicalize_url(u) == normalized:
            return u
    return None


async def run_firecrawl_batch(urls, *, options):
    """Return None if Firecrawl unavailable/failed; dict[str, ContentArtifact] on success."""
    if not settings.firecrawl_api_key:
        print("  [firecrawl] no API key -> unavailable")
        return None
    client = AsyncFirecrawlClient(
        api_key=settings.firecrawl_api_key,
        api_url=settings.firecrawl_api_url,
        timeout=settings.firecrawl_timeout_seconds,
    )
    formats = ["markdown"] + (["links"] if options.include_links else [])
    try:
        print(f"  [firecrawl] batch_scrape({len(urls)} urls, formats={formats})")
        result = await client.batch_scrape(
            urls,
            formats=formats,
            poll_interval=settings.firecrawl_poll_interval_seconds,
            timeout=settings.firecrawl_max_poll_seconds,
            only_main_content=True,
            ignore_invalid_urls=True,
        )
    except Exception as exc:
        print(f"  [firecrawl] FAILED: {exc}")
        return None

    artifacts: dict[str, ContentArtifact] = {}
    data = getattr(result, "data", None) or []
    print(f"  [firecrawl] returned {len(data)} docs")
    input_by_normalized = {canonicalize_url(u): u for u in urls}
    for doc in data:
        meta = getattr(doc, "metadata_dict", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        fetched_url = meta.get("source_url") or meta.get("url")
        input_url = _resolve_input_url(fetched_url, urls, input_by_normalized)
        if input_url is None:
            print(f"  [firecrawl] could not match doc to input URL (fetched={fetched_url})")
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
            links=None,
            error=ContentError(code="firecrawl_empty", message="empty markdown", retryable=True)
            if has_error
            else None,
        )
    return artifacts


async def run_batch_fetch(urls, *, params, options):
    """Firecrawl first; Crawl4AI for the whole batch only if Firecrawl returns None."""
    pending = list(urls)
    print(f"\nrun_batch_fetch: {len(pending)} URLs")

    firecrawl = await run_firecrawl_batch(pending, options=options)

    if firecrawl is not None:
        print(f"\n=> Firecrawl SUCCESS: {len(firecrawl)} artifacts")
        for url, art in firecrawl.items():
            print(
                f"   {url} | backend={art.fetch_backend} status={art.status} "
                f"md_len={len(art.markdown)} fetched={art.fetched_url}"
            )
        missing = [u for u in pending if u not in firecrawl]
        print(f"   missing from Firecrawl (not retried in this prototype): {missing}")
        return firecrawl

    print("\n=> Firecrawl UNAVAILABLE/FAILED -> Crawl4AI fallback for the whole batch")
    if not settings.crawl4ai_base_url:
        print("   [crawl4ai] CRAWL4AI_BASE_URL not set -> cannot run fallback in prototype")
        return {}

    from kindly_web_search_mcp_server.content.stages import _fetch_via_crawl4ai
    from kindly_web_search_mcp_server.content.remote_clients import get_crawl4ai_client

    if get_crawl4ai_client() is None:
        print("   [crawl4ai] client unavailable -> cannot run fallback")
        return {}

    artifacts: dict[str, ContentArtifact] = {}
    for url in pending:
        print(f"   [crawl4ai] fetching {url}")
        try:
            art = await _fetch_via_crawl4ai(url, options)
        except Exception as exc:
            print(f"   [crawl4ai] FAILED for {url}: {exc}")
            art = ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=None,
                status="error",
                source_type="unknown",
                fetch_backend="crawl4ai_remote",
                content_type=None,
                markdown="",
                error=ContentError(code="crawl4ai_failed", message=str(exc)[:300], retryable=True),
            )
        artifacts[url] = art
        print(
            f"   [crawl4ai] {url} | backend={art.fetch_backend} status={art.status} "
            f"md_len={len(art.markdown)}"
        )
    return artifacts


async def main():
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("FAIL: FIRECRAWL_API_KEY not set")
        return
    urls = ["https://docs.firecrawl.dev", "https://firecrawl.dev", "https://example.com"]
    params = BatchParams()
    options = FetchOptions(include_links=True)

    print("=" * 70)
    print("SCENARIO 1: Firecrawl available -> Firecrawl first")
    print("=" * 70)
    result = await run_batch_fetch(urls, params=params, options=options)
    print("\n=== SCENARIO 1 RESULT ===")
    print(f"backends used: {sorted({a.fetch_backend for a in result.values()})}")
    all_firecrawl = all(a.fetch_backend == "firecrawl_cloud" for a in result.values())
    print(f"all results from firecrawl_cloud: {all_firecrawl}")
    if all_firecrawl and result:
        print("\nPASS scenario 1: Firecrawl batch_scrape is the first backend.")
    else:
        print("\nFAIL scenario 1: see above.")

    if not settings.crawl4ai_base_url:
        print("\n(Skipping Crawl4AI fallback scenario: CRAWL4AI_BASE_URL not set.)")
    else:
        print("\n" + "=" * 70)
        print("SCENARIO 2: Firecrawl unavailable (bad key) -> Crawl4AI fallback")
        print("=" * 70)
        original_key = settings.firecrawl_api_key
        object.__setattr__(settings, "firecrawl_api_key", "fc-invalid-key-for-proto")
        try:
            result2 = await run_batch_fetch(urls, params=params, options=options)
        finally:
            object.__setattr__(settings, "firecrawl_api_key", original_key)

        print("\n=== SCENARIO 2 RESULT ===")
        if not result2:
            print("FAIL scenario 2: no results (Crawl4AI also unavailable?)")
            return
        backends2 = sorted({a.fetch_backend for a in result2.values()})
        print(f"backends used: {backends2}")
        any_firecrawl = any(a.fetch_backend == "firecrawl_cloud" for a in result2.values())
        any_crawl4ai = any(a.fetch_backend == "crawl4ai_remote" for a in result2.values())
        print(f"any firecrawl_cloud: {any_firecrawl} (should be False)")
        print(f"any crawl4ai_remote: {any_crawl4ai} (should be True)")
        if not any_firecrawl:
            print("\nPASS scenario 2: Firecrawl unavailable -> did not use Firecrawl.")
        else:
            print("\nFAIL scenario 2: Firecrawl was used despite bad key.")


if __name__ == "__main__":
    asyncio.run(main())
