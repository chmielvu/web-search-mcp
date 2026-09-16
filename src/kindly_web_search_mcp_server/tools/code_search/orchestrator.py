"""Backend-selected multi-channel code-search orchestration."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ...errors import classify_error
from .exa import search_exa
from .filters import filter_scoped_hits
from .github import search_github
from .grepapp import search_grepapp
from .huggingface import search_huggingface
from .hydration import hydrate_sources
from .issues import search_github_issues
from .models import (
    CodeSearchRequest,
    CodeSearchResultType,
    Diagnostic,
    ProviderResponse,
    RepoCandidate,
    Stats,
    normalize_hit_metadata,
)
from .query import QueryPlan
from .ranking import rank_candidates
from .reranking import RerankProfile, rerank_code_hits
from .sourcegraph import search_sourcegraph
from .windows import extract_source_windows

_ERROR_KIND_MAP: dict[str, str] = {
    "rate_limit": "rate_limit",
    "auth": "auth",
    "network": "network",
    "validation": "validation",
    "content": "not_found",
    "config": "provider",
    "unknown": "provider",
}


def _branch_failure(provider: str, exc: BaseException) -> ProviderResponse:
    structured = classify_error(exc, provider=provider)
    kind = _ERROR_KIND_MAP.get(structured.error_type, "provider")
    details: dict[str, Any] = {"action": structured.action} if structured.action else {}
    return ProviderResponse(
        provider=provider,
        diagnostics=[
            Diagnostic(
                provider=provider,
                outcome="error",
                message=structured.error,
                failure_kind=kind,  # type: ignore[arg-type]
                status_code=structured.status_code,
                retry_after_seconds=(
                    float(structured.retry_after) if structured.retry_after is not None else None
                ),
                details=details,
            )
        ],
    )


async def _run_provider(
    provider: str,
    operation: Any,
) -> ProviderResponse:
    try:
        result = await operation
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _branch_failure(provider, exc)
    if isinstance(result, ProviderResponse):
        return result
    return _branch_failure(provider, TypeError("provider returned an invalid response"))


def _outcome(responses: list[ProviderResponse], result_count: int) -> str:
    diagnostics = [diagnostic for response in responses for diagnostic in response.diagnostics]
    meaningful = [
        diagnostic for diagnostic in diagnostics if diagnostic.outcome in {"partial", "error"}
    ]
    if result_count:
        return "partial" if meaningful else "ok"
    if any(diagnostic.outcome == "partial" for diagnostic in meaningful):
        return "partial"
    transient_failures = {"network", "rate_limit", "incomplete_index", "provider", "budget"}
    if any(diagnostic.failure_kind in transient_failures for diagnostic in meaningful):
        return "partial"
    if meaningful:
        return "error"
    return "no_hit"


def _stats(responses: list[ProviderResponse], *, elapsed_ms: float) -> Stats:
    provider_counts: dict[str, int] = {}
    incomplete_providers: set[str] = set()
    request_count = 0
    for response in responses:
        request_count += response.request_count
        provider_counts[response.provider] = len(response.hits)
        for diag in response.diagnostics:
            if diag.outcome == "partial" or diag.failure_kind in (
                "incomplete_index",
                "rate_limit",
                "budget",
            ):
                incomplete_providers.add(response.provider)
    return Stats(
        provider_counts=provider_counts,
        request_count=request_count,
        incomplete_providers=sorted(incomplete_providers),
        elapsed_ms=elapsed_ms,
    )


def _provider_summaries(responses: list[ProviderResponse]) -> list[dict[str, Any]]:
    """Project provider responses into JSON-safe analytics-only summaries."""
    return [
        {
            "provider": response.provider,
            "hit_count": len(response.hits),
            "request_count": response.request_count,
            "outcome": response.outcome,
            "compiled_queries": response.metadata.get("compiled_queries"),
            "payload_json": response.metadata,
        }
        for response in responses
    ]


def _select_rerank_profile(
    plan: QueryPlan,
    request: CodeSearchRequest,
) -> RerankProfile:
    """Select the internal code-search rerank instructions for this mode."""
    if request.mode == "discovery":
        return "hybrid"
    return "code"


def _repositories(responses: list[ProviderResponse], hits: list[Any]) -> list[RepoCandidate]:
    merged: dict[str, RepoCandidate] = {}
    for response in responses:
        raw = response.metadata.get("repositories", [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            try:
                candidate = (
                    item if isinstance(item, RepoCandidate) else RepoCandidate.model_validate(item)
                )
            except (TypeError, ValueError):
                continue
            merged.setdefault(candidate.name_with_owner.casefold(), candidate)
    evidence: dict[str, list[Any]] = {}
    for hit in hits:
        if hit.repository:
            evidence.setdefault(hit.repository.casefold(), []).append(hit)
    for key, candidate in merged.items():
        proof = evidence.get(key, [])
        candidate.proof_hits = len(proof)
        candidate.proof_paths = list(dict.fromkeys(hit.path for hit in proof if hit.path))[:8]
        candidate.proof_providers = sorted({hit.provider for hit in proof})
        candidate.verified = bool(proof)
    return sorted(
        merged.values(),
        key=lambda item: (item.discovery_rank or 10_000, -item.stars, item.name_with_owner),
    )


async def execute_code_search(
    request: CodeSearchRequest,
    plan: QueryPlan,
    *,
    http_client: httpx.AsyncClient,
) -> CodeSearchResultType:
    """Infer and execute retrieval channels, with exclusive Hugging Face asset mode."""

    started = time.monotonic()
    if request.regexp and not plan.regex_source:
        stats = _stats([], elapsed_ms=(time.monotonic() - started) * 1000)
        diagnostic = Diagnostic(
            provider="code_search",
            outcome="skipped",
            message="regexp=true but the query is not a valid regular expression; nothing was searched.",
            failure_kind="validation",
            query=request.query,
            details={"regex_drop": True},
        )
        return CodeSearchResultType(
            query=request.query,
            outcome="no_hit",
            results=[],
            repositories=[],
            diagnostics=[diagnostic],
            stats=stats,
            query_metadata=plan.metadata,
            provider_summaries=[],
        )

    if request.mode == "huggingface":
        response = await _run_provider(
            "huggingface",
            search_huggingface(plan, request, http_client=http_client),
        )
        responses = [response]
        hits = list(response.hits)
        diagnostics = list(response.diagnostics)
        if not hits and not diagnostics:
            diagnostics.append(
                Diagnostic(
                    provider="huggingface",
                    outcome="no_hit",
                    message="No Hugging Face model or dataset cards matched the query.",
                    failure_kind="validation",
                    query=request.query,
                )
            )
        stats = _stats(responses, elapsed_ms=(time.monotonic() - started) * 1000)
        stats.returned_count = len(hits)
        stats.estimated_tokens = sum(len(hit.model_dump_json()) for hit in hits) // 4
        query_metadata = plan.metadata
        query_metadata.compiled_queries = {
            "huggingface": list(response.metadata.get("compiled_queries", []))
        }
        stats.elapsed_ms = (time.monotonic() - started) * 1000
        return CodeSearchResultType(
            query=request.query,
            outcome=_outcome(responses, len(hits)),  # type: ignore[arg-type]
            results=hits,
            repositories=[],
            diagnostics=diagnostics,
            stats=stats,
            query_metadata=query_metadata,
            provider_summaries=_provider_summaries(responses),
        )

    if request.mode == "issues":
        response = await _run_provider(
            "github",
            search_github_issues(plan, request, http_client=http_client),
        )
        responses = [response]
        hits = list(response.hits)
        diagnostics = list(response.diagnostics)
        if not hits and not diagnostics:
            diagnostics.append(
                Diagnostic(
                    provider="github",
                    outcome="no_hit",
                    message="No GitHub Issues or Discussions matched the query.",
                    failure_kind="validation",
                    query=request.query,
                )
            )
        stats = _stats(responses, elapsed_ms=(time.monotonic() - started) * 1000)
        stats.returned_count = len(hits)
        stats.estimated_tokens = sum(len(hit.model_dump_json()) for hit in hits) // 4
        query_metadata = plan.metadata
        query_metadata.compiled_queries = {
            "github": list(response.metadata.get("compiled_queries", []))
        }
        stats.elapsed_ms = (time.monotonic() - started) * 1000
        return CodeSearchResultType(
            query=request.query,
            outcome=_outcome(responses, len(hits)),  # type: ignore[arg-type]
            results=hits,
            repositories=[],
            diagnostics=diagnostics,
            stats=stats,
            query_metadata=query_metadata,
            provider_summaries=_provider_summaries(responses),
        )

    pre_diagnostics: list[Diagnostic] = []
    operations: list[tuple[str, Any]] = [
        ("sourcegraph", search_sourcegraph(plan, request, http_client=http_client)),
        ("grep.app", search_grepapp(plan, request, http_client=http_client)),
        ("exa", search_exa(plan, request, http_client=http_client)),
    ]
    if plan.search_text or not plan.qualifiers:
        operations.insert(0, ("github", search_github(plan, request, http_client=http_client)))
    else:
        pre_diagnostics.append(
            Diagnostic(
                provider="github",
                outcome="skipped",
                message="Qualifier-only query cannot be executed by GitHub REST; skipped.",
                failure_kind="validation",
                query=request.query,
            )
        )
    responses = await asyncio.gather(
        *(_run_provider(name, operation) for name, operation in operations),
        return_exceptions=False,
    )
    for response in responses:
        response.hits, scope_diagnostic = filter_scoped_hits(plan, request, response.hits)
        if scope_diagnostic is not None:
            response.diagnostics.append(scope_diagnostic)

    hits = [hit for response in responses for hit in response.hits]
    diagnostics = [
        *pre_diagnostics,
        *(diagnostic for response in responses for diagnostic in response.diagnostics),
    ]
    stats = _stats(responses, elapsed_ms=(time.monotonic() - started) * 1000)

    preliminary = rank_candidates(
        plan,
        hits,
        max_results=None,
    )

    if request.mode == "code":
        code_candidates = [
            hit
            for hit in preliminary
            if hit.repository and hit.path and hit.result_kind == "code_match"
        ]
        other_hits = [hit for hit in preliminary if hit not in code_candidates]

        if code_candidates:
            file_sources, hydration_diagnostics = await hydrate_sources(
                code_candidates,
                http_client=http_client,
                max_files=request.budget.max_hydrate_files,
                max_chars_per_file=request.budget.max_hydrated_chars_per_file,
            )
            diagnostics.extend(hydration_diagnostics)
            stats.hydration_count = len(file_sources)
            stats.truncated = stats.truncated or any(
                d.failure_kind == "budget" for d in hydration_diagnostics
            )
            extracted = extract_source_windows(
                plan,
                file_sources,
                code_candidates,
                max_results=request.budget.max_rerank_candidates,
            )
            hits = extracted + other_hits
        else:
            hits = other_hits
    else:
        hits = preliminary

    if hits:
        rerank_profile = _select_rerank_profile(plan, request)
        rerank_input_count = len(hits)
        rerank_started = time.monotonic()
        rerank = await rerank_code_hits(
            request.query,
            hits,
            research_goal=request.research_goal,
            profile=rerank_profile,
            max_candidates=request.budget.max_rerank_candidates,
            max_results=request.budget.max_rerank_results,
        )
        hits = rerank.hits
        stats.rerank_count = rerank.reranked_count
        stats.rerank_provider = rerank.provider
        stats.rerank_model = rerank.model
        stats.rerank_status = rerank.metadata.get("status")
        stats.rerank_duration_ms = (time.monotonic() - rerank_started) * 1000
        stats.rerank_input_count = rerank_input_count
        stats.rerank_output_count = len(hits)
        stats.rerank_payload = rerank.metadata
        if rerank.diagnostic:
            stats.rerank_diagnostic_outcome = rerank.diagnostic.outcome
            stats.rerank_diagnostic_message = rerank.diagnostic.message
            diagnostics.append(rerank.diagnostic)

    hits = [normalize_hit_metadata(hit) for hit in hits[: request.max_results]]
    stats.returned_count = len(hits)
    stats.estimated_tokens = sum(len(hit.model_dump_json()) for hit in hits) // 4
    stats.elapsed_ms = (time.monotonic() - started) * 1000

    if not hits:
        active_qualifiers = [f"{k}:{v}" for k, v in plan.qualifiers]
        guidance_parts = ["No code matches found."]
        if active_qualifiers:
            guidance_parts.append(
                f"Consider relaxing scope qualifiers: {', '.join(active_qualifiers)}."
            )
        if plan.regex_source:
            guidance_parts.append(
                "Consider verifying regex syntax or testing with literal/symbol search."
            )
        elif plan.mode == "code":
            guidance_parts.append(
                "Try searching with specific function/class identifier names, or use mode='discovery'. "
                "For library documentation, use quick_web_search mode='docs'."
            )
        diagnostics.append(
            Diagnostic(
                provider="code_search",
                outcome="no_hit",
                message=" ".join(guidance_parts),
                failure_kind="validation",
                query=request.query,
                details={"qualifiers": dict(plan.qualifiers), "mode": plan.mode},
            )
        )

    query_metadata = plan.metadata
    query_metadata.compiled_queries = {
        response.provider: list(response.metadata.get("compiled_queries", []))
        for response in responses
        if isinstance(response.metadata.get("compiled_queries"), list)
    }
    return CodeSearchResultType(
        query=request.query,
        outcome=_outcome(responses, len(hits)),  # type: ignore[arg-type]
        results=hits,
        repositories=_repositories(responses, hits),
        diagnostics=diagnostics,
        stats=stats,
        query_metadata=query_metadata,
        provider_summaries=_provider_summaries(responses),
    )
