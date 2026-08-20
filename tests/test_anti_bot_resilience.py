from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from kindly_web_search_mcp_server.content.artifact import ContentArtifact, ContentError
from kindly_web_search_mcp_server.content.fetch_pipeline import fetch_content_artifact
from kindly_web_search_mcp_server.content.resolvers.reddit import (
    RedditTarget,
    _fetch_reddit_arctic_shift,
    _parse_old_reddit_html,
    fetch_reddit_thread_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.wayback import fetch_wayback_snapshot_markdown
from kindly_web_search_mcp_server.content.safe_fetch import SafeFetchResult, _sniff_doc_type


def test_sniff_doc_type() -> None:
    assert _sniff_doc_type("application/pdf", "https://example.com/doc", b"%PDF-1.4...") == "pdf"
    assert _sniff_doc_type(None, "https://example.com/sheet.xlsx", b"PK\x03\x04...") == "xlsx"
    assert _sniff_doc_type(None, "https://example.com/file.docx", b"PK\x03\x04...") == "docx"
    assert _sniff_doc_type(None, "https://example.com/slides.pptx", b"PK\x03\x04...") == "pptx"
    assert _sniff_doc_type(None, "https://example.com/book.epub", b"PK\x03\x04...") == "epub"
    assert _sniff_doc_type(None, "https://example.com/nb.ipynb", b'{"cells": []}') == "ipynb"
    assert _sniff_doc_type("text/csv", "https://example.com/data", b"a,b,c\n1,2,3") == "csv"
    assert _sniff_doc_type("text/html", "https://example.com/page.html", b"<html></html>") is None


def test_parse_old_reddit_html() -> None:
    sample_html = """
    <html>
      <div class="entry unvoted">
        <a class="title" href="/r/Python/comments/123/title">Cool Python Projects</a>
        <a class="author" href="/u/pythonista">pythonista</a>
        <div class="score unvoted">42</div>
        <div class="usertext-body"><div class="md"><p>Here is my open-source project.</p></div></div>
      </div>
      <div class="comment">
        <a class="author" href="/u/coder">coder</a>
        <div class="usertext-body"><div class="md"><p>Looks awesome, great work!</p></div></div>
      </div>
    </html>
    """
    target = RedditTarget(subreddit="Python", post_id="123")
    rendered = _parse_old_reddit_html(sample_html, target)

    assert "# Reddit: Cool Python Projects (r/Python)" in rendered
    assert "u/pythonista" in rendered
    assert "Here is my open-source project." in rendered
    assert "## Top Comments" in rendered
    assert "### Comment by u/coder" in rendered
    assert "Looks awesome, great work!" in rendered


@pytest.mark.asyncio
async def test_reddit_arctic_shift_fallback() -> None:
    mock_post_json = {
        "data": [
            {
                "title": "Arctic Shift Title",
                "subreddit": "Python",
                "author": "dev_user",
                "score": 150,
                "selftext": "Text content from Arctic Shift.",
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_post_json

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        target = RedditTarget(subreddit="Python", post_id="123")
        rendered = await _fetch_reddit_arctic_shift(target)
        assert "# Reddit: Arctic Shift Title (r/Python)" in rendered
        assert "u/dev_user" in rendered
        assert "Text content from Arctic Shift." in rendered


@pytest.mark.asyncio
async def test_fetch_pipeline_local_stage_executes_when_jina_partial() -> None:
    # Simulates Jina returning low-quality / too_short partial response
    jina_stub = ContentArtifact(
        input_url="https://example.com/test",
        normalized_url="https://example.com/test",
        fetched_url="https://example.com/test",
        status="partial",
        source_type="html",
        fetch_backend="jina_reader",
        content_type="text/markdown",
        markdown="Too short.",
        word_count=2,
        quality_score=0.4,
        error=ContentError(code="too_short", message="too_short"),
    )
    local_success = ContentArtifact(
        input_url="https://example.com/test",
        normalized_url="https://example.com/test",
        fetched_url="https://example.com/test",
        status="success",
        source_type="html",
        fetch_backend="local",
        content_type="text/markdown",
        markdown="# Complete Article\n\nThis is the full rich content extracted via local BS4/Trafilatura engine.",
        word_count=150,
        quality_score=1.0,
    )

    with patch(
        "kindly_web_search_mcp_server.content.fetch_pipeline._resolve_tier1",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with patch(
            "kindly_web_search_mcp_server.content.fetch_pipeline._fetch_via_jina",
            new_callable=AsyncMock,
            return_value=jina_stub,
        ):
            with patch(
                "kindly_web_search_mcp_server.content.fetch_pipeline._fetch_via_local",
                new_callable=AsyncMock,
                return_value=local_success,
            ) as mock_local:
                art = await fetch_content_artifact("https://example.com/test")

                # Verify local fetch was called and returned
                assert mock_local.called
                assert art.status == "success"
                assert art.fetch_backend == "local"
                assert "Complete Article" in art.markdown


@pytest.mark.asyncio
async def test_fetch_pipeline_wayback_fallback_when_all_live_fail() -> None:
    error_artifact = ContentArtifact(
        input_url="https://example.com/dead-page",
        normalized_url="https://example.com/dead-page",
        fetched_url="https://example.com/dead-page",
        status="error",
        source_type="web",
        fetch_backend="safe_http",
        content_type=None,
        markdown="",
        error=ContentError(code="http_404", message="404 Not Found"),
    )
    wayback_success = ContentArtifact(
        input_url="https://example.com/dead-page",
        normalized_url="https://example.com/dead-page",
        fetched_url="http://web.archive.org/web/2026/https://example.com/dead-page",
        status="success",
        source_type="web_archive",
        fetch_backend="wayback_machine",
        content_type="text/markdown",
        markdown="# Archived Dead Page\n\nPreserved content from Internet Archive.",
        word_count=50,
        quality_score=0.9,
    )

    with patch(
        "kindly_web_search_mcp_server.content.fetch_pipeline._resolve_tier1",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with patch(
            "kindly_web_search_mcp_server.content.fetch_pipeline._fetch_via_jina",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "kindly_web_search_mcp_server.content.fetch_pipeline._fetch_via_local",
                new_callable=AsyncMock,
                return_value=error_artifact,
            ):
                with patch(
                    "kindly_web_search_mcp_server.content.fetch_pipeline.get_crawl4ai_client",
                    return_value=None,
                ):
                    with patch(
                        "kindly_web_search_mcp_server.content.fetch_pipeline.get_camoufox_client",
                        return_value=None,
                    ):
                        with patch(
                            "kindly_web_search_mcp_server.content.fetch_pipeline.fetch_wayback_snapshot_markdown",
                            new_callable=AsyncMock,
                            return_value=wayback_success,
                        ) as mock_wb:
                            art = await fetch_content_artifact("https://example.com/dead-page")

                            assert mock_wb.called
                            assert art.status == "success"
                            assert art.fetch_backend == "wayback_machine"
                            assert "Archived Dead Page" in art.markdown


@pytest.mark.asyncio
async def test_fetch_wayback_snapshot_markdown() -> None:
    mock_available_json = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": "http://web.archive.org/web/2026/https://example.com/page",
                "timestamp": "20260101",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_available_json

    mock_fetch = SafeFetchResult(
        input_url="http://web.archive.org/web/2026/https://example.com/page",
        fetched_url="http://web.archive.org/web/2026/https://example.com/page",
        content_type="text/html",
        body=b"<html><body><h1>Archived Header</h1><p>Archived Paragraph</p></body></html>",
        text="<html><body><h1>Archived Header</h1><p>Archived Paragraph</p></body></html>",
        is_pdf=False,
    )

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        with patch(
            "kindly_web_search_mcp_server.content.resolvers.wayback.safe_fetch_url",
            new_callable=AsyncMock,
            return_value=mock_fetch,
        ):
            artifact = await fetch_wayback_snapshot_markdown("https://example.com/page")
            assert artifact is not None
            assert artifact.status == "success"
            assert artifact.source_type == "web_archive"
            assert "Archived Snapshot (Wayback Machine)" in artifact.markdown


@pytest.mark.asyncio
async def test_fetch_reddit_thread_markdown() -> None:
    with patch(
        "kindly_web_search_mcp_server.content.resolvers.reddit._fetch_reddit_direct_json",
        new_callable=AsyncMock,
        return_value="# Reddit: Direct Post (r/Python)",
    ):
        rendered = await fetch_reddit_thread_markdown(
            "https://www.reddit.com/r/Python/comments/123/title/"
        )
        assert "# Reddit: Direct Post" in rendered
