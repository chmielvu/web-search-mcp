"""Telemetry semantic convention constants."""

from __future__ import annotations

from ..llm.phoenix_tracing import (
    INPUT_MIME_TYPE,
    INPUT_VALUE,
    LLM_INVOCATION_PARAMETERS,
    LLM_MODEL_NAME,
    LLM_SYSTEM,
    OPENINFERENCE_SPAN_KIND,
    OPENINFERENCE_SPAN_KIND_CHAIN,
    OPENINFERENCE_SPAN_KIND_LLM,
)

# ============================================================================
# SEMANTIC CONVENTION CONSTANTS
# ============================================================================

# --- HTTP Semantic Conventions (OTEL standard) ---
HTTP_REQUEST_METHOD = "http.request.method"
URL_FULL = "url.full"
SERVER_ADDRESS = "server.address"
SERVER_PORT = "server.port"
HTTP_RESPONSE_STATUS_CODE = "http.response.status_code"
HTTP_RESPONSE_BODY_SIZE = "http.response.body.size"
ERROR_TYPE = "error.type"
NETWORK_PROTOCOL_VERSION = "network.protocol.version"

# --- MCP Semantic Conventions (emerging OTEL standard) ---
MCP_METHOD_NAME = "mcp.method.name"
MCP_SERVER_NAME = "mcp.server.name"
MCP_SESSION_ID = "mcp.session.id"
MCP_RESOURCE_URI = "mcp.resource.uri"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"
RPC_SYSTEM = "rpc.system"
RPC_JSONRPC_VERSION = "rpc.jsonrpc.version"

# --- Provider Attributes (custom, domain-specific) ---
PROVIDER_NAME = "provider.name"
PROVIDER_STATUS = "provider.status"
PROVIDER_RESULT_COUNT = "provider.result_count"
PROVIDER_DURATION_MS = "provider.duration_ms"
PROVIDER_ERROR_TYPE = "provider.error_type"

# --- Search Attributes (custom) ---
SEARCH_QUERY = "search.query"
SEARCH_NUM_RESULTS_REQUESTED = "search.num_results_requested"
SEARCH_NUM_RESULTS_RETURNED = "search.num_results_returned"
SEARCH_PROVIDERS_REQUESTED = "search.providers_requested"
SEARCH_PROVIDERS_USED = "search.providers_used"
SEARCH_MERGE_ALGORITHM = "search.merge_algorithm"

# --- Cache Attributes ---
CACHE_TYPE = "cache.type"
CACHE_HIT = "cache.hit"
CACHE_LOOKUP_DURATION_MS = "cache.lookup_duration_ms"

# --- Content Resolution Attributes ---
CONTENT_STAGE = "content.stage"
CONTENT_STATUS = "content.status"
CONTENT_SIZE_BYTES = "content.size_bytes"
CONTENT_URL = "content.url"
CONTENT_WORD_COUNT = "content.word_count"
CONTENT_EXTRACTION_METHOD = "content.extraction_method"
CONTENT_FINAL_STAGE = "content.final_stage"
CONTENT_FALLBACK_COUNT = "content.fallback_count"

# --- RRF Merge Attributes ---
RRF_INPUT_LISTS = "rrf.input_lists"
RRF_INPUT_TOTAL = "rrf.input_total"
RRF_OUTPUT_TOTAL = "rrf.output_total"
RRF_DISCARDED_COUNT = "rrf.discarded_count"
RRF_OVERLAP_RATE = "rrf.overlap_rate"
RRF_PROVIDER_CONTRIBUTION = "rrf.provider_contribution"
RRF_PROVIDER_WEIGHT = "rrf.provider_weight"
RRF_SCORE = "rrf.score"
RRF_BEST_RANK = "rrf.best_rank"
RRF_PROVIDERS = "rrf.providers"

# --- Query Rewrite Attributes ---
REWRITE_POLICY = "rewrite.policy"
REWRITE_VARIANT_COUNT = "rewrite.variant_count"
REWRITE_HAS_PRECISION_SIGNALS = "rewrite.has_precision_signals"
REWRITE_MODEL = "rewrite.model"
REWRITE_VARIANT_TYPE = "rewrite.variant_type"
REWRITE_VARIANT_TEXT = "rewrite.variant_text"

# --- Reranking Attributes ---
RERANK_STAGE = "rerank.stage"
RERANK_INPUT_COUNT = "rerank.input_count"
RERANK_OUTPUT_COUNT = "rerank.output_count"
RERANK_REMOVED_COUNT = "rerank.removed_count"
RERANK_RELEVANCE_SCORE = "rerank.relevance_score"
RERANK_MODEL = "rerank.model"
RERANK_DIVERSITY_THRESHOLD = "rerank.diversity_threshold"
RERANK_SIMILARITY_SCORE = "rerank.similarity_score"
RERANK_BI_ENCODER_SCORE = "rerank.bi_encoder_score"

# --- Circuit Breaker Attributes ---
CIRCUIT_STATE = "circuit.state"
CIRCUIT_FAILURE_COUNT = "circuit.failure_count"
CIRCUIT_LAST_FAILURE_TIME = "circuit.last_failure_time"
CIRCUIT_EVENT = "circuit.event"
CIRCUIT_FAILURE_THRESHOLD = "circuit.failure_threshold"

# --- Result Attributes ---
RESULT_POSITION = "result.position"
RESULT_PROVIDER_COUNT = "result.provider_count"
RESULT_RRF_SCORE = "result.rrf_score"
RESULT_PROVIDER_SOURCES = "result.provider_sources"
RESULT_HAS_SNIPPET = "result.has_snippet"
RESULT_DOMAIN = "result.domain"
RESULT_TITLE = "result.title"
RESULT_URL = "result.url"

# --- Gemini Attributes ---
GEMINI_GROUNDING_QUERIES = "gemini.grounding_queries"
GEMINI_GROUNDING_CHUNKS = "gemini.grounding_chunks"
GEMINI_STRUCTURED_OUTPUT = "gemini.structured_output"

# --- YouTube Attributes ---
YOUTUBE_FORMAT = "youtube.format"
YOUTUBE_LANGUAGE = "youtube.language"
YOUTUBE_IS_TRANSLATED = "youtube.is_translated"
YOUTUBE_DURATION_SECONDS = "youtube.duration_seconds"
YOUTUBE_BACKEND_USED = "youtube.backend_used"
YOUTUBE_SEARCH_BACKEND = "youtube.search_backend"


# --- Span Status Values ---
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"

# --- Provider Names ---
PROVIDER_SEARXNG = "searxng"
PROVIDER_DDG = "ddg"
PROVIDER_GEMINI = "gemini"
PROVIDER_TAVILY = "tavily"
PROVIDER_BRAVE = "brave"
PROVIDER_JINA = "jina"

# --- Content Stages ---
CONTENT_STAGE_STACKEXCHANGE = "stackexchange"
CONTENT_STAGE_GITHUB = "github_issue"
CONTENT_STAGE_WIKIPEDIA = "wikipedia"
CONTENT_STAGE_ARXIV = "arxiv"
CONTENT_STAGE_HTTP_EXTRACT = "http_extract"
CONTENT_STAGE_CAMOUFOX = "camoufox"
CONTENT_STAGE_CRAWL4AI = "crawl4ai"

__all__ = [
    "CACHE_HIT",
    "CACHE_LOOKUP_DURATION_MS",
    "CACHE_TYPE",
    "CIRCUIT_EVENT",
    "CIRCUIT_FAILURE_COUNT",
    "CIRCUIT_FAILURE_THRESHOLD",
    "CIRCUIT_LAST_FAILURE_TIME",
    "CIRCUIT_STATE",
    "CONTENT_EXTRACTION_METHOD",
    "CONTENT_FALLBACK_COUNT",
    "CONTENT_FINAL_STAGE",
    "CONTENT_SIZE_BYTES",
    "CONTENT_STAGE",
    "CONTENT_STAGE_ARXIV",
    "CONTENT_STAGE_CAMOUFOX",
    "CONTENT_STAGE_CRAWL4AI",
    "CONTENT_STAGE_GITHUB",
    "CONTENT_STAGE_HTTP_EXTRACT",
    "CONTENT_STAGE_STACKEXCHANGE",
    "CONTENT_STAGE_WIKIPEDIA",
    "CONTENT_STATUS",
    "CONTENT_URL",
    "CONTENT_WORD_COUNT",
    "ERROR_TYPE",
    "GEMINI_GROUNDING_CHUNKS",
    "GEMINI_GROUNDING_QUERIES",
    "GEMINI_STRUCTURED_OUTPUT",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_SYSTEM",
    "GEN_AI_TOOL_NAME",
    "HTTP_REQUEST_METHOD",
    "HTTP_RESPONSE_BODY_SIZE",
    "HTTP_RESPONSE_STATUS_CODE",
    "INPUT_MIME_TYPE",
    "INPUT_VALUE",
    "LLM_INVOCATION_PARAMETERS",
    "LLM_MODEL_NAME",
    "LLM_SYSTEM",
    "MCP_METHOD_NAME",
    "MCP_RESOURCE_URI",
    "MCP_SERVER_NAME",
    "MCP_SESSION_ID",
    "NETWORK_PROTOCOL_VERSION",
    "OPENINFERENCE_SPAN_KIND",
    "OPENINFERENCE_SPAN_KIND_CHAIN",
    "OPENINFERENCE_SPAN_KIND_LLM",
    "PROVIDER_BRAVE",
    "PROVIDER_DDG",
    "PROVIDER_DURATION_MS",
    "PROVIDER_ERROR_TYPE",
    "PROVIDER_GEMINI",
    "PROVIDER_JINA",
    "PROVIDER_NAME",
    "PROVIDER_RESULT_COUNT",
    "PROVIDER_SEARXNG",
    "PROVIDER_STATUS",
    "PROVIDER_TAVILY",
    "RERANK_BI_ENCODER_SCORE",
    "RERANK_DIVERSITY_THRESHOLD",
    "RERANK_INPUT_COUNT",
    "RERANK_MODEL",
    "RERANK_OUTPUT_COUNT",
    "RERANK_RELEVANCE_SCORE",
    "RERANK_REMOVED_COUNT",
    "RERANK_SIMILARITY_SCORE",
    "RERANK_STAGE",
    "RESULT_DOMAIN",
    "RESULT_HAS_SNIPPET",
    "RESULT_POSITION",
    "RESULT_PROVIDER_COUNT",
    "RESULT_PROVIDER_SOURCES",
    "RESULT_RRF_SCORE",
    "RESULT_TITLE",
    "RESULT_URL",
    "REWRITE_HAS_PRECISION_SIGNALS",
    "REWRITE_MODEL",
    "REWRITE_POLICY",
    "REWRITE_VARIANT_COUNT",
    "REWRITE_VARIANT_TEXT",
    "REWRITE_VARIANT_TYPE",
    "RPC_JSONRPC_VERSION",
    "RPC_SYSTEM",
    "RRF_BEST_RANK",
    "RRF_DISCARDED_COUNT",
    "RRF_INPUT_LISTS",
    "RRF_INPUT_TOTAL",
    "RRF_OUTPUT_TOTAL",
    "RRF_OVERLAP_RATE",
    "RRF_PROVIDERS",
    "RRF_PROVIDER_CONTRIBUTION",
    "RRF_PROVIDER_WEIGHT",
    "RRF_SCORE",
    "SEARCH_MERGE_ALGORITHM",
    "SEARCH_NUM_RESULTS_REQUESTED",
    "SEARCH_NUM_RESULTS_RETURNED",
    "SEARCH_PROVIDERS_REQUESTED",
    "SEARCH_PROVIDERS_USED",
    "SEARCH_QUERY",
    "SERVER_ADDRESS",
    "SERVER_PORT",
    "STATUS_ERROR",
    "STATUS_SUCCESS",
    "STATUS_TIMEOUT",
    "URL_FULL",
    "YOUTUBE_BACKEND_USED",
    "YOUTUBE_DURATION_SECONDS",
    "YOUTUBE_FORMAT",
    "YOUTUBE_IS_TRANSLATED",
    "YOUTUBE_LANGUAGE",
    "YOUTUBE_SEARCH_BACKEND",
]
