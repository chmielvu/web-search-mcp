"""Concurrency semantics for `search.retrieval.retrieve_branches`.

Migration note (2026-07-22): the prior tests referenced the deleted
`search.branch_executor.ProviderExecutionPlan` /
`branch_executor.execute_search_branches` API and asserted on the
`BranchExecutionBatch.result_lists` /
`BranchExecutionBatch.provider_options_by_name` shape. Both the module and
those shapes were replaced by `search.retrieval.retrieve_branches()`,
`search.contracts.QueryBranch`, and `search.contracts.BranchOutcome` in the
2026-07-20 safe-refactor. These tests now patch
`retrieval._call_provider` (the per-provider seam) and assert on the
`BranchOutcome` / `DiagnosticsCollector` surface exposed by the current
code.

Pattern follows `tests/test_retrieval_budget.py` (canonical migration
reference): builds a `SearchRun` via `SimpleNamespace` since
`retrieve_branches` only reads `run.plan.branches`,
`run.plan.provider_arguments`, `run.diagnostics`, `run.outcomes`, and
`run.run_key` — none of which require a real `SearchPlan` instance.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search import retrieval
from kindly_web_search_mcp_server.search.contracts import (
    BranchOutcome,
    BranchRole,
    DiagnosticsCollector,
    QueryBranch,
)


def _result(provider: str, title: str, snippet: str = "snippet") -> WebSearchResult:
    return WebSearchResult(
        title=title,
        link=f"https://{provider}.example/{title.replace(' ', '-')}",
        snippet=snippet,
        providers=[provider],
        provider_count=1,
    )


def _branch(*providers: str, role: BranchRole = BranchRole.ORIGINAL_FREE) -> QueryBranch:
    return QueryBranch(
        role=role,
        query="legacy branch executor contract",
        provider_names=providers,
        why="test",
        support_terms=(),
        max_results=5,
    )


def _make_run(branches: QueryBranch | tuple[QueryBranch, ...]) -> Any:
    if isinstance(branches, QueryBranch):
        branches = (branches,)
    return SimpleNamespace(
        plan=SimpleNamespace(branches=tuple(branches), provider_arguments={}),
        request=SimpleNamespace(options=None),
        http_client=None,
        run_key="test-branch-executor",
        diagnostics=DiagnosticsCollector(),
        outcomes=(),
    )


@pytest.mark.asyncio
async def test_retrieve_branches_two_providers_both_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two-provider branch sees both provider names in
    `attempted_provider_names` and both call_provider records in
    `provider_calls`, in registration order.
    """
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 5.0)

    async def fast_call_provider(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[str, list[WebSearchResult]]:
        await asyncio.sleep(0)
        return provider_name, [_result(provider_name, "ok")]

    monkeypatch.setattr(retrieval, "_call_provider", fast_call_provider)

    run = _make_run(_branch("brave", "tavily"))
    outcomes: tuple[BranchOutcome, ...] = await retrieval.retrieve_branches(
        run, embedding_task=None
    )

    assert outcomes[0].branch.provider_names == ("brave", "tavily")
    assert outcomes[0].attempted_provider_names == ("brave", "tavily")
    assert [c["provider"] for c in outcomes[0].provider_calls] == ["brave", "tavily"]
    assert [c["status"] for c in outcomes[0].provider_calls] == ["success", "success"]
    assert [r.title for r in outcomes[0].results] == ["ok", "ok"]


@pytest.mark.asyncio
async def test_retrieve_branches_all_branches_return_results_regardless_of_slowness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two branches with different per-provider latencies both complete
    within the budget and report their respective provider results.
    """
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 2.0)

    async def varied(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[str, list[WebSearchResult]]:
        if provider_name == "slow":
            await asyncio.sleep(0.05)
        return provider_name, [_result(provider_name, f"{provider_name} branch")]

    monkeypatch.setattr(retrieval, "_call_provider", varied)

    fast = _branch("fast")
    slow = _branch("slow")
    run = _make_run((fast, slow))

    started = time.monotonic()
    outcomes = await retrieval.retrieve_branches(run, embedding_task=None)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert outcomes[0].branch.provider_names == ("fast",)
    assert outcomes[1].branch.provider_names == ("slow",)
    assert [r.title for r in outcomes[0].results] == ["fast branch"]
    assert [r.title for r in outcomes[1].results] == ["slow branch"]


@pytest.mark.asyncio
async def test_retrieve_branches_partial_results_preserved_across_budget_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branch with one fast + one slow provider loses the slow provider
    to the retrieve budget, but the fast provider's result is preserved on
    the BranchOutcome and the slow one is recorded as incomplete in
    `provider_calls`.
    """
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.1)

    async def mixed(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[str, list[WebSearchResult]]:
        if provider_name == "fast":
            await asyncio.sleep(0)
            return provider_name, [_result("fast", "partial branch")]
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            raise
        raise AssertionError("slow provider was not cancelled")

    monkeypatch.setattr(retrieval, "_call_provider", mixed)

    run = _make_run(_branch("fast", "slow"))
    outcomes = await retrieval.retrieve_branches(run, embedding_task=None)

    # Fast provider's partial result survives.
    assert [r.title for r in outcomes[0].results] == ["partial branch"]
    # Both providers recorded: fast=success, slow=incomplete.
    statuses = [c["status"] for c in outcomes[0].provider_calls]
    assert statuses == ["success", "incomplete"]
    # Diagnostics flag set.
    assert run.diagnostics.enrichment["retrieve_budget_exceeded"] is True
    # Slow provider carries a retrieve_budget error_type.
    slow_call = next(c for c in outcomes[0].provider_calls if c["provider"] == "slow")
    assert slow_call.get("error_type") == "retrieve_budget"
