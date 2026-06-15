"""LLM-backed entity extraction for search text."""

from __future__ import annotations

import json
import logging

from ..entity.models import EntitySpan
from ..llm.phoenix_tracing import LLMTraceContext
from ..llm.structured import StructuredLLMRequest
from ..llm.worker import build_llm_worker
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.registry import build_prompt

logger = logging.getLogger(__name__)


async def extract_entities(
    text: str,
    *,
    provider_name: str = "vercel",
    session_id: str | None = None,
) -> list[EntitySpan]:
    if not text.strip():
        return []

    system_prompt, user_prompt = build_prompt(
        "entity_extraction",
        query=text,
        research_goal=text,
        provider_name=provider_name,
    )
    worker = build_llm_worker()
    langfuse_trace = LLMTraceContext(
        trace_name="entity_extraction",
        session_id=session_id,
        metadata={
            "task": "structure_extract",
            "provider_name": provider_name,
        },
    )
    result = await worker.complete_structured(
        StructuredLLMRequest(
            task="structure_extract",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            reasoning_effort=REASONING_EFFORT_LOW,
            langfuse=langfuse_trace,
        )
    )
    payload = json.loads(result.content)
    raw_entities = payload.get("entities", []) if isinstance(payload, dict) else []
    entities: list[EntitySpan] = []
    for item in raw_entities:
        try:
            entities.append(EntitySpan.model_validate(item))
        except Exception:
            continue
    return entities
