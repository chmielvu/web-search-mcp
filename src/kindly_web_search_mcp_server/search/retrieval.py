"""Structured branch/provider retrieval for web search."""

from __future__ import annotations

import asyncio
import json
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
from .providers.base import ProviderRequestMetadata, get_provider_request_metadata

from ..heuristics.shaping import shape_for_branch
from ..heuristics.query_features import build_query_features


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, parsed.query, ""))


def _warning(
    provider: str,
    error_type: str,
    error: str,
    *,
    action: str | None = None,
    retry_after: float | None = None,
    retryable: bool | None = None,
) -> ProviderWarning:
    """Build a warning carrying the MCP error contract fields.

    ``action`` is an agent-actionable recovery hint, ``retry_after`` is the
    provider-issued throttle window (seconds), and ``retryable`` marks
    transient failures so the agent does not re-derive retry semantics from
    the free-text error.
    """
    return ProviderWarning(
        provider=provider,
        error=error,
        error_type=error_type,
        action=action,
        retry_after=retry_after,
        retryable=retryable,
    )


def _provider_action_hint(
    provider: str,
    error_type: str,
    retry_after: float | None,
) -> str | None:
    """Concise, agent-actionable recovery hint for a provider failure."""
    if error_type == "rate_limit":
        wait = f"{int(retry_after)}s" if retry_after else "30-60s"
        return (
            f"Provider {provider} is rate limited; wait {wait} before retrying "
            "or reduce query frequency."
        )
    if error_type in {"auth", "http_401", "http_403", "forbidden", "unauthorized"}:
        return f"Provider {provider} rejected credentials; verify the API key/token configuration."
    if error_type in {"timeout", "upstream", "network", "http_408", "http_425"} or (
        error_type and error_type.startswith("http_5")
    ):
        return f"Provider {provider} failed transiently; a retry may succeed."
    if error_type == "retrieve_budget":
        return (
            "Retrieve budget exhausted; reduce branch count or raise "
            "SEARCH_RETRIEVE_BUDGET_SECONDS."
        )
    if error_type in {"content", "config"}:
        return f"Provider {provider} returned an invalid response; check provider configuration."
    return None


async def _call_provider(
    run: SearchRun,
    branch: QueryBranch,
    provider_name: str,
    embedding_task: Awaitable[Sequence[float]] | None,
    *,
    retrieve_deadline: float,
) -> tuple[str, Sequence[WebSearchResult] | BaseException, ProviderRequestMetadata, str]:
    definition = get_provider_definition(provider_name)
    adapter = get_provider_adapter(provider_name)
    # Live budget only — do not trust catalog snapshot from import time.
    provider_cap = settings.search_retrieve_budget_seconds
    remaining = max(0.0, retrieve_deadline - time.monotonic())
    timeout = min(provider_cap, remaining)
    # Per-provider timeout cap (catalog) bounds a single call below the
    # global retrieve budget so slow providers cannot hog the fan-out.
    if definition.per_call_timeout_seconds is not None:
        timeout = min(timeout, definition.per_call_timeout_seconds)
    if timeout <= 0:
        return (
            provider_name,
            TimeoutError(),
            ProviderRequestMetadata(
                provider=provider_name,
                result_class="incomplete",
                error_type="retrieve_budget",
                error_summary="retrieve budget exhausted",
            ),
            branch.query,
        )
    query = branch.query
    rules_applied: tuple[str, ...] = ()
    transform_metadata: dict[str, Any] = {}
    understanding = run.plan.understanding if run.plan else None
    features = build_query_features(
        query,
        understanding=understanding,
        support_terms=branch.support_terms or (),
    )
    aug = shape_for_branch(
        branch.role.value, query, features, exact=not run.request.rewrite
    )
    query_for_call = aug.query
    provider_arguments = dict(
        run.plan.provider_arguments.get(provider_name, {}) if run.plan else {}
    )
    transform_metadata = dict(aug.metadata)
    rules_applied = aug.rules_applied
    if aug.changed or aug.rules_applied:
        run.diagnostics.query_shaping.append(
            {
                "provider": provider_name,
                "branch_role": branch.role.value,
                "original": run.request.query,
                "shaped": aug.query,
                "rules": list(aug.rules_applied),
                "metadata": transform_metadata,
            }
        )
    run.diagnostics.query_transform_rows.append(
        {
            "run_key": run.run_key,
            "branch_role": branch.role.value,
            "provider": provider_name,
            "original_query": query,
            "shaped_query": query_for_call,
            "changed": query_for_call != query,
            "rules_applied": list(rules_applied),
            "metadata_json": transform_metadata,
        }
    )
    try:
        result = await asyncio.wait_for(
            adapter(
                query_for_call,
                num_results=branch.max_results,
                options=run.request.options,
                arguments=provider_arguments,
                http_client=run.http_client,
                query_embedding=embedding_task if definition.requires_embedding else None,
            ),
            timeout=timeout,
        )
        normalized = [
            item.model_copy(update={"providers": sorted({*(item.providers or []), provider_name})})
            for item in result
        ]
        return (
            provider_name,
            normalized,
            get_provider_request_metadata()
            or ProviderRequestMetadata(
                provider=provider_name,
                result_class="nonempty" if normalized else "empty",
            ),
            query_for_call,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return (
            provider_name,
            TimeoutError(),
            get_provider_request_metadata()
            or ProviderRequestMetadata(
                provider=provider_name,
                result_class="timeout",
                error_type="timeout",
                error_summary="provider request timed out",
            ),
            query_for_call,
        )
    except Exception as exc:
        return (
            provider_name,
            exc,
            get_provider_request_metadata()
            or ProviderRequestMetadata(
                provider=provider_name,
                result_class="error",
                error_type=type(exc).__name__,
                error_summary=str(exc)[:500],
            ),
            query_for_call,
        )


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
    provider_result_rows: list[dict[str, Any]] | None = None,
    run_key: str = "",
    status_override: str | None = None,
    error_type_override: str | None = None,
    error_message_override: str | None = None,
    request_query: str | None = None,
    metadata: ProviderRequestMetadata | None = None,
) -> None:
    metadata = metadata or ProviderRequestMetadata(provider=name)
    response_meta_json = json.dumps(metadata.response_meta, ensure_ascii=False, default=str)
    common = {
        "request_query": request_query or branch.query,
        "request_url": metadata.endpoint,
        "http_status": metadata.http_status,
        "result_class": metadata.result_class,
        "response_meta_json": response_meta_json,
        "retry_after": metadata.retry_after,
        "retryable": metadata.retryable,
    }
    if status_override == "incomplete":
        warnings_by_name[name] = _warning(
            name,
            "retrieve_budget",
            "retrieve budget exhausted",
            action=_provider_action_hint(name, "retrieve_budget", None),
            retryable=False,
        )
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
                **common,
            }
        )
        return

    if value is None:
        return

    if isinstance(value, BaseException):
        error_type = (
            error_type_override
            or metadata.error_type
            or ("timeout" if isinstance(value, TimeoutError) else "provider_error")
        )
        retry_after = metadata.retry_after
        retryable = metadata.retryable
        warnings_by_name[name] = _warning(
            name,
            error_type,
            metadata.error_summary or error_type,
            action=_provider_action_hint(name, error_type, retry_after),
            retry_after=retry_after,
            retryable=retryable,
        )
        provider_calls.append(
            {
                "provider": name,
                "status": status_override or "error",
                "branch_role": branch.role.value,
                "error_type": error_type,
                "error_message": (error_message_override or metadata.error_summary or str(value))[
                    :500
                ],
                "num_results_returned": 0,
                "latency_ms": latency_ms,
                "candidate_urls": [],
                **common,
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
    # Collect provider_result rows for funnel uplift analytics
    if provider_result_rows is not None:
        from ..analytics.observability_store import _canonical_result_id as _cri
        for rank, item in enumerate(deduped_results, start=1):
            provider_result_rows.append({
                "provider_result_id": _cri(f"{name}|{branch_index}|{item.link}"),
                "provider_call_id": _cri(f"{run_key}|{branch_index}|{name}"),
                "run_key": run_key,
                "branch_id": _cri(f"{run_key}|{branch_index}"),
                "provider": name,
                "provider_rank": rank,
                "canonical_result_id": _cri(item.link),
                "raw_url": item.link,
                "title": getattr(item, "title", None),
                "snippet": getattr(item, "snippet", None),
                "raw_score": getattr(item, "score", None),
                "is_eligible": True,
                "rejection_reason": None,
                "payload_json": None,
            })

    for item in value:
        key = _canonical_url(item.link)
        if key not in rows:
            rows[key] = item
        else:
            existing = rows[key]
            existing.providers = sorted({*(existing.providers or []), *(item.providers or []), name})
            existing.provider_count = len(existing.providers)
    provider_calls.append(
        {
            "provider": name,
            "status": (
                "incomplete"
                if metadata.result_class == "incomplete"
                else "error"
                if metadata.result_class == "error"
                else "success"
            ),
            "branch_role": branch.role.value,
            "num_results_returned": len(value),
            "latency_ms": latency_ms,
            "candidate_urls": [item.link for item in value][:_MAX_URLS],
            **common,
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
    retrieve_deadline = time.monotonic() + retrieve_budget_seconds

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
        branch_provider_result_rows: list[list[dict[str, Any]]] = [
            [] for _ in range(len(run.plan.branches))
        ]

        tasks: list[
            asyncio.Task[
                tuple[
                    str,
                    Sequence[WebSearchResult] | BaseException,
                    ProviderRequestMetadata,
                    str,
                    float,
                ]
            ]
        ] = []
        slot_by_task: dict[asyncio.Task[Any], tuple[int, str]] = {}
        started_at: dict[tuple[int, str], float] = {}
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
                ) -> tuple[
                    str,
                    Sequence[WebSearchResult] | BaseException,
                    ProviderRequestMetadata,
                    str,
                    float,
                ]:
                    call_started = time.monotonic()
                    started_at[(branch_index, name)] = call_started
                    provider_name, value, metadata, request_query = await _call_provider(
                        run,
                        b,
                        n,
                        embedding_task,
                        retrieve_deadline=retrieve_deadline,
                    )
                    return (
                        provider_name,
                        value,
                        metadata,
                        request_query,
                        (time.monotonic() - call_started) * 1000.0,
                    )

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
                        time.monotonic()
                        - started_at.get((branch_index, provider_name), retrieve_started)
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
                        provider_result_rows=branch_provider_result_rows[branch_index],
                        run_key=run.run_key,
                        status_override="incomplete",
                        request_query=branch.query,
                        metadata=ProviderRequestMetadata(
                            provider=provider_name,
                            result_class="incomplete",
                            error_type="retrieve_budget",
                            error_summary="retrieve budget exhausted",
                        ),
                    )
                    continue

                if task not in done:
                    raise RuntimeError("Provider task missing from asyncio.wait partition")

                metadata = ProviderRequestMetadata(provider=provider_name)
                request_query = branch.query
                try:
                    _returned_name, value, metadata, request_query, latency_ms = task.result()
                except asyncio.CancelledError as exc:
                    elapsed_ms = (
                        time.monotonic()
                        - started_at.get((branch_index, provider_name), retrieve_started)
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
                        provider_result_rows=branch_provider_result_rows[branch_index],
                        run_key=run.run_key,
                        request_query=request_query,
                        metadata=metadata,
                    )
                    continue
                except Exception as exc:
                    elapsed_ms = (
                        time.monotonic()
                        - started_at.get((branch_index, provider_name), retrieve_started)
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
                        provider_result_rows=branch_provider_result_rows[branch_index],
                        run_key=run.run_key,
                        request_query=request_query,
                        metadata=metadata,
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
                    provider_result_rows=branch_provider_result_rows[branch_index],
                    run_key=run.run_key,
                    request_query=request_query,
                    metadata=metadata,
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
        # Collect provider_result rows for funnel uplift analytics
        all_provider_result_rows: list[dict[str, Any]] = []
        for branch_rows_list in branch_provider_result_rows:
            all_provider_result_rows.extend(branch_rows_list)
        run.diagnostics.provider_result_rows = all_provider_result_rows
        elapsed_ms = (time.monotonic() - retrieve_started) * 1000.0
        run.diagnostics.phase_timings["search.retrieve"] = elapsed_ms
        if run.diagnostics.enrichment is None:
            run.diagnostics.enrichment = {}
        run.diagnostics.enrichment["retrieve_budget_seconds"] = retrieve_budget_seconds
        run.diagnostics.enrichment["retrieve_budget_exceeded"] = retrieve_budget_exceeded
        span.set_attribute("search.provider_outcome_count", len(outcomes))
        span.set_attribute("search.retrieve_budget_exceeded", retrieve_budget_exceeded)
        return outcomes
