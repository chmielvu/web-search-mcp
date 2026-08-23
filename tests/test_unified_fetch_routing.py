from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.content.llms_txt import LlmsTxtResult, check_llms_txt
from kindly_web_search_mcp_server.content.safe_fetch import SafeFetchResult
from kindly_web_search_mcp_server.content.typed_content import (
    detect_content_format,
    render_typed_content,
)


def test_detects_json_rss_and_csv_formats() -> None:
    assert detect_content_format("https://example.com/data", "application/json", "{}") == "json"
    assert detect_content_format("https://example.com/feed", "application/rss+xml", "<rss />") == "rss"
    assert detect_content_format("https://example.com/data.csv", "text/plain", "a,b\n1,2") == "csv"


def test_renders_json_and_rss_with_links() -> None:
    json_md, json_meta, json_links = render_typed_content("json", '{"a": 1}', "https://example.com/data")
    assert "```json" in json_md
    assert json_meta["format"] == "json"
    assert json_links == []

    rss = """<rss><channel><title>Feed</title><item><title>Entry</title><link>https://example.com/item</link><description>Body</description></item></channel></rss>"""
    rss_md, rss_meta, rss_links = render_typed_content("rss", rss, "https://example.com/feed")
    assert "# Feed" in rss_md
    assert rss_meta["item_count"] == 1
    assert rss_links[0]["url"] == "https://example.com/item"


def test_renders_crlf_csv() -> None:
    rendered, metadata, links = render_typed_content(
        "csv",
        "Name,Age\rAlice,30\rBob,25\r",
        "https://example.com/team.csv",
    )

    assert "| Alice | 30 |" in rendered
    assert metadata["row_count"] == 2
    assert links == []


@pytest.mark.asyncio
async def test_llms_preflight_replaces_only_root_candidates() -> None:
    fetched = SafeFetchResult(
        input_url="https://example.com/llms.txt",
        fetched_url="https://example.com/llms.txt",
        content_type="text/plain; charset=utf-8",
        body=b"# Docs\n",
        text="# Docs\n",
        is_pdf=False,
    )
    with patch(
        "kindly_web_search_mcp_server.content.llms_txt.safe_fetch_url",
        new_callable=AsyncMock,
        return_value=fetched,
    ) as safe_fetch:
        root = await check_llms_txt("https://example.com/")
        nested = await check_llms_txt("https://example.com/docs")

    assert root == LlmsTxtResult(
        available=True,
        url="https://example.com/llms.txt",
        content="# Docs\n",
        content_type="text/plain; charset=utf-8",
    )
    assert nested.available is False
    safe_fetch.assert_awaited_once()
