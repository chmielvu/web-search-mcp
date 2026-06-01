from __future__ import annotations

from ..settings import settings
from .query_rewrite_cascade import cascade_query_rewrite
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
    raw_content, model_used = await cascade_query_rewrite(
        messages=messages,
        temperature=TEMPERATURE_BY_INTENT.get(
            intent, settings.query_rewrite_temperature
        ),
        timeout=settings.query_rewrite_cascade_timeout_seconds,
    )
    parsed = parse_query_rewrite_output(raw_content)
    if diagnostics:
        diagnostics.emit(
            "query_rewrite.raw_result",
            "Query rewrite call completed",
            {
                "target": target,
                "variant_count": len(parsed.variants),
                "model": model_used,
            },
        )
    return parsed.variants, model_used
