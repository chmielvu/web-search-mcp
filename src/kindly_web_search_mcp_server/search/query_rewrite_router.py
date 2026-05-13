from __future__ import annotations

import logging
from typing import Any

from ..settings import settings

try:
    from litellm.router import Router

    LITELLM_AVAILABLE = True
except ImportError:
    Router = None  # type: ignore[misc,assignment]
    LITELLM_AVAILABLE = False

logger = logging.getLogger(__name__)
_ROUTER: Any = None


def build_query_rewrite_router() -> Any:
    if not LITELLM_AVAILABLE or Router is None:
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
        model_list.append(
            {
                "model_name": "query-rewrite",
                "litellm_params": {
                    "model": "cerebras/llama3.1-8b",
                    "api_key": settings.cerebras_api_key,
                    "rpm": settings.query_rewrite_cerebras_rpm,
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
