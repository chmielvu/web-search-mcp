"""Helper builders for the 0.2 search pipeline."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..llm.worker import build_llm_worker
from ..llm.structured import StructuredLLMRequest
from ..llm.phoenix_tracing import LLMTraceContext
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.registry import build_prompt

from ..settings import settings
from .context import SearchContext
from .intents import SearchIntent
from .intent_policy import resolve_intent_policy
from .normalize import normalize_query
from .options import SearchOptions
from .query_rewrite_models import QueryVariant


class RewriteVariantResponse(BaseModel):
    variants: list[QueryVariant] = Field(default_factory=list)


def build_search_context(
    *,
    query: str,
    research_goal: str | None,
    session_id: str | None = None,
    num_results: int,
    search_options: SearchOptions | None,
    understanding_intent: SearchIntent,
    understanding_confidence: float,
    understanding_should_decompose: bool,
    understanding_rationale: str,
    entities: list,
    must_keep_terms: list[str],
) -> SearchContext:
    normalized_query = normalize_query(query)
    return SearchContext(
        raw_query=query,
        normalized_query=normalized_query,
        research_goal=research_goal,
        session_id=session_id,
        intent=understanding_intent,
        confidence=understanding_confidence,
        should_decompose=understanding_should_decompose,
        rationale=understanding_rationale,
        entities=tuple(entities),
        must_keep_terms=tuple(must_keep_terms),
        num_results=num_results,
        search_options=search_options,
    )


def parse_variant_payload(payload: object) -> list[QueryVariant]:
    if not isinstance(payload, dict):
        raise ValueError("Rewrite worker response must be a JSON object")
    raw_variants = payload.get("variants", [])
    if not isinstance(raw_variants, list):
        raise ValueError("Rewrite worker response missing variants array")
    variants: list[QueryVariant] = []
    seen: set[str] = set()
    for item in raw_variants:
        variant = QueryVariant.model_validate(item)
        key = normalize_query(variant.query).casefold()
        if key in seen:
            continue
        seen.add(key)
        variants.append(variant)
    if not variants:
        raise ValueError("Rewrite worker returned no usable variants")
    return variants


async def build_rewrite_variants(
    *,
    context: SearchContext,
    understanding_intent: SearchIntent,
    must_keep_terms: list[str],
) -> tuple[list[QueryVariant], str, int | None, int | None]:
    policy = resolve_intent_policy(understanding_intent)
    system_prompt, user_prompt = build_prompt(
        "worker_rewrite",
        query=context.normalized_query,
        research_goal=context.research_goal,
        intent=understanding_intent,
        must_keep_terms=must_keep_terms,
        provider_name="worker",
    )
    worker = build_llm_worker()
    langfuse_trace = LLMTraceContext(
        trace_name="query_rewrite",
        session_id=context.session_id,
        metadata={
            "task": "worker_rewrite",
            "intent": understanding_intent,
            "policy_version": policy.policy_version,
            "research_goal": context.research_goal or "",
        },
    )
    generation = await worker.complete_structured(
        StructuredLLMRequest(
            task="worker_rewrite",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=policy.rewrite_temperature,
            timeout_seconds=settings.query_rewrite_cascade_timeout_seconds,
            response_model=RewriteVariantResponse,
            reasoning_effort=REASONING_EFFORT_LOW,
            langfuse=langfuse_trace,
        )
    )
    payload = json.loads(generation.content)
    return (
        parse_variant_payload(payload),
        generation.model_name,
        generation.input_tokens,
        generation.output_tokens,
    )
