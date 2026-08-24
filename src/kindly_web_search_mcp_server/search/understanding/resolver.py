"""Hosted GLiNER2 query understanding resolver."""

from __future__ import annotations

import logging

from ...entity.gliner_client import GatewayAnalysis, get_gliner_client
from ...heuristics.understanding_fallback import resolve_fallback_understanding
from ...settings import settings
from ...training.query_understanding_jsonl import append_query_understanding_record
from ...training.session_state import get_session_state_store
from ...utils.observability import emit_observability_event
from ..normalize import normalize_query
from ..intents import SearchIntent
from .models import QueryUnderstandingResult

logger = logging.getLogger(__name__)


_GLINER_MODEL = "fastino/gliner2-multi-v1"


def _deterministic_fallback(reason: str, query: str = "") -> GatewayAnalysis:
    fb = resolve_fallback_understanding(query)
    rationale = f"{reason}: {fb.rationale}" if fb.rules else reason
    return GatewayAnalysis(
        understanding=QueryUnderstandingResult(
            intent=fb.intent,
            confidence=0.0,
            entities=[],
            relations=[],
            preserved_terms=[],
            compared_entities=list(fb.compared_entities),
            domain_hints=[],
            time_sensitivity=fb.time_sensitivity,
            rationale=rationale,
            should_decompose=fb.should_decompose,
        ),
        model_version=_GLINER_MODEL,
        latency_ms=0.0,
        fallback=True,
        error_reason=reason,
    )


async def resolve_query_understanding(
    *,
    query: str,
    research_goal: str | None,
    intent_hint: SearchIntent | None = None,
    session_id: str | None = None,
    run_key: str | None = None,
) -> QueryUnderstandingResult:
    """Resolve intent, grounded entities, and relations with one VPS request.

    ``research_goal`` and ``intent_hint`` remain accepted for caller
    compatibility, but query understanding is deliberately query-only and
    never constructs an LLM request.
    """
    del intent_hint
    normalized_query = normalize_query(query)
    client = get_gliner_client()
    try:
        analysis = await client.analyze_query(normalized_query)
    except Exception as exc:  # pragma: no cover - gateway owns normal failures
        logger.warning("hosted GLiNER2 resolver failed: %s", exc)
        analysis = _deterministic_fallback("gliner2-unexpected-error", query=normalized_query)

    understanding = analysis.understanding
    event_fields = {
        "query": normalized_query,
        "intent": understanding.intent,
        "confidence": understanding.confidence,
        "should_decompose": understanding.should_decompose,
        "model": analysis.model_version,
        "model_used": analysis.model_version,
        "provider": "gliner2",
        "latency_ms": analysis.latency_ms,
        "entities": [entity.model_dump() for entity in understanding.entities],
        "relations": [relation.model_dump() for relation in understanding.relations],
        "preserved_terms": understanding.preserved_terms,
        "compared_entities": understanding.compared_entities,
        "fallback": analysis.fallback,
        "fallback_reason": analysis.error_reason,
        "run_key": run_key,
    }
    emit_observability_event(
        logger,
        "search.query_understanding.resolved",
        **event_fields,
    )
    if settings.query_understanding_jsonl_enabled:
        try:
            await append_query_understanding_record(
                raw_query=query,
                normalized_query=normalized_query,
                research_goal=research_goal,
                understanding=understanding,
                model_name=analysis.model_version,
                prompt_name="gliner2-combined",
                path=settings.query_understanding_jsonl_path,
                session_id=session_id,
                decision_path="gliner2_fallback" if analysis.fallback else "gliner2",
                classifier_model=analysis.model_version,
                classifier_endpoint=f"{client.base_url}/v2/query-understanding",
                classifier_latency_ms=analysis.latency_ms,
                confidence_threshold=settings.intent_classifier_confidence_threshold,
                fallback_reason=analysis.error_reason,
            )
            if session_id:
                get_session_state_store().get(session_id).last_intent = understanding.intent
        except Exception as exc:
            logger.warning("query understanding JSONL write failed: %s", exc)

    return understanding
