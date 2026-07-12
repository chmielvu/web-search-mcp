"""Structured branch/provider retrieval for web search."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Sequence
from typing import Any

from urllib.parse import urlsplit, urlunsplit

from ..models import ProviderWarning, WebSearchResult
from ..telemetry.spans import get_tracer
from .contracts import BranchOutcome, QueryBranch, SearchRun
from .diagnostics import branch_outcome_preview
from .provider_health import get_provider_health
from .provider_registry import get_provider_adapter, get_provider_definition


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/"),
            parsed.query,
            "",
        )
    )


def _warning(provider: str, error_type: str, error: str) -> ProviderWarning:
    return ProviderWarning(provider=provider, error=error, error_type=error_type)


async def _call_provider(
    run: SearchRun,
    branch: QueryBranch,
    provider_name: str,
    embedding_task: Awaitable[Sequence[float]] | None,
) -> tuple[str, Sequence[WebSearchResult] | BaseException]:
    definition = get_provider_definition(provider_name)
    adapter = get_provider_adapter(provider_name)
    try:
        result = await asyncio.wait_for(
            adapter(
                branch.query,
                num_results=branch.max_results,
                options=run.request.options,
                arguments=(run.plan.provider_arguments.get(provider_name, {}) if run.plan else {}),
                http_client=run.http_client,
                query_embedding=embedding_task if definition.requires_embedding else None,
            ),
            timeout=definition.default_timeout_seconds,
        )
        normalized = [
            item.model_copy(update={"providers": sorted({*(item.providers or []), provider_name})})
            for item in result
        ]
        get_provider_health().mark_success(provider_name)
        return provider_name, normalized
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        get_provider_health().mark_failure_with_type(provider_name, error_type="timeout")
        return provider_name, TimeoutError()
    except Exception as exc:
        get_provider_health().mark_failure_with_type(provider_name, error_type=type(exc).__name__)
        return provider_name, exc


async def dispatch_branch(
    run: SearchRun,
    branch: QueryBranch,
    assigned_names: tuple[str, ...],
    embedding_task: Awaitable[Sequence[float]] | None,
) -> BranchOutcome:
    started = time.monotonic()
    skipped: list[str] = []
    attempted: list[str] = []
    warnings_by_name: dict[str, ProviderWarning] = {}
    provider_calls: list[dict[str, Any]] = []
    _MAX_URLS = 32

    async def _invoke(name: str) -> tuple[str, Sequence[WebSearchResult] | BaseException, float]:
        call_started = time.monotonic()
        _name, value = await _call_provider(run, branch, name, embedding_task)
        return _name, value, (time.monotonic() - call_started) * 1000.0

    task_names: dict[asyncio.Task[tuple[str, Sequence[WebSearchResult] | BaseException, float]], str] = (
        {}
    )
    for name in assigned_names:
        if not get_provider_health().is_healthy(name):
            skipped.append(name)
            warnings_by_name[name] = _warning(name, "cooldown", "provider cooldown active")
            provider_calls.append(
                {
                    "provider": name,
                    "status": "skipped",
                    "branch_target": branch.target.value,
                    "error_type": "cooldown",
                    "error_message": "provider cooldown active",
                    "num_results_returned": 0,
                    "latency_ms": 0.0,
                    "candidate_urls": [],
                }
            )
            continue
        attempted.append(name)
        task = asyncio.create_task(_invoke(name), name=f"search.provider.{name}")
        task_names[task] = name
    rows: list[WebSearchResult] = []
    canonical: set[str] = set()
    pending = set(task_names)
    cancelled = False
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                name, value, latency_ms = await task
                if isinstance(value, BaseException):
                    error_type = "timeout" if isinstance(value, TimeoutError) else "provider_error"
                    warnings_by_name[name] = _warning(name, error_type, error_type)
                    provider_calls.append(
                        {
                            "provider": name,
                            "status": "error",
                            "branch_target": branch.target.value,
                            "error_type": error_type,
                            "error_message": str(value)[:500],
                            "num_results_returned": 0,
                            "latency_ms": latency_ms,
                            "candidate_urls": [],
                        }
                    )
                    continue
                for item in value:
                    rows.append(item)
                    canonical.add(_canonical_url(item.link))
                provider_calls.append(
                    {
                        "provider": name,
                        "status": "success",
                        "branch_target": branch.target.value,
                        "num_results_returned": len(value),
                        "latency_ms": latency_ms,
                        "candidate_urls": [item.link for item in value][:_MAX_URLS],
                    }
                )
            if len(canonical) >= branch.max_results:
                cancelled = bool(pending)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                pending.clear()
    except asyncio.CancelledError:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise
    warnings = tuple(warnings_by_name[name] for name in assigned_names if name in warnings_by_name)
    return BranchOutcome(
        branch=branch,
        attempted_provider_names=tuple(attempted),
        skipped_provider_names=tuple(skipped),
        results=tuple(rows),
        warnings=warnings,
        elapsed_seconds=time.monotonic() - started,
        cancelled=cancelled,
        provider_calls=tuple(provider_calls),
    )


async def retrieve_branches(
    run: SearchRun,
    *,
    embedding_task: asyncio.Task[Sequence[float]] | None,
) -> tuple[BranchOutcome, ...]:
    if run.plan is None:
        raise RuntimeError("Search must be planned before retrieval")
    tracer = get_tracer()
    retrieve_started = time.monotonic()
    with tracer.start_as_current_span("search.retrieve") as span:
        span.set_attribute("search.run_key", run.run_key)
        span.set_attribute("search.branch_count", len(run.plan.branches))
        tasks: list[asyncio.Task[BranchOutcome]] = []
        async with asyncio.TaskGroup() as group:
            for branch_index, branch in enumerate(run.plan.branches):
                assigned = tuple(
                    name
                    for name in run.plan.selected_provider_names
                    if branch.target in get_provider_definition(name).targets
                )
                tasks.append(
                    group.create_task(
                        dispatch_branch(run, branch, assigned, embedding_task),
                        name=f"search.branch.{branch.target.value}",
                    )
                )
        outcomes = tuple(task.result() for task in tasks)
        run.outcomes = outcomes
        branch_rows: list[dict[str, Any]] = []
        for index, outcome in enumerate(outcomes):
            preview = branch_outcome_preview(outcome)
            preview["branch_index"] = index
            if run.plan is not None:
                assigned = tuple(
                    name
                    for name in run.plan.selected_provider_names
                    if outcome.branch.target in get_provider_definition(name).targets
                )
                preview["assigned_providers"] = list(assigned)
            calls_with_index = []
            for call in outcome.provider_calls:
                row = dict(call)
                row["branch_index"] = index
                calls_with_index.append(row)
            preview["provider_calls"] = calls_with_index
            branch_rows.append(preview)
        run.diagnostics.branch_results = branch_rows
        run.diagnostics.phase_timings["search.retrieve"] = (
            time.monotonic() - retrieve_started
        ) * 1000.0
        span.set_attribute("search.provider_outcome_count", len(outcomes))
        return outcomes
