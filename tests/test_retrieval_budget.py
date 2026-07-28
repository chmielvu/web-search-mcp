from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search import retrieval
from kindly_web_search_mcp_server.search.contracts import (
    BranchRole,
    DiagnosticsCollector,
    QueryBranch,
)
from kindly_web_search_mcp_server.search.providers.base import ProviderRequestMetadata


def _branch(*providers: str) -> QueryBranch:
    return QueryBranch(
        role=BranchRole.ORIGINAL_FREE,
        query="retrieve budget contract",
        provider_names=providers,
        max_results=5,
    )


def _run(*branches: QueryBranch) -> Any:
    return SimpleNamespace(
        plan=SimpleNamespace(branches=branches, provider_arguments={}, understanding=None),
        request=SimpleNamespace(options=None),
        http_client=None,
        run_key="retrieve-budget-test",
        diagnostics=DiagnosticsCollector(),
        outcomes=(),
    )


def _result(provider: str) -> WebSearchResult:
    return WebSearchResult(
        title=provider,
        link=f"https://{provider}.example/result",
        snippet="completed within the retrieve budget",
        providers=[provider],
        provider_count=1,
    )


@pytest.mark.asyncio
async def test_retrieve_budget_keeps_done_and_marks_pending_in_schedule_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow_cancelled = asyncio.Event()

    async def call_provider(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[
        str,
        list[WebSearchResult] | BaseException,
        ProviderRequestMetadata,
        str,
    ]:
        if provider_name == "fast":
            return (
                provider_name,
                [_result(provider_name)],
                ProviderRequestMetadata(provider=provider_name, result_class="nonempty"),
                _branch.query,
            )
        if provider_name == "failed":
            return (
                provider_name,
                RuntimeError("provider unavailable"),
                ProviderRequestMetadata(
                    provider=provider_name,
                    result_class="error",
                    error_type="provider_error",
                    error_summary="provider unavailable",
                ),
                _branch.query,
            )
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            slow_cancelled.set()
            raise
        raise AssertionError("slow provider was not cancelled")

    monkeypatch.setattr(retrieval, "_call_provider", call_provider)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.05)
    run = _run(_branch("fast", "failed", "slow"))

    started = time.monotonic()
    outcomes = await retrieval.retrieve_branches(run, embedding_task=None)
    elapsed = time.monotonic() - started

    calls = outcomes[0].provider_calls
    assert [call["provider"] for call in calls] == ["fast", "failed", "slow"]
    assert [call["status"] for call in calls] == ["success", "error", "incomplete"]
    assert [call.get("error_type") for call in calls] == [
        None,
        "provider_error",
        "retrieve_budget",
    ]
    assert [result.title for result in outcomes[0].results] == ["fast"]
    assert outcomes[0].attempted_provider_names == ("fast", "failed", "slow")
    assert outcomes[0].skipped_provider_names == ()
    assert slow_cancelled.is_set()
    assert elapsed < 0.5
    assert run.diagnostics.enrichment == {
        "retrieve_budget_seconds": 0.05,
        "retrieve_budget_exceeded": True,
    }


@pytest.mark.asyncio
async def test_retrieve_budget_returns_early_when_all_tasks_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def call_provider(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[str, list[WebSearchResult], ProviderRequestMetadata, str]:
        await asyncio.sleep(0.01)
        return (
            provider_name,
            [_result(provider_name)],
            ProviderRequestMetadata(provider=provider_name, result_class="nonempty"),
            _branch.query,
        )

    monkeypatch.setattr(retrieval, "_call_provider", call_provider)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 1.0)
    run = _run(_branch("first", "second"))

    started = time.monotonic()
    outcomes = await retrieval.retrieve_branches(run, embedding_task=None)

    assert time.monotonic() - started < 0.5
    assert [call["status"] for call in outcomes[0].provider_calls] == ["success", "success"]
    assert run.diagnostics.enrichment["retrieve_budget_exceeded"] is False


@pytest.mark.asyncio
async def test_retrieve_caller_cancellation_drains_every_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = {name: asyncio.Event() for name in ("first", "second")}
    cancelled = {name: asyncio.Event() for name in ("first", "second")}

    async def call_provider(
        _run: Any,
        _branch: QueryBranch,
        provider_name: str,
        _embedding_task: Any,
        *,
        retrieve_deadline: float = 0.0,
    ) -> tuple[str, list[WebSearchResult], ProviderRequestMetadata, str]:
        started[provider_name].set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled[provider_name].set()
            raise
        raise AssertionError("provider child was not cancelled")

    monkeypatch.setattr(retrieval, "_call_provider", call_provider)
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 30.0)
    task = asyncio.create_task(
        retrieval.retrieve_branches(_run(_branch("first", "second")), embedding_task=None)
    )
    await asyncio.gather(*(event.wait() for event in started.values()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert all(event.is_set() for event in cancelled.values())


@pytest.mark.asyncio
async def test_empty_provider_plan_returns_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.0)
    run = _run(_branch())

    outcomes = await retrieval.retrieve_branches(run, embedding_task=None)

    assert outcomes[0].provider_calls == ()
    assert outcomes[0].attempted_provider_names == ()
    assert run.diagnostics.enrichment["retrieve_budget_exceeded"] is False


@pytest.mark.asyncio
async def test_call_provider_uses_live_budget_not_catalog_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Advisor: catalog default_timeout_seconds is import-time; clamp must
    re-read settings.search_retrieve_budget_seconds on every call.
    """
    import kindly_web_search_mcp_server.search.provider_catalog as catalog

    # Snapshot at import may still hold the original budget.
    snapshot = catalog.PROVIDER_DEFINITIONS_LIST[0].default_timeout_seconds
    assert snapshot > 0

    # Override AFTER catalog materialization — live path must see 0.05.
    monkeypatch.setattr(retrieval.settings, "search_retrieve_budget_seconds", 0.05)

    seen_timeouts: list[float] = []

    async def slow_adapter(*_a: Any, **_k: Any) -> list[WebSearchResult]:
        await asyncio.sleep(10)
        return [_result("live")]

    def fake_def(name: str) -> Any:
        return SimpleNamespace(
            name=name,
            requires_embedding=False,
            # Stale catalog snapshot deliberately larger than live budget.
            default_timeout_seconds=snapshot,
        )

    monkeypatch.setattr(retrieval, "get_provider_definition", fake_def)
    monkeypatch.setattr(retrieval, "get_provider_adapter", lambda _n: slow_adapter)

    # Patch wait_for to record the timeout arg without changing behavior.
    real_wait_for = asyncio.wait_for

    async def tracking_wait_for(aw: Any, *, timeout: float | None = None) -> Any:
        if timeout is not None:
            seen_timeouts.append(float(timeout))
        return await real_wait_for(aw, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", tracking_wait_for)

    run = _run(_branch("live"))
    started = time.monotonic()
    name, value, _metadata, _request_query = await retrieval._call_provider(
        run,
        run.plan.branches[0],
        "live",
        None,
        retrieve_deadline=time.monotonic() + 30.0,
    )
    elapsed = time.monotonic() - started

    assert name == "live"
    assert isinstance(value, TimeoutError)
    assert elapsed < 1.0
    assert seen_timeouts, "wait_for should have been called"
    assert seen_timeouts[0] <= 0.05 + 1e-6
    assert seen_timeouts[0] < snapshot / 2
