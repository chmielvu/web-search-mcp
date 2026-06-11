"""LLM-backed query understanding."""

from __future__ import annotations

import logging

from ...settings import settings
from ...llm.worker import build_llm_worker
from ...llm.structured import StructuredLLMRequest
from ...prompts.registry import build_prompt
from ...training.session_state import get_session_state_store
from ...training.query_understanding_jsonl import append_query_understanding_record
from ...utils.observability import emit_observability_event
from ..intents import SearchIntent, normalize_intent
from ..normalize import normalize_query
from ..context import SearchContext
from .models import QueryUnderstandingResult

logger = logging.getLogger(__name__)


async def resolve_query_understanding(
    *,
    query: str,
    research_goal: str | None,
    intent_hint: SearchIntent | None = None,
    session_id: str | None = None,
) -> QueryUnderstandingResult:
    normalized_query = normalize_query(query)
    system_prompt, user_prompt = build_prompt(
        "query_understanding",
        query=normalized_query,
        research_goal=research_goal,
        intent=intent_hint,
        must_keep_terms=[],
        provider_name="groq",
    )
    worker = build_llm_worker()
    fallback_reason = "Query classifier unavailable; defaulting to general."
    result_model_name = "fallback-general"
    result_provider_name = "fallback"
    fallback_used = False
    try:
        result = await worker.complete_structured(
            StructuredLLMRequest(
                task="query_understand",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                timeout_seconds=settings.query_classifier_timeout_seconds,
                response_model=QueryUnderstandingResult,
            )
        )
        understanding = QueryUnderstandingResult.model_validate_json(result.content)
        understanding = understanding.model_copy(
            update={"intent": normalize_intent(understanding.intent)}
        )
        result_model_name = result.model_name
        result_provider_name = result.endpoint_name
    except Exception as exc:
        logger.warning("query understanding failed; falling back to general: %s", exc)
        fallback_used = True
        emit_observability_event(
            logger,
            "search.query_understanding.fallback",
            query=normalized_query,
            error=str(exc)[:300],
            fallback_intent="general",
            fallback_reason=fallback_reason,
        )
        understanding = QueryUnderstandingResult(
            intent="general",
            confidence=0.0,
            rationale=fallback_reason,
            should_decompose=False,
        )
    emit_observability_event(
        logger,
        "search.query_understanding.resolved",
        query=normalized_query,
        intent=understanding.intent,
        confidence=understanding.confidence,
        should_decompose=understanding.should_decompose,
        model=result_model_name,
        provider=result_provider_name,
        entities=[entity.model_dump() for entity in understanding.entities],
        preserved_terms=understanding.preserved_terms,
        fallback=fallback_used,
    )
    if settings.query_understanding_jsonl_enabled:
        try:
            await append_query_understanding_record(
                context=SearchContext(
                    raw_query=query,
                    normalized_query=normalized_query,
                    research_goal=research_goal,
                    session_id=session_id,
                    intent=understanding.intent,
                    confidence=understanding.confidence,
                    should_decompose=understanding.should_decompose,
                    rationale=understanding.rationale,
                    entities=tuple(understanding.entities),
                    must_keep_terms=tuple(understanding.must_keep_terms),
                    providers=None,
                    num_results=0,
                    search_options=None,
                    profile_name=understanding.intent,
                ),
                understanding=understanding,
                model_name=result_model_name,
                prompt_name="query_understanding",
                path=settings.query_understanding_jsonl_path,
                session_id=session_id,
            )
            if session_id:
                get_session_state_store().get(session_id).last_intent = understanding.intent
        except Exception as exc:
            logger.warning("query understanding JSONL write failed: %s", exc)
    return understanding
