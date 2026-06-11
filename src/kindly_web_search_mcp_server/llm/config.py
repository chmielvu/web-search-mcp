"""LLM endpoint config builders."""

from __future__ import annotations

from .models import LLMEndpoint
from ..settings import settings


def _openai_compatible_model(model: str) -> str:
    if model.startswith("openai/"):
        return model
    return f"openai/{model}"


def build_classifier_endpoint() -> LLMEndpoint:
    return LLMEndpoint(
        name="groq",
        model=f"groq/{settings.query_understanding_model.removeprefix('groq/')}",
        base_url=settings.groq_base_url,
        api_key=settings.groq_api_key,
        timeout_seconds=20.0,
    )


def build_vercel_gpt_oss_endpoint(*, timeout_seconds: float) -> LLMEndpoint:
    return LLMEndpoint(
        name="vercel",
        model=_openai_compatible_model(settings.vercel_rewrite_model),
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
        build_vercel_gpt_oss_endpoint(timeout_seconds=30.0),
    )
