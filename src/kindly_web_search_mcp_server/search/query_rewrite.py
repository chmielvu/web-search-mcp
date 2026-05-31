"""Query rewrite with provider-aware keyword and neural prompt paths."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from opentelemetry import trace

from .query_classifier_client import get_functiongemma_client
from ..settings import settings
from ..telemetry import (
    REWRITE_MODEL,
    REWRITE_POLICY,
    SEARCH_QUERY,
    record_query_length,
    record_query_rewrite,
)
from ..utils.diagnostics import Diagnostics
from ..utils.observability import emit_observability_event
from .normalize import normalize_query
from .query_policy_resolver import resolve_query_routing
from .query_rewrite_models import (
    QueryRewritePlan,
    QueryVariant,
    RewriteIntent,
)
from .query_rewrite_router import get_query_rewrite_router
from .query_rewrite_validate import (
    validate_community_variants,
    validate_keyword_variants,
    validate_neural_variants,
)
from .query_rewrite_plan import (
    active_target_flags,
    build_fallback_plan,
    build_rewrite_plan,
)
from .query_rewrite_requests import request_variants

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


async def rewrite_search_query(
    query: str,
    *,
    intent: RewriteIntent = "code",
    diagnostics: Diagnostics | None = None,
    research_goal: str | None = None,
    providers: list[str] | None = None,
) -> QueryRewritePlan:
    start_time = time.time()
    normalized_query = normalize_query(query)
    policy = await resolve_query_routing(query, diagnostics=diagnostics)
    max_variants = max(1, min(settings.query_rewrite_max_variants, 3))
    record_query_length(len(query), policy=policy.mode)

    with tracer.start_as_current_span(
        "query.rewrite",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: normalized_query[:500],
            REWRITE_POLICY: policy.mode,
            REWRITE_MODEL: settings.query_rewrite_model,
        },
    ) as span:
        if not settings.query_rewrite_enabled:
            duration = time.time() - start_time
            record_query_rewrite("bypass", 1, False, duration, "disabled")
            return build_fallback_plan(query, policy, "Query rewriting disabled.")
        if policy.mode == "bypass":
            duration = time.time() - start_time
            record_query_rewrite("bypass", 1, True, duration, "bypass")
            return build_fallback_plan(query, policy, policy.reason)

        router = get_query_rewrite_router()
        if router is None:
            duration = time.time() - start_time
            record_query_rewrite("fallback", 1, False, duration, "fallback")
            return build_fallback_plan(
                query, policy, "LiteLLM Router not available or no API keys configured."
            )

        include_keyword, include_neural, include_community, active_provider_names = (
            active_target_flags(providers)
        )
        if not include_keyword and not include_neural and not include_community:
            return build_fallback_plan(
                query, policy, "No active providers resolved for query rewrite."
            )

        if diagnostics:
            diagnostics.emit(
                "query_rewrite.start",
                "Starting provider-aware query rewrite",
                {
                    "query": query,
                    "intent": intent,
                    "policy": policy.mode,
                    "must_keep_terms": policy.must_keep_terms,
                    "active_provider_names": active_provider_names,
                },
            )

        try:
            classifier_client = get_functiongemma_client()
            classifier = await classifier_client.classify_query(
                normalized_query,
                research_goal=research_goal,
                must_keep_terms=policy.must_keep_terms,
            )
            if diagnostics:
                diagnostics.emit(
                    "query_rewrite.classifier",
                    "FunctionGemma classified query",
                    {
                        "intent": classifier.intent,
                        "should_decompose": classifier.should_decompose,
                        "confidence": classifier.confidence,
                        "routing": classifier.routing.model_dump(),
                    },
                )

            filtered_include_keyword = include_keyword and classifier.routing.keyword
            filtered_include_neural = include_neural and classifier.routing.neural
            filtered_include_community = (
                include_community and classifier.routing.community
            )
            if not any(
                [filtered_include_keyword, filtered_include_neural, filtered_include_community]
            ):
                filtered_include_keyword = include_keyword
                filtered_include_neural = include_neural
                filtered_include_community = include_community

            decomposition = None
            subquestion_variants: list[QueryVariant] | None = None
            if settings.query_decomposition_enabled and classifier.should_decompose:
                decomposition = await classifier_client.decompose_query(
                    normalized_query,
                    research_goal=research_goal,
                    classifier=classifier,
                    must_keep_terms=policy.must_keep_terms,
                    max_subquestions=settings.query_decomposition_max_subquestions,
                )
                if decomposition.should_decompose and decomposition.sub_questions:
                    subquestion_variants = [
                        QueryVariant(
                            kind="subquestion",
                            target=sub_question.target,
                            query=sub_question.question,
                            why=sub_question.why,
                            weight=sub_question.weight,
                        )
                        for sub_question in decomposition.sub_questions
                    ]
                    if diagnostics:
                        diagnostics.emit(
                            "query_rewrite.decomposition",
                            "FunctionGemma decomposed query",
                            {
                                "should_decompose": decomposition.should_decompose,
                                "sub_questions": [sq.model_dump() for sq in decomposition.sub_questions],
                            },
                        )

            async def _safe_variants(
                target: str,
            ) -> tuple[list[QueryVariant], str | None] | None:
                try:
                    variants, model = await request_variants(
                        router=router,
                        query=normalized_query,
                        intent=intent,
                        target=target,
                        policy=policy,
                        diagnostics=diagnostics,
                        research_goal=research_goal,
                    )
                    return variants, model
                except Exception as exc:
                    logger.warning("%s rewrite target failed: %s", target, exc)
                    return None

            tasks = []
            task_names: list[str] = []
            if filtered_include_keyword:
                tasks.append(_safe_variants("keyword"))
                task_names.append("keyword")
            if filtered_include_neural:
                tasks.append(_safe_variants("neural"))
                task_names.append("neural")
            if filtered_include_community:
                tasks.append(_safe_variants("community"))
                task_names.append("community")
            results = await asyncio.gather(*tasks)  # type: ignore[var-annotated]

            keyword_raw: list[QueryVariant] = []
            neural_raw: list[QueryVariant] = []
            community_raw: list[QueryVariant] = []
            models_used: list[str] = []
            for target_name, raw in zip(task_names, results, strict=False):
                if raw is None:
                    continue
                variants, model = raw
                if model:
                    models_used.append(model)
                if target_name == "keyword":
                    keyword_raw = variants
                elif target_name == "neural":
                    neural_raw = variants
                elif target_name == "community":
                    community_raw = variants

            keyword_valid = validate_keyword_variants(
                keyword_raw,
                intent=intent,
                must_keep_terms=policy.must_keep_terms,
            )
            keyword_valid = [
                variant
                for variant in keyword_valid
                if normalize_query(variant.query).casefold()
                != normalized_query.casefold()
            ]
            neural_valid = validate_neural_variants(
                neural_raw,
                must_keep_terms=policy.must_keep_terms,
            )
            community_valid = validate_community_variants(
                community_raw,
                must_keep_terms=policy.must_keep_terms,
            )
            community_valid = [
                variant
                for variant in community_valid
                if normalize_query(variant.query).casefold()
                != normalized_query.casefold()
            ]
            plan = build_rewrite_plan(
                query=query,
                policy=policy,
                intent=intent,
                keyword_variants=keyword_valid,
                neural_variants=neural_valid,
                community_variants=community_valid,
                subquestion_variants=subquestion_variants,
                include_keyword=filtered_include_keyword,
                include_neural=filtered_include_neural,
                include_community=filtered_include_community,
                classifier=classifier,
                decomposition=decomposition,
                max_variants=max_variants,
            )
            duration = time.time() - start_time
            record_query_rewrite(
                "expand",
                len(plan.variants),
                False,
                duration,
                ",".join(models_used) or "unknown",
            )
            span.set_attribute(
                "rewrite.active_provider_names", ",".join(active_provider_names)
            )
            emit_observability_event(
                logger,
                "query.rewrite.completed",
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                intent=intent,
                research_goal=research_goal,
                providers_requested=providers or [],
                active_provider_names=active_provider_names,
                final_queries=plan.final_queries,
                variants=[variant.model_dump() for variant in plan.variants],
                duration_ms=round(duration * 1000, 3),
                models_used=models_used,
            )
            return plan
        except Exception as exc:
            logger.warning("Query rewrite failed, using original query: %s", exc)
            duration = time.time() - start_time
            record_query_rewrite("fallback", 1, False, duration, "error")
            emit_observability_event(
                logger,
                "query.rewrite.error",
                level=logging.WARNING,
                query=query,
                normalized_query=normalized_query,
                policy=policy.mode,
                intent=intent,
                research_goal=research_goal,
                error_type=type(exc).__name__,
                error_message=str(exc),
                final_queries=[normalized_query],
            )
            return build_fallback_plan(
                query, policy, "Rewrite failed; original query preserved."
            )
