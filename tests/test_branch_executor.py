from __future__ import annotations

import asyncio

import httpx


def test_execute_search_branches_caps_concurrency_and_carries_metadata() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search.branch_executor import (
        SearchBranchSpec,
        execute_search_branches,
    )

    async def _run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        queries: list[tuple[str, int, list[str] | None]] = []

        async def _runner(
            query: str,
            *,
            num_results: int,
            http_client: httpx.AsyncClient,
            diagnostics,
            providers,
            search_options,
        ) -> list[WebSearchResult]:
            nonlocal active, peak
            queries.append((query, num_results, providers))
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
                    providers=providers or [],
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
                            max_results=2,
                            reason="first",
                        ),
                        SearchBranchSpec(
                            index=1,
                            query="branch two",
                            branch_type="comparative",
                            weight=0.9,
                            providers=["gemini"],
                            max_results=2,
                            reason="second",
                        ),
                        SearchBranchSpec(
                            index=2,
                            query="branch three",
                            branch_type="entity_expanded",
                            weight=0.8,
                            providers=None,
                            max_results=2,
                            reason="third",
                        ),
                    ],
                    http_client=client,
                    diagnostics=None,
                    search_options=None,
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

    asyncio.run(_run())
