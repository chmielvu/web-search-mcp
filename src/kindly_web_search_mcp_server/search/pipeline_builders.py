"""Builders for search context and enriched query rewrite variants."""

from __future__ import annotations

import json
import logging
import os
import re

import httpx
from pydantic import BaseModel, Field

from ..llm.phoenix_tracing import LLMTraceContext
from ..llm.structured import StructuredLLMRequest
from ..llm.worker import build_llm_worker
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.registry import build_prompt
from ..settings import settings
from .brave import spellcheck_brave, suggest_brave_queries
from .context import SearchContext
from .intents import SearchIntent
from .intent_policy import resolve_intent_policy
from .keyword_extract import extract_must_keep_terms
from .literal_passthrough import detect_literal_passthrough
from .normalize import normalize_query
from .options import SearchOptions
from .query_rewrite_models import QueryVariant
from .query_rewrite_preprocess import build_rewrite_preprocess_signals

logger = logging.getLogger(__name__)


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
    """Parse rewrite JSON from an optional ``<final_response>`` wrapper."""
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, str):
        match = re.search(r"<final_response>(.*?)</final_response>", payload, re.DOTALL)
        json_text = match.group(1).strip() if match else payload.strip()
        data = json.loads(json_text)
    else:
        raise ValueError("Rewrite worker response must be a JSON object or string")
    if not isinstance(data, dict) or not isinstance(data.get("variants"), list):
        raise ValueError("Rewrite worker response missing variants array")
    variants = [QueryVariant.model_validate(item) for item in data["variants"]]
    keyword = next((variant for variant in variants if variant.kind == "keyword_refined"), None)
    neural = next((variant for variant in variants if variant.kind == "neural_refined"), None)
    if keyword is None or neural is None:
        raise ValueError(
            "Rewrite worker must return keyword_refined and neural_refined variants. "
            f"Got kinds: {[variant.kind for variant in variants]}"
        )
    return [keyword, neural]


async def build_rewrite_variants(
    *,
    context: SearchContext,
    understanding_intent: SearchIntent,
    must_keep_terms: list[str],
    num_results: int | None = None,
    http_client: httpx.AsyncClient,
) -> tuple[list[QueryVariant], str, int | None, int | None]:
    policy = resolve_intent_policy(understanding_intent)
    research_text = context.research_goal or context.normalized_query
    try:
        rake_terms = await extract_must_keep_terms(research_text)
    except Exception:
        logger.warning(
            "RAKE extraction failed; continuing with existing must-keep terms", exc_info=True
        )
        rake_terms = []
    merged_must_keep = list(dict.fromkeys([*must_keep_terms, *rake_terms]))
    suggestions: list[str] = []
    entities: list[dict[str, str]] = []
    spellcheck: str | None = None

    suggest_key = os.environ.get("BRAVE_SUGGEST_API_KEY", settings.brave_suggest_api_key).strip()
    if suggest_key:
        try:
            enrichment = await suggest_brave_queries(
                context.normalized_query, http_client=http_client
            )
            suggestions = enrichment.get("suggestions", [])
            entities = enrichment.get("entities", [])
        except Exception:
            logger.warning(
                "Brave Autosuggest failed; continuing without suggestions", exc_info=True
            )
    if os.environ.get("BRAVE_API_KEY", settings.brave_api_key).strip():
        try:
            spellcheck = await spellcheck_brave(context.normalized_query, http_client=http_client)
        except Exception:
            logger.debug("Brave Spellcheck failed", exc_info=True)

    signals = build_rewrite_preprocess_signals(
        context.normalized_query,
        brave_suggestions=suggestions,
        brave_entities=entities,
        brave_spellcheck=spellcheck,
        must_keep_terms=merged_must_keep,
    )
    max_results = num_results or context.num_results
    if detect_literal_passthrough(context.normalized_query):
        reason = "Literal-syntax query detected. Bypassing LLM rewrite to preserve exact operators."
        return (
            [
                QueryVariant(
                    kind="keyword_refined",
                    target="keyword",
                    query=context.normalized_query,
                    why="Literal operators preserved for SERP providers.",
                    weight=1.0,
                    branch_type="keyword_refined",
                    reason=reason,
                    must_keep_terms=merged_must_keep,
                    max_results=max_results,
                ),
                QueryVariant(
                    kind="neural_refined",
                    target="neural",
                    query=context.normalized_query,
                    why="Literal query passed through as-is for neural providers.",
                    weight=1.0,
                    branch_type="neural_refined",
                    reason=reason,
                    must_keep_terms=merged_must_keep,
                    max_results=max_results,
                ),
            ],
            "",
            None,
            None,
        )

    system_prompt, user_prompt = build_prompt(
        "worker_rewrite",
        query=context.normalized_query,
        research_goal=context.research_goal,
        intent=understanding_intent,
        must_keep_terms=merged_must_keep,
        provider_name="worker",
        max_variants=2,
        rewrite_signals=signals.prompt_block(),
    )
    generation = await build_llm_worker().complete_structured(
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
            langfuse=LLMTraceContext(
                trace_name="query_rewrite",
                session_id=context.session_id,
                metadata={
                    "task": "worker_rewrite",
                    "intent": understanding_intent,
                    "policy_version": policy.policy_version,
                },
            ),
        )
    )
    return (
        parse_variant_payload(generation.content),
        generation.model_name,
        generation.input_tokens,
        generation.output_tokens,
    )
