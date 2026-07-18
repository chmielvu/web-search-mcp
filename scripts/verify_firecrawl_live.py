"""Live verification: Firecrawl batch_scrape is the first backend for batch_get_content.

Run: python scripts/verify_firecrawl_live.py
Requires FIRECRAWL_API_KEY in env.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "src")

from kindly_web_search_mcp_server.content.firecrawl_stage import run_firecrawl_batch
from kindly_web_search_mcp_server.content.options import FetchOptions


async def main() -> None:
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("FAIL: FIRECRAWL_API_KEY not set")
        return

    urls = ["https://docs.firecrawl.dev", "https://firecrawl.dev", "https://example.com"]
    print(f"Calling run_firecrawl_batch on {len(urls)} URLs...")
    result = await run_firecrawl_batch(
        urls, options=FetchOptions(include_links=True), batch_params=None
    )

    print(f"\nURLs covered by Firecrawl: {list(result.keys())}")
    if not result:
        print("FAIL: Firecrawl returned no artifacts — would fall back to Crawl4AI.")
        return

    all_ok = True
    for url, art in result.items():
        print("\n---")
        print(f"input_url: {url}")
        print(f"fetch_backend: {art.fetch_backend}")
        print(f"status: {art.status}")
        print(f"fetched_url: {art.fetched_url}")
        print(f"markdown_len: {len(art.markdown)}")
        preview = art.markdown[:200].replace("\n", " ")
        print(f"markdown_preview: {preview}")
        print(f"links_count: {len(art.links) if art.links else 0}")
        if art.error:
            print(f"error: {art.error.code} - {art.error.message}")
        if art.fetch_backend != "firecrawl_cloud":
            print(f"FAIL: {url} did not use firecrawl_cloud (got {art.fetch_backend})")
            all_ok = False

    covered = set(result.keys())
    uncovered = set(urls) - covered
    print(f"\nUncovered URLs (would fall back to Crawl4AI): {sorted(uncovered)}")

    if all_ok and covered:
        print(
            "\nPASS: Firecrawl batch_scrape is the first backend; uncovered URLs would fall back to Crawl4AI."
        )
    else:
        print("\nFAIL: see above.")


if __name__ == "__main__":
    asyncio.run(main())
