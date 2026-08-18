"""Backend-selected multi-channel code-search orchestration."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .docs import search_docs
from .exa import search_exa
from .github import hydrate_github_hits, search_github
from .grepapp import search_grepapp
from .filters import filter_scoped_hits
from .models import (
    CodeSearchRequest,
    CodeSearchResultType,
    normalize_hit_metadata,
    Diagnostic,
    ProviderResponse,
    RepoCandidate,
    Stats,
)
from .query import QueryPlan
from .ranking import compact_hits, rank_hits, verify_regex_hits
from .reranking import RerankProfile, rerank_code_hits
from .sourcegraph import search_sourcegraph


def _branch_failure(provider: str, exc: BaseException) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        diagnostics=[
            Diagnostic(
                provider=provider,
                outcome="error",
                message=f"{provider} branch failed ({type(exc).__name__})",
                failure_kind="provider",
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
            if diag.outcome == "partial" or diag.failure_kind in ("incomplete_index", "rate_limit", "budget"):
                incomplete_providers.add(response.provider)
    return Stats(
        provider_counts=provider_counts,
        request_count=request_count,
        incomplete_providers=sorted(incomplete_providers),
        elapsed_ms=elapsed_ms,
    )


def _select_rerank_profile(
    plan: QueryPlan,
    request: CodeSearchRequest,
) -> RerankProfile:
    """Select the internal code-search rerank instructions for this mode."""
    if request.mode == "docs":
        return "documentation"
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
    """Infer and execute useful retrieval channels without a public mode switch."""

    started = time.monotonic()
    operations: list[tuple[str, Any]] = [
        ("github", search_github(plan, request, http_client=http_client)),
        ("sourcegraph", search_sourcegraph(plan, request, http_client=http_client)),
        ("grep.app", search_grepapp(plan, request, http_client=http_client)),
        ("exa", search_exa(plan, request, http_client=http_client)),
    ]
    responses = await asyncio.gather(
        *(_run_provider(name, operation) for name, operation in operations),
        return_exceptions=False,
    )
    if request.mode == "docs" or request.repo_name or request.library_name:
        responses.extend(await search_docs(plan, request, http_client=http_client))
    for response in responses:
        response.hits, scope_diagnostic = filter_scoped_hits(plan, request, response.hits)
        if scope_diagnostic is not None:
            response.diagnostics.append(scope_diagnostic)

    hits = [hit for response in responses for hit in response.hits]
    diagnostics = [diagnostic for response in responses for diagnostic in response.diagnostics]
    stats = _stats(responses, elapsed_ms=(time.monotonic() - started) * 1000)

    preliminary = rank_hits(
        plan,
        hits,
        max_results=max(request.max_results, request.budget.max_rerank_candidates),
    )
    if any(hit.provider == "github" for hit in preliminary):
        hydration_diagnostics, hydration_count, hydration_truncated = await hydrate_github_hits(
            preliminary,
            http_client=http_client,
            max_files=request.budget.max_hydrate_files,
            max_chars_per_file=request.budget.max_hydrated_chars_per_file,
            deep=request.deep,
        )
        diagnostics.extend(hydration_diagnostics)
        stats.hydration_count = hydration_count
        stats.truncated = stats.truncated or hydration_truncated
    if plan.local_regex is not None:
        preliminary = verify_regex_hits(preliminary, plan.local_regex)
    hits = rank_hits(
        plan,
        preliminary,
        max_results=max(request.max_results, request.budget.max_rerank_candidates),
    )

    if hits:
        rerank_profile = _select_rerank_profile(plan, request)
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
        if rerank.diagnostic:
            diagnostics.append(rerank.diagnostic)

    candidate_count_pre_compact = len(hits)
    hits, compacted = compact_hits(
        hits,
        max_output_chars=request.budget.max_output_chars,
        max_results=request.max_results,
    )
    hits = [normalize_hit_metadata(hit) for hit in hits]
    stats.truncated = stats.truncated or compacted
    stats.dropped_count = max(0, candidate_count_pre_compact - len(hits))
    stats.returned_count = len(hits)
    stats.estimated_tokens = sum(len(hit.model_dump_json()) for hit in hits) // 4
    stats.elapsed_ms = (time.monotonic() - started) * 1000

    if not hits:
        active_qualifiers = [f"{k}:{v}" for k, v in plan.qualifiers]
        guidance_parts = ["No code matches found."]
        if active_qualifiers:
            guidance_parts.append(f"Consider relaxing scope qualifiers: {', '.join(active_qualifiers)}.")
        if plan.regex_source:
            guidance_parts.append("Consider verifying regex syntax or testing with literal/symbol search.")
        elif plan.mode == "code":
            guidance_parts.append("Try searching with specific function/class identifier names, or use mode='docs'/'discovery'.")
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
    )
