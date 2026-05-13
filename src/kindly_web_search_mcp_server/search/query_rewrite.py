"""Query rewrite with provider-aware keyword and neural prompt paths."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import ValidationError

from opentelemetry import trace

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
from .provider_config import resolve_providers_for_search
from .query_policy import RewritePolicy
from .query_policy_resolver import resolve_query_routing
from .query_rewrite_models import (
    KEYWORD_PROVIDER_NAMES,
    NEURAL_PROVIDER_NAMES,
    QueryRewritePlan,
    QueryVariant,
    RewriteIntent,
)
from .query_rewrite_prompts import build_query_rewrite_messages
from .query_rewrite_router import get_query_rewrite_router
from .query_rewrite_validate import (
    dedupe_keep_order,
    inject_missing_terms,
    parse_query_rewrite_output,
    validate_keyword_variants,
    validate_neural_variants,
)

logger = logging.getLogger(__name__)
tracer: Any = trace.get_tracer("web-search-mcp")


def _fallback_plan(query: str, policy: RewritePolicy, reason: str) -> QueryRewritePlan:
    cleaned = normalize_query(query)
    original = QueryVariant(
        kind="original",
        target="all",
        query=cleaned,
        why=reason,
        weight=1.0,
    )
    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=[original],
        final_queries=[cleaned],
    )


def _active_target_flags(providers: list[str] | None) -> tuple[bool, bool, list[str]]:
    active_provider_names = [config.name for config in resolve_providers_for_search(providers)]
    has_keyword = any(name in KEYWORD_PROVIDER_NAMES for name in active_provider_names)
    has_neural = any(name in NEURAL_PROVIDER_NAMES for name in active_provider_names)
    return has_keyword, has_neural, active_provider_names


async def _request_variants(
    *,
    router: Any,
    query: str,
    intent: RewriteIntent,
    target: str,
    policy: RewritePolicy,
    diagnostics: Diagnostics | None,
    research_goal: str | None,
) -> tuple[list[QueryVariant], str]:
    messages = build_query_rewrite_messages(
        query=query,
        research_goal=research_goal,
        must_keep_terms=policy.must_keep_terms,
        intent=intent,
        target=target,
    )
    response = await asyncio.wait_for(
        router.acompletion(
            model="query-rewrite",
            messages=messages,
            temperature=settings.query_rewrite_temperature,
            response_format={"type": "json_object"},
        ),
        timeout=settings.query_rewrite_timeout_seconds,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("Expected string JSON content from LLM")
    parsed = parse_query_rewrite_output(content)
    if diagnostics:
        diagnostics.emit(
            "query_rewrite.raw_result",
            "Query rewrite call completed",
            {"target": target, "variant_count": len(parsed.variants)},
        )
    return parsed.variants, response.model or "unknown"


def _build_plan(
    *,
    query: str,
    policy: RewritePolicy,
    intent: RewriteIntent,
    keyword_variants: list[QueryVariant],
    neural_variants: list[QueryVariant],
    include_keyword: bool,
    include_neural: bool,
    max_variants: int,
) -> QueryRewritePlan:
    cleaned = normalize_query(query)
    variants: list[QueryVariant] = []
    if include_keyword:
        variants.append(
            QueryVariant(
                kind="original",
                target="keyword",
                query=cleaned,
                why="Original query preserved as a keyword search candidate.",
                weight=1.15 if intent == "code" else 1.0,
            )
        )
        keyword_limit = max_variants if not include_neural else max(1, min(2, max_variants - 1))
        for variant in keyword_variants:
            if len(variants) >= keyword_limit:
                break
            variants.append(
                variant.model_copy(
                    update={"query": inject_missing_terms(variant.query, policy.must_keep_terms)}
                )
            )
    if include_neural and len(variants) < max_variants:
        variants.extend(
            variant.model_copy(
                update={"query": inject_missing_terms(variant.query, policy.must_keep_terms)}
            )
            for variant in neural_variants[:1]
        )
    if not variants:
        return _fallback_plan(query, policy, "Rewrite produced no usable variants.")
    final_queries = dedupe_keep_order([variant.query for variant in variants])
    return QueryRewritePlan(
        original_query=query,
        policy=policy,
        variants=variants,
        final_queries=final_queries,
    )


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
            return _fallback_plan(query, policy, "Query rewriting disabled.")
        if policy.mode == "bypass":
            duration = time.time() - start_time
            record_query_rewrite("bypass", 1, True, duration, "bypass")
            return _fallback_plan(query, policy, policy.reason)

        router = get_query_rewrite_router()
        if router is None:
            duration = time.time() - start_time
            record_query_rewrite("fallback", 1, False, duration, "fallback")
            return _fallback_plan(query, policy, "LiteLLM Router not available or no API keys configured.")

        include_keyword, include_neural, active_provider_names = _active_target_flags(providers)
        if not include_keyword and not include_neural:
            return _fallback_plan(query, policy, "No active providers resolved for query rewrite.")

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
            tasks = []
            if include_keyword:
                tasks.append(
                    _request_variants(
                        router=router,
                        query=normalized_query,
                        intent=intent,
                        target="keyword",
                        policy=policy,
                        diagnostics=diagnostics,
                        research_goal=research_goal,
                    )
                )
            if include_neural:
                tasks.append(
                    _request_variants(
                        router=router,
                        query=normalized_query,
                        intent=intent,
                        target="neural",
                        policy=policy,
                        diagnostics=diagnostics,
                        research_goal=research_goal,
                    )
                )
            results = await asyncio.gather(*tasks)
            keyword_raw: list[QueryVariant] = []
            neural_raw: list[QueryVariant] = []
            models_used: list[str] = []
            if include_keyword:
                keyword_raw, model_name = results[0]
                models_used.append(model_name)
            if include_neural:
                neural_result, model_name = results[-1]
                neural_raw = neural_result
                models_used.append(model_name)

            keyword_valid = validate_keyword_variants(
                keyword_raw,
                intent=intent,
                must_keep_terms=policy.must_keep_terms,
            )
            keyword_valid = [
                variant
                for variant in keyword_valid
                if normalize_query(variant.query).casefold() != normalized_query.casefold()
            ]
            neural_valid = validate_neural_variants(
                neural_raw,
                must_keep_terms=policy.must_keep_terms,
            )
            plan = _build_plan(
                query=query,
                policy=policy,
                intent=intent,
                keyword_variants=keyword_valid,
                neural_variants=neural_valid,
                include_keyword=include_keyword,
                include_neural=include_neural,
                max_variants=max_variants,
            )
            duration = time.time() - start_time
            record_query_rewrite("expand", len(plan.variants), False, duration, ",".join(models_used) or "unknown")
            span.set_attribute("rewrite.active_provider_names", ",".join(active_provider_names))
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
        except (TimeoutError, ValidationError, ValueError, IndexError) as exc:
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
            return _fallback_plan(query, policy, "Rewrite failed; original query preserved.")
