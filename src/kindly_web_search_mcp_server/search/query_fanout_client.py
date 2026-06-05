"""LLM fan-out client for decomposed branch generation."""

from __future__ import annotations

import logging

from ..settings import settings
from ..utils.diagnostics import Diagnostics
from .query_fanout import (
    build_fanout_messages,
    normalize_fanout_output,
    parse_fanout_output,
)
from .query_rewrite_cascade import cascade_query_rewrite
from .query_rewrite_models import QueryFanoutOutput, RewriteIntent

logger = logging.getLogger(__name__)

FANOUT_TEMPERATURE_BY_INTENT: dict[RewriteIntent, float] = {
    "code": 0.2,
    "general_research": 0.5,
    "comparison": 0.35,
}


async def generate_fanout_branches(
    *,
    query: str,
    intent: RewriteIntent,
    research_goal: str | None,
    must_keep_terms: list[str],
    active_provider_names: list[str],
    routing: dict[str, bool] | None = None,
    max_branches: int | None = None,
    diagnostics: Diagnostics | None = None,
) -> QueryFanoutOutput:
    if not settings.query_rewrite_enabled:
        return QueryFanoutOutput()
    if not (
        settings.cerebras_api_key or settings.groq_api_key or settings.hf_token
    ):
        return QueryFanoutOutput()

    messages = build_fanout_messages(
        query=query,
        research_goal=research_goal,
        must_keep_terms=must_keep_terms,
        intent=intent,
        active_provider_names=active_provider_names,
        routing=routing,
    )
    try:
        raw_content, model_used = await cascade_query_rewrite(
            messages=messages,
            temperature=FANOUT_TEMPERATURE_BY_INTENT.get(
                intent, settings.query_rewrite_temperature
            ),
            timeout=settings.query_rewrite_cascade_timeout_seconds,
        )
        parsed = parse_fanout_output(raw_content)
        normalized = normalize_fanout_output(
            parsed,
            must_keep_terms=must_keep_terms,
            max_branches=max_branches or settings.query_decomposition_max_branches,
        )
        if diagnostics:
            diagnostics.emit(
                "query_rewrite.fanout",
                "LLM fan-out call completed",
                {
                    "branch_count": len(normalized.branches),
                    "model": model_used,
                    "rationale": normalized.rationale,
                },
            )
        return normalized
    except Exception as exc:
        logger.warning("LLM fan-out generation failed: %s", exc)
        if diagnostics:
            diagnostics.emit(
                "query_rewrite.fanout_error",
                "LLM fan-out call failed",
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
        return QueryFanoutOutput()
