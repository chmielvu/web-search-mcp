"""LLM-backed entity extraction for search text."""

from __future__ import annotations

import json
import logging

from ..entity.models import EntitySpan
from ..llm.structured import StructuredLLMRequest
from ..llm.worker import build_llm_worker
from ..prompts.builders import REASONING_EFFORT_LOW
from ..prompts.registry import build_prompt

logger = logging.getLogger(__name__)


async def extract_entities(text: str, *, provider_name: str = "vercel") -> list[EntitySpan]:
    if not text.strip():
        return []

    system_prompt, user_prompt = build_prompt(
        "entity_extraction",
        query=text,
        research_goal=text,
        provider_name=provider_name,
    )
    worker = build_llm_worker()
    result = await worker.complete_structured(
        StructuredLLMRequest(
            task="structure_extract",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            reasoning_effort=REASONING_EFFORT_LOW,
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
