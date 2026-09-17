from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.models import ProviderWarning, fetch_next, make_next
from kindly_web_search_mcp_server.search.types import ScoredHit, SearchHit
from kindly_web_search_mcp_server.utils.public_output import (
    decode_web_search_overflow_cursor,
    page_overflow_cursor,
    to_public_web_search,
)


def _hit(url: str, *, snippet: str = "Snippet") -> ScoredHit:
    return ScoredHit(
        hit=SearchHit(
            title=f"Title for {url}",
            url=url,
            snippet=snippet,
            domain="example.com",
            adapter="exa",
        ),
        final_score=0.91,
        providers=("exa", "tavily"),
    )


def test_public_envelope_omits_redundant_status_and_internal_leaks() -> None:
    payload = to_public_web_search(
        query="Python 3.14 free-threading",
        hits=[_hit("https://example.com/a"), _hit("https://example.com/b")],
        overflow_items=[
            (
                "rrf",
                SearchHit(
                    title="Leftover",
                    url="https://example.com/overflow",
                    snippet="",
                    domain="example.com",
                    adapter="brave",
                ),
            )
        ],
        warnings=[
            ProviderWarning(
                provider="ddg",
                error="ConnectError",
                error_type="DDGSException",
                action="Retry later or continue with the ranked results.",
                retryable=True,
            )
        ],
    ).model_dump(exclude_none=True)

    assert "status" not in payload
    assert "intent" not in payload
    assert "query_variants" not in payload
    assert "has_more" not in payload
    assert payload["remaining"] == 1
    assert payload["cursor"]
    assert payload["results"][0]["citation_id"] == "c1"
    assert "score" not in payload["results"][0]
    assert payload["results"][0]["providers"] == ["exa", "tavily"]
    assert payload["results"][0]["consensus"] == 2
    next_call = payload["next"][0]
    assert next_call["tool"] == "fetch"
    assert "action" not in next_call
    assert len(next_call["query"]["urls"]) == 2
    assert payload["warnings"][0]["action"]


def test_empty_results_are_an_empty_list_not_a_status() -> None:
    payload = to_public_web_search(
        query="no hits",
        hits=[],
        overflow_items=[],
        warnings=[
            ProviderWarning(
                provider="brightdata",
                error="rate limited",
                error_type="rate_limit",
                action="Wait 30s before retrying.",
                retry_after=30.0,
                retryable=True,
            )
        ],
    ).model_dump(exclude_none=True)

    assert payload["results"] == []
    assert "status" not in payload
    assert "next" not in payload
    assert "cursor" not in payload
    assert payload["warnings"][0]["retryable"] is True


def test_overflow_cursor_pages_title_url_only() -> None:
    first = to_public_web_search(
        query="query",
        hits=[_hit("https://example.com/keep")],
        overflow_items=[
            (
                "mmr_fallback",
                SearchHit(
                    title="More",
                    url="https://example.com/more",
                    snippet="hidden",
                    domain="example.com",
                    adapter="brave",
                ),
            )
        ],
        warnings=None,
    )
    decoded = decode_web_search_overflow_cursor(first.cursor or "")
    paged = page_overflow_cursor(decoded).model_dump(exclude_none=True)

    assert paged["results"] == [
        {"citation_id": "c2", "title": "More", "url": "https://example.com/more"}
    ]
    assert "stage" not in paged["results"][0]
    assert "snippet" not in paged["results"][0]
    assert "status" not in paged
    assert paged["next"][0]["query"] == {"url": "https://example.com/more"}


def test_stale_cursor_version_tells_agent_to_research() -> None:
    with pytest.raises(ValueError, match="without cursor"):
        decode_web_search_overflow_cursor("e30")  # "{}"


def test_next_hint_has_no_hardcoded_fetch_action() -> None:
    hint = make_next(
        tool="youtube_transcript",
        query={"video_id_or_url": "https://youtu.be/dQw4w9wgGcQ"},
        why="Extract the transcript.",
    ).model_dump(exclude_none=True)
    assert hint["tool"] == "youtube_transcript"
    assert "action" not in hint

    fetch_hint = fetch_next(
        [f"https://example.com/{index}" for index in range(8)],
        why="Fetch the top pages.",
        limit=5,
    )
    assert fetch_hint is not None
    assert len(fetch_hint[0].query["urls"]) == 5
