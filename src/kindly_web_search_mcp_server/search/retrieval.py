"""Structured branch/provider retrieval for web search."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Sequence
from typing import Any

from urllib.parse import urlsplit, urlunsplit

from ..models import ProviderWarning, WebSearchResult
from ..settings import settings
from ..telemetry.spans import get_tracer
from ..utils.task_scope import cancel_and_drain_tasks
from .contracts import BranchOutcome, ProviderRankedResults, QueryBranch, SearchRun
from .diagnostics import branch_outcome_preview
from .provider_registry import get_provider_adapter, get_provider_definition


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, parsed.query, ""))


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
        return provider_name, normalized
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return provider_name, TimeoutError()
    except Exception as exc:
        return provider_name, exc


_MAX_URLS = 32


def _record_provider_result(
    *,
    branch: QueryBranch,
    branch_index: int,
    name: str,
    value: Sequence[WebSearchResult] | BaseException | None,
    latency_ms: float,
    rows: OrderedDict[str, WebSearchResult],
    warnings_by_name: dict[str, ProviderWarning],
    provider_calls: list[dict[str, Any]],
    provider_ranked_results_list: list[ProviderRankedResults],
    status_override: str | None = None,
    error_type_override: str | None = None,
    error_message_override: str | None = None,
) -> None:
    if status_override == "incomplete":
        warnings_by_name[name] = _warning(name, "retrieve_budget", "retrieve budget exhausted")
        provider_calls.append(
            {
                "provider": name,
                "status": "incomplete",
                "branch_role": branch.role.value,
                "error_type": error_type_override or "retrieve_budget",
                "error_message": error_message_override or "retrieve budget exhausted",
                "num_results_returned": 0,
                "latency_ms": latency_ms,
                "candidate_urls": [],
            }
        )
        return

    if value is None:
        return

    if isinstance(value, BaseException):
        error_type = error_type_override or (
            "timeout" if isinstance(value, TimeoutError) else "provider_error"
        )
        warnings_by_name[name] = _warning(name, error_type, error_type)
        provider_calls.append(
            {
                "provider": name,
                "status": status_override or "error",
                "branch_role": branch.role.value,
                "error_type": error_type,
                "error_message": (error_message_override or str(value))[:500],
                "num_results_returned": 0,
                "latency_ms": latency_ms,
                "candidate_urls": [],
            }
        )
        return

    seen_urls = set()
    deduped_results = []
    for item in value:
        url_key = _canonical_url(item.link)
        if url_key not in seen_urls:
            seen_urls.add(url_key)
            deduped_results.append(item)
    provider_ranked_results_list.append(
        ProviderRankedResults(
            branch_index=branch_index,
            branch_role=branch.role,
            provider_name=name,
            results=tuple(deduped_results),
        )
    )

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


def _assemble_branch_outcome(
    branch: QueryBranch,
    *,
    assigned_names: tuple[str, ...],
    skipped: tuple[str, ...],
    attempted: tuple[str, ...],
    rows: OrderedDict[str, WebSearchResult],
    warnings_by_name: dict[str, ProviderWarning],
    provider_calls: list[dict[str, Any]],
    provider_ranked_results: tuple[ProviderRankedResults, ...],
    elapsed_seconds: float,
) -> BranchOutcome:
    warnings = tuple(warnings_by_name[name] for name in assigned_names if name in warnings_by_name)
    return BranchOutcome(
        branch=branch,
        attempted_provider_names=attempted,
        skipped_provider_names=skipped,
        results=tuple(rows.values()),
        warnings=warnings,
        elapsed_seconds=elapsed_seconds,
        provider_calls=tuple(provider_calls),
        provider_ranked_results=provider_ranked_results,
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
    retrieve_budget_seconds = settings.search_retrieve_budget_seconds

    with tracer.start_as_current_span("search.retrieve") as span:
        span.set_attribute("search.run_key", run.run_key)
        span.set_attribute("search.branch_count", len(run.plan.branches))
        span.set_attribute("search.retrieve_budget_seconds", retrieve_budget_seconds)

        branch_assigned: list[tuple[str, ...]] = []
        branch_attempted: list[list[str]] = []
        branch_rows: list[OrderedDict[str, WebSearchResult]] = []
        branch_warnings: list[dict[str, ProviderWarning]] = []
        branch_calls: list[list[dict[str, Any]]] = []
        branch_provider_ranked_results: list[list[ProviderRankedResults]] = [
            [] for _ in range(len(run.plan.branches))
        ]

        tasks: list[asyncio.Task[tuple[str, Sequence[WebSearchResult] | BaseException, float]]] = []
        slot_by_task: dict[asyncio.Task[Any], tuple[int, str]] = {}
        started_at: dict[str, float] = {}
        for branch_index, branch in enumerate(run.plan.branches):
            assigned_names = branch.provider_names
            branch_assigned.append(assigned_names)
            attempted: list[str] = []
            branch_attempted.append(attempted)
            branch_rows.append(OrderedDict())
            branch_warnings.append({})
            branch_calls.append([])

            for name in assigned_names:

                async def _invoke(
                    b: QueryBranch = branch,
                    n: str = name,
                ) -> tuple[str, Sequence[WebSearchResult] | BaseException, float]:
                    call_started = time.monotonic()
                    started_at[n] = call_started
                    provider_name, value = await _call_provider(run, b, n, embedding_task)
                    return provider_name, value, (time.monotonic() - call_started) * 1000.0

                task = asyncio.create_task(_invoke(), name=f"search.provider.{name}")
                tasks.append(task)
                slot_by_task[task] = (branch_index, name)
                attempted.append(name)

        done: set[asyncio.Task[Any]] = set()
        pending: set[asyncio.Task[Any]] = set()
        try:
            if tasks:
                wait_timeout = max(0.0, retrieve_budget_seconds - 0.5)
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=wait_timeout,
                )
            retrieve_budget_exceeded = bool(pending)

            if pending:
                await cancel_and_drain_tasks(pending)

            for task in tasks:
                branch_index, provider_name = slot_by_task[task]
                branch = run.plan.branches[branch_index]
                rows = branch_rows[branch_index]
                warnings_by_name = branch_warnings[branch_index]
                calls = branch_calls[branch_index]

                if task in pending:
                    elapsed_ms = (
                        time.monotonic() - started_at.get(provider_name, retrieve_started)
                    ) * 1000.0
                    _record_provider_result(
                        branch=branch,
                        branch_index=branch_index,
                        name=provider_name,
                        value=None,
                        latency_ms=elapsed_ms,
                        rows=rows,
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        provider_ranked_results_list=branch_provider_ranked_results[branch_index],
                        status_override="incomplete",
                    )
                    continue

                if task not in done:
                    raise RuntimeError("Provider task missing from asyncio.wait partition")

                try:
                    _returned_name, value, latency_ms = task.result()
                except asyncio.CancelledError as exc:
                    elapsed_ms = (
                        time.monotonic() - started_at.get(provider_name, retrieve_started)
                    ) * 1000.0
                    _record_provider_result(
                        branch=branch,
                        branch_index=branch_index,
                        name=provider_name,
                        value=exc,
                        latency_ms=elapsed_ms,
                        rows=rows,
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        provider_ranked_results_list=branch_provider_ranked_results[branch_index],
                    )
                    continue
                except Exception as exc:
                    elapsed_ms = (
                        time.monotonic() - started_at.get(provider_name, retrieve_started)
                    ) * 1000.0
                    _record_provider_result(
                        branch=branch,
                        branch_index=branch_index,
                        name=provider_name,
                        value=exc,
                        latency_ms=elapsed_ms,
                        rows=rows,
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        provider_ranked_results_list=branch_provider_ranked_results[branch_index],
                    )
                    continue
                _record_provider_result(
                    branch=branch,
                    branch_index=branch_index,
                    name=provider_name,
                    value=value,
                    latency_ms=latency_ms,
                    rows=rows,
                    warnings_by_name=warnings_by_name,
                    provider_calls=calls,
                    provider_ranked_results_list=branch_provider_ranked_results[branch_index],
                )
        except asyncio.CancelledError:
            await cancel_and_drain_tasks(tasks)
            raise

        outcomes_list: list[BranchOutcome] = []
        for branch_index, branch in enumerate(run.plan.branches):
            outcomes_list.append(
                _assemble_branch_outcome(
                    branch,
                    assigned_names=branch_assigned[branch_index],
                    skipped=(),
                    attempted=tuple(branch_attempted[branch_index]),
                    rows=branch_rows[branch_index],
                    warnings_by_name=branch_warnings[branch_index],
                    provider_calls=branch_calls[branch_index],
                    provider_ranked_results=tuple(branch_provider_ranked_results[branch_index]),
                    elapsed_seconds=time.monotonic() - retrieve_started,
                )
            )

        outcomes = tuple(outcomes_list)
        run.outcomes = outcomes
        branch_rows_diag: list[dict[str, Any]] = []
        for index, outcome in enumerate(outcomes):
            preview = branch_outcome_preview(outcome)
            preview["branch_index"] = index
            calls_with_index = []
            for call in outcome.provider_calls:
                row = dict(call)
                row["branch_index"] = index
                calls_with_index.append(row)
            preview["provider_calls"] = calls_with_index
            branch_rows_diag.append(preview)
        run.diagnostics.branch_results = branch_rows_diag
        elapsed_ms = (time.monotonic() - retrieve_started) * 1000.0
        run.diagnostics.phase_timings["search.retrieve"] = elapsed_ms
        if run.diagnostics.enrichment is None:
            run.diagnostics.enrichment = {}
        run.diagnostics.enrichment["retrieve_budget_seconds"] = retrieve_budget_seconds
        run.diagnostics.enrichment["retrieve_budget_exceeded"] = retrieve_budget_exceeded
        span.set_attribute("search.provider_outcome_count", len(outcomes))
        span.set_attribute("search.retrieve_budget_exceeded", retrieve_budget_exceeded)
        return outcomes
