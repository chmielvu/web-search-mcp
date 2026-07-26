"""Declarative catalog — registers all models, providers, and chains.

All provider configs resolve from ``kindly_web_search_mcp_server.settings.settings``
at registration time.  Model IDs, base URLs, timeouts, and env-var names are
set here so that the rest of the system never touches ``settings`` directly.

KEY DESIGN
----------
Each model is defined **once** via ``define_model()``.  Different API keys,
timeouts, or delivery configs for the same model are handled by qualified
provider keys (e.g. ``"google"`` vs ``"google:second"``).  The ``:`` suffix
selects which ``ProviderConfig`` to use while reusing the same adapter.

Example::

    define_model("gemini-3.1-flash-lite", capabilities={CHAT, GROUNDING, ...})
    add_provider("gemini-3.1-flash-lite", "google",       as_google(..., api_key_env="GEMINI_API_KEY"))
    add_provider("gemini-3.1-flash-lite", "google:second", as_google(..., api_key_env="SECOND_GEMINI_API_KEY"))

    # Chains reference the qualified key:
    register_chain("gemini_grounding", [
        "gemini-3.1-flash-lite@google:second",   # uses SECOND_GEMINI_API_KEY
        "gemini-2.5-flash@google",               # uses GEMINI_API_KEY
    ])
"""

from __future__ import annotations

import os

from ..settings import settings
from .chain import register_chain
from .registry import (
    add_provider,
    as_embedding,
    as_google,
    as_huggingface,
    as_openai,
    as_rerank,
    define_model,
)
from .types import ModelCapability


def _register_all() -> None:
    # ─────────────────────────────────────────────────────────────────────
    # WORKER LLM: gpt-oss-120b
    #   Primary rewrite LLM — fast, cheap, OpenAI-compatible.
    #   Cerebras is cheapest; Groq second; HF/Nscale fallback; Vercel last.
    # ─────────────────────────────────────────────────────────────────────
    define_model(
        "gpt-oss-120b",
        display_name="GPT OSS 120B",
        description="Primary worker LLM — fast, cheap, OpenAI-compatible.",
        capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
    )
    add_provider(
        "gpt-oss-120b",
        "cerebras",
        as_openai(
            model_id=settings.cerebras_rewrite_model,
            base_url=settings.cerebras_base_url,
            api_key_env="CEREBRAS_API_KEY",
            cost_per_1m_input=0.35,
            cost_per_1m_output=0.75,
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gpt-oss-120b",
        "cerebras:second",
        as_openai(
            model_id=settings.cerebras_rewrite_model,
            base_url=settings.cerebras_base_url,
            api_key_env="SECOND_CEREBRAS_API_KEY",
            cost_per_1m_input=0.35,
            cost_per_1m_output=0.75,
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gpt-oss-120b",
        "groq",
        as_openai(
            model_id=settings.groq_rewrite_model,
            base_url=settings.groq_base_url,
            api_key_env="GROQ_API_KEY",
            cost_per_1m_input=0.15,
            cost_per_1m_output=0.60,
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gpt-oss-120b",
        "groq:second",
        as_openai(
            model_id=settings.groq_rewrite_model,
            base_url=settings.groq_base_url,
            api_key_env="SECOND_GROQ_API_KEY",
            cost_per_1m_input=0.15,
            cost_per_1m_output=0.60,
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gpt-oss-120b",
        "huggingface",
        as_huggingface(
            model_id=settings.huggingface_rewrite_model,
            api_key_env="HF_TOKEN",
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gpt-oss-120b",
        "vercel",
        as_openai(
            model_id=settings.vercel_rewrite_model,
            base_url=settings.vercel_ai_gateway_base_url,
            api_key_env="AI_GATEWAY_API_KEY",
            cost_per_1m_input=0.10,
            cost_per_1m_output=0.40,
            default_timeout=30.0,
        ),
    )
    # Cerebras rewrite fallbacks (verified via /v1/models and chat smoke calls).
    define_model(
        "zai-glm-4.7",
        display_name="GLM 4.7",
        description="Cerebras-hosted GLM 4.7 chat model.",
        capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
    )
    add_provider(
        "zai-glm-4.7",
        "cerebras",
        as_openai(
            model_id="zai-glm-4.7",
            base_url=settings.cerebras_base_url,
            api_key_env="CEREBRAS_API_KEY",
            default_timeout=30.0,
        ),
    )
    add_provider(
        "zai-glm-4.7",
        "cerebras:second",
        as_openai(
            model_id="zai-glm-4.7",
            base_url=settings.cerebras_base_url,
            api_key_env="SECOND_CEREBRAS_API_KEY",
            default_timeout=30.0,
        ),
    )
    define_model(
        "gemma-4-31b",
        display_name="Gemma 4 31B",
        description="Cerebras-hosted Gemma 4 31B chat model.",
        capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
    )
    add_provider(
        "gemma-4-31b",
        "cerebras",
        as_openai(
            model_id="gemma-4-31b",
            base_url=settings.cerebras_base_url,
            api_key_env="CEREBRAS_API_KEY",
            default_timeout=30.0,
        ),
    )
    add_provider(
        "gemma-4-31b",
        "cerebras:second",
        as_openai(
            model_id="gemma-4-31b",
            base_url=settings.cerebras_base_url,
            api_key_env="SECOND_CEREBRAS_API_KEY",
            default_timeout=30.0,
        ),
    )

    register_chain(
        "worker_llm",
        [
            "gpt-oss-120b@cerebras",
            "gpt-oss-120b@cerebras:second",
            "zai-glm-4.7@cerebras",
            "zai-glm-4.7@cerebras:second",
            "gemma-4-31b@cerebras",
            "gemma-4-31b@cerebras:second",
            "gpt-oss-120b@groq",
            "gpt-oss-120b@groq:second",
            "gpt-oss-120b@huggingface",
            "gpt-oss-120b@vercel",
        ],
    )

    # ─────────────────────────────────────────────────────────────────────
    # CLASSIFIER / QUERY UNDERSTANDING: gpt-oss-20b
    #   Smaller model — faster, cheaper.  Shorter timeout.
    # ─────────────────────────────────────────────────────────────────────
    define_model(
        "gpt-oss-20b",
        display_name="GPT OSS 20B",
        description="Classifier / query understanding — smaller, faster, cheaper.",
        capabilities={ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT},
    )
    add_provider(
        "gpt-oss-20b",
        "groq",
        as_openai(
            model_id=settings.query_understanding_model,
            base_url=settings.groq_base_url,
            api_key_env="GROQ_API_KEY",
            cost_per_1m_input=0.075,
            cost_per_1m_output=0.30,
            default_timeout=20.0,
        ),
    )
    add_provider(
        "gpt-oss-20b",
        "groq:second",
        as_openai(
            model_id=settings.query_understanding_model,
            base_url=settings.groq_base_url,
            api_key_env="SECOND_GROQ_API_KEY",
            cost_per_1m_input=0.075,
            cost_per_1m_output=0.30,
            default_timeout=20.0,
        ),
    )
    add_provider(
        "gpt-oss-20b",
        "vercel",
        as_openai(
            model_id=settings.vercel_rewrite_model,
            base_url=settings.vercel_ai_gateway_base_url,
            api_key_env="AI_GATEWAY_API_KEY",
            cost_per_1m_input=0.10,
            cost_per_1m_output=0.40,
            default_timeout=20.0,
        ),
    )
    # Additional live Groq text models (verified via /openai/v1/models).
    define_model(
        "llama-3.1-8b-instant",
        display_name="Llama 3.1 8B",
        description="Groq-hosted fast text model for lightweight chat tasks.",
        capabilities={ModelCapability.CHAT},
    )
    add_provider(
        "llama-3.1-8b-instant",
        "groq",
        as_openai(
            model_id="llama-3.1-8b-instant",
            base_url=settings.groq_base_url,
            api_key_env="GROQ_API_KEY",
            default_timeout=20.0,
        ),
    )
    add_provider(
        "llama-3.1-8b-instant",
        "groq:second",
        as_openai(
            model_id="llama-3.1-8b-instant",
            base_url=settings.groq_base_url,
            api_key_env="SECOND_GROQ_API_KEY",
            default_timeout=20.0,
        ),
    )
    define_model(
        "llama-3.3-70b-versatile",
        display_name="Llama 3.3 70B",
        description="Groq-hosted general-purpose text model.",
        capabilities={ModelCapability.CHAT},
    )
    add_provider(
        "llama-3.3-70b-versatile",
        "groq",
        as_openai(
            model_id="llama-3.3-70b-versatile",
            base_url=settings.groq_base_url,
            api_key_env="GROQ_API_KEY",
            default_timeout=30.0,
        ),
    )
    add_provider(
        "llama-3.3-70b-versatile",
        "groq:second",
        as_openai(
            model_id="llama-3.3-70b-versatile",
            base_url=settings.groq_base_url,
            api_key_env="SECOND_GROQ_API_KEY",
            default_timeout=30.0,
        ),
    )

    register_chain(
        "classifier_llm",
        [
            "gpt-oss-20b@groq",
            "gpt-oss-20b@groq:second",
            "gpt-oss-20b@vercel",
        ],
    )

    def _register_groq_text_model(
        canonical_id: str,
        display_name: str,
        description: str,
        *,
        structured_output: bool = False,
    ) -> None:
        capabilities = {ModelCapability.CHAT}
        if structured_output:
            capabilities.add(ModelCapability.STRUCTURED_OUTPUT)
        define_model(
            canonical_id,
            display_name=display_name,
            description=description,
            capabilities=capabilities,
        )
        for provider_key, api_key_env in (
            ("groq", "GROQ_API_KEY"),
            ("groq:second", "SECOND_GROQ_API_KEY"),
        ):
            add_provider(
                canonical_id,
                provider_key,
                as_openai(
                    model_id=canonical_id,
                    base_url=settings.groq_base_url,
                    api_key_env=api_key_env,
                    default_timeout=30.0,
                ),
            )

    _register_groq_text_model(
        "groq/compound",
        "Groq Compound",
        "Groq-hosted compound text model.",
    )
    _register_groq_text_model(
        "groq/compound-mini",
        "Groq Compound Mini",
        "Groq-hosted compact compound text model.",
    )
    _register_groq_text_model(
        "allam-2-7b",
        "ALLaM-2 7B",
        "Groq-hosted Arabic-capable text model.",
    )
    _register_groq_text_model(
        "qwen/qwen3.6-27b",
        "Qwen 3.6 27B",
        "Groq-hosted Qwen text model with multimodal input support.",
    )
    # ─────────────────────────────────────────────────────────────────────
    # CROSS-ENCODER RERANK: rerank-v4
    #   Cohere primary, OpenRouter/Voyage fallbacks.
    # ─────────────────────────────────────────────────────────────────────
    define_model(
        "rerank-v4",
        display_name="Cohere Rerank v4",
        description="Cross-encoder reranker — Cohere primary, OpenRouter/Voyage fallbacks.",
        capabilities={ModelCapability.RERANK},
    )
    add_provider(
        "rerank-v4",
        "cohere",
        as_rerank(
            model_id=settings.cohere_rerank_model,
            base_url=settings.cohere_rerank_base_url,
            api_key_env="COHERE_API_KEY",
            default_timeout=settings.cohere_rerank_timeout,
        ),
    )
    add_provider(
        "rerank-v4",
        "openrouter_rerank",
        as_rerank(
            model_id=settings.openrouter_rerank_model,
            base_url=settings.openrouter_rerank_base_url,
            api_key_env="OPENROUTER_API_KEY",
            default_timeout=settings.openrouter_rerank_timeout,
        ),
    )
    add_provider(
        "rerank-v4",
        "voyage",
        as_rerank(
            model_id=settings.voyage_rerank_model,
            base_url="https://api.voyageai.com/v1/rerank",
            api_key_env="VOYAGE_API_KEY",
            default_timeout=30.0,
        ),
    )
    register_chain(
        "cross_encoder_rerank",
        [
            "rerank-v4@cohere",
            "rerank-v4@openrouter_rerank",
            "rerank-v4@voyage",
        ],
    )

    # ─────────────────────────────────────────────────────────────────────
    # GOOGLE GEMINI MODELS
    #   Each model defined once.  Different chains use different qualified
    #   provider keys to select API keys and timeouts:
    #     @google              → GEMINI_API_KEY, 30s
    #     @google:second       → SECOND_GEMINI_API_KEY, 30s
    #     @google:rankllm      → GEMINI_API_KEY, rankllm_timeout_seconds
    # ─────────────────────────────────────────────────────────────────────

    # gemini-3.5-flash-lite — primary RankLLM + primary summarization
    rankllm_model = settings.rankllm_gemini_model
    define_model(
        "gemini-3.5-flash-lite",
        display_name="Gemini 3.5 Flash Lite",
        description="Primary RankLLM and summarization model.",
        capabilities={
            ModelCapability.CHAT,
            ModelCapability.RERANK,
            ModelCapability.URL_CONTEXT,
        },
    )
    add_provider(
        "gemini-3.5-flash-lite",
        "google",
        as_google(
            model_id=rankllm_model,
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.30,
            cost_per_1m_output=2.50,
        ),
    )
    add_provider(
        "gemini-3.5-flash-lite",
        "google:rankllm",
        as_google(
            model_id=rankllm_model,
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.30,
            cost_per_1m_output=2.50,
            default_timeout=settings.rankllm_timeout_seconds,
        ),
    )

    # gemini-3.1-flash-lite — grounding (second key), rankllm fallback,
    #   summarization fallback
    define_model(
        "gemini-3.1-flash-lite",
        display_name="Gemini 3.1 Flash Lite",
        description="Versatile fallback — grounding, rankllm, summarization.",
        capabilities={
            ModelCapability.CHAT,
            ModelCapability.GROUNDING,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.RERANK,
            ModelCapability.URL_CONTEXT,
        },
    )
    add_provider(
        "gemini-3.1-flash-lite",
        "google",
        as_google(
            model_id="gemini-3.1-flash-lite",
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.25,
            cost_per_1m_output=1.50,
        ),
    )
    add_provider(
        "gemini-3.1-flash-lite",
        "google:second",
        as_google(
            model_id="gemini-3.1-flash-lite",
            api_key_env="SECOND_GEMINI_API_KEY",
            cost_per_1m_input=0.25,
            cost_per_1m_output=1.50,
        ),
    )
    add_provider(
        "gemini-3.1-flash-lite",
        "google:rankllm",
        as_google(
            model_id="gemini-3.1-flash-lite",
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.25,
            cost_per_1m_output=1.50,
            default_timeout=settings.rankllm_timeout_seconds,
        ),
    )

    # gemini-2.5-flash — grounding fallback
    define_model(
        "gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        description="Grounding fallback and RankLLM OpenRouter fallback.",
        capabilities={
            ModelCapability.CHAT,
            ModelCapability.GROUNDING,
        },
    )
    add_provider(
        "gemini-2.5-flash",
        "google",
        as_google(
            model_id="gemini-2.5-flash",
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.30,
            cost_per_1m_output=2.50,
        ),
    )

    # rankllm-openrouter — OpenAI-compatible RankLLM fallback
    define_model(
        "rankllm-openrouter",
        display_name="RankLLM OpenRouter Fallback",
        description="OpenAI-compatible fallback for listwise RankLLM reranking.",
        capabilities={ModelCapability.CHAT, ModelCapability.RERANK},
    )
    add_provider(
        "rankllm-openrouter",
        "openrouter",
        as_openai(
            model_id=settings.rankllm_openrouter_model,
            base_url=settings.openrouter_chat_base_url,
            api_key_env="OPENROUTER_API_KEY",
            default_timeout=settings.rankllm_timeout_seconds,
        ),
    )

    # gemini-2.5-flash-lite — grounding final fallback
    define_model(
        "gemini-2.5-flash-lite",
        display_name="Gemini 2.5 Flash Lite",
        description="Grounding final fallback.",
        capabilities={ModelCapability.CHAT, ModelCapability.GROUNDING},
    )
    add_provider(
        "gemini-2.5-flash-lite",
        "google",
        as_google(
            model_id="gemini-2.5-flash-lite",
            api_key_env="GEMINI_API_KEY",
            cost_per_1m_input=0.10,
            cost_per_1m_output=0.40,
        ),
    )

    # gemma-4-26b-a4b-it — summarization final fallback
    define_model(
        "gemma-4-26b-a4b-it",
        display_name="Gemma 4 26B",
        description="Summarization final fallback — no JSON schema support.",
        capabilities={ModelCapability.CHAT},
    )
    add_provider(
        "gemma-4-26b-a4b-it",
        "google",
        as_google(
            model_id="gemma-4-26b-a4b-it",
            api_key_env="GEMINI_API_KEY",
        ),
    )

    # ─────────────────────────────────────────────────────────────────────
    # CHAINS — modular composition from the models above
    # ─────────────────────────────────────────────────────────────────────

    register_chain(
        "rankllm",
        [
            "gemini-3.5-flash-lite@google:rankllm",
            "gemini-3.1-flash-lite@google:rankllm",
            "rankllm-openrouter@openrouter",
        ],
    )

    register_chain(
        "gemini_grounding",
        [
            "gemini-3.1-flash-lite@google:second",
            "gemini-2.5-flash@google",
            "gemini-2.5-flash-lite@google",
        ],
    )

    custom_model = (os.environ.get("SUMMARY_GEMINI_MODEL") or "gemini-3.5-flash-lite").strip()
    register_chain(
        "summarization",
        [
            f"{custom_model}@google",
            "gemini-3.1-flash-lite@google",
            "gemma-4-26b-a4b-it@google",
        ],
    )

    # ─────────────────────────────────────────────────────────────────────
    # EMBEDDING
    # ─────────────────────────────────────────────────────────────────────
    define_model(
        "multilingual-e5-large-instruct",
        display_name="Multilingual E5 Large Instruct",
        description="HuggingFace embedding model for bi-encoder reranking.",
        capabilities={ModelCapability.EMBEDDING},
    )
    add_provider(
        "multilingual-e5-large-instruct",
        "huggingface",
        as_embedding(
            model_id=settings.hf_embedding_model,
            api_key_env="HF_TOKEN",
            default_timeout=settings.embedding_timeout_seconds,
        ),
    )
    register_chain(
        "embedding",
        [
            "multilingual-e5-large-instruct@huggingface",
        ],
    )


_register_all()
