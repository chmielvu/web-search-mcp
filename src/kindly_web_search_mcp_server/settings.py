from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass
from pathlib import Path


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

    # Query rewrite (Cerebras → Groq → HF Inference cascade)
    query_rewrite_enabled: bool = (
        os.environ.get("KINDLY_QUERY_REWRITE_ENABLED", "true").lower() == "true"
    )
    # fallback temperature for query rewrite when intent-specific temp is not set
    query_rewrite_temperature: float = float(
        os.environ.get("KINDLY_QUERY_REWRITE_TEMPERATURE", "0.0")
    )
    query_rewrite_cascade_timeout_seconds: float = float(
        os.environ.get("KINDLY_QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS", "20")
    )
    query_rewrite_max_variants: int = int(
        os.environ.get("KINDLY_QUERY_REWRITE_MAX_VARIANTS", "3")
    )
    mistral_api_key: str = os.environ.get("MISTRAL_API_KEY", "")

    # FunctionGemma classifier / decomposition
    query_classifier_enabled: bool = (
        os.environ.get("KINDLY_CLASSIFIER_ENABLED", "true").lower() == "true"
    )
    query_classifier_url: str = os.environ.get(
        "KINDLY_CLASSIFIER_URL",
        "https://functiongemma-classifier-373347358125.us-central1.run.app",
    )
    query_classifier_timeout_seconds: float = float(
        os.environ.get("KINDLY_CLASSIFIER_TIMEOUT_SECONDS", "10")
    )
    query_classifier_max_tokens: int = int(
        os.environ.get("KINDLY_CLASSIFIER_MAX_TOKENS", "500")
    )
    query_decomposition_enabled: bool = (
        os.environ.get("KINDLY_QUERY_DECOMPOSITION_ENABLED", "true").lower() == "true"
    )
    query_decomposition_timeout_seconds: float = float(
        os.environ.get("KINDLY_QUERY_DECOMPOSITION_TIMEOUT_SECONDS", "10")
    )
    query_decomposition_max_subquestions: int = int(
        os.environ.get("KINDLY_QUERY_DECOMPOSITION_MAX_SUBQUESTIONS", "3")
    )

    # Query rewrite providers (Cerebras → Groq → HF Inference cascade)
    cerebras_api_key: str = os.environ.get("CEREBRAS_API_KEY", "")
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    hf_token: str = os.environ.get("HF_TOKEN", "")

    # Embeddings (Hugging Face Inference Provider)
    hf_inference_provider: str = os.environ.get(
        "KINDLY_HF_INFERENCE_PROVIDER", "hf-inference"
    )
    hf_embedding_model: str = os.environ.get(
        "KINDLY_HF_EMBEDDING_MODEL", "ibm-granite/granite-embedding-97m-multilingual-r2"
    )
    embedding_dim: int = int(os.environ.get("KINDLY_EMBEDDING_DIM", "384"))

    # Reranking (Voyage primary, Jina fallback)
    reranking_enabled: bool = (
        os.environ.get("KINDLY_RERANKING_ENABLED", "true").lower() == "true"
    )
    rerank_provider: str = os.environ.get("KINDLY_RERANK_PROVIDER", "voyage").lower()
    bi_encoder_top_k: int = int(os.environ.get("KINDLY_BI_ENCODER_TOP_K", "100"))
    rerank_top_k: int = int(os.environ.get("KINDLY_RERANK_TOP_K", "10"))
    voyage_api_key: str = os.environ.get("VOYAGE_API_KEY", "")
    voyage_rerank_model: str = os.environ.get(
        "KINDLY_VOYAGE_RERANK_MODEL", "rerank-2.5"
    )
    jina_rerank_model: str = os.environ.get(
        "KINDLY_JINA_RERANK_MODEL", "jina-reranker-v3"
    )
    rerank_score_threshold: float = float(
        os.environ.get("KINDLY_RERANK_SCORE_THRESHOLD", "0.0")
    )
    diversity_threshold: float = float(
        os.environ.get("KINDLY_DIVERSITY_THRESHOLD", "0.85")
    )
    mmr_lambda_param: float = float(os.environ.get("KINDLY_MMR_LAMBDA", "0.5"))
    rerank_recency_weight: float = float(
        os.environ.get("RERANK_RECENCY_WEIGHT", "0.15")
    )
    rerank_recency_half_life_days: int = int(
        os.environ.get("RERANK_RECENCY_HALF_LIFE_DAYS", "90")
    )

    analytics_enabled: bool = (
        os.environ.get("KINDLY_ANALYTICS_ENABLED", "true").lower() == "true"
    )
    analytics_duckdb_path: str = os.environ.get(
        "KINDLY_ANALYTICS_DUCKDB_PATH",
        str(Path(".kindly") / "analytics" / "search_events.duckdb"),
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

    # Academic Search Providers
    # Semantic Scholar (optional, 100 RPS with key vs 1 RPS shared)
    s2_api_key: str = os.environ.get("KINDLY_S2_API_KEY", "")
    s2_timeout: int = int(os.environ.get("KINDLY_S2_TIMEOUT", "30"))
    s2_max_retries: int = int(
        os.environ.get("KINDLY_S2_MAX_RETRIES", "0")
    )  # 0 = fail fast

    # OpenAlex (optional, polite pool with email)
    openalex_email: str = os.environ.get("KINDLY_OPENALEX_EMAIL", "")
    openalex_api_key: str = os.environ.get("KINDLY_OPENALEX_API_KEY", "")

    # CrossRef (optional, polite pool with mailto)
    crossref_mailto: str = os.environ.get("CROSSREF_MAILTO", "")

    # PubMed (optional, higher rate limit with key)
    pubmed_api_key: str = os.environ.get("PUBMED_API_KEY", "")

    # CORE (optional, required for full-text search)
    core_api_key: str = os.environ.get("CORE_API_KEY", "")

    # Academic search defaults
    academic_default_sources: str = os.environ.get(
        "KINDLY_ACADEMIC_DEFAULT_SOURCES", "arxiv,semanticscholar"
    )
    academic_max_results: int = int(os.environ.get("KINDLY_ACADEMIC_MAX_RESULTS", "10"))

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

    # =====================================================================
    # OpenTelemetry / Grafana Observability (Phase 1 of observability work)
    # =====================================================================
    # These enable first-class traces + metrics export to Grafana Cloud
    # (or local collector / Alloy). We prefer standard OTEL_* env vars
    # for compatibility with the broader ecosystem, but provide
    # KINDLY_ + GRAFANA_CLOUD_* convenience vars for Windows/pwsh ergonomics.

    otel_enabled: bool = os.environ.get("KINDLY_OTEL_ENABLED", "true").lower() == "true"

    # Sampling (head-based). 1.0 = all traces (expensive). 0.1 = 10% typical for dev/prod.
    otel_sampling_ratio: float = float(
        os.environ.get("KINDLY_OTEL_SAMPLING_RATIO", "0.15")
    )

    # Service identity overrides (fall back to telemetry.py defaults + package version)
    otel_service_name: str = os.environ.get("OTEL_SERVICE_NAME", "web-search-mcp")
    otel_service_namespace: str = os.environ.get("OTEL_SERVICE_NAMESPACE", "kindly-mcp")
    otel_deployment_environment: str = os.environ.get(
        "DEPLOYMENT_ENV", os.environ.get("KINDLY_OTEL_ENVIRONMENT", "development")
    )

    # Grafana Cloud convenience (recommended for Windows users who dislike manual Base64)
    # When these are set, telemetry.py can auto-construct the Authorization header.
    grafana_cloud_instance_id: str = os.environ.get("GRAFANA_CLOUD_INSTANCE_ID", "")
    grafana_cloud_api_key: str = os.environ.get("GRAFANA_CLOUD_API_KEY", "")
    grafana_cloud_otlp_endpoint: str = os.environ.get("GRAFANA_CLOUD_OTLP_ENDPOINT", "")

    # Prometheus sidecar / Alloy scrape support
    prometheus_enabled: bool = (
        os.environ.get("KINDLY_PROMETHEUS_ENABLED", "false").lower() == "true"
    )
    prometheus_port: int = int(
        os.environ.get("KINDLY_PROMETHEUS_PORT", "0")
    )  # 0 = disabled / dynamic

    # Attribute safety (used by utils/observability.py and telemetry)
    observability_max_text_chars: int = int(
        os.environ.get("KINDLY_OBSERVABILITY_MAX_TEXT_CHARS", "20000")
    )
    observability_max_items: int = int(
        os.environ.get("KINDLY_OBSERVABILITY_MAX_ITEMS", "10")
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

        # OTel / Observability validation
        if not (0.0 < self.otel_sampling_ratio <= 1.0):
            raise ValueError(
                f"otel_sampling_ratio must be in (0.0, 1.0], got {self.otel_sampling_ratio!r}. "
                "Set KINDLY_OTEL_SAMPLING_RATIO (e.g. 0.1 for 10% head sampling)."
            )
        if self.observability_max_text_chars < 1024:
            raise ValueError(
                "observability_max_text_chars must be >= 1024 to avoid truncating useful debug info."
            )
        if self.observability_max_items < 1:
            raise ValueError("observability_max_items must be >= 1.")


settings = Settings()
