"""End-to-end search pipeline for the 0.2 control plane."""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..cache.result_memory import get_result_memory_store
from ..embeddings import embed_query
from ..models import WebSearchResponse
from ..settings import settings
from ..telemetry import record_domain_diversity
from ..training.query_understanding_jsonl import append_query_outcome_record
from ..training.session_state import get_session_state_store
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from ..search_instrumented import search_single_query
from ..rerank import rerank_results
from .branch_executor import (
    SearchBranchSpec,
    execute_search_branches,
    select_providers_for_variant,
)
from .finalize_results import build_search_response, maybe_extract_entities
from .flow_observability import emit_result_lists_summary, serialize_query_variants
from .merge import merge_search_results
from .options import SearchOptions
from .pipeline_builders import build_rewrite_variants, build_search_context
from .profiles.registry import apply_profile_search_options
from .profiles.resolve import resolve_search_profile
from .provider_plan import build_cache_identity, build_provider_execution_plan
from .query_policy import RewritePolicy
from .result_memory_pipeline import (
    inject_result_memory_candidates,
    store_result_memory_results,
)
from .understanding.resolver import resolve_query_understanding
from .normalize import normalize_query

logger = logging.getLogger(__name__)


async def run_search_pipeline(
    query: str,
    *,
    num_results: int,
    rewrite: bool,
    diagnostics: Diagnostics | None,
    providers: list[str] | None,
    research_goal: str | None,
    search_options: SearchOptions | None,
    query_entities: list | None = None,
    session_id: str | None = None,
) -> WebSearchResponse:
    del query_entities
    normalized_query = normalize_query(query)
    understanding = await resolve_query_understanding(
        query=query,
        research_goal=research_goal,
        intent_hint=None,
        session_id=session_id,
    )
    context = build_search_context(
        query=query,
        research_goal=research_goal,
        session_id=session_id,
        providers=providers,
        num_results=num_results,
        search_options=search_options,
        understanding_intent=understanding.intent,
        understanding_confidence=understanding.confidence,
        understanding_should_decompose=understanding.should_decompose,
        understanding_rationale=understanding.rationale,
        entities=understanding.entities,
        must_keep_terms=understanding.must_keep_terms,
    )
    profile = resolve_search_profile(context.intent)
    search_options = apply_profile_search_options(search_options, profile)
    provider_plan = build_provider_execution_plan(
        profile=profile,
        context=context,
        public_options=search_options,
    )
    cache_identity = build_cache_identity(
        query=normalized_query,
        profile=profile,
        provider_plan=provider_plan,
        search_options=search_options,
        rewrite_enabled=rewrite,
    )
    if rewrite:
        rewrite_variants, rewrite_model = await build_rewrite_variants(
            context=context,
            understanding_intent=context.intent,
            must_keep_terms=list(context.must_keep_terms),
        )
    else:
        from .query_rewrite_models import QueryVariant

        rewrite_variants = [
            QueryVariant(
                kind="original",
                target="keyword",
                query=normalized_query,
                why="Rewrite disabled by caller.",
                weight=1.0,
            )
        ]
        rewrite_model = "disabled"

    emit_observability_event(
        logger,
        "search.pipeline.plan",
        query=query,
        normalized_query=normalized_query,
        intent=context.intent,
        confidence=context.confidence,
        should_decompose=context.should_decompose,
        profile=context.profile_name,
        cache_identity=cache_identity,
        rewrite_model=rewrite_model,
        variants=serialize_query_variants(rewrite_variants),
        providers_requested=providers or [],
    )

    active_provider_names = list(provider_plan.provider_names)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5, read=20, write=20, pool=20),
        follow_redirects=True,
    ) as client:
        branch_specs: list[SearchBranchSpec] = []
        for index, variant in enumerate(rewrite_variants):
            variant_providers = select_providers_for_variant(
                variant, active_provider_names
            )
            branch_specs.append(
                SearchBranchSpec(
                    index=index,
                    query=variant.query,
                    branch_type=variant.branch_type or variant.kind,
                    weight=variant.weight,
                    providers=variant_providers or active_provider_names or providers,
                    max_results=variant.max_results or num_results,
                    reason=variant.reason or variant.why,
                    must_keep_terms=variant.must_keep_terms,
                    provider_arguments={
                        name: bundle.arguments
                        for name, bundle in provider_plan.options.bundles.items()
                        if bundle.arguments
                    }
                    or None,
                )
            )
        branch_batch = await execute_search_branches(
            branch_specs,
            http_client=client,
            diagnostics=diagnostics,
            search_options=search_options,
            search_runner=search_single_query,
            max_concurrency=settings.query_decomposition_max_concurrency,
        )

    result_lists = branch_batch.result_lists
    branch_queries = branch_batch.branch_queries
    branch_providers = branch_batch.branch_providers
    list_weights = branch_batch.list_weights

    emit_result_lists_summary(
        logger,
        "search.pipeline.branches",
        query=query,
        result_lists=result_lists,
        branch_queries=branch_queries,
        branch_providers=branch_providers,
        list_weights=list_weights,
        branch_metadata=branch_batch.branch_metadata,
    )

    (
        result_lists,
        list_weights,
        query_vec_for_mem,
        memory_injected,
    ) = await inject_result_memory_candidates(
        query=query,
        normalized_query=normalized_query,
        result_lists=result_lists,
        list_weights=list_weights,
        get_result_memory_store_fn=get_result_memory_store,
        embed_query_fn=embed_query,
    )

    merged = merge_search_results(
        result_lists,
        list_weights=list_weights,
        provider_weights=provider_plan.provider_weights or profile.provider_weights,
    )
    record_domain_diversity(
        len(set(r.domain for r in merged if r.domain)),
        len(merged),
        active_provider_names,
    )

    if settings.reranking_enabled and len(merged) > 1:
        try:
            merged = await rerank_results(
                query=normalized_query,
                candidates=merged,
                top_k=num_results,
                searxng_time_range=search_options.searxng_time_range if search_options else None,
                research_goal=research_goal,
                query_type_hint=context.intent,
            )
        except Exception as exc:
            logger.warning("Reranking failed in search pipeline: %s", exc)

    await store_result_memory_results(
        normalized_query=normalized_query,
        results=merged,
        query_embedding=query_vec_for_mem,
        get_result_memory_store_fn=get_result_memory_store,
        embed_query_fn=embed_query,
    )

    if memory_injected:
        injected_urls = {result.link for result in memory_injected}
        survived = [result for result in merged if result.link in injected_urls]
        if survived:
            emit_observability_event(
                logger,
                "result_memory.candidate_survived",
                query=query,
                injected_count=len(memory_injected),
                survived_count=len(survived),
                survived_urls=[result.link for result in survived][:5],
            )

    result_offset = search_options.result_offset if search_options else 0
    final_results = merged[result_offset : result_offset + num_results]
    candidate_count = len(merged)
    has_more = result_offset + len(final_results) < candidate_count
    next_offset = result_offset + len(final_results) if has_more else None
    final_results = await maybe_extract_entities(query=query, results=final_results)

    if session_id:
        session_state = get_session_state_store().get(session_id)
        session_state.last_intent = context.intent
        for result in final_results:
            get_session_state_store().mark_seen(session_id, result.link)

    if settings.web_results_index_enabled and final_results:
        try:
            from ..index import index_final_results

            asyncio.ensure_future(index_final_results(normalized_query, final_results))
        except Exception as exc:
            logger.debug("index_final_results fire-and-forget failed: %s", exc)

    rewrite_policy = RewritePolicy(
        mode="expand" if rewrite else "bypass",
        reason=understanding.rationale,
        must_keep_terms=list(context.must_keep_terms),
    )
    _, _, response = build_search_response(
        query=query,
        normalized_query=normalized_query,
        research_goal=research_goal,
        rewrite=rewrite,
        rewrite_policy=rewrite_policy,
        unique_domains=len(set(r.domain for r in merged if r.domain)),
        merged=merged,
        final_results=final_results,
        providers=providers,
        result_offset=result_offset,
        candidate_count=candidate_count,
        has_more=has_more,
        next_offset=next_offset,
    )

    try:
        await append_query_outcome_record(
            context=context,
            understanding=understanding,
            results=[
                {
                    "title": result.title,
                    "link": result.link,
                    "snippet": result.snippet,
                    "providers": result.providers or [],
                }
                for result in final_results
            ],
            path=settings.query_understanding_jsonl_path,
            session_id=session_id,
        )
    except Exception as exc:
        logger.warning("query outcome JSONL write failed: %s", exc)
    return response
