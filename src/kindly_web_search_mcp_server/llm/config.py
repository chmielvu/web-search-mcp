"""LLM endpoint config builders."""

from __future__ import annotations

from ..settings import settings
from .models import LLMEndpoint


def build_classifier_endpoint() -> LLMEndpoint:
    return LLMEndpoint(
        name="groq",
        model=settings.query_understanding_model,
        base_url=settings.groq_base_url,
        api_key=settings.groq_api_key,
        timeout_seconds=20.0,
    )


def build_vercel_gpt_oss_endpoint(*, timeout_seconds: float) -> LLMEndpoint:
    return LLMEndpoint(
        name="vercel",
        model=settings.vercel_rewrite_model,
        base_url=settings.vercel_ai_gateway_base_url,
        api_key=settings.vercel_ai_gateway_api_key,
        timeout_seconds=timeout_seconds,
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
            name="huggingface",
            model=settings.huggingface_rewrite_model,
            base_url="https://router.huggingface.co",
            api_key=settings.hf_token,
            timeout_seconds=30.0,
            client_type="huggingface",
        ),
        build_vercel_gpt_oss_endpoint(timeout_seconds=30.0),
    )
