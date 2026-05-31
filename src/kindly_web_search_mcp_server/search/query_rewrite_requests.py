from __future__ import annotations

import asyncio
from typing import Any

from ..settings import settings
from .query_rewrite_models import QueryVariant, RewriteIntent
from .query_rewrite_prompts import build_query_rewrite_messages
from .query_rewrite_validate import parse_query_rewrite_output
from .query_policy import RewritePolicy
from ..utils.diagnostics import Diagnostics


TEMPERATURE_BY_INTENT: dict[RewriteIntent, float] = {
    "code": 0.15,
    "general_research": 0.5,
    "comparison": 0.3,
}


async def request_variants(
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
            temperature=TEMPERATURE_BY_INTENT.get(
                intent, settings.query_rewrite_temperature
            ),
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
