"""Cascade through query rewrite providers: Cerebras pool → Groq → HF Inference.

Cerebras free tier rotates its model roster — some models are temporarily
unavailable while others remain active.  We try each model sequentially
and only skip to the next provider on a genuine rate-limit (429).
"""

from __future__ import annotations

import asyncio
import logging

from litellm import acompletion
from litellm.exceptions import (
    APIConnectionError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)

from ..settings import settings

logger = logging.getLogger(__name__)

CEREBRAS_POOL: list[tuple[str, str]] = [
    ("cerebras/llama3.1-8b", "llama3.1-8b"),
    ("cerebras/gpt-oss-120b", "gpt-oss-120b"),
    ("cerebras/zai-glm-4.7", "zai-glm-4.7"),
    ("cerebras/qwen-3-235b-a22b-instruct-2507", "qwen-235b"),
]

_MODEL_NOT_AVAILABLE = (
    ServiceUnavailableError,
    NotFoundError,
    APIConnectionError,
)


async def cascade_query_rewrite(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    timeout: float,
) -> tuple[str, str]:
    """Run query rewrite through the provider cascade.

    Tier 1 — Cerebras pool (free, direct API, sequential on model-not-found).
    Tier 2 — Groq (free, direct API).
    Tier 3 — HF Inference / together (paid, $0.05/M, last resort).

    Returns ``(raw_json_content, model_short_name)``.
    """

    # ── Tier 1: Cerebras ────────────────────────────────────────────
    if settings.cerebras_api_key:
        for model_id, short_name in CEREBRAS_POOL:
            try:
                response = await asyncio.wait_for(
                    acompletion(
                        model=model_id,
                        messages=messages,
                        temperature=temperature,
                        response_format={"type": "json_object"},
                        api_key=settings.cerebras_api_key,
                    ),
                    timeout=timeout,
                )
                content = response.choices[0].message.content
                if isinstance(content, str):
                    return content, short_name
            except RateLimitError:
                logger.debug("Cerebras rate-limited, skipping to Groq")
                break  # all Cerebras models share the same key → skip pool
            except _MODEL_NOT_AVAILABLE:
                logger.debug("Cerebras %s unavailable, trying next", short_name)
                continue
            except Exception as exc:
                logger.debug("Cerebras %s error: %s", short_name, exc)
                continue

    # ── Tier 2: Groq ─────────────────────────────────────────────────
    if settings.groq_api_key:
        try:
            response = await asyncio.wait_for(
                acompletion(
                    model="groq/llama-3.1-8b-instant",
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    api_key=settings.groq_api_key,
                ),
                timeout=timeout,
            )
            content = response.choices[0].message.content
            if isinstance(content, str):
                return content, "groq/llama-3.1-8b-instant"
        except Exception as exc:
            logger.debug("Groq query rewrite failed: %s", exc)

    # ── Tier 3: HF Inference (together/gpt-oss-20b) ──────────────────
    if settings.hf_token:
        try:
            response = await asyncio.wait_for(
                acompletion(
                    model="huggingface/together/openai/gpt-oss-20b",
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    api_key=settings.hf_token,
                ),
                timeout=timeout,
            )
            content = response.choices[0].message.content
            if isinstance(content, str):
                return content, "gpt-oss-20b"
        except Exception as exc:
            logger.debug("HF Inference query rewrite failed: %s", exc)

    raise RuntimeError("All query rewrite providers exhausted")
