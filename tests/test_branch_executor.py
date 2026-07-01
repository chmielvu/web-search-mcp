from __future__ import annotations

import asyncio
from unittest.mock import patch


def _build_provider_plan():
    from kindly_web_search_mcp_server.search.options import SearchOptions
    from kindly_web_search_mcp_server.search.provider_options import (
        ProviderOptionBundle,
        ProviderOptionSet,
    )
    from kindly_web_search_mcp_server.search.provider_plan import ProviderExecutionPlan

    bundles = {
        "searxng": ProviderOptionBundle(provider_name="searxng"),
        "brave": ProviderOptionBundle(provider_name="brave"),
    }
    return ProviderExecutionPlan(
        intent="general",
        policy_version="1.0",
        provider_names=("searxng", "brave"),
        provider_weights={"searxng": 1.0, "brave": 1.0},
        search_options=SearchOptions(),
        options=ProviderOptionSet(bundles=bundles),
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
        captured_calls: list[dict] = []

        async def _mock_dispatch(
            query: str,
            providers: list,
            http_client,
            *,
            num_results: int,
            deadline_seconds: float,
            search_options=None,
            provider_options_by_name=None,
            run_key=None,
        ) -> list:
            nonlocal active, peak
            captured_calls.append(
                {
                    "query": query,
                    "num_results": num_results,
                    "provider_options_by_name": provider_options_by_name,
                }
            )
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

        with patch(
            "kindly_web_search_mcp_server.search.branch_executor.dispatch_providers",
            side_effect=_mock_dispatch,
        ):
            task = asyncio.create_task(
                execute_search_branches(
                    [
                        SearchBranchSpec(
                            index=0,
                            intent="general",
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
                            intent="general",
                            query="branch two",
                            branch_type="comparative",
                            weight=0.9,
                            providers=["brave"],
                            provider_options_by_name=provider_plan.options.bundles,
                            max_results=2,
                            reason="second",
                        ),
                        SearchBranchSpec(
                            index=2,
                            intent="general",
                            query="branch three",
                            branch_type="entity_expanded",
                            weight=0.8,
                            providers=None,
                            provider_options_by_name=provider_plan.options.bundles,
                            max_results=2,
                            reason="third",
                        ),
                    ],
                    http_client=None,  # type: ignore[arg-type]
                    search_options=None,
                    provider_plan=provider_plan,
                    max_concurrency=2,
                )
            )

            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert peak == 2
            release.set()
            batch = await asyncio.wait_for(task, timeout=1.0)

        assert [c["query"] for c in captured_calls] == [
            "branch one",
            "branch two",
            "branch three",
        ]
        assert batch.branch_queries == ["branch one", "branch two", "branch three"]
        assert batch.list_weights == [1.2, 0.9, 0.8]
        assert batch.branch_metadata[0]["branch_index"] == 0
        assert batch.branch_metadata[1]["branch_type"] == "comparative"
        assert batch.branch_metadata[2]["branch_result_count"] == 1
        assert captured_calls[0]["provider_options_by_name"]["searxng"].provider_name == "searxng"

    asyncio.run(_run())


def test_execute_search_branches_keeps_completed_results_when_one_branch_times_out() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search.branch_executor import (
        SearchBranchSpec,
        execute_search_branches,
    )

    async def _run() -> None:
        provider_plan = _build_provider_plan()

        async def _mock_dispatch(
            query: str,
            providers: list,
            http_client,
            *,
            num_results: int,
            deadline_seconds: float,
            search_options=None,
            provider_options_by_name=None,
            run_key=None,
            branch_index=None,
            branch_attempt_id=None,
            tool_call_id=None,
        ) -> list:
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

        with patch(
            "kindly_web_search_mcp_server.search.branch_executor.dispatch_providers",
            side_effect=_mock_dispatch,
        ), patch(
            "kindly_web_search_mcp_server.utils.task_scope.DEFAULT_DRAIN_SECONDS",
            0.0,
        ):
            batch = await execute_search_branches(
                [
                    SearchBranchSpec(
                        index=0,
                        intent="general",
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
                        intent="general",
                        query="fast branch",
                        branch_type="related",
                        weight=1.0,
                        providers=["searxng"],
                        provider_options_by_name=provider_plan.options.bundles,
                        max_results=2,
                        reason="fast",
                    ),
                ],
                http_client=None,  # type: ignore[arg-type]
                search_options=None,
                provider_plan=provider_plan,
                max_concurrency=2,
                deadline_seconds=0.01,
            )

        assert batch.branch_queries == ["slow branch", "fast branch"]
        assert batch.result_lists[0] == []
        assert [result.title for result in batch.result_lists[1]] == ["fast branch"]

    asyncio.run(_run())


def test_execute_search_branches_keeps_provider_partials_after_inner_deadline() -> None:
    from kindly_web_search_mcp_server.models import WebSearchResult
    from kindly_web_search_mcp_server.search import branch_executor
    from kindly_web_search_mcp_server.search.branch_executor import (
        SearchBranchSpec,
        execute_search_branches,
    )
    from kindly_web_search_mcp_server.utils import task_scope

    async def _run() -> None:
        provider_plan = _build_provider_plan()
        captured_deadlines: list[float] = []

        async def _mock_dispatch(
            query: str,
            providers: list,
            http_client,
            *,
            num_results: int,
            deadline_seconds: float,
            search_options=None,
            provider_options_by_name=None,
            run_key=None,
            branch_index=None,
            branch_attempt_id=None,
            tool_call_id=None,
        ) -> list:
            captured_deadlines.append(deadline_seconds)
            await asyncio.sleep(0.005)
            return [
                WebSearchResult(
                    title="partial branch",
                    link="https://example.com/partial-branch",
                    snippet="partial branch",
                    providers=["searxng"],
                )
            ]

        with (
            patch.object(branch_executor.settings, "provider_group_deadline_seconds", 0.01),
            patch.object(task_scope, "DEFAULT_DRAIN_SECONDS", 0.005),
            patch(
                "kindly_web_search_mcp_server.search.branch_executor.dispatch_providers",
                side_effect=_mock_dispatch,
            ),
        ):
            batch = await execute_search_branches(
                [
                    SearchBranchSpec(
                        index=0,
                        intent="general",
                        query="partial branch",
                        branch_type="related",
                        weight=1.0,
                        providers=["searxng"],
                        provider_options_by_name=provider_plan.options.bundles,
                        max_results=2,
                        reason="partial",
                    )
                ],
                http_client=None,  # type: ignore[arg-type]
                search_options=None,
                provider_plan=provider_plan,
                max_concurrency=1,
            )

        assert captured_deadlines == [0.01]
        assert [result.title for result in batch.result_lists[0]] == ["partial branch"]

    asyncio.run(_run())
