from __future__ import annotations

import logging
from typing import Any

from ..settings import settings

logger = logging.getLogger(__name__)
_ROUTER: Any = None

CEREBRAS_MODEL_POOL: tuple[tuple[str, int], ...] = (
    ("llama3.1-8b", 10),
    ("gpt-oss-120b", 10),
    ("zai-glm-4.7", 10),
)


def _cerebras_model_entries() -> list[tuple[str, int]]:
    total_rpm = settings.query_rewrite_cerebras_rpm
    count = len(CEREBRAS_MODEL_POOL)
    base = total_rpm // count
    remainder = total_rpm % count
    entries: list[tuple[str, int]] = []
    for i, (model_id, _) in enumerate(CEREBRAS_MODEL_POOL):
        entries.append((model_id, base + (1 if i < remainder else 0)))
    return entries


def build_query_rewrite_router() -> Any:
    try:
        from litellm.router import Router
    except ImportError:
        logger.debug("LiteLLM not available, query rewrite disabled")
        return None

    model_list = []
    if settings.mistral_api_key:
        model_list.append(
            {
                "model_name": "query-rewrite",
                "litellm_params": {
                    "model": f"mistral/{settings.query_rewrite_model}",
                    "api_key": settings.mistral_api_key,
                    "rpm": settings.query_rewrite_mistral_rpm,
                },
            }
        )
    if settings.cerebras_api_key:
        for model_id, rpm_share in _cerebras_model_entries():
            model_list.append(
                {
                    "model_name": "query-rewrite",
                    "litellm_params": {
                        "model": f"cerebras/{model_id}",
                        "api_key": settings.cerebras_api_key,
                        "rpm": rpm_share,
                    },
                }
            )
    if settings.groq_api_key:
        model_list.append(
            {
                "model_name": "query-rewrite",
                "litellm_params": {
                    "model": "groq/llama-3.1-8b-instant",
                    "api_key": settings.groq_api_key,
                    "rpm": settings.query_rewrite_groq_rpm,
                },
            }
        )
    if not model_list:
        logger.debug("No query rewrite API keys configured")
        return None
    return Router(
        model_list=model_list,
        routing_strategy="simple-shuffle",
        num_retries=2,
        retry_after=1,
        allowed_fails=3,
        cooldown_time=30,
    )


def get_query_rewrite_router() -> Any:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = build_query_rewrite_router()
    return _ROUTER
