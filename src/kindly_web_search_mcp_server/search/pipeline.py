"""End-to-end search pipeline for the 0.2 control plane."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx

from ..cache.result_memory import get_result_memory_store
from ..embeddings import embed_query
from ..models import WebSearchResponse
from ..settings import settings
from ..telemetry import record_domain_diversity
from ..training.query_understanding_jsonl import append_query_outcome_record
from ..training.session_state import get_session_state_store
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event, set_current_run_key
from ..rerank import rerank_results
from ..rerank.models import RerankEmbeddingContext
from ..ab_testing.wiring import get_ab_overrides
from ..ab_testing.shadow_runner import run_shadow
from ..analytics.duckdb_store import (
    insert_search_run as analytics_insert_search_run,
    insert_final_results as analytics_insert_final_results,
    insert_query_rewrites as analytics_insert_query_rewrites,
)
from ..analytics.judge_runner import run_judge_evaluation
from ..analytics.quality_metrics import compute_search_quality
from .branch_executor import (
    execute_search_branches,
)
from .branch_planner import build_search_branch_specs
from .finalize_results import build_search_response, maybe_extract_entities
from .flow_observability import emit_result_lists_summary, serialize_query_variants
from .merge import merge_search_results
from .options import SearchOptions
from . import search_single_query
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
    research_goal: str | None,
    search_options: SearchOptions | None,
    session_id: str | None = None,
) -> WebSearchResponse:
    run_key = str(uuid.uuid4())
    set_current_run_key(run_key)
    pipeline_start = asyncio.get_event_loop().time()

    normalized_query = normalize_query(query)
    understanding = await resolve_query_understanding(
        query=query,
        research_goal=research_goal,
        intent_hint=None,
        session_id=session_id,
        run_key=run_key,
    )
    context = build_search_context(
        query=query,
        research_goal=research_goal,
        session_id=session_id,
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
        intent=context.intent,
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
        providers_requested=context.profile_name,
    )

    # Best-effort dual-write: query rewrites
    try:
        for index, variant in enumerate(rewrite_variants):
            analytics_insert_query_rewrites(
                run_key=run_key,
                variant_index=index,
                query=variant.query,
                kind=getattr(variant, "kind", None),
                target=getattr(variant, "target", None),
                weight=getattr(variant, "weight", None),
                reason=getattr(variant, "why", None),
                branch_type=getattr(variant, "branch_type", None),
                max_results=getattr(variant, "max_results", None),
                model=rewrite_model,
                duration_ms=None,
                payload_json={
                    "must_keep_terms": getattr(variant, "must_keep_terms", None),
                },
            )
    except Exception as exc:
        logger.debug("analytics insert_query_rewrites failed: %s", exc)

    active_provider_names = list(provider_plan.provider_names)
    read_timeout = settings.search_http_read_timeout_seconds
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.search_http_connect_timeout_seconds,
            read=read_timeout,
            write=read_timeout,
            pool=read_timeout,
        ),
        follow_redirects=True,
    ) as client:
        branch_specs = build_search_branch_specs(
            normalized_query=normalized_query,
            rewrite_variants=rewrite_variants,
            num_results=num_results,
            active_provider_names=active_provider_names,
            provider_plan=provider_plan,
        )
        branch_batch = await execute_search_branches(
            branch_specs,
            http_client=client,
            diagnostics=diagnostics,
            search_options=search_options,
            provider_plan=provider_plan,
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

    # ------------------------------------------------------------------
    # A/B experiment override: check if this run_key is enrolled in
    # a provider_weights experiment
    # ------------------------------------------------------------------
    base_provider_weights = provider_plan.provider_weights or profile.provider_weights
    pw_ab_overrides = get_ab_overrides(
        run_key=run_key, layer="provider_weights"
    ) if run_key else None
    pw_shadow_mode = bool(pw_ab_overrides and pw_ab_overrides.get("shadow_mode"))

    if pw_ab_overrides and not pw_shadow_mode:
        # Non-shadow mode: merge variant provider_weights over the base weights
        pw_config = pw_ab_overrides.get("config", {})
        variant_weights = pw_config.get("provider_weights", {})
        if variant_weights:
            merged_weights = dict(base_provider_weights)
            merged_weights.update(variant_weights)
            effective_weights = merged_weights
        else:
            effective_weights = base_provider_weights
    else:
        effective_weights = base_provider_weights

    merged = merge_search_results(
        result_lists,
        list_weights=list_weights,
        provider_weights=effective_weights,
        run_key=run_key,
    )

    # Shadow mode: fire-and-forget variant merge and record comparison
    if pw_shadow_mode and pw_ab_overrides and run_key:
        pw_ab_config = pw_ab_overrides.get("config", {})
        shadow_variant_weights = pw_ab_config.get("provider_weights", {})
        if shadow_variant_weights:
            shadow_weights = dict(base_provider_weights)
            shadow_weights.update(shadow_variant_weights)
        else:
            shadow_weights = base_provider_weights

        control_result_summary = {
            "num_results": len(merged),
            "domains": len(set(r.domain for r in merged if r.domain)),
        }

        async def _shadow_merge_fn(
            result_lists=result_lists,
            list_weights=list_weights,
            shadow_weights=shadow_weights,
            run_key=run_key,
        ) -> dict:
            shadow_merged = merge_search_results(
                result_lists,
                list_weights=list_weights,
                provider_weights=shadow_weights,
                run_key=run_key,
            )
            return {
                "num_results": len(shadow_merged),
                "domains": len(set(r.domain for r in shadow_merged if r.domain)),
                "top_links": [r.link for r in shadow_merged[:5]],
            }

        asyncio.ensure_future(
            run_shadow(
                run_key=run_key,
                experiment_id=pw_ab_overrides["experiment_id"],
                variant=pw_ab_overrides["variant_key"],
                layer="provider_weights",
                shadow_fn=_shadow_merge_fn,
                shadow_kwargs={},
                control_duration_ms=0.0,
                control_result_summary=control_result_summary,
            )
        )

    record_domain_diversity(
        len(set(r.domain for r in merged if r.domain)),
        len(merged),
        active_provider_names,
    )

    embedding_ctx_for_index: RerankEmbeddingContext | None = None
    if settings.reranking_enabled and len(merged) > 1:
        try:
            # Check A/B testing overrides for the reranking layer
            ab_overrides = get_ab_overrides(
                run_key=run_key, layer="reranking"
            ) if run_key else None

            if ab_overrides and ab_overrides.get("shadow_mode"):
                # Shadow mode: run production rerank normally, fire-and-forget variant
                ab_config = ab_overrides.get("config", {})
                pre_rerank_results = list(merged)
                rerank_out = await rerank_results(
                    query=normalized_query,
                    candidates=merged,
                    top_k=num_results,
                    searxng_time_range=search_options.searxng_time_range
                    if search_options
                    else None,
                    research_goal=research_goal,
                    query_type_hint=context.intent,
                    run_key=run_key,
                )
                embedding_ctx_for_index = rerank_out.embedding_context
                merged = rerank_out.results

                # Fire-and-forget the variant rerank as a shadow
                shadow_kwargs = {
                    "query": normalized_query,
                    "candidates": pre_rerank_results,
                    "top_k": num_results,
                    "searxng_time_range": search_options.searxng_time_range
                    if search_options
                    else None,
                    "research_goal": research_goal,
                    "query_type_hint": context.intent,
                    "run_key": run_key,
                    "ab_overrides": ab_config or None,
                }
                asyncio.ensure_future(
                    run_shadow(
                        run_key=run_key,
                        experiment_id=ab_overrides["experiment_id"],
                        variant=ab_overrides["variant_key"],
                        layer="reranking",
                        shadow_fn=rerank_results,
                        shadow_kwargs=shadow_kwargs,
                        control_duration_ms=0.0,
                        control_result_summary=None,
                    )
                )
            else:
                # Normal / A/B override mode: pass ab_overrides config directly
                ab_config = ab_overrides.get("config") if ab_overrides else None
                rerank_out = await rerank_results(
                    query=normalized_query,
                    candidates=merged,
                    top_k=num_results,
                    searxng_time_range=search_options.searxng_time_range
                    if search_options
                    else None,
                    research_goal=research_goal,
                    query_type_hint=context.intent,
                    run_key=run_key,
                    ab_overrides=ab_config,
                )
                embedding_ctx_for_index = rerank_out.embedding_context
                merged = rerank_out.results
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
        if session_state is not None:
            session_state.last_intent = context.intent
        for result in final_results:
            get_session_state_store().mark_seen(session_id, result.link)

    if (
        settings.web_results_index_enabled
        and final_results
        and embedding_ctx_for_index is not None
    ):
        try:
            from ..index import index_final_results

            dense_embeddings: list[list[float]] = []
            indexed_results: list = []
            texts: list[str] = []
            for r in final_results:
                # Skip results that originated from Qdrant to prevent feedback loop
                if r.providers and "qdrant" in r.providers:
                    continue
                emb = embedding_ctx_for_index.find(r.link.strip())
                if emb is None:
                    continue
                dense_embeddings.append(emb.dense)
                texts.append(emb.text)
                indexed_results.append(r)

            entity_dicts = [
                {"text": e.text, "label": e.label}
                for r in final_results
                if r.entities
                for e in r.entities
            ] or None

            if indexed_results:
                asyncio.ensure_future(
                    index_final_results(
                        normalized_query,
                        indexed_results,
                        dense_embeddings,
                        texts=texts,
                        intent=context.intent,
                        entities=entity_dicts,
                    )
                )
        except Exception as exc:
            logger.debug("index_final_results fire-and-forget failed: %s", exc)

    rewrite_policy = RewritePolicy(
        mode="expand" if rewrite else "bypass",
        reason=understanding.rationale,
        must_keep_terms=list(context.must_keep_terms),
    )
    duration_ms = round((asyncio.get_event_loop().time() - pipeline_start) * 1000.0, 3)
    _, _, response = build_search_response(
        query=query,
        normalized_query=normalized_query,
        research_goal=research_goal,
        rewrite=rewrite,
        rewrite_policy=rewrite_policy,
        unique_domains=len(set(r.domain for r in merged if r.domain)),
        merged=merged,
        final_results=final_results,
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

    # Best-effort dual-write: search_run
    try:
        analytics_insert_search_run(
            run_key=run_key,
            query=query,
            normalized_query=normalized_query,
            research_goal=research_goal,
            num_results_requested=num_results,
            rewrite_enabled=rewrite,
            session_id=session_id,
            duration_ms=duration_ms,
            final_result_count=len(final_results),
            candidate_count=candidate_count,
            has_more=has_more,
            result_offset=result_offset,
            status="success",
            error_type=None,
            payload_json={
                "intent": context.intent,
                "confidence": context.confidence,
                "profile": context.profile_name,
                "providers_active": active_provider_names,
                "rewrite_variants": len(rewrite_variants),
                "rewrite_model": rewrite_model,
            },
        )
    except Exception as exc:
        logger.debug("analytics insert_search_run failed: %s", exc)

    # Best-effort dual-write: final_results
    try:
        for position, result in enumerate(final_results, start=1):
            analytics_insert_final_results(
                run_key=run_key,
                rank=position,
                link=result.link,
                title=result.title,
                snippet=result.snippet,
                domain=result.domain or "",
                final_score=result.score,
                payload_json={
                    "provider_count": result.provider_count,
                    "providers": result.providers or [],
                    "entities": (
                        [e.model_dump() for e in result.entities]
                        if result.entities
                        else None
                    ),
                },
            )
    except Exception as exc:
        logger.debug("analytics insert_final_results failed: %s", exc)

    # Best-effort dual-write: search quality metrics
    try:
        compute_search_quality(run_key)
    except Exception as exc:
        logger.debug("compute_search_quality failed: %s", exc)

    # Judge evaluation (opt-in, fire-and-forget, never blocks the pipeline)
    if settings.judge_evaluation_enabled:
        try:
            asyncio.ensure_future(
                run_judge_evaluation(
                    run_key=run_key,
                    query=query,
                    intent=context.intent,
                    results=final_results,
                    tool_name="web_search",
                )
            )
        except Exception as exc:
            logger.debug("judge evaluation fire-and-forget failed: %s", exc)

    return response
