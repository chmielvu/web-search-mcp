"""Declarative catalog for providers, models, capabilities, and fallback chains."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ModelCapability(str, Enum):
    CHAT = "chat"
    STRUCTURED_OUTPUT = "structured_output"
    GROUNDING = "grounding"
    URL_CONTEXT = "url_context"
    RERANK = "rerank"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    spec_id: str
    provider: str
    model_id: str
    base_url: str | None
    api_key_env: str
    capabilities: set[ModelCapability]
    cost_per_1m_input: float | None = None
    cost_per_1m_output: float | None = None
    default_timeout: float = 30.0

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()


@dataclass(frozen=True, slots=True)
class FallbackChainSpec:
    name: str
    primary: ModelSpec
    fallbacks: tuple[ModelSpec, ...]


# Model specifications across provider protocols
CEREBRAS_GPT_OSS_120B = ModelSpec(
    spec_id="cerebras/gpt-oss-120b",
    provider="cerebras",
    model_id="gpt-oss-120b",
    base_url="https://api.cerebras.ai/v1",
    api_key_env="CEREBRAS_API_KEY",
    capabilities={ModelCapability.CHAT},
    cost_per_1m_input=0.35,
    cost_per_1m_output=0.75,
    default_timeout=30.0,
)

GROQ_GPT_OSS_120B = ModelSpec(
    spec_id="groq/gpt-oss-120b",
    provider="groq",
    model_id="openai/gpt-oss-120b",
    base_url="https://api.groq.com/openai/v1",
    api_key_env="GROQ_API_KEY",
    capabilities={ModelCapability.CHAT},
    cost_per_1m_input=0.15,
    cost_per_1m_output=0.60,
    default_timeout=30.0,
)

GROQ_GPT_OSS_20B = ModelSpec(
    spec_id="groq/gpt-oss-20b",
    provider="groq",
    model_id="openai/gpt-oss-20b",
    base_url="https://api.groq.com/openai/v1",
    api_key_env="GROQ_API_KEY",
    capabilities={ModelCapability.CHAT},
    cost_per_1m_input=0.075,
    cost_per_1m_output=0.30,
    default_timeout=20.0,
)

HF_NSCALE_GPT_OSS_120B = ModelSpec(
    spec_id="huggingface/gpt-oss-120b:nscale",
    provider="huggingface",
    model_id="openai/gpt-oss-120b:nscale",
    base_url="https://router.huggingface.co",
    api_key_env="HF_TOKEN",
    capabilities={ModelCapability.CHAT},
    default_timeout=30.0,
)

VERCEL_GPT_OSS_20B = ModelSpec(
    spec_id="vercel/gpt-oss-20b",
    provider="vercel",
    model_id="openai/gpt-oss-20b",
    base_url="https://ai-gateway.vercel.sh/v1",
    api_key_env="AI_GATEWAY_API_KEY",
    capabilities={ModelCapability.CHAT},
    cost_per_1m_input=0.10,
    cost_per_1m_output=0.40,
    default_timeout=30.0,
)

COHERE_RERANK_V4_FAST = ModelSpec(
    spec_id="cohere/rerank-v4.0-fast",
    provider="cohere",
    model_id="rerank-v4.0-fast",
    base_url="https://api.cohere.com/v2/rerank",
    api_key_env="COHERE_API_KEY",
    capabilities={ModelCapability.RERANK},
    default_timeout=5.0,
)

OPENROUTER_COHERE_RERANK_4_FAST = ModelSpec(
    spec_id="openrouter/cohere/rerank-4-fast",
    provider="openrouter",
    model_id="cohere/rerank-4-fast",
    base_url="https://openrouter.ai/api/v1/rerank",
    api_key_env="OPENROUTER_API_KEY",
    capabilities={ModelCapability.RERANK},
    default_timeout=5.0,
)

VOYAGE_RERANK_2_5 = ModelSpec(
    spec_id="voyage/rerank-2.5",
    provider="voyage",
    model_id="rerank-2.5",
    base_url="https://api.voyageai.com/v1/rerank",
    api_key_env="VOYAGE_API_KEY",
    capabilities={ModelCapability.RERANK},
    default_timeout=30.0,
)

GEMINI_2_5_FLASH = ModelSpec(
    spec_id="google/gemini-2.5-flash",
    provider="google",
    model_id="gemini-2.5-flash",
    base_url=None,
    api_key_env="GEMINI_API_KEY",
    capabilities={ModelCapability.CHAT, ModelCapability.GROUNDING, ModelCapability.RERANK},
    cost_per_1m_input=0.30,
    cost_per_1m_output=2.50,
    default_timeout=30.0,
)

OPENROUTER_GEMINI_2_5_FLASH = ModelSpec(
    spec_id="openrouter/google/gemini-2.5-flash",
    provider="openrouter",
    model_id="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
    capabilities={ModelCapability.CHAT, ModelCapability.RERANK},
    default_timeout=30.0,
)

HF_E5_LARGE_EMBEDDING = ModelSpec(
    spec_id="huggingface/multilingual-e5-large-instruct",
    provider="huggingface",
    model_id="intfloat/multilingual-e5-large-instruct",
    base_url="https://api-inference.huggingface.co",
    api_key_env="HF_TOKEN",
    capabilities={ModelCapability.EMBEDDING},
    default_timeout=30.0,
)


_CHAINS: Mapping[str, FallbackChainSpec] = {
    "worker_llm": FallbackChainSpec(
        name="worker_llm",
        primary=CEREBRAS_GPT_OSS_120B,
        fallbacks=(GROQ_GPT_OSS_120B, HF_NSCALE_GPT_OSS_120B, VERCEL_GPT_OSS_20B),
    ),
    "classifier_llm": FallbackChainSpec(
        name="classifier_llm",
        primary=GROQ_GPT_OSS_20B,
        fallbacks=(VERCEL_GPT_OSS_20B,),
    ),
    "cross_encoder_rerank": FallbackChainSpec(
        name="cross_encoder_rerank",
        primary=COHERE_RERANK_V4_FAST,
        fallbacks=(OPENROUTER_COHERE_RERANK_4_FAST, VOYAGE_RERANK_2_5),
    ),
    "rankllm": FallbackChainSpec(
        name="rankllm",
        primary=GEMINI_2_5_FLASH,
        fallbacks=(OPENROUTER_GEMINI_2_5_FLASH,),
    ),
}


def get_chain(name: str) -> FallbackChainSpec:
    if name not in _CHAINS:
        raise KeyError(f"Unknown fallback chain: {name}")
    return _CHAINS[name]
