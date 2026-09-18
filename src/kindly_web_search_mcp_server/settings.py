from __future__ import annotations

import os

os.environ.setdefault("TQDM_DISABLE", "1")

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
    DEFAULT_BLOCKLIST_DB,
    DEFAULT_CODE_FETCH_SNAPSHOT_DB,
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


def _parse_float_dict_env(raw: str, default: dict[str, float], *, name: str) -> dict[str, float]:
    """Parse a JSON object env string into a dict of float values.

    Raises ValueError (caught at Settings construction) on invalid JSON or on
    any key/value that is not a non-empty string / finite number.
    """
    if not raw or not raw.strip():
        return dict(default)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object.")
    cleaned: dict[str, float] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} keys must be non-empty strings.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}[{key!r}] must be a number.")
        cleaned[key.strip()] = float(value)
    return cleaned


def _env_int(name: str, default: int) -> int:
    """Read an integer setting, naming the variable when the value is unusable.

    The message matters more than the exception type here: these defaults are
    evaluated while the module is imported, so the raise site has no logger and
    the variable name is the only clue the operator gets.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw.strip()!r}.") from exc


def _env_float(name: str, default: float) -> float:
    """Read a numeric setting, naming the variable when the value is unusable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw.strip()!r}.") from exc


@dataclass
class Settings:
    """Runtime configuration (env-first).

    Note: keep this module lightweight; it is imported by tests.
    """

    # Provider env vars follow ecosystem conventions when the upstream tool
    # already defines them (for example `TAVILY_API_KEY`, `BRAVE_API_KEY`).
    # Project-owned knobs use the `` prefix.
    # Search providers (SearXNG is primary)

    query_rewrite_cascade_timeout_seconds: float = _env_float(
        "QUERY_REWRITE_CASCADE_TIMEOUT_SECONDS", 20.0
    )
    query_rewrite_max_variants: int = _env_int("QUERY_REWRITE_MAX_VARIANTS", 2)
    graph_expansion_enabled: bool = (
        os.environ.get("GRAPH_EXPANSION_ENABLED", "false").lower() == "true"
    )
    graph_expansion_max_related_queries: int = _env_int("GRAPH_EXPANSION_MAX_RELATED_QUERIES", 2)
    graph_expansion_max_age_seconds: float = _env_float("GRAPH_EXPANSION_MAX_AGE_SECONDS", 86400.0)
    query_classifier_timeout_seconds: float = _env_float("CLASSIFIER_TIMEOUT_SECONDS", 10.0)
    # Hosted unified-ml GLiNER2 gateway used for query understanding.
    intent_classifier_url: str = os.environ.get("INTENT_CLASSIFIER_URL", "http://127.0.0.1:8000")
    intent_classifier_timeout_seconds: float = _env_float("INTENT_CLASSIFIER_TIMEOUT_SECONDS", 3.0)
    intent_classifier_confidence_threshold: float = _env_float(
        "INTENT_CLASSIFIER_CONFIDENCE_THRESHOLD", 0.5
    )
    intent_classifier_enabled: bool = (
        os.environ.get("INTENT_CLASSIFIER_ENABLED", "true").lower() == "true"
    )
    query_decomposition_enabled: bool = (
        os.environ.get("QUERY_DECOMPOSITION_ENABLED", "true").lower() == "true"
    )
    query_decomposition_timeout_seconds: float = _env_float(
        "QUERY_DECOMPOSITION_TIMEOUT_SECONDS", 10.0
    )
    query_decomposition_max_subquestions: int = _env_int("QUERY_DECOMPOSITION_MAX_SUBQUESTIONS", 3)
    query_decomposition_max_branches: int = _env_int("DECOMPOSITION_MAX_BRANCHES", 10)
    query_decomposition_max_concurrency: int = _env_int("DECOMPOSITION_MAX_CONCURRENCY", 4)
    # HTTP timeouts for the search provider client (seconds). The connect phase
    # is kept short while read/write/pool allow slow providers to respond.
    search_http_connect_timeout_seconds: float = _env_float(
        "SEARCH_HTTP_CONNECT_TIMEOUT_SECONDS", 10.0
    )
    search_http_read_timeout_seconds: float = _env_float("SEARCH_HTTP_READ_TIMEOUT_SECONDS", 30.0)
    search_retrieve_budget_seconds: float = _env_float("SEARCH_RETRIEVE_BUDGET_SECONDS", 20.0)
    huggingface_semantic_search_url: str = os.environ.get(
        "HUGGINGFACE_SEMANTIC_SEARCH_URL",
        "https://davanstrien-hub-search-api.hf.space",
    )
    huggingface_semantic_search_timeout_seconds: float = _env_float(
        "HUGGINGFACE_SEMANTIC_SEARCH_TIMEOUT_SECONDS",
        _env_float("SEARCH_RETRIEVE_BUDGET_SECONDS", 20.0),
    )
    huggingface_semantic_search_min_interval_seconds: float = _env_float(
        "HUGGINGFACE_SEMANTIC_SEARCH_MIN_INTERVAL_SECONDS", 0.25
    )
    query_understanding_jsonl_enabled: bool = (
        os.environ.get("QUERY_UNDERSTANDING_JSONL_ENABLED", "true").lower() == "true"
    )
    query_understanding_jsonl_path: str = os.environ.get(
        "QUERY_UNDERSTANDING_JSONL_PATH",
        DEFAULT_QUERY_UNDERSTANDING_JSONL,
    )

    # Query rewrite providers (Groq → HF Inference → Vercel cascade)
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    hf_token: str = os.environ.get("HF_TOKEN", "")
    groq_base_url: str = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    vercel_ai_gateway_api_key: str = os.environ.get("AI_GATEWAY_API_KEY", "")
    vercel_ai_gateway_base_url: str = os.environ.get(
        "VERCEL_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1"
    )
    query_understanding_model: str = os.environ.get(
        "QUERY_UNDERSTANDING_MODEL", "openai/gpt-oss-20b"
    )
    freshness_max_age_days: int = _env_int("FRESHNESS_MAX_AGE_DAYS", 90)
    groq_rewrite_model: str = os.environ.get("GROQ_REWRITE_MODEL", "openai/gpt-oss-120b")
    huggingface_rewrite_model: str = os.environ.get(
        "HUGGINGFACE_REWRITE_MODEL", "openai/gpt-oss-120b:nscale"
    )
    vercel_rewrite_model: str = os.environ.get("VERCEL_REWRITE_MODEL", "openai/gpt-oss-20b")

    # Embeddings (fastembed-snowflake service; SSH-tunnel to VPS port 8001)
    embedding_provider: str = os.environ.get("EMBEDDING_PROVIDER", "fastembed")
    embedding_endpoint_url: str = os.environ.get(
        "EMBEDDING_ENDPOINT_URL",
        "http://127.0.0.1:8001",
    )
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "snowflake/snowflake-arctic-embed-s")
    embedding_dim: int = _env_int("EMBEDDING_DIM", 384)
    embedding_timeout_seconds: float = _env_float("EMBEDDING_TIMEOUT_SECONDS", 30.0)
    embedding_max_retries: int = _env_int("EMBEDDING_MAX_RETRIES", 1)
    embedding_retry_delay_seconds: float = _env_float("EMBEDDING_RETRY_DELAY_SECONDS", 1.0)
    # Reranking (Voyage cross-encoder; RankLLM optional via RANKLLM_ENABLED)

    rerank_bi_encoder_timeout_seconds: float = _env_float("RERANK_BI_ENCODER_TIMEOUT_SECONDS", 15.0)
    rerank_bi_encoder_text_max_chars: int = _env_int("RERANK_BI_ENCODER_TEXT_MAX_CHARS", 384)
    rerank_bi_encoder_batch_size: int = _env_int("RERANK_BI_ENCODER_BATCH_SIZE", 64)
    rerank_bi_encoder_max_concurrent_batches: int = _env_int(
        "RERANK_BI_ENCODER_MAX_CONCURRENT_BATCHES", 3
    )
    voyage_api_key: str = os.environ.get("VOYAGE_API_KEY", "")
    voyage_rerank_model: str = os.environ.get("VOYAGE_RERANK_MODEL", "rerank-2.5")
    voyage_rerank_fallback_model: str = os.environ.get(
        "VOYAGE_RERANK_FALLBACK_MODEL", "rerank-2.5-lite"
    )
    voyage_rerank_timeout: float = _env_float("VOYAGE_RERANK_TIMEOUT", 30.0)

    mmr_lambda_param: float = _env_float("MMR_LAMBDA", 0.7)
    diversity_max_per_host: int = _env_int("DIVERSITY_MAX_PER_HOST", 2)

    # RankLLM Settings
    rankllm_openrouter_model: str = os.environ.get(
        "RANKLLM_OPENROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"
    )
    rankllm_gemini_model: str = os.environ.get("RANKLLM_GEMINI_MODEL", "gemini-3.5-flash-lite")
    rankllm_timeout_seconds: float = _env_float("RANKLLM_TIMEOUT_SECONDS", 20.0)
    rankllm_max_passage_words: int = _env_int("RANKLLM_MAX_PASSAGE_WORDS", 300)
    rankllm_temperature: float = _env_float("RANKLLM_TEMPERATURE", 0.0)
    rankllm_window_size: int = _env_int("RANKLLM_WINDOW_SIZE", 20)
    rankllm_stride: int = _env_int("RANKLLM_STRIDE", 10)
    rankllm_num_passes: int = _env_int("RANKLLM_NUM_PASSES", 3)
    rankllm_enabled: bool = os.environ.get("RANKLLM_ENABLED", "false").lower() == "true"

    rerank_recency_weight: float = _env_float("RERANK_RECENCY_WEIGHT", 0.15)
    rerank_recency_half_life_days: int = _env_int("RERANK_RECENCY_HALF_LIFE_DAYS", 90)
    # Optional content extraction through the hosted GLiNER2 gateway.
    # Query understanding has its separate INTENT_CLASSIFIER_ENABLED gate.
    entity_extraction_enabled: bool = (
        os.environ.get("ENTITY_EXTRACTION_ENABLED", "false").lower() == "true"
    )
    gliner_model: str = os.environ.get("GLINER_MODEL", "fastino/gliner2.5-multi-v1")
    gliner_threshold: float = _env_float("GLINER_THRESHOLD", 0.5)

    analytics_enabled: bool = os.environ.get("ANALYTICS_ENABLED", "true").lower() == "true"
    analytics_shutdown_drain_timeout_seconds: float = _env_float(
        "ANALYTICS_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 5.0
    )
    analytics_duckdb_path: str = os.environ.get(
        "ANALYTICS_DUCKDB_PATH",
        DEFAULT_ANALYTICS_DB,
    )
    vss_enabled: bool = os.environ.get("VSS_ENABLED", "true").lower() == "true"
    flockmtl_enabled: bool = os.environ.get("FLOCKMTL_ENABLED", "true").lower() == "true"

    # Judge inference chain (2026-08-22): HF router retired. Stage 1 =
    # Gemini API hosting Gemma via the native google-genai SDK (Gemma has
    # no reliable OpenAI-compat access and no responseSchema support, so
    # stage 1 sends plain text and relies on the prompt footer + tolerant
    # parser). Stage 2 = NanoGPT (OpenAI-compatible) serving DeepSeek V4
    # Flash thinking WITH strict json_schema. NanoGPT docs spell the env
    # var NANOGPT_API_KEY; underscored legacy spelling kept as fallback.
    judge_gemini_model: str = os.environ.get("JUDGE_GEMINI_MODEL", "gemma-4-26b-a4b-it")
    nano_gpt_api_key: str = os.environ.get("NANOGPT_API_KEY") or os.environ.get(
        "NANO_GPT_API_KEY", ""
    )
    judge_nanogpt_model: str = os.environ.get(
        "JUDGE_NANOGPT_MODEL",
        "deepseek/deepseek-v4-flash-0731:thinking",
    )
    judge_nanogpt_base_url: str = os.environ.get(
        "JUDGE_NANOGPT_BASE_URL",
        "https://nano-gpt.com/api/subscription/v1",
    )
    # Per-stage retry policy: attempts = 1 + max_retries, exponential
    # backoff initial*2**attempt capped at the ceiling.
    judge_stage_max_retries: int = _env_int("JUDGE_STAGE_MAX_RETRIES", 2)
    judge_retry_initial_backoff_seconds: float = _env_float(
        "JUDGE_RETRY_INITIAL_BACKOFF_SECONDS", 1.0
    )
    judge_retry_max_backoff_seconds: float = _env_float("JUDGE_RETRY_MAX_BACKOFF_SECONDS", 8.0)

    # Process logs DuckDB — centralized, 48h TTL, FTS enabled
    process_logs_enabled: bool = os.environ.get("PROCESS_LOGS_ENABLED", "true").lower() == "true"
    process_logs_sqlite_path: str = os.environ.get(
        "PROCESS_LOGS_SQLITE_PATH",
        os.environ.get("PROCESS_LOGS_DUCKDB_PATH", DEFAULT_PROCESS_LOGS_DB),
    )
    process_logs_ttl_hours: int = _env_int("PROCESS_LOGS_TTL_HOURS", 48)

    # Page cache (separate SQLite WAL file, NOT shared with analytics DB)
    page_cache_sqlite_path: str = os.environ.get(
        "PAGE_CACHE_SQLITE_PATH",
        DEFAULT_PAGE_CACHE_DB,
    )

    # Transcript cache (separate SQLite WAL file for YouTube transcript caching)
    transcript_cache_sqlite_path: str = os.environ.get(
        "TRANSCRIPT_CACHE_SQLITE_PATH",
        DEFAULT_TRANSCRIPT_CACHE_DB,
    )

    # Blocklist store (uBlacklist-style globs filtered out of every web search)
    blocklist_sqlite_path: str = os.environ.get(
        "BLOCKLIST_SQLITE_PATH",
        DEFAULT_BLOCKLIST_DB,
    )

    # Code-search cache tiers. Search results are short-lived; immutable
    # GitHub blob content can safely live much longer.
    code_search_cache_ttl_seconds: int = _env_int("CODE_SEARCH_CACHE_TTL_SECONDS", 1800)
    code_search_cache_max_entries: int = _env_int("CODE_SEARCH_CACHE_MAX_ENTRIES", 256)
    code_search_hydration_cache_ttl_seconds: int = _env_int(
        "CODE_SEARCH_HYDRATION_CACHE_TTL_SECONDS", 2592000
    )
    code_fetch_snapshot_ttl_seconds: int = _env_int("CODE_FETCH_SNAPSHOT_TTL_SECONDS", 300)
    code_fetch_snapshot_sqlite_path: str = os.environ.get(
        "CODE_FETCH_SNAPSHOT_SQLITE_PATH",
        DEFAULT_CODE_FETCH_SNAPSHOT_DB,
    )

    # Telegram search provider (Telethon MTProto)
    telegram_api_id: str = os.environ.get("TELEGRAM_API_ID", "")
    telegram_api_hash: str = os.environ.get("TELEGRAM_API_HASH", "")
    telegram_session_string: str = os.environ.get("TELEGRAM_SESSION_STRING", "")
    telegram_public_search_daily_budget: int = _env_int("TELEGRAM_PUBLIC_SEARCH_DAILY_BUDGET", 8)
    telegram_flood_sleep_threshold: int = _env_int("TELEGRAM_FLOOD_SLEEP_THRESHOLD", 60)
    telegram_registry_duckdb_path: str = os.environ.get(
        "TELEGRAM_REGISTRY_DUCKDB_PATH",
        str(TELEGRAM_DIR / "registry.duckdb"),
    )

    # OpenRouter API (shared by all OpenRouter integrations)
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    openrouter_chat_base_url: str = os.environ.get(
        "OPENROUTER_CHAT_BASE_URL", "https://openrouter.ai/api/v1"
    )

    # Grok native search through the direct xAI Responses API
    grok_backend: str = os.environ.get("GROK_BACKEND", "xai").strip().lower()
    grok_xai_api_key: str = os.environ.get("XAI_API_KEY", "")
    grok_xai_base_url: str = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
    grok_model: str = os.environ.get("GROK_MODEL", "grok-4.5")
    grok_timeout_seconds: float = _env_float("GROK_TIMEOUT_SECONDS", 60.0)
    grok_max_turns: int = _env_int("GROK_MAX_TURNS", 3)
    grok_store: bool = os.environ.get("GROK_STORE", "false").strip().lower() == "true"

    # Vertex can serve Grok text Responses, but not the native search tools used
    # here. These fields document the separate Vertex configuration boundary.
    grok_vertex_project_id: str = os.environ.get("GROK_VERTEX_PROJECT_ID", "")
    grok_vertex_location: str = os.environ.get("GROK_VERTEX_LOCATION", "global")
    grok_vertex_model: str = os.environ.get("GROK_VERTEX_MODEL", "grok-4.20-reasoning")
    grok_vertex_base_url: str = os.environ.get("GROK_VERTEX_BASE_URL", "")
    # Gemini Grounding (for gemini_search MCP tool)
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_second_api_key: str = os.environ.get("GEMINI_SECOND_API_KEY", "")
    # Model selection handled via hardcoded fallback tier in gemini_search_tool.py
    # Antigravity managed-agent backend for gemini_search (Interactions API; uses GEMINI_API_KEY)
    gemini_search_backend: str = (
        os.environ.get("GEMINI_SEARCH_BACKEND", "grounding").strip().lower()
    )
    antigravity_model: str = os.environ.get("ANTIGRAVITY_MODEL", "gemini-3.7-flash")
    antigravity_max_total_tokens: int = _env_int("ANTIGRAVITY_MAX_TOTAL_TOKENS", 60000)
    antigravity_timeout_seconds: float = _env_float("ANTIGRAVITY_TIMEOUT_SECONDS", 240.0)
    antigravity_poll_interval_seconds: float = _env_float("ANTIGRAVITY_POLL_INTERVAL_SECONDS", 5.0)

    # Unified fetch defaults (dsh-webfetch-compatible; intentionally not public tool knobs)
    web_fetch_workers: int = _env_int("KINDLY_WEB_FETCH_WORKERS", 4)
    web_fetch_wave_size: int = _env_int("KINDLY_WEB_FETCH_WAVE_SIZE", 10)
    web_fetch_timeout_seconds: float = _env_float("KINDLY_WEB_FETCH_TIMEOUT_SECONDS", 20.0)
    web_fetch_max_body_bytes: int = _env_int("KINDLY_WEB_FETCH_MAX_BODY_BYTES", 5 * 1024 * 1024)

    # YouTube Transcript
    youtube_transcript_proxy_url: str = os.environ.get("YOUTUBE_TRANSCRIPT_PROXY_URL", "")
    youtube_transcript_max_chars: int = _env_int("YOUTUBE_TRANSCRIPT_MAX_CHARS", 50000)
    youtube_transcript_timeout_seconds: float = _env_float(
        "YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS", 30.0
    )

    # YouTube Transcript Backend (auto|ytdlp|cf_whisper|whisper|api)
    youtube_transcript_backend: str = os.environ.get("YOUTUBE_TRANSCRIPT_BACKEND", "auto")

    # Whisper ASR (HF Space) for videos without captions
    whisper_space_url: str = os.environ.get("WHISPER_SPACE_URL", "")
    whisper_space_id: str = os.environ.get("WHISPER_SPACE_ID", "")
    whisper_space_timeout_seconds: float = _env_float("WHISPER_SPACE_TIMEOUT_SECONDS", 300.0)
    # Self-hosted cobalt audio fetcher (audio bytes for ASR; bypasses the
    # local 1 MiB per-stream CDN cap). Empty -> tier skipped.
    cobalt_base_url: str = os.environ.get("COBALT_BASE_URL", "")
    cobalt_timeout_seconds: float = _env_float("COBALT_TIMEOUT_SECONDS", 120.0)

    # YouTube Search (uses SearXNG with youtube engine)
    youtube_search_engine: str = os.environ.get("YOUTUBE_SEARCH_ENGINE", "youtube")

    # YouTube Data API v3 (optional, enables enriched search)
    youtube_api_key: str = os.environ.get("GOOGLE_API_KEY", "")
    youtube_api_timeout_seconds: float = _env_float("YOUTUBE_API_TIMEOUT_SECONDS", 15.0)
    youtube_api_daily_quota: int = _env_int("YOUTUBE_API_DAILY_QUOTA", 10000)
    youtube_api_language: str = os.environ.get("YOUTUBE_API_LANGUAGE", "")
    youtube_api_region: str = os.environ.get("YOUTUBE_API_REGION", "")

    # Cloudflare Workers AI Whisper (ASR for captionless videos, replaces HF Space)
    cf_whisper_account_id: str = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    cf_whisper_api_token: str = os.environ.get("CLOUDFLARE_API_TOKEN", "") or os.environ.get(
        "CLOUDFLARE_API_KEY", ""
    )
    cf_whisper_max_audio_seconds: int = _env_int("CF_WHISPER_MAX_AUDIO_SECONDS", 600)
    cf_whisper_api_base_url: str = os.environ.get(
        "CF_WHISPER_API_BASE_URL", "https://api.cloudflare.com/client/v4"
    )

    # Academic Search Providers
    # Semantic Scholar (optional, 100 RPS with key vs 1 RPS shared)
    s2_api_key: str = os.environ.get("S2_API_KEY", "")
    s2_timeout: int = _env_int("S2_TIMEOUT", 30)
    s2_max_retries: int = _env_int("S2_MAX_RETRIES", 0)  # 0 = fail fast

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
    academic_max_results: int = _env_int("ACADEMIC_MAX_RESULTS", 10)

    search_router_api_key: str = os.environ.get("SEARCH_ROUTER_API_KEY", "")
    tavily_api_key: str = os.environ.get("TAVILY_API_KEY", "")
    exa_api_key: str = os.environ.get("EXA_API_KEY", "")
    brave_api_key: str = os.environ.get("BRAVE_API_KEY", "")
    brave_suggest_api_key: str = os.environ.get("BRAVE_SUGGEST_API_KEY", "")
    brave_goggles_by_intent: dict[str, list[str]] = field(
        default_factory=lambda: _parse_json_dict_env(
            os.environ.get("BRAVE_GOGGLES_BY_INTENT", "{}"), {}
        )
    )
    jina_api_key: str = os.environ.get("JINA_API_KEY", "")
    google_cse_api_key: str = os.environ.get("GOOGLE_API_KEY", "")
    google_cse_engine_id: str = "771d303cf528e4b7c"
    # Google Discovery Engine / Agent Search (OAuth bearer, never an API key).
    discovery_engine_quota_project: str = os.environ.get(
        "DISCOVERY_ENGINE_QUOTA_PROJECT",
        os.environ.get("GOOGLE_CLOUD_PROJECT", "magdalenka-ecosystem"),
    )
    discovery_engine_gcloud_bin: str = os.environ.get(
        "DISCOVERY_ENGINE_GCLOUD_BIN",
        os.environ.get("AGENT_SEARCH_GCLOUD_BIN", ""),
    )

    # Provider master switch. Keep enabled by default; use DISABLED_PROVIDERS
    # to turn off noisy providers like reddit without changing code.
    providers_enabled: bool = os.environ.get("PROVIDERS_ENABLED", "true").lower() == "true"
    disabled_providers: tuple[str, ...] = _parse_csv_env(
        os.environ.get("DISABLED_PROVIDERS", "serpapi,degoog")
    )

    # New SERP providers (Serper, SerpApi, BrightData)
    serper_api_key: str = os.environ.get("SERPER_API_KEY", "")
    serpapi_enabled: bool = os.environ.get("SERPAPI_ENABLED", "false").lower() == "true"
    serpapi_api_key: str = os.environ.get("SERPAPI_API_KEY", "")
    serpapi_default_engine: str = os.environ.get("SERPAPI_DEFAULT_ENGINE", "yahoo")
    serpapi_engines: str = os.environ.get(
        "SERPAPI_ENGINES", ""
    )  # comma-separated, e.g. "yahoo,naver"
    serpapi_disabled_engines: tuple[str, ...] = _parse_csv_env(
        os.environ.get("SERPAPI_DISABLED_ENGINES", "*")
    )
    brightdata_api_key: str = os.environ.get("BRIGHTDATA_API_KEY", "")
    brightdata_zone: str = os.environ.get("BRIGHTDATA_ZONE", "sdk_serp")
    brightdata_payload_extra: str = os.environ.get("BRIGHTDATA_PAYLOAD_EXTRA", "")
    # Web Unlocker zone is a different product from SERP. Do not reuse
    # BRIGHTDATA_SERP_ZONE / sdk_serp here — that zone cannot unlock pages.
    brightdata_unlocker_zone: str = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "")
    brightdata_unlocker_timeout_seconds: float = _env_float(
        "BRIGHTDATA_UNLOCKER_TIMEOUT_SECONDS", 90.0
    )
    langsearch_api_key: str = os.environ.get("LANGSEARCH_API_KEY", "")
    langsearch_base_url: str = os.environ.get("LANGSEARCH_BASE_URL", "https://api.langsearch.com")

    # SERP semaphore limit (controls concurrency for paid_serp providers)
    serp_semaphore_limit: int = _env_int("SERP_SEMAPHORE_LIMIT", 2)

    # SearXNG config (consolidated from raw os.environ reads in searxng.py)
    searxng_base_url: str = os.environ.get("SEARXNG_BASE_URL", "")
    searxng_headers_json: str = os.environ.get("SEARXNG_HEADERS_JSON", "")
    searxng_user_agent: str = os.environ.get("SEARXNG_USER_AGENT", "")
    searxng_language: str = os.environ.get("SEARXNG_LANGUAGE", "")
    searxng_safesearch: str = os.environ.get("SEARXNG_SAFESEARCH", "")
    searxng_timeout_seconds: str = os.environ.get("SEARXNG_TIMEOUT_SECONDS", "")

    # DeGoog search aggregator (self-hosted)
    degoog_base_url: str = os.environ.get("DEGOOG_BASE_URL", "")

    # Reddit config (consolidated from raw os.environ read in reddit.py)
    reddit_delay_seconds: float = _env_float("REDDIT_DELAY_SECONDS", 2.0)
    reddit_client_id: str = os.environ.get("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.environ.get("REDDIT_CLIENT_SECRET", "")

    # StackExchange config (consolidated from raw os.environ reads in stackexchange.py)
    stackexchange_sites: str = os.environ.get("STACKEXCHANGE_SITES", "stackoverflow")
    stackexchange_app_key: str = os.environ.get("STACKEXCHANGE_APP_KEY", "")

    # Composio Search toolkit
    composio_api_key: str = os.environ.get("COMPOSIO_API_KEY", "")
    composio_user_id: str = os.environ.get("COMPOSIO_USER_ID", "")
    composio_search_toolkit_version: str = os.environ.get(
        "COMPOSIO_SEARCH_TOOLKIT_VERSION", "20260618_00"
    )
    composio_timeout_seconds: float = _env_float("COMPOSIO_TIMEOUT_SECONDS", 25.0)
    composio_max_retries: int = _env_int("COMPOSIO_MAX_RETRIES", 2)

    # Parallel AI Search API
    parallel_api_key: str = os.environ.get("PARALLEL_API_KEY", "")

    # Context7 documentation API (optional bearer; anonymous works with limits)
    context7_api_key: str = os.environ.get("CONTEXT7_API_KEY", "")

    # RRF tuning
    rrf_k: int = _env_int("RRF_K", 60)
    rrf_provider_weights: dict[str, float] = field(
        default_factory=lambda: _parse_float_dict_env(
            os.environ.get("RRF_PROVIDER_WEIGHTS_JSON", ""),
            default={
                "exa": 2.0,
                "tavily": 1.5,
                "google_discovery_engine": 1.3,
                "ddg": 0.8,
                "qdrant": 0.8,
                "searxng": 0.8,
                "degoog": 0.8,
            },
            name="RRF_PROVIDER_WEIGHTS_JSON",
        )
    )
    rrf_bm25_weight: float = _env_float("RRF_BM25_WEIGHT", 1.0)

    # Remote web results index (Qdrant on HF Space)
    # Indexes final search results (dense + BM25 sparse vectors) for future discovery.
    # Master flag; empty URL silently disables indexing.
    web_results_index_enabled: bool = (
        os.environ.get("WEB_RESULTS_INDEX_ENABLED", "true").lower() == "true"
    )
    qdrant_space_url: str = os.environ.get(
        "QDRANT_SPACE_URL", "https://chmielvu-web-index.hf.space"
    )

    # FastMCP tool visibility profile
    tool_profile: str = os.environ.get("TOOL_PROFILE", "regular")

    # FastMCP tool search (opt-in; wires RegexSearchTransform after profile selection)
    # No legacy aliases (per joint plan: no backward compat).
    tool_search_enabled: bool = os.environ.get("TOOL_SEARCH_ENABLED", "false").lower() == "true"

    # Deep research (self-hosted node-DeepResearch engine; SEP-1686 background-capable tool)
    deep_research_url: str = os.environ.get("DEEP_RESEARCH_URL", "http://13.140.176.104:3001")
    deep_research_secret: str = os.environ.get("DEEP_RESEARCH_SECRET", "")
    deep_research_timeout_seconds: float = _env_float("DEEP_RESEARCH_TIMEOUT_SECONDS", 600.0)

    # Per-tool rate limiting
    # Internal field names use "cheap" to reflect multi-tool scope
    # Rate-limit and concurrency settings for web search (prefixed with ).
    rate_limit_cheap_rps: float = _env_float("RATE_LIMIT_WEB_SEARCH_RPS", 4.0)
    rate_limit_cheap_burst: int = _env_int("RATE_LIMIT_WEB_SEARCH_BURST", 12)
    rate_limit_expensive_rps: float = _env_float("RATE_LIMIT_EXPENSIVE_RPS", 0.5)
    rate_limit_expensive_burst: int = _env_int("RATE_LIMIT_EXPENSIVE_BURST", 1)

    # =====================================================================
    # OpenTelemetry / Grafana Observability (Phase 1 of observability work)
    # =====================================================================
    # These enable first-class traces + metrics export to Grafana Cloud
    # (or local collector / Alloy). We prefer standard OTEL_* env vars
    # for compatibility with the broader ecosystem, but provide
    #  + GRAFANA_CLOUD_* convenience vars for Windows/pwsh ergonomics.

    otel_enabled: bool = os.environ.get("OTEL_ENABLED", "true").lower() == "true"

    # Sampling (head-based). 1.0 = all traces (expensive). 0.1 = 10% typical for dev/prod.
    otel_sampling_ratio: float = _env_float("OTEL_SAMPLING_RATIO", 0.15)

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
    prometheus_port: int = _env_int("PROMETHEUS_PORT", 0)  # 0 = disabled / dynamic

    # Attribute safety (used by utils/observability.py and telemetry)
    observability_max_text_chars: int = _env_int("OBSERVABILITY_MAX_TEXT_CHARS", 20000)
    observability_max_items: int = _env_int("OBSERVABILITY_MAX_ITEMS", 10)

    # =====================================================================
    # LLM Judge Evaluation (opt-in, for automatic quality assessment of search runs)
    # =====================================================================
    judge_evaluation_enabled: bool = (
        os.environ.get("JUDGE_EVALUATION_ENABLED", "false").lower() == "true"
    )
    judge_model: str = os.environ.get("JUDGE_MODEL", "openai/gpt-oss-120b")
    judge_timeout_seconds: float = _env_float("JUDGE_TIMEOUT_SECONDS", 10.0)

    # =====================================================================
    # Crawl4AI remote server (Docker on VPS)
    # =====================================================================
    crawl4ai_base_url: str = os.environ.get("CRAWL4AI_BASE_URL", "")
    # Bearer token for the Crawl4AI server (server-side CRAWL4AI_API_TOKEN);
    # sent as `Authorization: Bearer <token>` on every request when set.
    crawl4ai_token: str = os.environ.get("CRAWL4AI_TOKEN", "")
    # When set (e.g. http://vps-ip:11235), all Crawl4AI calls go remote.
    # When empty, Crawl4AI is skipped; fallback to Jina Reader.

    crawl4ai_timeout_seconds: float = _env_float("CRAWL4AI_TIMEOUT_SECONDS", 120.0)
    crawl4ai_max_pages_sitemap: int = _env_int("CRAWL4AI_MAX_PAGES_SITEMAP", 100)
    crawl4ai_health_cache_seconds: float = _env_float("CRAWL4AI_HEALTH_CACHE_SECONDS", 30.0)

    # =====================================================================
    # Firecrawl Cloud (optional provider configuration retained for future fetch routing)
    # The unified fetch tool currently uses the local pipeline and configured sidecars.
    firecrawl_api_key: str = os.environ.get("FIRECRAWL_API_KEY", "")

    firecrawl_api_url: str = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
    firecrawl_timeout_seconds: float = _env_float("FIRECRAWL_TIMEOUT_SECONDS", 60.0)
    firecrawl_poll_interval_seconds: float = _env_float("FIRECRAWL_POLL_INTERVAL_SECONDS", 2.0)
    firecrawl_max_poll_seconds: float = _env_float("FIRECRAWL_MAX_POLL_SECONDS", 120.0)

    # =====================================================================
    # Camoufox sidecar (stealth-Firefox on VPS)
    # =====================================================================
    camoufox_base_url: str = os.environ.get("CAMOUFOX_BASE_URL", "")
    # When set (e.g. http://127.0.0.1:3000 via SSH tunnel), Camoufox is the last-resort browser.
    # When empty, Camoufox stage is skipped.
    camoufox_timeout_seconds: float = _env_float("CAMOUFOX_TIMEOUT_SECONDS", 30.0)
    camoufox_health_cache_seconds: float = _env_float("CAMOUFOX_HEALTH_CACHE_SECONDS", 30.0)

    # =====================================================================
    # Apify hard-platform scrapers (X/Twitter resolver; Reddit last-resort)
    # =====================================================================
    # When APIFY_API_TOKEN is unset, every Apify-backed layer is inert and
    # the existing free cascades behave exactly as before.
    apify_api_token: str = os.environ.get("APIFY_API_TOKEN", "")
    apify_twitter_actor: str = os.environ.get("APIFY_TWITTER_ACTOR", "fastdata~twitter-scraper")
    apify_reddit_actor: str = os.environ.get("APIFY_REDDIT_ACTOR", "openclawai~reddit-scraper")
    # Try the paid Apify layer BEFORE the free Reddit cascade instead of after it.
    apify_reddit_first: bool = os.environ.get("APIFY_REDDIT_FIRST", "false").lower() == "true"
    apify_timeout_seconds: float = _env_float("APIFY_TIMEOUT_SECONDS", 90.0)
    # Escape hatch merged last into every Actor run input (JSON object string),
    # e.g. APIFY_EXTRA_INPUT_JSON='{"maxItems":5}' for actor schema quirks.
    apify_extra_input_json: dict = field(
        default_factory=lambda: _parse_json_dict_env(
            os.environ.get("APIFY_EXTRA_INPUT_JSON", "{}"), {}
        )
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
        if self.rankllm_window_size <= 0:
            raise ValueError(f"rankllm_window_size must be > 0, got {self.rankllm_window_size!r}.")
        if self.rankllm_stride <= 0:
            raise ValueError(f"rankllm_stride must be > 0, got {self.rankllm_stride!r}.")
        if self.rankllm_num_passes <= 0:
            raise ValueError(f"rankllm_num_passes must be > 0, got {self.rankllm_num_passes!r}.")

        if self.diversity_max_per_host <= 0:
            raise ValueError(
                f"diversity_max_per_host must be >= 1, got {self.diversity_max_per_host}"
            )
        if self.rrf_bm25_weight < 0.0:
            raise ValueError(f"rrf_bm25_weight must be >= 0, got {self.rrf_bm25_weight!r}.")
        for provider_name, weight in self.rrf_provider_weights.items():
            if weight < 0.0:
                raise ValueError(
                    f"rrf_provider_weights[{provider_name!r}] must be >= 0, got {weight!r}."
                )

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
        _ALLOWED_TOOL_PROFILES = frozenset({"regular", "full"})
        normalized_profile = self.tool_profile.strip().lower()
        if normalized_profile not in _ALLOWED_TOOL_PROFILES:
            allowed = ", ".join(sorted(_ALLOWED_TOOL_PROFILES))
            raise ValueError(f"tool_profile must be one of: {allowed}. Got {self.tool_profile!r}.")
        self.tool_profile = normalized_profile

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
        for key in self.brave_goggles_by_intent:
            if key not in _CANONICAL_SEARCH_INTENTS:
                raise ValueError(
                    f"BRAVE_GOGGLES_BY_INTENT key {key!r} must be a canonical intent name."
                )


settings = Settings()


def get_env_value(name: str, fallback: str = "") -> str:
    """Read a current environment value with an optional fallback."""
    return os.environ.get(name, fallback)
