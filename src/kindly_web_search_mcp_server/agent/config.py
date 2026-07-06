from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from .models import ResearchDepth

# Centralized source of truth (for consistency with OTel, analytics, rate limits, etc.)
from ..settings import Settings


@dataclass(frozen=True)
class DepthProfile:
    name: ResearchDepth
    run_limit: int
    timeout_seconds: float


@dataclass(frozen=True)
class AgenticResearchConfig:
    """Agentic research runtime config.

    Defaults are now sourced from the central Settings (which parses
    AGENTIC_RESEARCH_* and NANOGPT_API_KEY etc. in one place).
    This improves consistency and avoids duplicated env parsing logic.

    Callers (runner, tests) can still override individual fields for tests
    or per-request config (e.g. AgenticResearchConfig(api_key="...")).
    """

    model_name: str = field(default_factory=lambda: Settings().agentic_research_model)
    fallback_models: str = field(
        default_factory=lambda: Settings().agentic_research_fallback_models
    )
    gemini_fallback_model: str = field(
        default_factory=lambda: Settings().agentic_research_gemini_fallback_model
    )
    hf_router_base_url: str = field(
        default_factory=lambda: Settings().agentic_research_hf_router_base_url
    )
    hf_fallback_model: str = field(
        default_factory=lambda: Settings().agentic_research_hf_fallback_model
    )
    base_url: str = field(default_factory=lambda: Settings().agentic_research_base_url)
    api_key: str = field(default_factory=lambda: Settings().nanogpt_api_key)
    gemini_api_key: str = field(default_factory=lambda: Settings().gemini_api_key)
    hf_token: str = field(default_factory=lambda: Settings().hf_token)
    temperature: float = field(default_factory=lambda: Settings().agentic_research_temperature)
    timeout_seconds: float = field(
        default_factory=lambda: Settings().agentic_research_timeout_seconds
    )
    max_retries: int = field(default_factory=lambda: Settings().agentic_research_max_retries)

    quick_run_limit: int = field(
        default_factory=lambda: Settings().agentic_research_quick_run_limit
    )
    normal_run_limit: int = field(
        default_factory=lambda: Settings().agentic_research_normal_run_limit
    )
    deep_run_limit: int = field(default_factory=lambda: Settings().agentic_research_deep_run_limit)
    quick_timeout_seconds: float = field(
        default_factory=lambda: Settings().agentic_research_quick_timeout_seconds
    )
    normal_timeout_seconds: float = field(
        default_factory=lambda: Settings().agentic_research_normal_timeout_seconds
    )
    deep_timeout_seconds: float = field(
        default_factory=lambda: Settings().agentic_research_deep_timeout_seconds
    )
    default_num_results: int = field(
        default_factory=lambda: Settings().agentic_research_default_num_results
    )

    # Phoenix OTLP endpoint (delegated to central settings for consistency)
    phoenix_collector_endpoint: str = field(
        default_factory=lambda: Settings().phoenix_collector_endpoint
    )

    # External MCP support (best-effort load when config provided; requires langchain-mcp-adapters package at runtime)
    external_mcp_config: str = field(
        default_factory=lambda: Settings().agentic_research_external_mcp_config
    )

    def model_chain(self) -> tuple[str, ...]:
        models = [self.model_name.strip()]
        models.extend(item.strip() for item in self.fallback_models.split(",") if item.strip())
        deduped: list[str] = []
        for model in models:
            if model and model not in deduped:
                deduped.append(model)
        return tuple(deduped)


def depth_profile_for(depth: ResearchDepth) -> DepthProfile:
    # Use a single Settings instance for the profile lookup
    s = Settings()
    if depth == "quick":
        return DepthProfile(
            "quick",
            s.agentic_research_quick_run_limit,
            s.agentic_research_quick_timeout_seconds,
        )
    if depth == "deep":
        return DepthProfile(
            "deep",
            s.agentic_research_deep_run_limit,
            s.agentic_research_deep_timeout_seconds,
        )
    return DepthProfile(
        "normal",
        s.agentic_research_normal_run_limit,
        s.agentic_research_normal_timeout_seconds,
    )
