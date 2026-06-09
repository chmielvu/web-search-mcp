"""LLM endpoint config builders."""

from __future__ import annotations

from .models import LLMEndpoint
from ..settings import settings


def build_classifier_endpoint() -> LLMEndpoint:
    return LLMEndpoint(
        name="vercel",
        model=settings.query_understanding_model,
        base_url=settings.vercel_ai_gateway_base_url,
        api_key=settings.vercel_ai_gateway_api_key,
        timeout_seconds=20.0,
    )


def build_worker_endpoints() -> tuple[LLMEndpoint, ...]:
    return (
        LLMEndpoint(
            name="cerebras",
            model=settings.cerebras_rewrite_model,
            base_url=settings.cerebras_base_url,
            api_key=settings.cerebras_api_key,
            timeout_seconds=30.0,
        ),
        LLMEndpoint(
            name="groq",
            model=settings.groq_rewrite_model,
            base_url=settings.groq_base_url,
            api_key=settings.groq_api_key,
            timeout_seconds=30.0,
        ),
        LLMEndpoint(
            name="vercel",
            model=settings.vercel_rewrite_model,
            base_url=settings.vercel_ai_gateway_base_url,
            api_key=settings.vercel_ai_gateway_api_key,
            timeout_seconds=30.0,
        ),
    )
