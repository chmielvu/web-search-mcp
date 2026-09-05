from __future__ import annotations

import base64
import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from fastmcp.exceptions import ToolError

from kindly_web_search_mcp_server.models import (
    WebSearchHit,
    WebSearchOverflowHit,
    WebSearchPublicResponse,
    WebSearchResult,
)
from kindly_web_search_mcp_server.rerank.models import RerankOverflowItem, RerankOutput
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    ProviderRankedResults,
    QueryBranch,
    WebSearchRequest,
)
from kindly_web_search_mcp_server.search.filters import TemporalWindow
from kindly_web_search_mcp_server.search.options import SearchOptions
from kindly_web_search_mcp_server.search.ranking import rank_and_finalize
from kindly_web_search_mcp_server.search.service import SearchRun
from kindly_web_search_mcp_server.tools.search import web_search
from kindly_web_search_mcp_server.utils.public_output import (
    decode_web_search_overflow_cursor,
    encode_web_search_overflow_cursor,
    to_public_hit,
    to_public_web_search,
    to_public_web_search_from_run,
)

import httpx


def _result(index: int, *, published_date: str | None = "2026-06-01") -> WebSearchResult:
    return WebSearchResult(
        title=f"Doc {index}",
        link=f"https://host{index}.test/{index}",
        snippet=f"Snippet {index}",
        domain=f"host{index}.test",
        published_date=published_date,
        providers=["searxng"],
        final_score=1.0 - (index * 0.01),
    )


def _run_with(
    results: list[WebSearchResult], *, options: SearchOptions | None = None
) -> tuple[SearchRun, BranchOutcome]:
    branch = QueryBranch(
        role=BranchRole.ORIGINAL,
        query="q",
        provider_names=("test",),
        max_results=15,
    )
    outcome = BranchOutcome(
        branch=branch,
        attempted_provider_names=("test",),
        provider_ranked_results=(ProviderRankedResults(0, branch.role, "test", tuple(results)),),
    )
    request_kwargs: dict[str, object] = {"query": "q", "research_goal": "goal"}
    if options is not None:
        request_kwargs["options"] = options
    run = SearchRun(
        request=WebSearchRequest(**request_kwargs),  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(),
        run_key="t-run",
    )
    return run, outcome


class TestWebSearchPublicOutput(unittest.IsolatedAsyncioTestCase):
    async def test_page1_full_hits_leftover_cursor(self) -> None:
        items = [_result(index) for index in range(40)]
        run, outcome = _run_with(items)

        async def fake_rerank(
            _query: str, candidates: list[WebSearchResult], **_kwargs: object
        ) -> RerankOutput:
            pool = list(candidates)
            return RerankOutput(
                results=pool[:15],
                overflow_items=[
                    *(RerankOverflowItem(stage="rankllm", result=item) for item in pool[15:30]),
                    *(RerankOverflowItem(stage="cross", result=item) for item in pool[30:40]),
                ],
            )

        try:
            with patch(
                "kindly_web_search_mcp_server.search.ranking.rerank_results",
                fake_rerank,
            ):
                response = await rank_and_finalize(run, (outcome,), embedding_task=None)
            run.response = response
            public = to_public_web_search_from_run(run)
        finally:
            await run.http_client.aclose()

        self.assertEqual(len(public.results), 15)
        self.assertTrue(all(isinstance(hit, WebSearchHit) for hit in public.results))
        self.assertTrue(
            all(
                getattr(hit, "url", None) and getattr(hit, "snippet", None)
                for hit in public.results
            )
        )
        self.assertFalse(
            any(
                hasattr(hit, "stage") and getattr(hit, "stage", None)
                for hit in public.results
                if isinstance(hit, WebSearchHit)
            )
        )
        self.assertIs(public.has_more, True)
        self.assertEqual(public.remaining, 25)
        self.assertEqual(public.next[0].tool, "fetch")  # type: ignore[index]
        self.assertNotIn("cursor", public.next[0].query)  # type: ignore[index]
        self.assertIn("Evaluate these results first", public.next[0].why)  # type: ignore[index]
        decoded = decode_web_search_overflow_cursor(public.cursor or "")
        stages = [item["stage"] for item in decoded["items"]]
        self.assertEqual(stages[:15], ["rankllm"] * 15)
        self.assertEqual(stages[15:], ["cross"] * 10)

    async def test_cursor_page_does_not_retrieve(self) -> None:
        encoded = encode_web_search_overflow_cursor(
            {
                "v": 1,
                "kind": "web_search_overflow",
                "query": "q",
                "intent": None,
                "query_variants": None,
                "citation_base": 15,
                "items": [
                    {"title": "Later", "url": "https://example.com/16", "stage": "rankllm"},
                ],
            }
        )
        execute = AsyncMock()
        ctx = AsyncMock()
        with patch(
            "kindly_web_search_mcp_server.search.service.execute_web_search",
            execute,
        ):
            out = await web_search(cursor=encoded, ctx=ctx)
        self.assertEqual(execute.await_count, 0)
        self.assertIsInstance(out, WebSearchPublicResponse)
        self.assertIsInstance(out.results[0], WebSearchOverflowHit)
        self.assertEqual(out.results[0].citation_id, "c16")
        dumped = out.model_dump()
        self.assertNotIn("snippet", dumped["results"][0])

    async def test_fewer_than_15_omits_cursor(self) -> None:
        items = [_result(index) for index in range(7)]
        public = to_public_web_search(
            query="q",
            intent=None,
            query_variants=None,
            hits=items,
            overflow_items=[],
            warnings=None,
        )
        dumped = public.model_dump()
        self.assertEqual(public.status, "ok")
        self.assertNotIn("cursor", dumped)
        self.assertNotIn("has_more", dumped)
        self.assertNotIn("remaining", dumped)
        self.assertEqual(public.next[0].query["urls"], [item.link for item in items])  # type: ignore[index]

    def test_empty_search_omits_next_and_cursor(self) -> None:
        public = to_public_web_search(
            query="q",
            intent=None,
            query_variants=None,
            hits=[],
            overflow_items=[],
            warnings=None,
        )
        dumped = public.model_dump()
        self.assertEqual(public.status, "empty")
        self.assertEqual(public.results, [])
        self.assertNotIn("next", dumped)
        self.assertNotIn("cursor", dumped)

    async def test_invalid_cursor_messages(self) -> None:
        ctx = AsyncMock()
        with self.assertRaises(ToolError) as invalid:
            await web_search(cursor="not-base64", ctx=ctx)
        self.assertIn("Invalid web_search cursor", str(invalid.exception))

        unsupported = base64.urlsafe_b64encode(
            json.dumps({"v": 2, "kind": "web_search_overflow", "items": []}).encode("utf-8")
        ).decode("ascii")
        with self.assertRaises(ToolError) as version:
            await web_search(cursor=unsupported, ctx=ctx)
        self.assertIn("Unsupported web_search cursor version", str(version.exception))

        empty = encode_web_search_overflow_cursor(
            {
                "v": 1,
                "kind": "web_search_overflow",
                "query": "q",
                "intent": None,
                "query_variants": None,
                "citation_base": 15,
                "items": [],
            }
        )
        with self.assertRaises(ToolError) as remaining:
            await web_search(cursor=empty, ctx=ctx)
        self.assertIn("web_search cursor has no remaining results", str(remaining.exception))

    async def test_temporal_filter_drops_overflow_outside_window(self) -> None:
        page = [_result(index, published_date="2026-06-01") for index in range(15)]
        stale = _result(15, published_date="2010-01-01")
        run, outcome = _run_with(
            page + [stale],
            options=SearchOptions(
                temporal=TemporalWindow(start=date(2026, 1, 1), end=date(2026, 12, 31)),
            ),
        )

        async def fake_rerank(
            _query: str, candidates: list[WebSearchResult], **_kwargs: object
        ) -> RerankOutput:
            pool = list(candidates)
            assert all(item.link != stale.link for item in pool)
            return RerankOutput(results=pool[:15])

        try:
            with patch(
                "kindly_web_search_mcp_server.search.ranking.rerank_results",
                fake_rerank,
            ):
                response = await rank_and_finalize(run, (outcome,), embedding_task=None)
            run.response = response
            public = to_public_web_search_from_run(run)
        finally:
            await run.http_client.aclose()

        self.assertIsNone(public.cursor)
        overflow_urls = [item.link for _, item in run.diagnostics.overflow_ranked]
        self.assertNotIn(stale.link, overflow_urls)

    def test_no_fabricated_fields(self) -> None:
        dumped = to_public_hit(
            WebSearchResult(title="T", link="https://example.com", snippet="S"),
            "c1",
        ).model_dump()
        for forbidden in (
            "score",
            "consensus",
            "providers",
            "fetch_hint",
            "link",
            "query_shaping",
        ):
            self.assertNotIn(forbidden, dumped)


if __name__ == "__main__":
    unittest.main()
