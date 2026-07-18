"""Live production verification: batch_get_content (Firecrawl first) + get_content (single-URL)."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "src")

from kindly_web_search_mcp_server.content.batch_orchestrator import BatchParams, run_batch_fetch
from kindly_web_search_mcp_server.content.fetch_pipeline import fetch_content_artifact
from kindly_web_search_mcp_server.content.options import FetchOptions


async def main():
    if not os.environ.get("FIRECRAWL_API_KEY"):
        print("FAIL: FIRECRAWL_API_KEY not set")
        return

    urls = ["https://docs.firecrawl.dev", "https://firecrawl.dev", "https://example.com"]

    # ---- batch_get_content path (Firecrawl first) ----
    print("=" * 70)
    print("BATCH: run_batch_fetch (Firecrawl first, Crawl4AI fallback)")
    print("=" * 70)
    params = BatchParams(max_concurrency=4, per_item_char_length=8000, total_char_budget=24000)
    options = FetchOptions(include_links=True)
    result = await run_batch_fetch(urls=urls, params=params, cursor=None, fetch_options=options)
    print(f"total_requested: {result['total_requested']}")
    print(f"total_returned: {result['total_returned']}")
    print(f"has_more: {result['has_more']}")
    backends = sorted({r["fetch_backend"] for r in result["results"]})
    print(f"backends used: {backends}")
    for r in result["results"]:
        print(
            f"  {r['input_url']} | backend={r['fetch_backend']} status={r['status']} chars={len(r['page_content'])}"
        )
    batch_pass = bool(result["results"]) and all(
        r["fetch_backend"] == "firecrawl_cloud" for r in result["results"]
    )
    print(f"BATCH PASS: {batch_pass}")

    # ---- get_content path (single-URL, restored) ----
    print("\n" + "=" * 70)
    print("SINGLE: fetch_content_artifact (get_content path)")
    print("=" * 70)
    art = await fetch_content_artifact("https://example.com", fetch_options=FetchOptions())
    print(f"input_url: {art.input_url}")
    print(f"fetch_backend: {art.fetch_backend}")
    print(f"status: {art.status}")
    print(f"markdown_len: {len(art.markdown)}")
    print(f"markdown_preview: {art.markdown[:150].replace(chr(10), ' ')}")
    single_pass = art.status == "success" and len(art.markdown) > 0
    print(f"SINGLE PASS: {single_pass}")

    print("\n=== FINAL ===")
    print(f"batch_get_content (Firecrawl first): {'PASS' if batch_pass else 'FAIL'}")
    print(f"get_content (single-URL): {'PASS' if single_pass else 'FAIL'}")
    if batch_pass and single_pass:
        print("\nBOTH TOOLS WORK.")
    else:
        print("\nAT LEAST ONE TOOL FAILED.")


if __name__ == "__main__":
    asyncio.run(main())
