from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from kindly_web_search_mcp_server.middleware import query_guidance
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    QueryBranch,
)
from kindly_web_search_mcp_server.search.evidence import render_search_hit_text
from kindly_web_search_mcp_server.search.filters import TemporalWindow
from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.providers.brightdata import (
    BrightDataError,
    _engine_failure,
    parse_brightdata_response,
)
from kindly_web_search_mcp_server.search.providers.exa import search_exa
from kindly_web_search_mcp_server.search.providers.searxng import search_searxng
from kindly_web_search_mcp_server.search.providers.tavily import search_tavily
from kindly_web_search_mcp_server.search.ranking import _collect_expansion_evidence
from kindly_web_search_mcp_server.search.retrieval import _record_provider_result
from kindly_web_search_mcp_server.search.types import (
    EngineCall,
    QueryIntegrity,
    ScoredHit,
    SearchHit,
)
from kindly_web_search_mcp_server.settings import settings
from kindly_web_search_mcp_server.utils.public_output import to_public_hit


def test_brightdata_full_json_preserves_native_order_and_answer_sources() -> None:
    payload = {
        "general": {
            "query": "effective optimized query",
            "detected_query": "effective optimized query",
            "results_cnt": 123,
        },
        "featured_snippets": [
            {
                "title": "Featured",
                "link": "https://featured.example/evidence",
                "description": "Featured evidence",
                "global_rank": 3,
            }
        ],
        "knowledge": {
            "title": "Entity",
            "link": "https://entity.example/",
            "description": "Sourced description",
            "description_source": "Reference",
            "description_link": "https://reference.example/entity",
        },
        "forums": {
            "items": [
                {
                    "title": "Forum thread",
                    "link": "https://forum.example/thread",
                    "answers": [
                        {"text": "First answer", "is_top_answer": False},
                        {"text": "Accepted answer", "is_top_answer": True},
                    ],
                }
            ]
        },
        "organic": [
            {
                "title": "First organic",
                "link": "https://one.example/",
                "description": "One",
                "global_rank": 1,
            },
            {
                "title": "Second organic",
                "link": "https://two.example/",
                "description": "Two",
                "global_rank": 2,
            },
        ],
    }

    call = parse_brightdata_response(
        payload,
        "web",
        5,
        adapter="brightdata",
        sent_query="original submitted query",
    )

    assert [hit.title for hit in call.hits] == [
        "First organic",
        "Second organic",
        "Featured",
        "Entity",
        "Forum thread",
    ]
    assert call.hits[3].url == "https://reference.example/entity"
    assert call.hits[4].snippet == "Accepted answer"
    assert call.integrity is not None
    assert call.integrity.sent_query == "original submitted query"
    assert call.integrity.truncated is False
    assert call.integrity.result_count == 123


def test_brightdata_rejects_boolean_numeric_fields() -> None:
    call = parse_brightdata_response(
        {
            "general": {
                "query": "query",
                "detected_query": "query",
                "results_cnt": True,
            },
            "organic": [
                {
                    "title": "Boolean rank",
                    "link": "https://boolean.example/",
                    "global_rank": True,
                },
                {
                    "title": "Numeric rank",
                    "link": "https://numeric.example/",
                    "global_rank": 2,
                },
            ],
        },
        "web",
        2,
        adapter="brightdata",
        sent_query="query",
    )

    assert [hit.title for hit in call.hits] == ["Numeric rank", "Boolean rank"]
    assert call.hits[1].engine_rank is None
    assert call.integrity is not None
    assert call.integrity.result_count is None


def test_brightdata_envelope_error_retains_typed_metadata() -> None:
    payload = {
        "status_code": 429,
        "headers": {
            "x-brd-error-code": "sr_rate_limit",
            "x-brd-error": "Reduce request rate",
            "retry-after": "17",
        },
    }

    with pytest.raises(BrightDataError) as raised:
        parse_brightdata_response(
            payload,
            "web",
            5,
            adapter="brightdata",
            sent_query="query",
        )

    error = raised.value
    assert error.metadata is not None
    assert error.metadata.provider == "brightdata"
    assert error.metadata.http_status == 429
    assert error.metadata.response_meta["x_brd_error_code"] == "sr_rate_limit"
    assert error.metadata.retry_after == 17.0
    failure = _engine_failure(error)
    assert failure.kind == "rate_limited"
    assert failure.code == "sr_rate_limit"
    assert failure.retry_after == 17.0


@pytest.mark.asyncio
async def test_exa_requests_page_text_and_preserves_author(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
    request_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Result",
                        "url": "https://example.com/result",
                        "author": "Ada Author",
                        "text": "Useful extracted page text",
                        "highlights": ["Relevant passage"],
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        call = await search_exa("query", num_results=1, http_client=client)

    assert request_payload["contents"] == {
        "highlights": True,
        "text": {"maxCharacters": 4000},
    }
    assert call.hits[0].snippet == "Useful extracted page text"
    assert call.hits[0].source_name == "Ada Author"
    assert call.hits[0].highlights == ("Relevant passage",)


@pytest.mark.asyncio
async def test_tavily_requests_dates_without_unused_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    request_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Dated result",
                        "url": "https://example.com/dated",
                        "content": "Current source",
                        "published_date": "2026-09-16",
                        "score": 0.91,
                    }
                ]
            },
            request=request,
        )

    options = SearchOptions(
        temporal=TemporalWindow(
            start=date(2026, 9, 10),
            end=date(2026, 9, 17),
            bucket="week",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        call = await search_tavily(
            "query",
            num_results=1,
            search_options=options,
            http_client=client,
        )

    assert request_payload["include_published_date"] is True
    assert request_payload["time_range"] == "week"
    assert "include_answer" not in request_payload
    assert call.hits[0].published == "2026-09-16"
    assert call.hits[0].provider_score == 0.91


@pytest.mark.asyncio
async def test_searxng_preserves_contributing_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "searxng_base_url", "https://search.example")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Consensus result",
                        "url": "https://example.com/consensus",
                        "content": "Found by multiple engines",
                        "engines": ["google", "bing"],
                        "score": 1.5,
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        call = await search_searxng("query", num_results=1, http_client=client)

    assert call.hits[0].source_engines == ("google", "bing")
    assert call.hits[0].provider_score is not None
    assert call.hits[0].provider_score > 0.0


def test_retrieval_diagnostics_describe_deduplicated_hits() -> None:
    branch = QueryBranch(
        role=BranchRole.ORIGINAL,
        query="query",
        provider_names=("provider",),
        max_results=5,
    )
    duplicate = SearchHit(
        title="Duplicate",
        url="https://example.com/item",
        snippet="Same URL",
        domain="example.com",
        adapter="provider",
        provider_score=0.75,
    )
    call = EngineCall(
        adapter="provider",
        query="query",
        hits=(duplicate, duplicate),
        expansion=("related query",),
        integrity=QueryIntegrity(
            sent_query="query with required terms",
            detected_query="query",
            truncated=True,
            result_count=10,
        ),
    )
    warnings: dict = {}
    provider_calls: list[dict] = []
    branch_calls: list[EngineCall] = []
    provider_result_rows: list[dict] = []

    _record_provider_result(
        branch=branch,
        branch_index=0,
        name="provider",
        value=call,
        latency_ms=1.0,
        warnings_by_name=warnings,
        provider_calls=provider_calls,
        branch_engine_calls=branch_calls,
        provider_result_rows=provider_result_rows,
    )

    assert len(branch_calls[0].hits) == 1
    assert provider_calls[0]["num_results_returned"] == 1
    assert provider_calls[0]["candidate_urls"] == ["https://example.com/item"]
    assert provider_calls[0]["status"] == "partial"
    assert provider_calls[0]["payload_json"]["expansion"] == ["related query"]
    assert provider_calls[0]["payload_json"]["query_integrity"]["truncated"] is True
    assert provider_result_rows[0]["raw_score"] == 0.75
    assert provider_result_rows[0]["payload_json"]["provider_score"] == 0.75
    assert warnings["provider"].error_type == "query_truncated"


def test_merge_preserves_complementary_provider_testimony() -> None:
    first = SearchHit(
        title="Result",
        url="https://example.com/item",
        snippet="Short",
        domain="example.com",
        adapter="provider_a",
        provider_score=0.8,
        source_name="Publisher",
        source_kind="official",
        highlights=("First highlight",),
        source_engines=("engine-a",),
    )
    second = SearchHit(
        title="Result with context",
        url="https://example.com/item",
        snippet="A substantially longer evidence-bearing snippet",
        domain="example.com",
        adapter="provider_b",
        provider_score=0.6,
        published="2026-09-17",
        highlights=("Second highlight",),
        source_engines=("engine-b",),
        origin_adapters=("archive",),
        answer_kind="featured_snippet",
    )

    merged = reciprocal_rank_fusion([[first], [second]], k=60)
    hit, _, providers = merged[0]

    assert hit.adapter == "provider_b"
    assert hit.provider_score == 0.6
    assert hit.source_name == "Publisher"
    assert hit.source_kind == "official"
    assert hit.published == "2026-09-17"
    assert hit.highlights == ("Second highlight", "First highlight")
    assert hit.source_engines == ("engine-b", "engine-a")
    assert hit.origin_adapters == ("archive",)
    assert hit.answer_kind == "featured_snippet"
    assert providers == ("provider_a", "provider_b")


def test_evidence_rendering_uses_unique_highlights_without_changing_public_wire() -> None:
    hit = SearchHit(
        title="Evidence title",
        url="https://example.com/evidence",
        snippet="Primary evidence passage",
        domain="example.com",
        adapter="exa",
        provider_score=0.9,
        source_name="Researcher",
        highlights=("Primary evidence passage", "Independent supporting passage"),
        answer_kind="featured_snippet",
    )
    rendered = render_search_hit_text(hit, max_chars=4000)
    public = to_public_hit(
        ScoredHit(hit=hit, final_score=0.8, providers=("exa",)),
        "c1",
    ).model_dump(exclude_none=True)

    assert rendered == "Evidence title\nPrimary evidence passage\nIndependent supporting passage"
    assert public["snippet"] == "Primary evidence passage"
    assert public["providers"] == ["exa"]
    assert "provider_score" not in public
    assert "source_name" not in public
    assert "answer_kind" not in public
    assert "score" not in public
    assert "status" not in public


def test_expansion_evidence_requires_independent_call_support() -> None:
    branch = QueryBranch(
        role=BranchRole.ORIGINAL,
        query="base query",
        provider_names=("one", "two"),
        max_results=5,
    )
    outcomes = (
        BranchOutcome(
            branch=branch,
            calls=(
                EngineCall(
                    adapter="one",
                    query="base query one",
                    expansion=("shared expansion", "single expansion"),
                ),
                EngineCall(
                    adapter="two",
                    query="base query two",
                    expansion=("Shared Expansion",),
                ),
            ),
        ),
    )

    signals = _collect_expansion_evidence(outcomes, source_query="base query")

    assert [(signal.query, signal.support_count) for signal in signals] == [
        ("shared expansion", 2),
        ("single expansion", 1),
    ]
    assert signals[0].adapters == ("one", "two")


def test_guidance_derives_provider_count_from_public_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(query_guidance, "_gemini_is_available", lambda: False)

    message, _, _ = query_guidance._guide_web_search(
        {
            "query": "query",
            "intent": "research",
            "results": [
                {"url": "https://one.example", "providers": ["exa", "tavily"]},
                {"url": "https://two.example", "providers": ["tavily"]},
            ],
        }
    )

    assert message.startswith("2 results from 2 providers.")
