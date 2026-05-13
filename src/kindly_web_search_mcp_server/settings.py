from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass


def _parse_json_dict(raw: str, default: dict) -> dict:
    """Parse a JSON dict from an environment variable string."""
    if not raw.strip():
        return default
    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, dict):
            return {k: float(v) for k, v in parsed.items()}
    except (_json.JSONDecodeError, ValueError):
        pass
    return default


@dataclass
class Settings:
    """Runtime configuration (env-first).

    Note: keep this module lightweight; it is imported by tests.
    """

    # Search providers (removed Serper - SearXNG is primary)
    # Semantic Cache (LanceDB)
    lancedb_dir: str = os.environ.get("KINDLY_LANCEDB_DIR", "./lancedb_data")
    semantic_cache_enabled: bool = (
        os.environ.get("KINDLY_SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
    )
    semantic_cache_min_score: float = float(
        os.environ.get("KINDLY_SEMANTIC_CACHE_MIN_SCORE", "0.92")
    )

    # Query rewrite (Mistral)
    query_rewrite_enabled: bool = (
        os.environ.get("KINDLY_QUERY_REWRITE_ENABLED", "true").lower() == "true"
    )
    query_rewrite_model: str = os.environ.get(
        "KINDLY_QUERY_REWRITE_MODEL", "mistral-small-2603"
    )
    # temperature=0 for deterministic output (LangChain MultiQueryRetriever pattern)
    query_rewrite_temperature: float = float(
        os.environ.get("KINDLY_QUERY_REWRITE_TEMPERATURE", "0.0")
    )
    query_rewrite_timeout_seconds: float = float(
        os.environ.get("KINDLY_QUERY_REWRITE_TIMEOUT_SECONDS", "20")
    )
    query_rewrite_max_variants: int = int(
        os.environ.get("KINDLY_QUERY_REWRITE_MAX_VARIANTS", "3")
    )
    mistral_api_key: str = os.environ.get("MISTRAL_API_KEY", "")

    # Query rewrite multi-provider (free-tier load distribution)
    cerebras_api_key: str = os.environ.get("CEREBRAS_API_KEY", "")
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    # Provider RPM estimates for free tier (used by LiteLLM Router for weighted selection)
    query_rewrite_mistral_rpm: int = int(os.environ.get("KINDLY_QUERY_REWRITE_MISTRAL_RPM", "30"))
    query_rewrite_cerebras_rpm: int = int(os.environ.get("KINDLY_QUERY_REWRITE_CEREBRAS_RPM", "30"))
    query_rewrite_groq_rpm: int = int(os.environ.get("KINDLY_QUERY_REWRITE_GROQ_RPM", "30"))

    # Embeddings (Hugging Face Inference Provider)
    hf_inference_provider: str = os.environ.get(
        "KINDLY_HF_INFERENCE_PROVIDER", "hf-inference"
    )
    hf_embedding_model: str = os.environ.get(
        "KINDLY_HF_EMBEDDING_MODEL", "ibm-granite/granite-embedding-97m-multilingual-r2"
    )
    embedding_dim: int = int(os.environ.get("KINDLY_EMBEDDING_DIM", "384"))

    # Reranking (Jina API)
    reranking_enabled: bool = (
        os.environ.get("KINDLY_RERANKING_ENABLED", "true").lower() == "true"
    )
    bi_encoder_top_k: int = int(os.environ.get("KINDLY_BI_ENCODER_TOP_K", "100"))
    rerank_top_k: int = int(os.environ.get("KINDLY_RERANK_TOP_K", "10"))
    jina_rerank_model: str = os.environ.get(
        "KINDLY_JINA_RERANK_MODEL", "jina-reranker-v3"
    )
    diversity_threshold: float = float(
        os.environ.get("KINDLY_DIVERSITY_THRESHOLD", "0.85")
    )
    mmr_lambda_param: float = float(
        os.environ.get("KINDLY_MMR_LAMBDA", "0.5")
    )

    # Pollinations API (for gemini-search provider in web_search mix)
    pollinations_api_key: str = os.environ.get("POLLINATIONS_API_KEY", "")

    # Gemini Grounding (for gemini_search MCP tool)
    gemini_api_key: str = os.environ.get("KINDLY_GEMINI_API_KEY", "")
    # Model selection handled via hardcoded fallback tier in gemini_search_tool.py

    # YouTube Transcript
    youtube_transcript_proxy_url: str = os.environ.get(
        "KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL", ""
    )
    youtube_transcript_max_chars: int = int(
        os.environ.get("KINDLY_YOUTUBE_TRANSCRIPT_MAX_CHARS", "50000")
    )
    youtube_transcript_timeout_seconds: float = float(
        os.environ.get("KINDLY_YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS", "30")
    )

    # YouTube Search (uses SearXNG with youtube engine)
    youtube_search_engine: str = os.environ.get(
        "KINDLY_YOUTUBE_SEARCH_ENGINE", "youtube"
    )

    # Provider modes (default: paid providers disabled/conditional)
    # Modes: always, conditional, never
    # - always: Always fires (free providers like SearXNG, DDG)
    # - conditional: Only when explicitly requested via providers param
    # - never: Never fires, even if API key present
    ddg_mode: str = os.environ.get("KINDLY_DDG_MODE", "always")
    tavily_mode: str = os.environ.get("KINDLY_TAVILY_MODE", "never")
    brave_mode: str = os.environ.get("KINDLY_BRAVE_MODE", "never")
    jina_mode: str = os.environ.get("KINDLY_JINA_MODE", "conditional")
    gemini_mode: str = os.environ.get("KINDLY_GEMINI_SEARCH_MODE", "always")
    composio_llm_search_mode: str = os.environ.get(
        "KINDLY_COMPOSIO_LLM_SEARCH_MODE", "always"
    )

    # Composio Search toolkit
    composio_api_key: str = os.environ.get("COMPOSIO_API_KEY", "")
    composio_user_id: str = os.environ.get("KINDLY_COMPOSIO_USER_ID", "")
    composio_search_toolkit_version: str = os.environ.get(
        "KINDLY_COMPOSIO_SEARCH_TOOLKIT_VERSION", "20260424_00"
    )
    composio_timeout_seconds: float = float(
        os.environ.get("KINDLY_COMPOSIO_TIMEOUT_SECONDS", "25")
    )
    composio_max_retries: int = int(os.environ.get("KINDLY_COMPOSIO_MAX_RETRIES", "2"))

    # RRF tuning
    rrf_k: int = int(os.environ.get("KINDLY_RRF_K", "60"))
    rrf_provider_weights: dict = None  # type: ignore[assignment]  # set in __post_init__

    # Default num_results for web_search
    default_num_results: int = int(os.environ.get("KINDLY_DEFAULT_NUM_RESULTS", "5"))

    # Per-tool rate limiting
    # Internal field names use "cheap" to reflect multi-tool scope
    # Env vars retain "WEB_SEARCH" prefix for backward compatibility
    rate_limit_cheap_rps: float = float(
        os.environ.get("KINDLY_RATE_LIMIT_WEB_SEARCH_RPS", "4.0")
    )
    rate_limit_cheap_burst: int = int(
        os.environ.get("KINDLY_RATE_LIMIT_WEB_SEARCH_BURST", "12")
    )
    rate_limit_expensive_rps: float = float(
        os.environ.get("KINDLY_RATE_LIMIT_EXPENSIVE_RPS", "0.5")
    )
    rate_limit_expensive_burst: int = int(
        os.environ.get("KINDLY_RATE_LIMIT_EXPENSIVE_BURST", "1")
    )

    def __post_init__(self) -> None:
        if self.rrf_provider_weights is None:
            # Provider weights rationale (Bruch et al. 2022: per-list weighting is more impactful than k tuning):
            # - tavily: 1.3 (optimized for AI assistants, structured extraction, freshness)
            # - gemini: 1.2 (Google grounding, high recall for factual/research queries)
            # - composio_llm_search: 1.15 (LLM-enhanced relevance ranking)
            # - jina: 1.1 (semantic search expertise, deep understanding)
            # - searxng: 1.0 (baseline, free/open-source aggregator with meta-search breadth)
            # - brave: 1.0 (baseline, independent index, privacy-focused)
            # - ddg: 0.7 (aggregator, less freshness for navigational queries, penalized for instant answers)
            # Note: weights are query-type dependent. Future: adaptive weighting by intent classification.
            self.rrf_provider_weights = _parse_json_dict(
                os.environ.get("KINDLY_RRF_PROVIDER_WEIGHTS", ""),
                default={
                    "searxng": 1.0,
                    "ddg": 0.7,
                    "tavily": 1.3,
                    "brave": 1.0,
                    "jina": 1.1,
                    "gemini": 1.2,
                    "composio_llm_search": 1.15,
                },
            )

        # Validate numeric parameters
        if not 0.0 <= self.mmr_lambda_param <= 1.0:
            raise ValueError(
                f"mmr_lambda_param must be in [0, 1], got {self.mmr_lambda_param!r}. "
                "Set KINDLY_MMR_LAMBDA env var to a value between 0 and 1."
            )
        if not 0.0 < self.semantic_cache_min_score <= 1.0:
            raise ValueError(
                f"semantic_cache_min_score must be in (0, 1], got {self.semantic_cache_min_score!r}. "
                "Set KINDLY_SEMANTIC_CACHE_MIN_SCORE env var."
            )
        if self.rrf_k <= 0:
            raise ValueError(
                f"rrf_k must be > 0, got {self.rrf_k!r}. "
                "Set KINDLY_RRF_K env var to a positive integer."
            )


settings = Settings()
