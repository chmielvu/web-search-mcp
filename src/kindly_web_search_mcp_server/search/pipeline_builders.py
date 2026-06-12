"""Helper builders for the 0.2 search pipeline."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..llm.worker import build_llm_worker
from ..llm.structured import StructuredLLMRequest
from ..prompts.registry import build_prompt
from ..settings import settings
from .context import SearchContext
from .intents import SearchIntent
from .normalize import normalize_query
from .options import SearchOptions
from .profiles.registry import resolve_profile_name
from .query_rewrite_models import QueryVariant

REWRITE_TEMPERATURE_BY_INTENT: dict[SearchIntent, float] = {
    "general": 0.35,
    "ai_coding": 0.15,
    "digital_humanities": 0.25,
    "comparison": 0.2,
}


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
    profile_name = resolve_profile_name(understanding_intent)
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
        profile_name=profile_name,
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
) -> tuple[list[QueryVariant], str]:
    system_prompt, user_prompt = build_prompt(
        "worker_rewrite",
        query=context.normalized_query,
        research_goal=context.research_goal,
        intent=understanding_intent,
        must_keep_terms=must_keep_terms,
        provider_name="worker",
    )
    worker = build_llm_worker()
    generation = await worker.complete_structured(
        StructuredLLMRequest(
            task="worker_rewrite",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=REWRITE_TEMPERATURE_BY_INTENT[understanding_intent],
            timeout_seconds=settings.query_rewrite_cascade_timeout_seconds,
            response_model=RewriteVariantResponse,
        )
    )
    payload = json.loads(generation.content)
    return parse_variant_payload(payload), generation.endpoint_name
