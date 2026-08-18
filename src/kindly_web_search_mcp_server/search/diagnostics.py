"""Immutable search diagnostics for CLI ``--diagnostics`` and observability persistence.

``DiagnosticsCollector`` on ``SearchRun`` is the mutable write surface during the
pipeline; ``build_diagnostics`` projects it (plus ``plan`` / ``outcomes`` fallbacks)
into a frozen ``SearchDiagnostics`` payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import BranchOutcome, DiagnosticsCollector, SearchRun


class _DiagBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DiagnosticsEnrichment(_DiagBase):
    rake_terms: tuple[str, ...] = ()
    brave_autosuggest: tuple[str, ...] = ()
    intent: str | None = None
    understanding_confidence: float | None = None
    policy_version: str | None = None
    retrieve_budget_seconds: float | None = None
    retrieve_budget_exceeded: bool | None = None


class DiagnosticsRewrite(_DiagBase):
    enabled: bool = False
    model: str | None = None
    prompt: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    branch_count: int | None = None


class DiagnosticsProviderCall(_DiagBase):
    provider: str
    status: str
    branch_index: int | None = None
    branch_role: str | None = None
    num_results_returned: int | None = None
    latency_ms: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    candidate_urls: tuple[str, ...] = ()


class DiagnosticsBranch(_DiagBase):
    branch_index: int
    branch_role: str
    branch_query: str
    branch_why: str = ""
    support_terms: tuple[str, ...] = ()
    max_results: int = 15
    assigned_providers: tuple[str, ...] = ()
    attempted_providers: tuple[str, ...] = ()
    skipped_providers: tuple[str, ...] = ()
    results_count: int = 0
    latency_ms: float | None = None
    provider_calls: tuple[DiagnosticsProviderCall, ...] = ()


class DiagnosticsRerankStage(_DiagBase):
    stage: str
    provider: str | None = None
    model: str | None = None
    input_count: int | None = None
    output_count: int | None = None
    duration_ms: float | None = None
    max_score: float | None = None
    avg_score: float | None = None
    status: str | None = None
    error_type: str | None = None


class DiagnosticsMergeCounts(_DiagBase):
    merged_count: int = 0
    candidate_count: int = 0
    reranked_count: int = 0
    final_result_count: int = 0
    branch_count: int = 0
    provider_count: int = 0


class DiagnosticsEmbeddings(_DiagBase):
    model_id: str = "intfloat/multilingual-e5-large-instruct"
    query_embedding_dim: int | None = None
    candidate_count: int = 0


class SearchDiagnostics(_DiagBase):
    run_key: str
    query: str
    total_latency_ms: float
    enrichment: DiagnosticsEnrichment = Field(default_factory=DiagnosticsEnrichment)
    rewrite: DiagnosticsRewrite = Field(default_factory=DiagnosticsRewrite)
    branches: tuple[DiagnosticsBranch, ...] = ()
    merge: DiagnosticsMergeCounts = Field(default_factory=DiagnosticsMergeCounts)
    rerank_stages: tuple[DiagnosticsRerankStage, ...] = ()
    embeddings: DiagnosticsEmbeddings = Field(default_factory=DiagnosticsEmbeddings)
    phase_timings: tuple[tuple[str, float], ...] = ()
    selected_providers: tuple[str, ...] = ()
    skipped_providers: tuple[str, ...] = ()


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item is not None and str(item))
    return ()


def _enrichment_from_run(run: SearchRun, dc: DiagnosticsCollector) -> DiagnosticsEnrichment:
    raw = dc.enrichment if isinstance(dc.enrichment, dict) else {}
    plan = run.plan
    intent = dc.intent
    confidence = dc.understanding_confidence
    if plan is not None:
        if intent is None:
            intent = str(plan.understanding.intent)
        if confidence is None:
            confidence = plan.understanding.confidence
    return DiagnosticsEnrichment(
        rake_terms=_coerce_str_tuple(raw.get("rake_terms")),
        brave_autosuggest=_coerce_str_tuple(raw.get("brave_autosuggest")),

        intent=intent,
        understanding_confidence=confidence,
        policy_version=plan.policy_version if plan is not None else None,
        retrieve_budget_seconds=raw.get("retrieve_budget_seconds"),
        retrieve_budget_exceeded=raw.get("retrieve_budget_exceeded"),
    )


def _rewrite_from_collector(
    dc: DiagnosticsCollector, *, rewrite_enabled: bool
) -> DiagnosticsRewrite:
    raw = dc.rewrite_metadata if isinstance(dc.rewrite_metadata, dict) else {}
    return DiagnosticsRewrite(
        enabled=rewrite_enabled,
        model=raw.get("model") if isinstance(raw.get("model"), str) else None,
        prompt=raw.get("prompt") if isinstance(raw.get("prompt"), str) else None,
        input_tokens=raw.get("input_tokens") if isinstance(raw.get("input_tokens"), int) else None,
        output_tokens=raw.get("output_tokens")
        if isinstance(raw.get("output_tokens"), int)
        else None,
        latency_ms=raw.get("latency_ms")
        if isinstance(raw.get("latency_ms"), (int, float))
        else None,
        error=raw.get("error") if isinstance(raw.get("error"), str) else None,
        branch_count=raw.get("branch_count") if isinstance(raw.get("branch_count"), int) else None,
    )


def _provider_call_from_dict(row: dict[str, Any]) -> DiagnosticsProviderCall | None:
    provider = row.get("provider")
    status = row.get("status")
    if not isinstance(provider, str) or not isinstance(status, str):
        return None
    urls = row.get("candidate_urls")
    url_tuple = _coerce_str_tuple(urls) if urls is not None else ()
    latency = row.get("latency_ms")
    return DiagnosticsProviderCall(
        provider=provider,
        status=status,
        branch_index=row.get("branch_index") if isinstance(row.get("branch_index"), int) else None,
        branch_role=row.get("branch_role") if isinstance(row.get("branch_role"), str) else None,
        num_results_returned=row.get("num_results_returned")
        if isinstance(row.get("num_results_returned"), int)
        else None,
        latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
        error_type=row.get("error_type") if isinstance(row.get("error_type"), str) else None,
        error_message=row.get("error_message")
        if isinstance(row.get("error_message"), str)
        else None,
        candidate_urls=url_tuple,
    )


def _branches_from_run(run: SearchRun, dc: DiagnosticsCollector) -> tuple[DiagnosticsBranch, ...]:
    if dc.branch_results:
        branches: list[DiagnosticsBranch] = []
        for index, row in enumerate(dc.branch_results):
            if not isinstance(row, dict):
                continue
            calls = tuple(
                parsed
                for parsed in (
                    _provider_call_from_dict(item)
                    for item in row.get("provider_calls") or []
                    if isinstance(item, dict)
                )
                if parsed is not None
            )
            role = row.get("branch_role")
            query = row.get("branch_query")
            if not isinstance(role, str) or not isinstance(query, str):
                continue
            latency = row.get("latency_ms")
            branches.append(
                DiagnosticsBranch(
                    branch_index=row.get("branch_index", index)
                    if isinstance(row.get("branch_index"), int)
                    else index,
                    branch_role=role,
                    branch_query=query,
                    branch_why=str(row.get("branch_why") or ""),
                    support_terms=_coerce_str_tuple(row.get("support_terms")),
                    max_results=int(row.get("max_results") or 15),
                    assigned_providers=_coerce_str_tuple(row.get("assigned_providers")),
                    attempted_providers=_coerce_str_tuple(row.get("attempted_providers")),
                    skipped_providers=_coerce_str_tuple(row.get("skipped_providers")),
                    results_count=int(row.get("results_count") or 0),
                    latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
                    provider_calls=calls,
                )
            )
        return tuple(branches)

    plan = run.plan
    if plan is None:
        return ()
    output: list[DiagnosticsBranch] = []
    for index, (branch, outcome) in enumerate(zip(plan.branches, run.outcomes, strict=False)):
        output.append(
            DiagnosticsBranch(
                branch_index=index,
                branch_role=branch.role.value,
                branch_query=branch.query,
                branch_why=branch.why,
                support_terms=branch.support_terms,
                max_results=branch.max_results,
                assigned_providers=branch.provider_names,
                attempted_providers=outcome.attempted_provider_names,
                skipped_providers=outcome.skipped_provider_names,
                results_count=len(outcome.results),
                latency_ms=outcome.elapsed_seconds * 1000.0,
                provider_calls=(),
            )
        )
    return tuple(output)


def _merge_counts(run: SearchRun, dc: DiagnosticsCollector) -> DiagnosticsMergeCounts:
    raw = dc.merge_counts if isinstance(dc.merge_counts, dict) else {}
    response = run.response
    providers_used: set[str] = set()
    for outcome in run.outcomes:
        providers_used.update(outcome.attempted_provider_names)
    return DiagnosticsMergeCounts(
        merged_count=int(raw.get("merged_count", raw.get("merged", 0)) or 0),
        candidate_count=int(raw.get("candidate_count") or 0),
        reranked_count=int(raw.get("reranked_count", raw.get("reranked", 0)) or 0),
        final_result_count=len(response.results) if response is not None else 0,
        branch_count=int(raw.get("branch_count", len(run.outcomes)) or 0),
        provider_count=int(raw.get("provider_count", len(providers_used)) or 0),
    )


def _rerank_stages(dc: DiagnosticsCollector) -> tuple[DiagnosticsRerankStage, ...]:
    stages: list[DiagnosticsRerankStage] = []
    for row in dc.rerank_stage_summaries:
        if not isinstance(row, dict):
            continue
        stage = row.get("stage")
        if not isinstance(stage, str):
            continue
        duration = row.get("duration_ms")
        stages.append(
            DiagnosticsRerankStage(
                stage=stage,
                provider=row.get("provider") if isinstance(row.get("provider"), str) else None,
                model=row.get("model") if isinstance(row.get("model"), str) else None,
                input_count=row.get("input_count")
                if isinstance(row.get("input_count"), int)
                else None,
                output_count=row.get("output_count")
                if isinstance(row.get("output_count"), int)
                else None,
                duration_ms=float(duration) if isinstance(duration, (int, float)) else None,
                max_score=row.get("max_score")
                if isinstance(row.get("max_score"), (int, float))
                else None,
                avg_score=row.get("avg_score")
                if isinstance(row.get("avg_score"), (int, float))
                else None,
                status=row.get("status") if isinstance(row.get("status"), str) else None,
                error_type=row.get("error_type")
                if isinstance(row.get("error_type"), str)
                else None,
            )
        )
    return tuple(stages)


def _embeddings(dc: DiagnosticsCollector) -> DiagnosticsEmbeddings:
    dim = len(dc.query_embedding) if dc.query_embedding else None
    return DiagnosticsEmbeddings(
        query_embedding_dim=dim,
        candidate_count=len(dc.candidate_embeddings),
    )


def build_diagnostics(run: SearchRun, total_latency_ms: float) -> SearchDiagnostics:
    """Project a completed or in-flight ``SearchRun`` into an immutable diagnostics payload."""
    dc = run.diagnostics
    plan = run.plan
    timings = (
        tuple(sorted(dc.phase_timings.items()))
        if dc.phase_timings
        else tuple(sorted(run.timings.items()))
    )
    selected: tuple[str, ...] = ()
    if plan is not None:
        names: set[str] = set()
        for b in plan.branches:
            names.update(b.provider_names)
        selected = tuple(sorted(names))
    skipped: set[str] = set()
    for outcome in run.outcomes:
        skipped.update(outcome.skipped_provider_names)
    if isinstance(dc.enrichment, dict):
        for name in dc.enrichment.get("skipped_providers") or []:
            if isinstance(name, str):
                skipped.add(name)

    return SearchDiagnostics(
        run_key=run.run_key,
        query=run.request.query,
        total_latency_ms=float(
            dc.total_latency_ms if dc.total_latency_ms is not None else total_latency_ms
        ),
        enrichment=_enrichment_from_run(run, dc),
        rewrite=_rewrite_from_collector(dc, rewrite_enabled=run.request.rewrite),
        branches=_branches_from_run(run, dc),
        merge=_merge_counts(run, dc),
        rerank_stages=_rerank_stages(dc),
        embeddings=_embeddings(dc),
        phase_timings=timings,
        selected_providers=selected,
        skipped_providers=tuple(sorted(skipped)),
    )


def branch_outcome_preview(outcome: BranchOutcome) -> dict[str, Any]:
    """Lightweight dict used when populating ``DiagnosticsCollector.branch_results``."""
    return {
        "branch_role": outcome.branch.role.value,
        "branch_query": outcome.branch.query,
        "branch_why": outcome.branch.why,
        "support_terms": list(outcome.branch.support_terms),
        "max_results": outcome.branch.max_results,
        "attempted_providers": list(outcome.attempted_provider_names),
        "skipped_providers": list(outcome.skipped_provider_names),
        "results_count": len(outcome.results),
        "latency_ms": outcome.elapsed_seconds * 1000.0,
        "provider_calls": [],
    }
