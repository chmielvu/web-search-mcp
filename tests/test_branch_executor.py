from __future__ import annotations

import asyncio

import httpx


def _build_provider_plan():
    from kindly_web_search_mcp_server.search.context import SearchContext
    from kindly_web_search_mcp_server.search.options import SearchOptions
    from kindly_web_search_mcp_server.search.profiles.resolve import resolve_search_profile
    from kindly_web_search_mcp_server.search.provider_plan import (
        build_provider_execution_plan,
    )

    profile = resolve_search_profile("general")
    context = SearchContext(
        raw_query="FastAPI docs",
        normalized_query="FastAPI docs",
        research_goal=None,
        session_id="session-1",
        intent="general",
        confidence=0.9,
        should_decompose=False,
        rationale="clear request",
        entities=(),
        must_keep_terms=(),
        num_results=5,
        search_options=SearchOptions(),
        profile_name="general",
    )
    return build_provider_execution_plan(
        profile=profile,
        intent=context.intent,
        public_options=context.search_options,
    )


def test_execute_search_branches_caps_concurrency_and_carries_metadata() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search.branch_executor import (
        SearchBranchSpec,
        execute_search_branches,
    )

    async def _run() -> None:
        provider_plan = _build_provider_plan()
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        queries: list[tuple[str, int, dict[str, object] | None]] = []

        async def _runner(
            query: str,
            *,
            num_results: int,
            http_client: httpx.AsyncClient,
            diagnostics,
            search_options,
            provider_plan=None,
            provider_options_by_name=None,
            run_key=None,
        ) -> list[WebSearchResult]:
            nonlocal active, peak
            queries.append((query, num_results, provider_options_by_name))
            active += 1
            peak = max(peak, active)
            if peak >= 2:
                started.set()
            await release.wait()
            active -= 1
            return [
                WebSearchResult(
                    title=query,
                    link=f"https://example.com/{query.replace(' ', '-')}",
                    snippet=query,
                    providers=["searxng"],
                )
            ]

        async with httpx.AsyncClient() as client:
            task = asyncio.create_task(
                execute_search_branches(
                    [
                        SearchBranchSpec(
                        index=0,
                        query="branch one",
                        branch_type="related",
                        weight=1.2,
                        providers=["searxng"],
                        provider_options_by_name=provider_plan.options.bundles,
                        max_results=2,
                        reason="first",
                    ),
                        SearchBranchSpec(
                            index=1,
                            query="branch two",
                            branch_type="comparative",
                            weight=0.9,
                            providers=["gemini"],
                            provider_options_by_name=provider_plan.options.bundles,
                            max_results=2,
                            reason="second",
                        ),
                        SearchBranchSpec(
                            index=2,
                            query="branch three",
                            branch_type="entity_expanded",
                            weight=0.8,
                            providers=None,
                            provider_options_by_name=provider_plan.options.bundles,
                            max_results=2,
                            reason="third",
                        ),
                    ],
                    http_client=client,
                    diagnostics=None,
                    search_options=None,
                    provider_plan=provider_plan,
                    search_runner=_runner,
                    max_concurrency=2,
                )
            )

            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert peak == 2
            release.set()
            batch = await asyncio.wait_for(task, timeout=1.0)

        assert [query for query, _, _ in queries] == [
            "branch one",
            "branch two",
            "branch three",
        ]
        assert batch.branch_queries == ["branch one", "branch two", "branch three"]
        assert batch.list_weights == [1.2, 0.9, 0.8]
        assert batch.branch_metadata[0]["branch_index"] == 0
        assert batch.branch_metadata[1]["branch_type"] == "comparative"
        assert batch.branch_metadata[2]["branch_result_count"] == 1
        assert queries[0][2]["searxng"].provider_name == "searxng"

    asyncio.run(_run())


def test_execute_search_branches_keeps_completed_results_when_one_branch_times_out() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search.branch_executor import (
        SearchBranchSpec,
        execute_search_branches,
    )

    async def _run() -> None:
        provider_plan = _build_provider_plan()

        async def _runner(
            query: str,
            *,
            num_results: int,
            http_client: httpx.AsyncClient,
            diagnostics,
            search_options,
            provider_plan=None,
            provider_options_by_name=None,
            run_key=None,
        ) -> list[WebSearchResult]:
            if query == "slow branch":
                await asyncio.sleep(0.2)
                return [
                    WebSearchResult(
                        title="slow branch",
                        link="https://example.com/slow-branch",
                        snippet="slow branch",
                        providers=["searxng"],
                    )
                ]
            return [
                WebSearchResult(
                    title="fast branch",
                    link="https://example.com/fast-branch",
                    snippet="fast branch",
                    providers=["searxng"],
                )
            ]

        async with httpx.AsyncClient() as client:
            batch = await execute_search_branches(
                [
                    SearchBranchSpec(
                        index=0,
                        query="slow branch",
                        branch_type="related",
                        weight=1.0,
                        providers=["searxng"],
                        provider_options_by_name=provider_plan.options.bundles,
                        max_results=2,
                        reason="slow",
                    ),
                    SearchBranchSpec(
                        index=1,
                        query="fast branch",
                        branch_type="related",
                        weight=1.0,
                        providers=["searxng"],
                        provider_options_by_name=provider_plan.options.bundles,
                        max_results=2,
                        reason="fast",
                    ),
                ],
                http_client=client,
                diagnostics=None,
                search_options=None,
                provider_plan=provider_plan,
                search_runner=_runner,
                max_concurrency=2,
                deadline_seconds=0.01,
            )

        assert batch.branch_queries == ["slow branch", "fast branch"]
        assert batch.result_lists[0] == []
        assert [result.title for result in batch.result_lists[1]] == [
            "fast branch"
        ]

    asyncio.run(_run())
