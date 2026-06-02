from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import os

from .models import ResearchDepth


@dataclass(frozen=True)
class DepthProfile:
    name: ResearchDepth
    run_limit: int
    timeout_seconds: float


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: str) -> float:
    return float(_env(name, default))


def _env_int(name: str, default: str) -> int:
    return int(_env(name, default))


@dataclass(frozen=True)
class AgenticResearchConfig:
    model_name: str = field(
        default_factory=lambda: _env(
            "KINDLY_AGENTIC_RESEARCH_MODEL",
            "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
        )
    )
    base_url: str = field(
        default_factory=lambda: _env(
            "KINDLY_AGENTIC_RESEARCH_BASE_URL",
            "https://nano-gpt.com/api/subscription/v1",
        )
    )
    api_key: str = field(default_factory=lambda: _env("NANOGPT_API_KEY", ""))
    temperature: float = field(
        default_factory=lambda: _env_float("KINDLY_AGENTIC_RESEARCH_TEMPERATURE", "0")
    )
    timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "KINDLY_AGENTIC_RESEARCH_TIMEOUT_SECONDS", "180"
        )
    )
    max_retries: int = field(
        default_factory=lambda: _env_int("KINDLY_AGENTIC_RESEARCH_MAX_RETRIES", "2")
    )
    quick_run_limit: int = field(
        default_factory=lambda: _env_int("KINDLY_AGENTIC_RESEARCH_QUICK_RUN_LIMIT", "6")
    )
    normal_run_limit: int = field(
        default_factory=lambda: _env_int(
            "KINDLY_AGENTIC_RESEARCH_NORMAL_RUN_LIMIT", "10"
        )
    )
    deep_run_limit: int = field(
        default_factory=lambda: _env_int("KINDLY_AGENTIC_RESEARCH_DEEP_RUN_LIMIT", "16")
    )
    quick_timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "KINDLY_AGENTIC_RESEARCH_QUICK_TIMEOUT_SECONDS", "120"
        )
    )
    normal_timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "KINDLY_AGENTIC_RESEARCH_NORMAL_TIMEOUT_SECONDS", "180"
        )
    )
    deep_timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "KINDLY_AGENTIC_RESEARCH_DEEP_TIMEOUT_SECONDS", "300"
        )
    )
    default_num_results: int = field(
        default_factory=lambda: _env_int(
            "KINDLY_AGENTIC_RESEARCH_DEFAULT_NUM_RESULTS", "5"
        )
    )


def depth_profile_for(depth: ResearchDepth) -> DepthProfile:
    config = AgenticResearchConfig()
    if depth == "quick":
        return DepthProfile("quick", config.quick_run_limit, config.quick_timeout_seconds)
    if depth == "deep":
        return DepthProfile("deep", config.deep_run_limit, config.deep_timeout_seconds)
    return DepthProfile("normal", config.normal_run_limit, config.normal_timeout_seconds)
