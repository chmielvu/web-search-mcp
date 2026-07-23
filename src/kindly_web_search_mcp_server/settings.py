from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path

# Load .env before class definitions evaluate os.environ.get()
# Use override=True so values in .env take precedence over stale shell exports.
# Without this, a leftover `export TELEGRAM_SESSION_STRING=...` from a previous
# session silently overrides the .env value and pinpoints the wrong account.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    _settings_pkg = Path(__file__).resolve().parent
    _project_root = _settings_pkg.parent.parent
    load_dotenv(_project_root / ".env", override=True)
    load_dotenv(override=True)

from .utils.paths import (
    DEFAULT_ANALYTICS_DB,
    DEFAULT_EXPERIMENTS_YAML,
    DEFAULT_PAGE_CACHE_DB,
    DEFAULT_PROCESS_LOGS_DB,
    DEFAULT_QUERY_UNDERSTANDING_JSONL,
    DEFAULT_TRANSCRIPT_CACHE_DB,
    TELEGRAM_DIR,
)


def _parse_csv_env(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated environment string into a normalized tuple."""
    items: list[str] = []
    for item in raw.split(","):
        value = item.strip().casefold()
        if value:
            items.append(value)
    return tuple(dict.fromkeys(items))


def _parse_json_dict_env(raw: str, default: dict[str, list[str]]) -> dict[str, list[str]]:
    """Parse a JSON object env string into a dict of non-empty string lists.

    Raises ValueError (caught at Settings construction) on invalid JSON or on
    any key/value that is not a non-empty string / non-empty list of strings.
    """
    if not raw or not raw.strip():
        return dict(default)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"BRAVE_GOGGLES_BY_INTENT must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("BRAVE_GOGGLES_BY_INTENT must be a JSON object.")
    cleaned: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("BRAVE_GOGGLES_BY_INTENT keys must be non-empty strings.")
        if not isinstance(value, list) or not value:
            raise ValueError(f"BRAVE_GOGGLES_BY_INTENT[{key!r}] must be a non-empty list.")
        items = [str(v).strip() for v in value]
        if not all(items):
            raise ValueError(f"BRAVE_GOGGLES_BY_INTENT[{key!r}] entries must be non-empty strings.")
        cleaned[key.strip()] = items
    return cleaned


def _parse_string_dict_env(raw: str, *, name: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{name} must be a JSON object of string values.")
    return dict(value)


@dataclass
class Settings:
    """Runtime configuration (env-first).

    Note: keep this module lightweight; it is imported by tests.
    """

    # Provider env vars follow ecosystem conventions when the upstream tool
    # already defines them (for example `TAVILY_API_KEY`, `BRAVE_API_KEY`).
    # Project-owned knobs use the `` prefix.
    # Search providers (SearXNG is primary)

    query_rewrite_cascade_timeout_seconds: float = float(
        os.environ.get("QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS", "20")
    )
    query_rewrite_max_variants: int = int(os.environ.get("QUERY_REWRITE_MAX_VARIANTS", "2"))
    query_classifier_timeout_seconds: float = float(
        os.environ.get("CLASSIFIER_TIMEOUT_SECONDS", "10")
    )
    # ONNX intent classifier (primary path, replaces LLM for intent resolution)
    intent_classifier_url: str = os.environ.get("INTENT_CLASSIFIER_URL", "http://127.0.0.1:18686")
    intent_classifier_timeout_seconds: float = float(
        os.environ.get("INTENT_CLASSIFIER_TIMEOUT_SECONDS", "3")
    )
    intent_classifier_confidence_threshold: float = float(
        os.environ.get("INTENT_CLASSIFIER_CONFIDENCE_THRESHOLD", "0.50")
    )
    intent_classifier_enabled: bool = (
        os.environ.get("INTENT_CLASSIFIER_ENABLED", "true").lower() == "true"
    )
    query_decomposition_enabled: bool = (
        os.environ.get("QUERY_DECOMPOSITION_ENABLED", "true").lower() == "true"
    )
    query_decomposition_timeout_seconds: float = float(
        os.environ.get("QUERY_DECOMPOSITION_TIMEOUT_SECONDS", "10")
    )
    query_decomposition_max_subquestions: int = int(
        os.environ.get("QUERY_DECOMPOSITION_MAX_SUBQUESTIONS", "3")
    )
    query_decomposition_max_branches: int = int(os.environ.get("DECOMPOSITION_MAX_BRANCHES", "10"))
    query_decomposition_max_concurrency: int = int(
        os.environ.get("DECOMPOSITION_MAX_CONCURRENCY", "4")
    )
    # HTTP timeouts for the search provider client (seconds). The connect phase
    # is kept short while read/write/pool allow slow providers to respond.
    search_http_connect_timeout_seconds: float = float(
        os.environ.get("SEARCH_HTTP_CONNECT_TIMEOUT_SECONDS", "10")
    )
    search_http_read_timeout_seconds: float = float(
        os.environ.get("SEARCH_HTTP_READ_TIMEOUT_SECONDS", "30")
    )
    search_retrieve_budget_seconds: float = float(
        os.environ.get("SEARCH_RETRIEVE_BUDGET_SECONDS", "20")
    )
    query_understanding_jsonl_enabled: bool = (
        os.environ.get("QUERY_UNDERSTANDING_JSONL_ENABLED", "true").lower() == "true"
    )
    query_understanding_jsonl_path: str = os.environ.get(
        "QUERY_UNDERSTANDING_JSONL_PATH",
        DEFAULT_QUERY_UNDERSTANDING_JSONL,
    )

    # Query rewrite providers (Cerebras → Groq → HF Inference cascade)
    cerebras_api_key: str = os.environ.get("CEREBRAS_API_KEY", "")
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    hf_token: str = os.environ.get("HF_TOKEN", "")
    cerebras_base_url: str = os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
    groq_base_url: str = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    vercel_ai_gateway_api_key: str = os.environ.get("AI_GATEWAY_API_KEY", "")
    vercel_ai_gateway_base_url: str = os.environ.get(
        "VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1"
    )
    query_understanding_model: str = os.environ.get(
        "QUERY_UNDERSTANDING_MODEL", "openai/gpt-oss-20b"
    )
    cerebras_rewrite_model: str = os.environ.get("CEREBRAS_REWRITE_MODEL", "gpt-oss-120b")
    groq_rewrite_model: str = os.environ.get("GROQ_REWRITE_MODEL", "openai/gpt-oss-120b")
    huggingface_rewrite_model: str = os.environ.get(
        "HUGGINGFACE_REWRITE_MODEL", "openai/gpt-oss-120b:nscale"
    )
    vercel_rewrite_model: str = os.environ.get("VERCEL_REWRITE_MODEL", "openai/gpt-oss-20b")

    # Embeddings (Hugging Face Inference Provider)
    hf_inference_provider: str = os.environ.get("HF_INFERENCE_PROVIDER", "hf-inference")
    hf_embedding_model: str = os.environ.get(
        "HF_EMBEDDING_MODEL", "intfloat/multilingual-e5-large-instruct"
    )
    embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", "1024"))
    embedding_timeout_seconds: float = float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "30.0"))
    embedding_max_retries: int = int(os.environ.get("EMBEDDING_MAX_RETRIES", "1"))
    embedding_retry_delay_seconds: float = float(
        os.environ.get("EMBEDDING_RETRY_DELAY_SECONDS", "5.0")
    )

    # Reranking (Cohere primary, OpenRouter Cohere 4-fast fallback, Voyage last;
    # listwise LLM reranker stays in the default stack but is tightly bounded)

    rerank_bi_encoder_timeout_seconds: float = float(
        os.environ.get("RERANK_BI_ENCODER_TIMEOUT_SECONDS", "15.0")
    )
    rerank_bi_encoder_text_max_chars: int = int(
        os.environ.get("RERANK_BI_ENCODER_TEXT_MAX_CHARS", "384")
    )
    rerank_bi_encoder_batch_size: int = int(os.environ.get("RERANK_BI_ENCODER_BATCH_SIZE", "64"))
    rerank_bi_encoder_max_concurrent_batches: int = int(
        os.environ.get("RERANK_BI_ENCODER_MAX_CONCURRENT_BATCHES", "3")
    )

    voyage_api_key: str = os.environ.get("VOYAGE_API_KEY", "")
    voyage_rerank_model: str = os.environ.get("VOYAGE_RERANK_MODEL", "rerank-2.5")
    jina_rerank_model: str = os.environ.get("JINA_RERANK_MODEL", "jina-reranker-v3")
    cohere_api_key: str = os.environ.get("COHERE_API_KEY", "")
    cohere_rerank_model: str = os.environ.get("COHERE_RERANK_MODEL", "rerank-v4.0-fast")
    cohere_rerank_base_url: str = os.environ.get(
        "COHERE_RERANK_BASE_URL", "https://api.cohere.com/v2/rerank"
    )
    cohere_rerank_timeout: float = float(os.environ.get("COHERE_RERANK_TIMEOUT", "5.0"))
    openrouter_rerank_model: str = os.environ.get("OPENROUTER_RERANK_MODEL", "cohere/rerank-4-fast")
    openrouter_rerank_base_url: str = os.environ.get(
        "OPENROUTER_RERANK_BASE_URL", "https://openrouter.ai/api/v1/rerank"
    )
    openrouter_rerank_timeout: float = float(os.environ.get("OPENROUTER_RERANK_TIMEOUT", "5.0"))

    rerank_score_thresholds_json: str = os.environ.get("RERANK_SCORE_THRESHOLDS_JSON", "{}")
    diversity_similarity_threshold: float = float(
        os.environ.get("DIVERSITY_SIMILARITY_THRESHOLD", "0.85")
    )
    mmr_lambda_param: float = float(os.environ.get("MMR_LAMBDA", "0.80"))
    diversity_max_per_host: int = int(os.environ.get("DIVERSITY_MAX_PER_HOST", "2"))

    # RankLLM Settings
    rankllm_openrouter_model: str = os.environ.get(
        "RANKLLM_OPENROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"
    )
    rankllm_gemini_model: str = os.environ.get("RANKLLM_GEMINI_MODEL", "gemini-3.1-flash-lite")
    rankllm_timeout_seconds: float = float(os.environ.get("RANKLLM_TIMEOUT_SECONDS", "20.0"))
    rankllm_max_passage_words: int = int(os.environ.get("RANKLLM_MAX_PASSAGE_WORDS", "300"))
    rankllm_temperature: float = float(os.environ.get("RANKLLM_TEMPERATURE", "0.0"))

    rerank_recency_weight: float = float(os.environ.get("RERANK_RECENCY_WEIGHT", "0.15"))
    rerank_recency_half_life_days: int = int(os.environ.get("RERANK_RECENCY_HALF_LIFE_DAYS", "90"))
    # Entity extraction (GLiNER2, optional extra, opt-in only)
    # Per joint plan: explicit disabled by default; error events on failure when enabled.
    entity_extraction_enabled: bool = (
        os.environ.get("ENTITY_EXTRACTION_ENABLED", "false").lower() == "true"
    )
    gliner_model: str = os.environ.get("GLINER_MODEL", "fastino/gliner2-base-v1")
    gliner_threshold: float = float(os.environ.get("GLINER_THRESHOLD", "0.5"))

    # Entity overlap feature for rerank (measured only; off by default)
    rerank_entity_overlap_enabled: bool = (
        os.environ.get("RERANK_ENTITY_OVERLAP_ENABLED", "false").lower() == "true"
    )
    rerank_entity_overlap_weight: float = float(
        os.environ.get("RERANK_ENTITY_OVERLAP_WEIGHT", "0.15")
    )

    analytics_enabled: bool = os.environ.get("ANALYTICS_ENABLED", "true").lower() == "true"
    analytics_shutdown_drain_timeout_seconds: float = float(
        os.environ.get("ANALYTICS_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", "5.0")
    )
    analytics_duckdb_path: str = os.environ.get(
        "ANALYTICS_DUCKDB_PATH",
        DEFAULT_ANALYTICS_DB,
    )
    vss_enabled: bool = os.environ.get("VSS_ENABLED", "true").lower() == "true"
    flockmtl_enabled: bool = os.environ.get("FLOCKMTL_ENABLED", "true").lower() == "true"

    # Process logs DuckDB — centralized, 48h TTL, FTS enabled
    process_logs_enabled: bool = os.environ.get("PROCESS_LOGS_ENABLED", "true").lower() == "true"
    process_logs_sqlite_path: str = os.environ.get(
        "PROCESS_LOGS_SQLITE_PATH",
        os.environ.get("PROCESS_LOGS_DUCKDB_PATH", DEFAULT_PROCESS_LOGS_DB),
    )
    process_logs_ttl_hours: int = int(os.environ.get("PROCESS_LOGS_TTL_HOURS", "48"))

    # Page cache (Phase 5.2: separate DuckDB file, NOT shared with analytics DB)
    page_cache_sqlite_path: str = os.environ.get(
        "PAGE_CACHE_SQLITE_PATH",
        os.environ.get("PAGE_CACHE_DUCKDB_PATH", DEFAULT_PAGE_CACHE_DB),
    )

    # Transcript cache (separate DuckDB file for YouTube transcript caching)
    transcript_cache_sqlite_path: str = os.environ.get(
        "TRANSCRIPT_CACHE_SQLITE_PATH",
        os.environ.get("TRANSCRIPT_CACHE_DUCKDB_PATH", DEFAULT_TRANSCRIPT_CACHE_DB),
    )

    # Telegram search provider (Telethon MTProto)
    telegram_api_id: str = os.environ.get("TELEGRAM_API_ID", "")
    telegram_api_hash: str = os.environ.get("TELEGRAM_API_HASH", "")
    telegram_session_string: str = os.environ.get("TELEGRAM_SESSION_STRING", "")
    telegram_public_search_daily_budget: int = int(
        os.environ.get("TELEGRAM_PUBLIC_SEARCH_DAILY_BUDGET", "8")
    )
    telegram_flood_sleep_threshold: int = int(
        os.environ.get("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "60")
    )
    telegram_registry_duckdb_path: str = os.environ.get(
        "TELEGRAM_REGISTRY_DUCKDB_PATH",
        str(TELEGRAM_DIR / "registry.duckdb"),
    )

    # OpenRouter API (shared by all OpenRouter integrations)
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    openrouter_chat_base_url: str = os.environ.get(
        "OPENROUTER_CHAT_BASE_URL", "https://openrouter.ai/api/v1"
    )

    # Grok Search via OpenRouter (native web_search + x_search for xAI models)
    grok_model: str = os.environ.get("GROK_MODEL", "x-ai/grok-4.3")
    grok_timeout_seconds: float = float(os.environ.get("GROK_TIMEOUT_SECONDS", "60.0"))
    # Gemini Grounding (for gemini_search MCP tool)
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_second_api_key: str = os.environ.get("GEMINI_SECOND_API_KEY", "")
    # Model selection handled via hardcoded fallback tier in gemini_search_tool.py

    # Gemini summaries (for get_content / batch_get_content optional summaries)
    summary_gemini_model: str = os.environ.get("SUMMARY_GEMINI_MODEL", "gemini-3.1-flash-lite")
    summary_gemma_fallback_model: str = os.environ.get(
        "SUMMARY_GEMMA_FALLBACK_MODEL", "gemma-4-26b-a4b-it"
    )
    summary_max_tokens: int = int(os.environ.get("SUMMARY_MAX_TOKENS", "1200"))

    # YouTube Transcript
    youtube_transcript_proxy_url: str = os.environ.get("YOUTUBE_TRANSCRIPT_PROXY_URL", "")
    youtube_transcript_max_chars: int = int(os.environ.get("YOUTUBE_TRANSCRIPT_MAX_CHARS", "50000"))
    youtube_transcript_timeout_seconds: float = float(
        os.environ.get("YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS", "30")
    )

    # YouTube Transcript Backend (auto|ytdlp|api)
    youtube_transcript_backend: str = os.environ.get("YOUTUBE_TRANSCRIPT_BACKEND", "auto")

    # Whisper ASR (HF Space) for videos without captions
    whisper_space_url: str = os.environ.get("WHISPER_SPACE_URL", "")
    whisper_space_timeout_seconds: float = float(
        os.environ.get("WHISPER_SPACE_TIMEOUT_SECONDS", "300")
    )

    # YouTube Search (uses SearXNG with youtube engine)
    youtube_search_engine: str = os.environ.get("YOUTUBE_SEARCH_ENGINE", "youtube")

    # YouTube Data API v3 (optional, enables enriched search)
    youtube_api_key: str = os.environ.get("GOOGLE_API_KEY", "")
    youtube_api_timeout_seconds: float = float(os.environ.get("YOUTUBE_API_TIMEOUT_SECONDS", "15"))
    youtube_api_daily_quota: int = int(os.environ.get("YOUTUBE_API_DAILY_QUOTA", "10000"))
    youtube_api_language: str = os.environ.get("YOUTUBE_API_LANGUAGE", "")
    youtube_api_region: str = os.environ.get("YOUTUBE_API_REGION", "")

    # Academic Search Providers
    # Semantic Scholar (optional, 100 RPS with key vs 1 RPS shared)
    s2_api_key: str = os.environ.get("S2_API_KEY", "")
    s2_timeout: int = int(os.environ.get("S2_TIMEOUT", "30"))
    s2_max_retries: int = int(os.environ.get("S2_MAX_RETRIES", "0"))  # 0 = fail fast

    # OpenAlex (optional, polite pool with email)
    openalex_email: str = os.environ.get("OPENALEX_EMAIL", "")
    openalex_api_key: str = os.environ.get("OPENALEX_API_KEY", "")

    # CrossRef (optional, polite pool with mailto)
    crossref_mailto: str = os.environ.get("CROSSREF_MAILTO", "")

    # PubMed (optional, higher rate limit with key)
    pubmed_api_key: str = os.environ.get("PUBMED_API_KEY", "")

    # CORE (optional, required for full-text search)
    core_api_key: str = os.environ.get("CORE_API_KEY", "")

    # Academic search defaults
    academic_default_sources: str = os.environ.get(
        "ACADEMIC_DEFAULT_SOURCES", "arxiv,semanticscholar"
    )
    academic_max_results: int = int(os.environ.get("ACADEMIC_MAX_RESULTS", "10"))

    search_router_api_key: str = os.environ.get("SEARCH_ROUTER_API_KEY", "")
    tavily_api_key: str = os.environ.get("TAVILY_API_KEY", "")
    brave_api_key: str = os.environ.get("BRAVE_API_KEY", "")
    brave_suggest_api_key: str = os.environ.get("BRAVE_SUGGEST_API_KEY", "")
    brave_spellcheck_api_key: str = os.environ.get("BRAVE_SPELLCHECK_API_KEY", "")
    brave_goggles_by_intent: dict[str, list[str]] = field(
        default_factory=lambda: _parse_json_dict_env(
            os.environ.get("BRAVE_GOGGLES_BY_INTENT", "{}"), {}
        )
    )
    jina_api_key: str = os.environ.get("JINA_API_KEY", "")
    google_cse_api_key: str = os.environ.get("GOOGLE_API_KEY", "")
    google_cse_engine_id: str = "771d303cf528e4b7c"

    # Provider master switch. Keep enabled by default; use DISABLED_PROVIDERS
    # to turn off noisy providers like reddit without changing code.
    providers_enabled: bool = os.environ.get("PROVIDERS_ENABLED", "true").lower() == "true"
    disabled_providers: tuple[str, ...] = _parse_csv_env(os.environ.get("DISABLED_PROVIDERS", ""))

    # New SERP providers (Serper, SerpApi, BrightData)
    serper_api_key: str = os.environ.get("SERPER_API_KEY", "")
    serpapi_api_key: str = os.environ.get("SERPAPI_API_KEY", "")
    serpapi_default_engine: str = os.environ.get("SERPAPI_DEFAULT_ENGINE", "yahoo")
    serpapi_engines: str = os.environ.get(
        "SERPAPI_ENGINES", ""
    )  # comma-separated, e.g. "yahoo,baidu,naver"
    brightdata_api_key: str = os.environ.get("BRIGHTDATA_API_KEY", "")
    brightdata_zone: str = os.environ.get("BRIGHTDATA_ZONE", "sdk_serp")
    brightdata_payload_extra: str = os.environ.get("BRIGHTDATA_PAYLOAD_EXTRA", "")
    langsearch_api_key: str = os.environ.get("LANGSEARCH_API_KEY", "")
    langsearch_base_url: str = os.environ.get("LANGSEARCH_BASE_URL", "https://api.langsearch.com")

    # SERP semaphore limit (controls concurrency for paid_serp providers)
    serp_semaphore_limit: int = int(os.environ.get("SERP_SEMAPHORE_LIMIT", "2"))

    # SearXNG config (consolidated from raw os.environ reads in searxng.py)
    searxng_base_url: str = os.environ.get("SEARXNG_BASE_URL", "")
    searxng_headers_json: str = os.environ.get("SEARXNG_HEADERS_JSON", "")
    searxng_user_agent: str = os.environ.get("SEARXNG_USER_AGENT", "")
    searxng_language: str = os.environ.get("SEARXNG_LANGUAGE", "")
    searxng_safesearch: str = os.environ.get("SEARXNG_SAFESEARCH", "")

    # DeGoog search aggregator (self-hosted)
    degoog_base_url: str = os.environ.get("DEGOOG_BASE_URL", "")

    # Reddit config (consolidated from raw os.environ read in reddit.py)
    reddit_delay_seconds: float = float(os.environ.get("REDDIT_DELAY_SECONDS", "2"))

    # StackExchange config (consolidated from raw os.environ reads in stackexchange.py)
    stackexchange_sites: str = os.environ.get("STACKEXCHANGE_SITES", "stackoverflow")
    stackexchange_app_key: str = os.environ.get("STACKEXCHANGE_APP_KEY", "")

    # Composio Search toolkit
    composio_api_key: str = os.environ.get("COMPOSIO_API_KEY", "")
    composio_user_id: str = os.environ.get("COMPOSIO_USER_ID", "")
    composio_search_toolkit_version: str = os.environ.get(
        "COMPOSIO_SEARCH_TOOLKIT_VERSION", "20260618_00"
    )
    composio_timeout_seconds: float = float(os.environ.get("COMPOSIO_TIMEOUT_SECONDS", "25"))
    composio_max_retries: int = int(os.environ.get("COMPOSIO_MAX_RETRIES", "2"))

    # Parallel AI Search API
    parallel_api_key: str = os.environ.get("PARALLEL_API_KEY", "")

    # RRF tuning
    rrf_k: int = int(os.environ.get("RRF_K", "60"))
    blocklist_duckdb_path: str = ""

    # Remote web results index (Qdrant on HF Space)
    # Indexes final search results (dense + BM25 sparse vectors) for future discovery.
    # Master flag; empty URL silently disables indexing.
    web_results_index_enabled: bool = (
        os.environ.get("WEB_RESULTS_INDEX_ENABLED", "false").lower() == "true"
    )
    qdrant_space_url: str = os.environ.get(
        "QDRANT_SPACE_URL", "https://chmielvu-web-index.hf.space"
    )

    # FastMCP tool visibility profile
    tool_profile: str = os.environ.get("TOOL_PROFILE", "regular")

    # FastMCP tool search (opt-in; wires RegexSearchTransform after profile selection)
    # No legacy aliases (per joint plan: no backward compat).
    tool_search_enabled: bool = os.environ.get("TOOL_SEARCH_ENABLED", "false").lower() == "true"

    # Per-tool rate limiting
    # Internal field names use "cheap" to reflect multi-tool scope
    # Rate-limit and concurrency settings for web search (prefixed with ).
    rate_limit_cheap_rps: float = float(os.environ.get("RATE_LIMIT_WEB_SEARCH_RPS", "4.0"))
    rate_limit_cheap_burst: int = int(os.environ.get("RATE_LIMIT_WEB_SEARCH_BURST", "12"))
    rate_limit_expensive_rps: float = float(os.environ.get("RATE_LIMIT_EXPENSIVE_RPS", "0.5"))
    rate_limit_expensive_burst: int = int(os.environ.get("RATE_LIMIT_EXPENSIVE_BURST", "1"))

    # =====================================================================
    # OpenTelemetry / Grafana Observability (Phase 1 of observability work)
    # =====================================================================
    # These enable first-class traces + metrics export to Grafana Cloud
    # (or local collector / Alloy). We prefer standard OTEL_* env vars
    # for compatibility with the broader ecosystem, but provide
    #  + GRAFANA_CLOUD_* convenience vars for Windows/pwsh ergonomics.

    otel_enabled: bool = os.environ.get("OTEL_ENABLED", "true").lower() == "true"

    # Sampling (head-based). 1.0 = all traces (expensive). 0.1 = 10% typical for dev/prod.
    otel_sampling_ratio: float = float(os.environ.get("OTEL_SAMPLING_RATIO", "0.15"))

    # Service identity overrides (fall back to telemetry.py defaults + package version)
    otel_service_name: str = os.environ.get("OTEL_SERVICE_NAME", "web-search-mcp")
    otel_service_namespace: str = os.environ.get("OTEL_SERVICE_NAMESPACE", "web-search-mcp")
    otel_deployment_environment: str = os.environ.get(
        "DEPLOYMENT_ENV", os.environ.get("OTEL_ENVIRONMENT", "development")
    )

    # Grafana Cloud convenience (recommended for Windows users who dislike manual Base64)
    # When these are set, telemetry.py can auto-construct the Authorization header.
    grafana_cloud_instance_id: str = os.environ.get("GRAFANA_CLOUD_INSTANCE_ID", "")
    grafana_cloud_api_key: str = os.environ.get("GRAFANA_CLOUD_API_KEY", "")
    grafana_cloud_otlp_endpoint: str = os.environ.get("GRAFANA_CLOUD_OTLP_ENDPOINT", "")

    # Phoenix (Arize) observability through the local SSH forward.
    phoenix_project_name: str = os.environ.get("PHOENIX_PROJECT_NAME", "web-search-mcp")
    phoenix_collector_endpoint: str = os.environ.get(
        "PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces"
    )
    phoenix_client_headers: dict[str, str] = field(
        default_factory=lambda: _parse_string_dict_env(
            os.environ.get("PHOENIX_CLIENT_HEADERS", ""),
            name="PHOENIX_CLIENT_HEADERS",
        )
    )
    # Prometheus sidecar / Alloy scrape support
    prometheus_enabled: bool = os.environ.get("PROMETHEUS_ENABLED", "false").lower() == "true"
    prometheus_port: int = int(os.environ.get("PROMETHEUS_PORT", "0"))  # 0 = disabled / dynamic

    # Attribute safety (used by utils/observability.py and telemetry)
    observability_max_text_chars: int = int(os.environ.get("OBSERVABILITY_MAX_TEXT_CHARS", "20000"))
    observability_max_items: int = int(os.environ.get("OBSERVABILITY_MAX_ITEMS", "10"))

    # =====================================================================
    # LLM Judge Evaluation (opt-in, for automatic quality assessment of search runs)
    # =====================================================================
    judge_evaluation_enabled: bool = (
        os.environ.get("JUDGE_EVALUATION_ENABLED", "false").lower() == "true"
    )
    judge_model: str = os.environ.get("JUDGE_MODEL", "openai/gpt-oss-120b")
    judge_timeout_seconds: float = float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "10.0"))

    # =====================================================================
    # Crawl4AI remote server (Docker on VPS)
    # =====================================================================
    crawl4ai_base_url: str = os.environ.get("CRAWL4AI_BASE_URL", "")
    # When set (e.g. http://vps-ip:11235), all Crawl4AI calls go remote.
    # When empty, Crawl4AI is skipped; fallback to Jina Reader.

    crawl4ai_timeout_seconds: float = float(os.environ.get("CRAWL4AI_TIMEOUT_SECONDS", "120"))
    crawl4ai_max_pages_sitemap: int = int(os.environ.get("CRAWL4AI_MAX_PAGES_SITEMAP", "100"))
    crawl4ai_health_cache_seconds: float = float(
        os.environ.get("CRAWL4AI_HEALTH_CACHE_SECONDS", "30")
    )

    # =====================================================================
    # Firecrawl Cloud (batch scrape primary backend for batch_get_content)
    # =====================================================================
    firecrawl_api_key: str = os.environ.get("FIRECRAWL_API_KEY", "")
    # When set, batch_get_content tries Firecrawl Cloud first.
    # When empty, Firecrawl is skipped and the existing per-URL pipeline runs.

    firecrawl_api_url: str = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
    firecrawl_timeout_seconds: float = float(os.environ.get("FIRECRAWL_TIMEOUT_SECONDS", "60.0"))
    firecrawl_poll_interval_seconds: float = float(
        os.environ.get("FIRECRAWL_POLL_INTERVAL_SECONDS", "2.0")
    )
    firecrawl_max_poll_seconds: float = float(os.environ.get("FIRECRAWL_MAX_POLL_SECONDS", "120.0"))

    # =====================================================================
    # Camoufox sidecar (stealth-Firefox on VPS)
    # =====================================================================
    camoufox_base_url: str = os.environ.get("CAMOUFOX_BASE_URL", "")
    # When set (e.g. http://127.0.0.1:3000 via SSH tunnel), Camoufox is the last-resort browser.
    # When empty, Camoufox stage is skipped.
    camoufox_timeout_seconds: float = float(os.environ.get("CAMOUFOX_TIMEOUT_SECONDS", "30"))
    camoufox_health_cache_seconds: float = float(
        os.environ.get("CAMOUFOX_HEALTH_CACHE_SECONDS", "30")
    )

    # =====================================================================
    # A/B Testing Framework (opt-in, experiment config via YAML)
    # =====================================================================
    ab_testing_enabled: bool = os.environ.get("AB_TESTING_ENABLED", "false").lower() == "true"
    ab_config_path: str = os.environ.get("AB_CONFIG_PATH", DEFAULT_EXPERIMENTS_YAML)
    ab_shadow_mode_default: bool = (
        os.environ.get("AB_SHADOW_MODE_DEFAULT", "true").lower() == "true"
    )
    ab_assignment_cache_ttl_seconds: int = int(
        os.environ.get("AB_ASSIGNMENT_CACHE_TTL_SECONDS", "300")
    )

    def __post_init__(self) -> None:
        # Validate numeric parameters
        if not 0.0 <= self.mmr_lambda_param <= 1.0:
            raise ValueError(
                f"mmr_lambda_param must be in [0, 1], got {self.mmr_lambda_param!r}. "
                "Set MMR_LAMBDA env var to a value between 0 and 1."
            )
        if not 0.0 <= self.gliner_threshold <= 1.0:
            raise ValueError(
                f"gliner_threshold must be in [0, 1], got {self.gliner_threshold!r}. "
                "Set GLINER_THRESHOLD env var."
            )
        if not 0.0 <= self.rerank_entity_overlap_weight <= 1.0:
            raise ValueError(
                f"rerank_entity_overlap_weight must be in [0, 1], got {self.rerank_entity_overlap_weight!r}."
            )

        if self.rerank_bi_encoder_timeout_seconds <= 0:
            raise ValueError(
                "rerank_bi_encoder_timeout_seconds must be > 0, "
                f"got {self.rerank_bi_encoder_timeout_seconds!r}."
            )
        if self.rerank_bi_encoder_text_max_chars <= 0:
            raise ValueError(
                "rerank_bi_encoder_text_max_chars must be > 0, "
                f"got {self.rerank_bi_encoder_text_max_chars!r}."
            )
        if self.rerank_bi_encoder_batch_size <= 0:
            raise ValueError(
                "rerank_bi_encoder_batch_size must be > 0, "
                f"got {self.rerank_bi_encoder_batch_size!r}."
            )
        if self.rerank_bi_encoder_max_concurrent_batches <= 0:
            raise ValueError(
                "rerank_bi_encoder_max_concurrent_batches must be > 0, "
                f"got {self.rerank_bi_encoder_max_concurrent_batches!r}."
            )
        if self.rrf_k <= 0:
            raise ValueError(
                f"rrf_k must be > 0, got {self.rrf_k!r}. Set RRF_K env var to a positive integer."
            )

        if self.diversity_max_per_host <= 0:
            raise ValueError(
                f"diversity_max_per_host must be >= 1, got {self.diversity_max_per_host}"
            )
        if not 0.0 <= self.diversity_similarity_threshold <= 1.0:
            raise ValueError(
                f"diversity_similarity_threshold must be in [0, 1], got {self.diversity_similarity_threshold!r}."
            )

        import json

        try:
            thresholds = json.loads(self.rerank_score_thresholds_json)
        except Exception as exc:
            raise ValueError(f"rerank_score_thresholds_json is not valid JSON: {exc}")
        if not isinstance(thresholds, dict):
            raise ValueError("rerank_score_thresholds_json must be a JSON object (dict)")
        for k, v in thresholds.items():
            if not isinstance(k, str):
                raise ValueError("rerank_score_thresholds_json keys must be strings")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("rerank_score_thresholds_json values must be float/int")
            if not 0.0 <= float(v) <= 1.0:
                raise ValueError("rerank_score_thresholds_json values must be in [0, 1]")

        # OTel / Observability validation
        if not (0.0 < self.otel_sampling_ratio <= 1.0):
            raise ValueError(
                f"otel_sampling_ratio must be in (0.0, 1.0], got {self.otel_sampling_ratio!r}. "
                "Set OTEL_SAMPLING_RATIO (e.g. 0.1 for 10% head sampling)."
            )
        if self.observability_max_text_chars < 1024:
            raise ValueError(
                "observability_max_text_chars must be >= 1024 to avoid truncating useful debug info."
            )

        # Phoenix collector endpoint is optional — when set, OTLP traces go to Phoenix.

        if self.observability_max_items < 1:
            raise ValueError("observability_max_items must be >= 1.")
        from .tools.profiles import normalize_tool_profile

        _CANONICAL_SEARCH_INTENTS = frozenset(
            {
                "general",
                "ai_coding_and_infrastructure",
                "digital_humanities",
                "comparison",
                "social_media",
                "news",
            }
        )
        self.tool_profile = normalize_tool_profile(self.tool_profile)
        for key in self.brave_goggles_by_intent:
            if key not in _CANONICAL_SEARCH_INTENTS:
                raise ValueError(
                    f"BRAVE_GOGGLES_BY_INTENT key {key!r} must be a canonical intent name."
                )


settings = Settings()


def get_env_value(name: str, fallback: str = "") -> str:
    """Read a current environment value with an optional fallback."""
    return os.environ.get(name, fallback)
