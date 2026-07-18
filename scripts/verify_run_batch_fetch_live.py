"""Live verification of the REAL run_batch_fetch: Firecrawl first, Crawl4AI fallback."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "src")

from kindly_web_search_mcp_server.content.batch_orchestrator import BatchParams, run_batch_fetch
from kindly_web_search_mcp_server.content.options import FetchOptions
from kindly_web_search_mcp_server.settings import settings


async def main():
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("FAIL: FIRECRAWL_API_KEY not set")
        return

    urls = ["https://docs.firecrawl.dev", "https://firecrawl.dev", "https://example.com"]
    params = BatchParams(max_concurrency=4, per_item_char_length=8000, total_char_budget=24000)
    options = FetchOptions(include_links=True)

    print("=" * 70)
    print("REAL run_batch_fetch — Firecrawl first")
    print("=" * 70)
    result = await run_batch_fetch(urls=urls, params=params, cursor=None, fetch_options=options)

    print(f"\ntotal_requested: {result['total_requested']}")
    print(f"total_returned: {result['total_returned']}")
    print(f"total_chars_returned: {result['total_chars_returned']}")
    print(f"has_more: {result['has_more']}")

    backends = sorted({r["fetch_backend"] for r in result["results"]})
    print(f"backends used: {backends}")

    for r in result["results"]:
        print(
            f"  {r['input_url']} | backend={r['fetch_backend']} status={r['status']} "
            f"chars={len(r['page_content'])}"
        )

    all_firecrawl = all(r["fetch_backend"] == "firecrawl_cloud" for r in result["results"])
    print(f"\nall results from firecrawl_cloud: {all_firecrawl}")
    if all_firecrawl and result["results"]:
        print("\nPASS: real run_batch_fetch uses Firecrawl batch_scrape first.")
    else:
        print("\nFAIL: see above.")


if __name__ == "__main__":
    asyncio.run(main())
