"""Structured branch/provider retrieval for web search."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Sequence
from dataclasses import replace
from typing import Any

from ..models import ProviderWarning
from ..settings import settings
from ..telemetry.spans import get_tracer
from ..utils.query_pipeline import build_query_features, shape_for_branch
from ..utils.task_scope import cancel_and_drain_tasks
from ..utils.url_canonicalize import canonicalize_url
from .contracts import BranchOutcome, QueryBranch, SearchRun
from .diagnostics import branch_outcome_preview
from .evidence import testimony_payload
from .provider_registry import get_provider_adapter, get_provider_definition
from .providers.base import ProviderRequestMetadata, get_provider_request_metadata
from .types import EngineCall, SearchHit


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
    if error_type == "query_truncated":
        return (
            f"Provider {provider} truncated the submitted query; shorten or split the query "
            "if missing terms are important."
        )
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
) -> tuple[str, EngineCall | BaseException, ProviderRequestMetadata, str]:
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
    aug = shape_for_branch(branch.role.value, query, features, exact=not run.request.rewrite)
    query_for_call = aug.query
    provider_arguments = dict(
        run.plan.provider_arguments.get(provider_name, {}) if run.plan else {}
    )
    transform_metadata = dict(aug.metadata)
    rules_applied = aug.rules_applied
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
        result_class = (
            "nonempty" if result.hits else ("error" if result.failure is not None else "empty")
        )
        return (
            provider_name,
            result,
            get_provider_request_metadata()
            or ProviderRequestMetadata(
                provider=provider_name,
                result_class=result_class,
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


def _query_integrity_payload(call: EngineCall) -> dict[str, Any] | None:
    integrity = call.integrity
    if integrity is None:
        return None
    return {
        "sent_query": integrity.sent_query,
        "detected_query": integrity.detected_query,
        "truncated": integrity.truncated,
        "spelling": integrity.spelling,
        "result_count": integrity.result_count,
    }


def _call_payload(call: EngineCall) -> dict[str, Any]:
    failure = call.failure
    return {
        "expansion": list(call.expansion),
        "query_integrity": _query_integrity_payload(call),
        "failure": (
            {
                "kind": failure.kind,
                "message": failure.message,
                "code": failure.code,
                "retryable": failure.retryable,
                "retry_after": failure.retry_after,
            }
            if failure is not None
            else None
        ),
    }


def _record_provider_result(
    *,
    branch: QueryBranch,
    branch_index: int,
    name: str,
    value: EngineCall | BaseException | None,
    latency_ms: float,
    warnings_by_name: dict[str, ProviderWarning],
    provider_calls: list[dict[str, Any]],
    branch_engine_calls: list[EngineCall],
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

    if value.failure is not None:
        failure_kind_map = {
            "rate_limited": "rate_limit",
            "timeout": "timeout",
            "bot_challenge": "bot_challenge",
            "permission_denied": "auth",
            "budget_exhausted": "retrieve_budget",
            "upstream_5xx": "upstream",
            "upstream_4xx": "http_status",
            "parse_error": "provider_error",
            "query_truncated": "provider_error",
            "empty_result": "empty",
        }
        error_type = error_type_override or failure_kind_map.get(
            value.failure.kind, "provider_error"
        )
        retry_after = value.failure.retry_after or metadata.retry_after
        retryable = (
            value.failure.retryable if value.failure.retryable is not None else metadata.retryable
        )
        warnings_by_name[name] = _warning(
            name,
            error_type,
            value.failure.message,
            action=_provider_action_hint(name, error_type, retry_after),
            retry_after=retry_after,
            retryable=retryable,
        )

    seen_urls: set[str] = set()
    deduped_hits: list[SearchHit] = []
    for item in value.hits:
        url_key = canonicalize_url(item.url)
        if url_key not in seen_urls:
            seen_urls.add(url_key)
            deduped_hits.append(item)
    if value.integrity is not None and value.integrity.truncated and name not in warnings_by_name:
        detected = value.integrity.detected_query or "an altered query"
        error_type = "query_truncated"
        warnings_by_name[name] = _warning(
            name,
            error_type,
            f"Provider detected {detected!r} after truncating the submitted query.",
            action=_provider_action_hint(name, error_type, None),
            retryable=False,
        )
    branch_engine_calls.append(replace(value, hits=tuple(deduped_hits)))

    # Collect provider_result rows for funnel uplift analytics
    if provider_result_rows is not None:
        from ..analytics.ids import _canonical_result_id as _cri

        for rank, item in enumerate(deduped_hits, start=1):
            provider_result_rows.append(
                {
                    "provider_result_id": _cri(f"{name}|{branch_index}|{item.url}"),
                    "provider_call_id": _cri(f"{run_key}|{branch_index}|{name}"),
                    "run_key": run_key,
                    "branch_id": _cri(f"{run_key}|{branch_index}"),
                    "provider": name,
                    "provider_rank": rank,
                    "canonical_result_id": _cri(item.url),
                    "raw_url": item.url,
                    "title": item.title,
                    "snippet": item.snippet,
                    "raw_score": item.provider_score,
                    "is_eligible": True,
                    "rejection_reason": None,
                    "payload_json": testimony_payload(item),
                }
            )

    provider_calls.append(
        {
            "provider": name,
            "status": (
                "incomplete"
                if metadata.result_class == "incomplete"
                else "error"
                if (metadata.result_class == "error" or value.failure is not None)
                else "partial"
                if value.integrity is not None and value.integrity.truncated
                else "success"
            ),
            "branch_role": branch.role.value,
            "num_results_returned": len(deduped_hits),
            "latency_ms": latency_ms,
            "candidate_urls": [item.url for item in deduped_hits][:_MAX_URLS],
            "payload_json": _call_payload(value),
            **common,
        }
    )


def _assemble_branch_outcome(
    branch: QueryBranch,
    *,
    assigned_names: tuple[str, ...],
    attempted: tuple[str, ...],
    calls: tuple[EngineCall, ...],
    warnings_by_name: dict[str, ProviderWarning],
    provider_calls: list[dict[str, Any]],
    elapsed_seconds: float,
) -> BranchOutcome:
    warnings = tuple(warnings_by_name[name] for name in assigned_names if name in warnings_by_name)
    return BranchOutcome(
        branch=branch,
        attempted_provider_names=attempted,
        calls=calls,
        warnings=warnings,
        elapsed_seconds=elapsed_seconds,
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
    retrieve_budget_seconds = settings.search_retrieve_budget_seconds
    retrieve_deadline = time.monotonic() + retrieve_budget_seconds

    with tracer.start_as_current_span("search.retrieve") as span:
        span.set_attribute("search.run_key", run.run_key)
        span.set_attribute("search.branch_count", len(run.plan.branches))
        span.set_attribute("search.retrieve_budget_seconds", retrieve_budget_seconds)

        branch_assigned: list[tuple[str, ...]] = []
        branch_attempted: list[list[str]] = []
        branch_warnings: list[dict[str, ProviderWarning]] = []
        branch_calls: list[list[dict[str, Any]]] = []
        branch_engine_calls: list[list[EngineCall]] = [[] for _ in range(len(run.plan.branches))]
        branch_provider_result_rows: list[list[dict[str, Any]]] = [
            [] for _ in range(len(run.plan.branches))
        ]

        tasks: list[
            asyncio.Task[
                tuple[
                    str,
                    EngineCall | BaseException,
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
            branch_warnings.append({})
            branch_calls.append([])

            for name in assigned_names:

                async def _invoke(
                    b: QueryBranch = branch,
                    n: str = name,
                    i: int = branch_index,
                ) -> tuple[
                    str,
                    EngineCall | BaseException,
                    ProviderRequestMetadata,
                    str,
                    float,
                ]:
                    call_started = time.monotonic()
                    started_at[(i, n)] = call_started
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
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        branch_engine_calls=branch_engine_calls[branch_index],
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
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        branch_engine_calls=branch_engine_calls[branch_index],
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
                        warnings_by_name=warnings_by_name,
                        provider_calls=calls,
                        branch_engine_calls=branch_engine_calls[branch_index],
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
                    warnings_by_name=warnings_by_name,
                    provider_calls=calls,
                    branch_engine_calls=branch_engine_calls[branch_index],
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
                    attempted=tuple(branch_attempted[branch_index]),
                    calls=tuple(branch_engine_calls[branch_index]),
                    warnings_by_name=branch_warnings[branch_index],
                    provider_calls=branch_calls[branch_index],
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
        run.diagnostics.query_integrities = [
            {
                "branch_index": branch_index,
                "branch_role": outcome.branch.role.value,
                "provider": call.adapter,
                **payload,
            }
            for branch_index, outcome in enumerate(outcomes)
            for call in outcome.calls
            if (payload := _query_integrity_payload(call)) is not None
        ]
        elapsed_ms = (time.monotonic() - retrieve_started) * 1000.0
        run.diagnostics.phase_timings["search.retrieve"] = elapsed_ms
        if run.diagnostics.enrichment is None:
            run.diagnostics.enrichment = {}
        run.diagnostics.enrichment["retrieve_budget_seconds"] = retrieve_budget_seconds
        run.diagnostics.enrichment["retrieve_budget_exceeded"] = retrieve_budget_exceeded
        span.set_attribute("search.provider_outcome_count", len(outcomes))
        span.set_attribute("search.retrieve_budget_exceeded", retrieve_budget_exceeded)
        return outcomes
