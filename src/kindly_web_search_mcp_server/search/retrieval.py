"""Structured branch/provider retrieval for web search."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
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
        if provider_name != "degoog":
            get_provider_health().mark_failure_with_type(provider_name, error_type="timeout")
        return provider_name, TimeoutError()
    except Exception as exc:
        if provider_name != "degoog":
            get_provider_health().mark_failure_with_type(provider_name, error_type=type(exc).__name__)
        return provider_name, exc


async def dispatch_branch(
    run: SearchRun,
    branch: QueryBranch,
    embedding_task: Awaitable[Sequence[float]] | None,
) -> BranchOutcome:
    started = time.monotonic()
    assigned_names = branch.provider_names
    if not assigned_names:
        return BranchOutcome(
            branch=branch,
            attempted_provider_names=(),
            skipped_provider_names=(),
            results=(),
            warnings=(),
            elapsed_seconds=0.0,
            provider_calls=(),
        )

    skipped: list[str] = []
    attempted: list[str] = []
    warnings_by_name: dict[str, ProviderWarning] = {}
    provider_calls: list[dict[str, Any]] = []
    _MAX_URLS = 32

    # Partition into healthy (attempted) and cooldown (skipped)
    tasks: list[asyncio.Task[tuple[str, Sequence[WebSearchResult] | BaseException, float]]] = []
    task_order: list[str] = []
    for name in assigned_names:
        if name != "degoog" and not get_provider_health().is_healthy(name):
            skipped.append(name)
            warnings_by_name[name] = _warning(name, "cooldown", "provider cooldown active")
            provider_calls.append(
                {
                    "provider": name,
                    "status": "skipped",
                    "branch_role": branch.role.value,
                    "error_type": "cooldown",
                    "error_message": "provider cooldown active",
                    "num_results_returned": 0,
                    "latency_ms": 0.0,
                    "candidate_urls": [],
                }
            )
            continue
        attempted.append(name)
        task_order.append(name)

        async def _invoke(n: str) -> tuple[str, Sequence[WebSearchResult] | BaseException, float]:
            call_started = time.monotonic()
            _name, value = await _call_provider(run, branch, n, embedding_task)
            return _name, value, (time.monotonic() - call_started) * 1000.0

        tasks.append(asyncio.create_task(_invoke(name), name=f"search.provider.{name}"))

    if not tasks:
        return BranchOutcome(
            branch=branch,
            attempted_provider_names=tuple(attempted),
            skipped_provider_names=tuple(skipped),
            results=(),
            warnings=tuple(warnings_by_name[name] for name in assigned_names if name in warnings_by_name),
            elapsed_seconds=time.monotonic() - started,
            provider_calls=tuple(provider_calls),
        )

    # Concurrent gather all healthy provider tasks
    try:
        gathered = await asyncio.gather(*tasks, return_exceptions=False)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    # Process in original provider_names order, canonical-deduplicate
    rows_by_name: dict[str, tuple[str, Sequence[WebSearchResult] | BaseException, float]] = {}
    for name, result in zip(task_order, gathered):
        rows_by_name[name] = result

    rows: OrderedDict[str, WebSearchResult] = OrderedDict()
    for name in assigned_names:
        if name not in rows_by_name:
            continue
        _name, value, latency_ms = rows_by_name[name]
        if isinstance(value, BaseException):
            error_type = "timeout" if isinstance(value, TimeoutError) else "provider_error"
            warnings_by_name[name] = _warning(name, error_type, error_type)
            provider_calls.append(
                {
                    "provider": name,
                    "status": "error",
                    "branch_role": branch.role.value,
                    "error_type": error_type,
                    "error_message": str(value)[:500],
                    "num_results_returned": 0,
                    "latency_ms": latency_ms,
                    "candidate_urls": [],
                }
            )
            continue
        for item in value:
            key = _canonical_url(item.link)
            if key not in rows:
                rows[key] = item
            else:
                existing = rows[key]
                existing.providers = sorted({*existing.providers, *(item.providers or []), name})
                existing.provider_count = len(existing.providers)
        provider_calls.append(
            {
                "provider": name,
                "status": "success",
                "branch_role": branch.role.value,
                "num_results_returned": len(value),
                "latency_ms": latency_ms,
                "candidate_urls": [item.link for item in value][:_MAX_URLS],
            }
        )

    warnings = tuple(warnings_by_name[name] for name in assigned_names if name in warnings_by_name)
    return BranchOutcome(
        branch=branch,
        attempted_provider_names=tuple(attempted),
        skipped_provider_names=tuple(skipped),
        results=tuple(rows.values()),
        warnings=warnings,
        elapsed_seconds=time.monotonic() - started,
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
            for branch in run.plan.branches:
                tasks.append(
                    group.create_task(
                        dispatch_branch(run, branch, embedding_task),
                        name=f"search.branch.{branch.role.value}",
                    )
                )
        outcomes = tuple(task.result() for task in tasks)
        run.outcomes = outcomes
        branch_rows: list[dict[str, Any]] = []
        for index, outcome in enumerate(outcomes):
            preview = branch_outcome_preview(outcome)
            preview["branch_index"] = index
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