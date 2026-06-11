from __future__ import annotations

import base64
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


def _decode_langfuse_mcp_auth_header(raw: str) -> tuple[str, str]:
    """Decode Langfuse MCP Basic auth into public/secret keys.

    Expected input is either ``Basic <base64(pk:sk)>`` or the base64 token
    itself. Returns ``("", "")`` when the input is empty or malformed.
    """
    token = raw.strip().strip('"').strip("'")
    if not token:
        return "", ""
    if token.lower().startswith("basic "):
        token = token.split(None, 1)[1].strip()
    if not token:
        return "", ""
    padding = (-len(token)) % 4
    if padding:
        token = f"{token}{'=' * padding}"
    try:
        decoded = base64.b64decode(token.encode("ascii"), validate=False).decode(
            "utf-8"
        )
    except (ValueError, UnicodeDecodeError):
        return "", ""
    if ":" not in decoded:
        return "", ""
    public_key, secret_key = decoded.split(":", 1)
    return public_key.strip(), secret_key.strip()


def resolve_langfuse_credentials(
    *,
    public_key: str = "",
    secret_key: str = "",
    base_url: str = "",
    mcp_auth_header: str = "",
) -> tuple[str, str, str]:
    """Resolve Langfuse credentials from standard envs or MCP auth header."""
    resolved_public_key = public_key or os.environ.get(
        "LANGFUSE_PUBLIC_KEY", os.environ.get("KINDLY_LANGFUSE_PUBLIC_KEY", "")
    )
    resolved_secret_key = secret_key or os.environ.get(
        "LANGFUSE_SECRET_KEY", os.environ.get("KINDLY_LANGFUSE_SECRET_KEY", "")
    )
    resolved_base_url = base_url or os.environ.get(
        "LANGFUSE_BASE_URL",
        os.environ.get("KINDLY_LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    )
    resolved_header = mcp_auth_header or os.environ.get(
        "LANGFUSE_MCP_AUTH_HEADER",
        os.environ.get("KINDLY_LANGFUSE_MCP_AUTH_HEADER", ""),
    )

    if not resolved_public_key or not resolved_secret_key:
        header_public_key, header_secret_key = _decode_langfuse_mcp_auth_header(
            resolved_header
        )
        resolved_public_key = resolved_public_key or header_public_key
        resolved_secret_key = resolved_secret_key or header_secret_key

    return resolved_public_key, resolved_secret_key, resolved_base_url


@dataclass
class Settings:
    """Runtime configuration (env-first).

    Note: keep this module lightweight; it is imported by tests.
    """

    # Search providers (removed Serger - SearXNG is primary)

    query_rewrite_cascade_timeout_seconds: float = float(
        os.environ.get("KINDLY_QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS", "20")
    )
    query_classifier_timeout_seconds: float = float(
        os.environ.get("KINDLY_CLASSIFIER_TIMEOUT_SECONDS", "10")
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
    query_decomposition_max_branches: int = int(
        os.environ.get("KINDLY_DECOMPOSITION_MAX_BRANCHES", "10")
    )
    query_decomposition_max_concurrency: int = int(
        os.environ.get("KINDLY_DECOMPOSITION_MAX_CONCURRENCY", "4")
    )
    query_understanding_jsonl_enabled: bool = (
        os.environ.get("KINDLY_QUERY_UNDERSTANDING_JSONL_ENABLED", "true").lower()
        == "true"
    )
    query_understanding_jsonl_path: str = os.environ.get(
        "KINDLY_QUERY_UNDERSTANDING_JSONL_PATH",
        str(Path(".kindly") / "training" / "query_understanding.jsonl"),
    )

    # Query rewrite providers (Cerebras → Groq → HF Inference cascade)
    cerebras_api_key: str = os.environ.get("CEREBRAS_API_KEY", "")
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    hf_token: str = os.environ.get("HF_TOKEN", "")
    cerebras_base_url: str = os.environ.get(
        "KINDLY_CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"
    )
    groq_base_url: str = os.environ.get(
        "KINDLY_GROQ_BASE_URL", "https://api.groq.com/openai/v1"
    )
    vercel_ai_gateway_api_key: str = os.environ.get("AI_GATEWAY_API_KEY", "")
    vercel_ai_gateway_base_url: str = os.environ.get(
        "KINDLY_VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1"
    )
    query_understanding_model: str = os.environ.get(
        "KINDLY_QUERY_UNDERSTANDING_MODEL", "openai/gpt-oss-20b"
    )
    cerebras_rewrite_model: str = os.environ.get(
        "KINDLY_CEREBRAS_REWRITE_MODEL", "cerebras/gpt-oss-120b"
    )
    groq_rewrite_model: str = os.environ.get(
        "KINDLY_GROQ_REWRITE_MODEL", "groq/gpt-oss-120b"
    )
    vercel_rewrite_model: str = os.environ.get(
        "KINDLY_VERCEL_REWRITE_MODEL", "groq/gpt-oss-20b"
    )

    # Embeddings (Hugging Face Inference Provider)
    hf_inference_provider: str = os.environ.get(
        "KINDLY_HF_INFERENCE_PROVIDER", "hf-inference"
    )
    hf_embedding_model: str = os.environ.get(
        "KINDLY_HF_EMBEDDING_MODEL", "ibm-granite/granite-embedding-97m-multilingual-r2"
    )
    embedding_dim: int = int(os.environ.get("KINDLY_EMBEDDING_DIM", "384"))

    # Reranking (Voyage primary, Jina fallback; gcp_cloudrun for custom GCP Cloud Run / TEI / FastAPI supported)
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
    # GCP Cloud Run reranker (TEI or custom FastAPI /rerank endpoint). Private by default; client handles IAM ID tokens.
    rerank_gcp_cloudrun_url: str = os.environ.get("KINDLY_RERANK_GCP_CLOUDRUN_URL", "")
    rerank_gcp_model: str = os.environ.get(
        "KINDLY_RERANK_GCP_MODEL", "BAAI/bge-reranker-v2-m3"
    )
    rerank_gcp_timeout: float = float(
        os.environ.get("KINDLY_RERANK_GCP_TIMEOUT", "30.0")
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

    # Entity extraction (GLiNER2, optional extra, opt-in only)
    # Per joint plan: explicit disabled by default; error events on failure when enabled.
    entity_extraction_enabled: bool = (
        os.environ.get("KINDLY_ENTITY_EXTRACTION_ENABLED", "false").lower() == "true"
    )
    gliner_model: str = os.environ.get(
        "KINDLY_GLINER_MODEL", "fastino/gliner2-base-v1"
    )
    gliner_threshold: float = float(
        os.environ.get("KINDLY_GLINER_THRESHOLD", "0.5")
    )

    # Entity overlap feature for rerank (measured only; off by default)
    rerank_entity_overlap_enabled: bool = (
        os.environ.get("KINDLY_RERANK_ENTITY_OVERLAP_ENABLED", "false").lower() == "true"
    )
    rerank_entity_overlap_weight: float = float(
        os.environ.get("KINDLY_RERANK_ENTITY_OVERLAP_WEIGHT", "0.15")
    )

    analytics_enabled: bool = (
        os.environ.get("KINDLY_ANALYTICS_ENABLED", "true").lower() == "true"
    )
    analytics_duckdb_path: str = os.environ.get(
        "KINDLY_ANALYTICS_DUCKDB_PATH",
        str(Path(".kindly") / "analytics" / "search_events.duckdb"),
    )

    # Page cache (Phase 5.2: separate DuckDB file, NOT shared with analytics DB)
    page_cache_duckdb_path: str = os.environ.get(
        "KINDLY_PAGE_CACHE_DUCKDB_PATH",
        str(Path(".kindly") / "cache" / "page_cache.duckdb"),
    )

    # Pollinations API (for gemini-search provider in web_search mix)
    pollinations_api_key: str = os.environ.get("POLLINATIONS_API_KEY", "")

    # OpenRouter API (shared by all OpenRouter integrations)
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")

    # Grok Search via OpenRouter (native web_search + x_search for xAI models)
    grok_model: str = os.environ.get("KINDLY_GROK_MODEL", "x-ai/grok-4.3")
    grok_timeout_seconds: float = float(
        os.environ.get("KINDLY_GROK_TIMEOUT_SECONDS", "60.0")
    )
    # Light provider mode for web_search RRF pipeline
    grok_web_search_mode: str = os.environ.get(
        "KINDLY_GROK_WEB_SEARCH_MODE", "conditional"
    )

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
    brave_mode: str = os.environ.get("KINDLY_BRAVE_MODE", "always")
    jina_mode: str = os.environ.get("KINDLY_JINA_MODE", "conditional")
    gemini_mode: str = os.environ.get("KINDLY_GEMINI_SEARCH_MODE", "always")
    composio_llm_search_mode: str = os.environ.get(
        "KINDLY_COMPOSIO_LLM_SEARCH_MODE", "always"
    )
    google_cse_api_key: str = os.environ.get("KINDLY_GOOGLE_CSE_API_KEY", "")
    google_cse_engine_id: str = os.environ.get("KINDLY_GOOGLE_CSE_ENGINE_ID", "")
    google_cse_timeout_seconds: float = float(
        os.environ.get("KINDLY_GOOGLE_CSE_TIMEOUT_SECONDS", "20")
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

    # =====================================================================
    # Result Memory (Qdrant local store) - Phase 7
    # =====================================================================
    # Injects historical candidates (from semantically similar past queries)
    # as a lower-weight virtual provider list into RRF merge.
    # Uses Qdrant :memory: or persistent path. Collection per (embed model, dim).
    # No LanceDB/semantic cache semantics.
    result_memory_path: str = os.environ.get("KINDLY_RESULT_MEMORY_PATH", "")
    result_memory_enabled: bool = (
        os.environ.get("KINDLY_RESULT_MEMORY_ENABLED", "true").lower() == "true"
    )
    result_memory_candidate_weight: float = float(
        os.environ.get("KINDLY_RESULT_MEMORY_CANDIDATE_WEIGHT", "0.5")
    )
    result_memory_candidate_limit: int = int(
        os.environ.get("KINDLY_RESULT_MEMORY_CANDIDATE_LIMIT", "5")
    )
    result_memory_min_similarity: float = float(
        os.environ.get("KINDLY_RESULT_MEMORY_MIN_SIMILARITY", "0.65")
    )

    # Remote web results index (Qdrant on HF Space)
    # Indexes final search results (dense + BM25 sparse vectors) for future discovery.
    # Master flag; empty URL silently disables indexing.
    web_results_index_enabled: bool = (
        os.environ.get("KINDLY_WEB_RESULTS_INDEX_ENABLED", "false").lower() == "true"
    )
    qdrant_space_url: str = os.environ.get(
        "KINDLY_QDRANT_SPACE_URL", "https://chmielvu-web-index.hf.space"
    )

    # Qdrant search provider (reads from the same index)
    qdrant_search_enabled: bool = (
        os.environ.get("KINDLY_QDRANT_SEARCH_ENABLED", "true").lower() == "true"
    )

    # FastMCP tool visibility profile
    tool_profile: str = os.environ.get("KINDLY_TOOL_PROFILE", "regular")

    # FastMCP tool search (opt-in; wires RegexSearchTransform after profile selection)
    # No legacy aliases (per joint plan: no backward compat).
    tool_search_enabled: bool = (
        os.environ.get("KINDLY_TOOL_SEARCH_ENABLED", "false").lower() == "true"
    )

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

    # Langfuse (hybrid observability for agentic ReAct module)
    # Primary: standard LANGFUSE_* envs (Langfuse SDK + OTLP client read them directly).
    # KINDLY_* are Windows/pwsh convenience fallbacks (mirrors GRAFANA_CLOUD_* pattern).
    langfuse_public_key: str = os.environ.get(
        "LANGFUSE_PUBLIC_KEY", os.environ.get("KINDLY_LANGFUSE_PUBLIC_KEY", "")
    )
    langfuse_secret_key: str = os.environ.get(
        "LANGFUSE_SECRET_KEY", os.environ.get("KINDLY_LANGFUSE_SECRET_KEY", "")
    )
    langfuse_base_url: str = os.environ.get(
        "LANGFUSE_BASE_URL",
        os.environ.get("KINDLY_LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    )
    langfuse_mcp_auth_header: str = os.environ.get(
        "LANGFUSE_MCP_AUTH_HEADER",
        os.environ.get("KINDLY_LANGFUSE_MCP_AUTH_HEADER", ""),
    )

    # =====================================================================
    # Agentic Research (LangChain/LangGraph ReAct module: agentic_web_research)
    # =====================================================================
    # Centralized parsing of KINDLY_AGENTIC_RESEARCH_* + NANOGPT_API_KEY.
    # This replaces the previous direct os.environ reads in agent/config.py
    # for consistency with OTel, analytics, rate limits, etc.
    # The agent/ subpackage now delegates to Settings for defaults.

    agentic_research_model: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_MODEL", "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B"
    )
    agentic_research_fallback_models: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_FALLBACK_MODELS",
        "minimax/minimax-m3:thinking,mistralai/mistral-small-4-119b-2603:thinking",
    )
    agentic_research_gemini_fallback_model: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_GEMINI_FALLBACK_MODEL", "gemini-3.5-flash"
    )
    agentic_research_hf_router_base_url: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_HF_ROUTER_BASE_URL",
        "https://router.huggingface.co/v1",
    )
    agentic_research_hf_fallback_model: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_HF_FALLBACK_MODEL",
        "openai/gpt-oss-120b:novita",
    )
    agentic_research_base_url: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_BASE_URL", "https://nano-gpt.com/api/subscription/v1"
    )
    # NANOGPT_API_KEY is the canonical key name used by the default agentic model provider
    nanogpt_api_key: str = os.environ.get("NANOGPT_API_KEY", "")
    agentic_research_temperature: float = float(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_TEMPERATURE", "0")
    )
    agentic_research_timeout_seconds: float = float(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_TIMEOUT_SECONDS", "180")
    )
    agentic_research_max_retries: int = int(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_MAX_RETRIES", "2")
    )

    # Depth profile controls (quick/normal/deep affect tool budget + timeout)
    agentic_research_quick_run_limit: int = int(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_QUICK_RUN_LIMIT", "6")
    )
    agentic_research_normal_run_limit: int = int(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_NORMAL_RUN_LIMIT", "10")
    )
    agentic_research_deep_run_limit: int = int(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_DEEP_RUN_LIMIT", "16")
    )
    agentic_research_quick_timeout_seconds: float = float(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_QUICK_TIMEOUT_SECONDS", "120")
    )
    agentic_research_normal_timeout_seconds: float = float(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_NORMAL_TIMEOUT_SECONDS", "180")
    )
    agentic_research_deep_timeout_seconds: float = float(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_DEEP_TIMEOUT_SECONDS", "300")
    )
    agentic_research_default_num_results: int = int(
        os.environ.get("KINDLY_AGENTIC_RESEARCH_DEFAULT_NUM_RESULTS", "5")
    )

    # External MCP tools (via langchain-mcp-adapters) for the ReAct agent.
    # If KINDLY_AGENTIC_RESEARCH_EXTERNAL_MCP_CONFIG is set (JSON string or filesystem path
    # to a servers config), the runner will best-effort load and merge additional tools
    # (no master enable flag; the presence of a non-empty config enables the attempt).
    # Example: '{"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}}'
    agentic_research_external_mcp_config: str = os.environ.get(
        "KINDLY_AGENTIC_RESEARCH_EXTERNAL_MCP_CONFIG", ""
    )

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

    # =====================================================================
    # LLM Judge Evaluation (opt-in, for automatic quality assessment of search runs)
    # =====================================================================
    judge_evaluation_enabled: bool = (
        os.environ.get("KINDLY_JUDGE_EVALUATION_ENABLED", "false").lower() == "true"
    )
    judge_model: str = os.environ.get(
        "KINDLY_JUDGE_MODEL", "google/gemini-2.0-flash-001"
    )
    judge_timeout_seconds: float = float(
        os.environ.get("KINDLY_JUDGE_TIMEOUT_SECONDS", "10.0")
    )

    # =====================================================================
    # A/B Testing Framework (opt-in, experiment config via YAML)
    # =====================================================================
    ab_testing_enabled: bool = (
        os.environ.get("KINDLY_AB_TESTING_ENABLED", "false").lower() == "true"
    )
    ab_config_path: str = os.environ.get("KINDLY_AB_CONFIG_PATH", "")

    def __post_init__(self) -> None:
        if self.rrf_provider_weights is None:
            # Provider weights rationale (Bruch et al. 2022: per-list weighting is more impactful than k tuning):
            # - tavily: 1.3 (optimized for AI assistants, structured extraction, freshness)
            # - gemini: 1.2 (Google grounding, high recall for factual/research queries)
            # - composio_llm_search: 1.15 (LLM-enhanced relevance ranking)
            # - grok_openrouter: 1.5 (Grok native web+X search on OpenRouter, high relevance)
            # - jina: 1.1 (semantic search expertise, deep understanding)
            # - searxng: 1.0 (baseline, free/open-source aggregator with meta-search breadth)
            # - brave: 1.0 (baseline, independent index, privacy-focused)
            # - search_router: 1.0 (free general SERP, general-purpose index)
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
                    "grok_openrouter": 1.5,
                    "search_router": 1.0,
                },
            )

        # Validate numeric parameters
        if not 0.0 <= self.mmr_lambda_param <= 1.0:
            raise ValueError(
                f"mmr_lambda_param must be in [0, 1], got {self.mmr_lambda_param!r}. "
                "Set KINDLY_MMR_LAMBDA env var to a value between 0 and 1."
            )
        if not 0.0 <= self.gliner_threshold <= 1.0:
            raise ValueError(
                f"gliner_threshold must be in [0, 1], got {self.gliner_threshold!r}. "
                "Set KINDLY_GLINER_THRESHOLD env var."
            )
        if not 0.0 <= self.rerank_entity_overlap_weight <= 1.0:
            raise ValueError(
                f"rerank_entity_overlap_weight must be in [0, 1], got {self.rerank_entity_overlap_weight!r}."
            )
        if self.rrf_k <= 0:
            raise ValueError(
                f"rrf_k must be > 0, got {self.rrf_k!r}. "
                "Set KINDLY_RRF_K env var to a positive integer."
            )

        # Result memory validation (Phase 7)
        if not (0.0 <= self.result_memory_candidate_weight <= 5.0):
            raise ValueError(
                f"result_memory_candidate_weight must be in [0, 5], got {self.result_memory_candidate_weight!r}. "
                "Set KINDLY_RESULT_MEMORY_CANDIDATE_WEIGHT env var."
            )
        if self.result_memory_candidate_limit < 0:
            raise ValueError(
                f"result_memory_candidate_limit must be >= 0, got {self.result_memory_candidate_limit!r}."
            )
        if not (0.0 <= self.result_memory_min_similarity <= 1.0):
            raise ValueError(
                f"result_memory_min_similarity must be in [0, 1], got {self.result_memory_min_similarity!r}. "
                "Set KINDLY_RESULT_MEMORY_MIN_SIMILARITY env var."
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

        # Langfuse (optional, for agentic hybrid tracing). Standard LANGFUSE_* preferred.
        # If only one of public/secret is set, Langfuse client will surface a clear error on use.
        if bool(self.langfuse_public_key) != bool(self.langfuse_secret_key):
            # Non-fatal here; telemetry/agent code guards usage.
            pass

        # Agentic research validation (run limits and timeouts must be positive)
        for name, val in [
            ("quick_run_limit", self.agentic_research_quick_run_limit),
            ("normal_run_limit", self.agentic_research_normal_run_limit),
            ("deep_run_limit", self.agentic_research_deep_run_limit),
            ("quick_timeout_seconds", self.agentic_research_quick_timeout_seconds),
            ("normal_timeout_seconds", self.agentic_research_normal_timeout_seconds),
            ("deep_timeout_seconds", self.agentic_research_deep_timeout_seconds),
            ("default_num_results", self.agentic_research_default_num_results),
        ]:
            if val <= 0:
                raise ValueError(
                    f"agentic_research_{name} must be > 0, got {val}. "
                    f"Check KINDLY_AGENTIC_RESEARCH_* env var."
                )
        if self.observability_max_items < 1:
            raise ValueError("observability_max_items must be >= 1.")
        from .tools.profiles import normalize_tool_profile

        self.tool_profile = normalize_tool_profile(self.tool_profile)


settings = Settings()
