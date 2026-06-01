"""OpenTelemetry instrumentation for web-search-mcp server.

Comprehensive telemetry following:
- OTEL HTTP Semantic Conventions
- OTEL MCP Semantic Conventions (emerging standard)
- Grafana Cloud Application Observability best practices
- Three-layer MCP observability model

Three-layer observability:
  Layer 1: Transport/Protocol - JSON-RPC health, MCP session, message latency
  Layer 2: Tool Execution - Provider calls, cache, RRF merge, content resolution
  Layer 3: Agentic Performance - Task success, self-correction, hallucination detection

USAGE:
    Set Grafana Cloud environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-eu-west-2.grafana.net/otlp
    OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<YOUR_TOKEN>

    Import and initialize at startup (BEFORE any HTTP operations):
    from telemetry import init_telemetry
    init_telemetry()  # Auto-instruments HTTPX, sets up metrics/traces/logs

SEMANTIC CONVENTIONS:
    HTTP: http.request.method, url.full, server.address, http.response.status_code
    MCP: mcp.method.name, mcp.server.name, gen_ai.tool.name, gen_ai.operation.name
    Search: search.query, search.num_results, search.providers_used
    Provider: provider.name, provider.status, provider.result_count
    Content: content.stage, content.status, content.size_bytes
"""

from __future__ import annotations

import os
import logging
import json
import platform
import socket
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace, metrics

# SDK imports are part of the default runtime dependencies.
try:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import (
        ParentBased,
        TraceIdRatioBased,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import (
        Resource,
        SERVICE_NAME,
        SERVICE_VERSION,
        SERVICE_NAMESPACE,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )

    _OTEL_SDK_AVAILABLE = True
except ImportError:
    _OTEL_SDK_AVAILABLE = False


# Prometheus exporter for Alloy scraping (optional). Keep this import lazy:
# importing prometheus_client at MCP startup can block in Windows WMI platform
# detection before the stdio handshake starts.
def _get_prometheus_metric_reader() -> Any | None:
    try:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
    except ImportError:
        return None
    return PrometheusMetricReader


def build_grafana_cloud_headers(
    instance_id: str = "", api_key: str = "", endpoint: str = ""
) -> dict[str, str]:
    """Build OTLP headers for Grafana Cloud from convenience variables.

    Windows/pwsh users often prefer setting three simple vars instead of
    constructing a Base64 Authorization header manually.

    Returns a dict suitable for OTLPSpanExporter / OTLPMetricExporter headers=.
    Falls back to empty dict (standard OTEL_* handling) if insufficient data.
    """
    if not instance_id or not api_key:
        return {}

    # Basic auth: username = instance ID (numeric), password = API key (glc_...)
    import base64

    token = base64.b64encode(f"{instance_id}:{api_key}".encode("utf-8")).decode("ascii")
    auth = f"Basic {token}"

    headers = {"Authorization": auth}

    # If a custom endpoint was provided via the convenience var, the caller
    # (init_telemetry) is responsible for using it instead of OTEL_EXPORTER_OTLP_ENDPOINT.
    # We only return the auth header here.
    return headers


# Logging bridge (experimental but useful)
try:
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.instrumentation.logging.handler import LoggingHandler

    LOGS_AVAILABLE = True
except ImportError:
    LOGS_AVAILABLE = False


_initialized = False
_otel_logging_handler: logging.Handler | None = None


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

# --- Semantic Cache Attributes ---
CACHE_SIMILARITY_SCORE = "cache.similarity_score"
CACHE_CONTENT_TYPE = "cache.content_type"
CACHE_TTL_SECONDS = "cache.ttl_seconds"
CACHE_SEARCH_TYPE = "cache.search_type"
CACHE_VECTOR_DISTANCE = "cache.vector_distance"

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

# --- Gemini/Perplexity Attributes ---
GEMINI_GROUNDING_QUERIES = "gemini.grounding_queries"
GEMINI_GROUNDING_CHUNKS = "gemini.grounding_chunks"
GEMINI_STRUCTURED_OUTPUT = "gemini.structured_output"
PERPLEXITY_DEPTH = "perplexity.depth"
PERPLEXITY_SOURCE_COUNT = "perplexity.source_count"
PERPLEXITY_MODEL = "perplexity.model"

# --- YouTube Attributes ---
YOUTUBE_FORMAT = "youtube.format"
YOUTUBE_LANGUAGE = "youtube.language"
YOUTUBE_IS_TRANSLATED = "youtube.is_translated"
YOUTUBE_DURATION_SECONDS = "youtube.duration_seconds"

# --- Agentic Performance Attributes ---
TASK_SUCCESS = "task.success"
TASK_TURNS_TO_COMPLETION = "task.turns_to_completion"
SELF_CORRECTION_ATTEMPTS = "self_correction.attempts"
TOOL_HALLUCINATION = "tool.hallucination_detected"

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
CONTENT_STAGE_NODRIVER = "nodriver"


# ============================================================================
# METRIC SINGLETONS
# ============================================================================

# Provider metrics
_provider_call_counter: metrics.Counter | None = None
_provider_duration_histogram: metrics.Histogram | None = None
_provider_results_counter: metrics.Counter | None = None

# Search metrics
_search_total_counter: metrics.Counter | None = None
_search_duration_histogram: metrics.Histogram | None = None
_search_merge_histogram: metrics.Histogram | None = None

# Cache metrics
_cache_request_counter: metrics.Counter | None = None
_cache_duration_histogram: metrics.Histogram | None = None

# MCP protocol metrics
_mcp_tool_counter: metrics.Counter | None = None
_mcp_error_counter: metrics.Counter | None = None

# Content resolution metrics
_content_resolution_counter: metrics.Counter | None = None
_content_duration_histogram: metrics.Histogram | None = None
_content_fallback_counter: metrics.Counter | None = None
_content_error_counter: metrics.Counter | None = None

# RRF merge metrics
_rrf_merge_counter: metrics.Counter | None = None
_rrf_provider_contribution_counter: metrics.Counter | None = None
_rrf_score_histogram: metrics.Histogram | None = None

# Query rewrite metrics
_rewrite_counter: metrics.Counter | None = None
_rewrite_duration_histogram: metrics.Histogram | None = None

# Reranking metrics
_rerank_counter: metrics.Counter | None = None
_rerank_duration_histogram: metrics.Histogram | None = None
_rerank_score_histogram: metrics.Histogram | None = None
_rerank_diversity_counter: metrics.Counter | None = None

# Semantic cache metrics
_cache_score_histogram: metrics.Histogram | None = None
_cache_ttl_counter: metrics.Counter | None = None
_cache_hybrid_counter: metrics.Counter | None = None

# Circuit breaker metrics
_circuit_state_gauge: metrics.UpDownCounter | None = None
_circuit_event_counter: metrics.Counter | None = None

# Gemini/Perplexity metrics
_gemini_counter: metrics.Counter | None = None
_perplexity_counter: metrics.Counter | None = None

# YouTube metrics
_youtube_transcript_counter: metrics.Counter | None = None
_youtube_search_counter: metrics.Counter | None = None

# Query quality metrics (Phase 2)
_query_length_histogram: metrics.Histogram | None = None
_domain_diversity_histogram: metrics.Histogram | None = None


# ============================================================================
# INITIALIZATION
# ============================================================================


def init_telemetry(
    service_name: str = "web-search-mcp",
    service_version: str = "1.0.8",
    prometheus_port: int | None = None,
) -> None:
    """Initialize OpenTelemetry SDK with Grafana Cloud export.

    This MUST be called at server startup before any HTTP operations.
    Auto-instruments httpx for automatic HTTP span creation.

    Preferred configuration (standard OTEL):
        OTEL_EXPORTER_OTLP_ENDPOINT + OTEL_EXPORTER_OTLP_HEADERS (Basic auth)

    Windows / convenience path (recommended in this repo):
        GRAFANA_CLOUD_INSTANCE_ID + GRAFANA_CLOUD_API_KEY + GRAFANA_CLOUD_OTLP_ENDPOINT
        (or the KINDLY_* equivalents). These are automatically turned into the
        correct Authorization header.

    Sampling is controlled via KINDLY_OTEL_SAMPLING_RATIO (default 0.15 in Settings).

    Optional:
        KINDLY_PROMETHEUS_PORT / KINDLY_PROMETHEUS_ENABLED
        OTEL_SERVICE_NAME / OTEL_SERVICE_NAMESPACE
        DEPLOYMENT_ENV
    """
    global _initialized
    if _initialized:
        logging.debug("Telemetry already initialized, skipping")
        return

    # Allow overrides from env
    service_name = os.environ.get("OTEL_SERVICE_NAME", service_name)
    service_version = os.environ.get("OTEL_SERVICE_VERSION", service_version)

    # ------------------------------------------------------------------
    # Endpoint + Header resolution (supports both standard OTEL_* and
    # the Grafana Cloud convenience variables exposed via Settings)
    # ------------------------------------------------------------------
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers: dict[str, str] = {}

    # 1. Try Grafana Cloud convenience path first (Windows-friendly)
    gcloud_instance = os.environ.get("GRAFANA_CLOUD_INSTANCE_ID", "")
    gcloud_key = os.environ.get("GRAFANA_CLOUD_API_KEY", "")
    gcloud_endpoint = os.environ.get("GRAFANA_CLOUD_OTLP_ENDPOINT", "")

    if gcloud_instance and gcloud_key:
        headers = build_grafana_cloud_headers(gcloud_instance, gcloud_key)
        if not endpoint:
            endpoint = (
                gcloud_endpoint
                or "https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
            )
        logging.info("Using Grafana Cloud convenience variables for OTLP auth")

    # 2. Fall back to classic OTEL_* raw header
    if not headers:
        headers_raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
        if headers_raw:
            for part in headers_raw.split(","):
                part = part.strip()
                if "=" in part:
                    key, val = part.split("=", 1)
                    headers[key.strip()] = val.strip().replace("%20", " ")
                elif ":" in part:
                    key, val = part.split(":", 1)
                    headers[key.strip()] = val.strip()

    if not endpoint:
        if not _OTEL_SDK_AVAILABLE or not LOGS_AVAILABLE:
            logging.info(
                "OpenTelemetry runtime packages are unavailable, but no OTLP endpoint is configured; "
                "telemetry remains disabled."
            )
        else:
            logging.info(
                "OTEL_EXPORTER_OTLP_ENDPOINT not set - telemetry disabled. "
                "To enable, set endpoint from Grafana Cloud → Connections → OpenTelemetry "
                "or use GRAFANA_CLOUD_* convenience variables."
            )
        return

    if not _OTEL_SDK_AVAILABLE:
        logging.warning(
            "OpenTelemetry SDK not installed; telemetry export disabled and MCP startup will continue."
        )
        return

    # Allow port override from env
    if prometheus_port is None:
        port_env = os.environ.get("KINDLY_PROMETHEUS_PORT", "0")
        prometheus_port = int(port_env) if port_env else None

    # === RESOURCE (Grafana Cloud Application Observability) ===
    hostname = socket.gethostname()
    pid = os.getpid()

    resource_attrs = {
        # Service identity (required for Grafana Cloud)
        SERVICE_NAME: service_name,
        SERVICE_NAMESPACE: os.environ.get("OTEL_SERVICE_NAMESPACE", "kindly-mcp"),
        SERVICE_VERSION: service_version,
        "service.instance.id": f"{hostname}-{pid}",
        # Deployment context (respect both DEPLOYMENT_ENV and our KINDLY setting)
        "deployment.environment": os.environ.get(
            "DEPLOYMENT_ENV", os.environ.get("KINDLY_OTEL_ENVIRONMENT", "development")
        ),
        # Host context
        "host.name": hostname,
        "host.arch": "amd64",
        "host.os.type": os.environ.get("HOST_OS_TYPE", "windows"),
        # Process context
        "process.pid": pid,
        "process.executable.name": "python",
        "process.runtime.name": "cpython",
        "process.runtime.version": os.environ.get(
            "PYTHON_VERSION", platform.python_version()
        ),
        # OTEL SDK info
        "telemetry.sdk.language": "python",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.version": "1.20.0",
    }
    resource = Resource.create(resource_attrs)

    # === SAMPLING (head-based, configurable) ===
    # Read from KINDLY_OTEL_SAMPLING_RATIO (preferred) or OTEL_TRACES_SAMPLER_ARG
    sampling_ratio = float(
        os.environ.get(
            "KINDLY_OTEL_SAMPLING_RATIO",
            os.environ.get("OTEL_TRACES_SAMPLER_ARG", "0.15"),
        )
    )
    sampler = ParentBased(TraceIdRatioBased(sampling_ratio))

    # === TRACES ===
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    # BatchSpanProcessor: batches spans, exports every 5s or 512 spans
    trace_exporter = OTLPSpanExporter(
        endpoint=f"{endpoint}/v1/traces",
        headers=headers,
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            trace_exporter,
            max_queue_size=2048,
            schedule_delay_millis=5000,
            max_export_batch_size=512,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    # === AUTO-INSTRUMENTATION ===
    # Instrument httpx for automatic HTTP client spans (HTTP semantic conventions)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logging.info("HTTPX auto-instrumentation enabled - all HTTP calls traced")
    except ImportError:
        logging.info(
            "opentelemetry-instrumentation-httpx not installed - "
            "HTTP calls not auto-traced. Install: uv pip install opentelemetry-instrumentation-httpx"
        )

    # === METRICS ===
    metric_readers: list[Any] = []

    prometheus_metric_reader = (
        _get_prometheus_metric_reader() if prometheus_port else None
    )
    if prometheus_port and prometheus_metric_reader is not None:
        # Prometheus endpoint for Alloy scraping
        prometheus_reader = prometheus_metric_reader(port=prometheus_port)
        metric_readers.append(prometheus_reader)
        logging.info(f"Prometheus metrics endpoint started on port {prometheus_port}")
    else:
        # OTLP direct export (60s interval)
        metric_exporter = OTLPMetricExporter(
            endpoint=f"{endpoint}/v1/metrics",
            headers=headers,
        )
        metric_reader = PeriodicExportingMetricReader(
            exporter=metric_exporter,
            export_interval_millis=60000,
        )
        metric_readers.append(metric_reader)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=metric_readers,
    )
    metrics.set_meter_provider(meter_provider)

    # === LOGS (experimental) ===
    if LOGS_AVAILABLE:
        try:
            log_exporter = OTLPLogExporter(
                endpoint=f"{endpoint}/v1/logs",
                headers=headers,
            )
            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(log_exporter)
            )
            set_logger_provider(logger_provider)

            global _otel_logging_handler
            if _otel_logging_handler is None:
                _otel_logging_handler = LoggingHandler(
                    level=logging.NOTSET,
                    logger_provider=logger_provider,
                )
                setattr(_otel_logging_handler, "_kindly_otlp_handler", True)
                logging.getLogger().addHandler(_otel_logging_handler)

            logging.info(
                "OTLP log export enabled - standard logging bridged to OpenTelemetry"
            )
        except Exception as e:
            logging.warning(f"Failed to initialize log export: {e}")

    # Configure structlog for Loki JSON format with trace context injection.
    # Default: plain text logs for local development unless telemetry export is enabled.
    try:
        from .utils.structured_logging import configure_structlog

        json_logs = os.environ.get("KINDLY_STRUCTURED_LOGGING", "").lower() in (
            "true",
            "1",
            "yes",
        )
        if endpoint and not json_logs:
            json_logs = True
        configure_structlog(json_output=json_logs)
        if json_logs:
            logging.info("Structured logging enabled - JSON format for Grafana Loki")
    except ImportError:
        logging.info(
            "structlog not installed - using standard Python logging. Install: pip install structlog"
        )

    _initialized = True
    logging.info(
        f"OpenTelemetry initialized: service={service_name}, endpoint={endpoint}"
    )
    endpoint_url = urlparse(endpoint)
    logging.info(
        json.dumps(
            {
                "event": "telemetry.startup",
                "service_name": service_name,
                "service_namespace": "kindly-mcp",
                "service_version": service_version,
                "deployment_environment": os.environ.get(
                    "DEPLOYMENT_ENV", "development"
                ),
                "host_name": hostname,
                "process_pid": pid,
                "otlp_endpoint_host": endpoint_url.hostname,
                "otlp_endpoint_path": endpoint_url.path,
                "signals": {
                    "traces": True,
                    "metrics": True,
                    "logs": LOGS_AVAILABLE,
                    "httpx_instrumentation": True,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


# ============================================================================
# TRACER / METER ACCESSORS
# ============================================================================


def get_tracer(name: str = "web-search-mcp") -> trace.Tracer:
    """Get tracer for manual span creation."""
    return trace.get_tracer(name)


def get_meter(name: str = "web-search-mcp") -> metrics.Meter:
    """Get meter for custom metrics."""
    return metrics.get_meter(name)


# ============================================================================
# METRIC GETTERS (Lazy initialization)
# ============================================================================


def get_provider_metrics() -> tuple[
    metrics.Counter, metrics.Histogram, metrics.Counter
]:
    """Get provider metrics (call counter, duration histogram, results counter)."""
    meter = get_meter()
    global \
        _provider_call_counter, \
        _provider_duration_histogram, \
        _provider_results_counter

    if _provider_call_counter is None:
        _provider_call_counter = meter.create_counter(
            name="web_search_provider_calls_total",
            description="Total provider API calls",
            unit="1",
        )

    if _provider_duration_histogram is None:
        _provider_duration_histogram = meter.create_histogram(
            name="web_search_provider_duration_seconds",
            description="Provider call latency distribution",
            unit="s",
            # Bucket boundaries: 10ms, 20ms, 50ms, 100ms, 200ms, 500ms, 1s, 2s, 5s, 10s
            explicit_bucket_boundaries_advisory=[
                0.01,
                0.02,
                0.05,
                0.1,
                0.2,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
            ],
        )

    if _provider_results_counter is None:
        _provider_results_counter = meter.create_counter(
            name="web_search_provider_results_total",
            description="Total results returned by provider",
            unit="1",
        )

    return (
        _provider_call_counter,
        _provider_duration_histogram,
        _provider_results_counter,
    )


def get_search_metrics() -> tuple[
    metrics.Counter, metrics.Histogram, metrics.Histogram
]:
    """Get search metrics (total counter, duration histogram, merge histogram)."""
    meter = get_meter()
    global _search_total_counter, _search_duration_histogram, _search_merge_histogram

    if _search_total_counter is None:
        _search_total_counter = meter.create_counter(
            name="web_search_requests_total",
            description="Total web_search tool invocations",
            unit="1",
        )

    if _search_duration_histogram is None:
        _search_duration_histogram = meter.create_histogram(
            name="web_search_duration_seconds",
            description="Complete search pipeline latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[
                0.1,
                0.2,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
                30.0,
                60.0,
            ],
        )

    if _search_merge_histogram is None:
        _search_merge_histogram = meter.create_histogram(
            name="web_search_merge_duration_seconds",
            description="RRF merge algorithm latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.001, 0.002, 0.005, 0.01, 0.02, 0.05],
        )

    return _search_total_counter, _search_duration_histogram, _search_merge_histogram


def get_search_total_metric() -> metrics.Counter:
    """Get search total counter directly (convenience function for search_instrumented.py)."""
    total_counter, _, _ = get_search_metrics()
    return total_counter


def get_cache_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get cache metrics (request counter, duration histogram)."""
    meter = get_meter()
    global _cache_request_counter, _cache_duration_histogram

    if _cache_request_counter is None:
        _cache_request_counter = meter.create_counter(
            name="web_search_cache_requests_total",
            description="Cache lookup requests",
            unit="1",
        )

    if _cache_duration_histogram is None:
        _cache_duration_histogram = meter.create_histogram(
            name="web_search_cache_duration_seconds",
            description="Cache lookup latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.001, 0.005, 0.01, 0.02, 0.05, 0.1],
        )

    return _cache_request_counter, _cache_duration_histogram


def get_mcp_metrics() -> tuple[metrics.Counter, metrics.Counter]:
    """Get MCP protocol metrics (tool counter, error counter)."""
    meter = get_meter()
    global _mcp_tool_counter, _mcp_error_counter

    if _mcp_tool_counter is None:
        _mcp_tool_counter = meter.create_counter(
            name="mcp_tool_invocations_total",
            description="MCP tool call count",
            unit="1",
        )

    if _mcp_error_counter is None:
        _mcp_error_counter = meter.create_counter(
            name="mcp_errors_total",
            description="MCP protocol errors",
            unit="1",
        )

    return _mcp_tool_counter, _mcp_error_counter


def get_content_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get content resolution metrics."""
    meter = get_meter()
    global \
        _content_resolution_counter, \
        _content_duration_histogram, \
        _content_fallback_counter, \
        _content_error_counter

    if _content_resolution_counter is None:
        _content_resolution_counter = meter.create_counter(
            name="web_search_content_resolutions_total",
            description="Content resolution attempts by stage",
            unit="1",
        )

    if _content_duration_histogram is None:
        _content_duration_histogram = meter.create_histogram(
            name="web_search_content_duration_seconds",
            description="Content extraction latency per stage",
            unit="s",
            explicit_bucket_boundaries_advisory=[
                0.1,
                0.5,
                1.0,
                2.0,
                5.0,
                10.0,
                20.0,
                30.0,
            ],
        )

    if _content_fallback_counter is None:
        _content_fallback_counter = meter.create_counter(
            name="web_search_content_fallback_total",
            description="Content resolution fallbacks to later stages (trafilatura, jina, browser)",
            unit="1",
        )

    if _content_error_counter is None:
        _content_error_counter = meter.create_counter(
            name="web_search_content_errors_total",
            description="Content resolution errors by stage",
            unit="1",
        )

    return _content_resolution_counter, _content_duration_histogram


def get_rrf_metrics() -> tuple[metrics.Counter, metrics.Counter, metrics.Histogram]:
    """Get RRF merge metrics."""
    meter = get_meter()
    global _rrf_merge_counter, _rrf_provider_contribution_counter, _rrf_score_histogram

    if _rrf_merge_counter is None:
        _rrf_merge_counter = meter.create_counter(
            name="web_search_rrf_merge_total",
            description="RRF merge operations with discarded/overlap details",
            unit="1",
        )

    if _rrf_provider_contribution_counter is None:
        _rrf_provider_contribution_counter = meter.create_counter(
            name="web_search_rrf_provider_contribution",
            description="How many final results came from each provider",
            unit="1",
        )

    if _rrf_score_histogram is None:
        _rrf_score_histogram = meter.create_histogram(
            name="web_search_rrf_score_distribution",
            description="Distribution of final RRF scores",
            unit="1",
            explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0],
        )

    return _rrf_merge_counter, _rrf_provider_contribution_counter, _rrf_score_histogram


def get_rewrite_metrics() -> tuple[metrics.Counter, metrics.Histogram]:
    """Get query rewrite metrics."""
    meter = get_meter()
    global _rewrite_counter, _rewrite_duration_histogram

    if _rewrite_counter is None:
        _rewrite_counter = meter.create_counter(
            name="web_search_query_rewrite_total",
            description="Query rewrite operations by policy",
            unit="1",
        )

    if _rewrite_duration_histogram is None:
        _rewrite_duration_histogram = meter.create_histogram(
            name="web_search_query_rewrite_duration_seconds",
            description="Query rewrite latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
        )

    return _rewrite_counter, _rewrite_duration_histogram


def get_rerank_metrics() -> tuple[
    metrics.Counter, metrics.Histogram, metrics.Histogram, metrics.Counter
]:
    """Get reranking metrics."""
    meter = get_meter()
    global \
        _rerank_counter, \
        _rerank_duration_histogram, \
        _rerank_score_histogram, \
        _rerank_diversity_counter

    if _rerank_counter is None:
        _rerank_counter = meter.create_counter(
            name="web_search_rerank_total",
            description="Reranking pipeline executions by stage",
            unit="1",
        )

    if _rerank_duration_histogram is None:
        _rerank_duration_histogram = meter.create_histogram(
            name="web_search_rerank_duration_seconds",
            description="Rerank stage latency",
            unit="s",
            explicit_bucket_boundaries_advisory=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0],
        )

    if _rerank_score_histogram is None:
        _rerank_score_histogram = meter.create_histogram(
            name="web_search_rerank_scores",
            description="Relevance score distribution from Jina reranker (shifted +1.0 to handle negative scores)",
            unit="1",
            # Buckets for shifted range: raw scores -1.0 to 1.0 become 0.0 to 2.0
            explicit_bucket_boundaries_advisory=[
                0.0,
                0.2,
                0.4,
                0.6,
                0.8,
                1.0,
                1.2,
                1.4,
                1.6,
                1.8,
                2.0,
            ],
        )

    if _rerank_diversity_counter is None:
        _rerank_diversity_counter = meter.create_counter(
            name="web_search_rerank_diversity_removals",
            description="Results removed by diversity pruning",
            unit="1",
        )

    return (
        _rerank_counter,
        _rerank_duration_histogram,
        _rerank_score_histogram,
        _rerank_diversity_counter,
    )


def get_semantic_cache_metrics() -> tuple[
    metrics.Histogram, metrics.Counter, metrics.Counter
]:
    """Get semantic cache detailed metrics."""
    meter = get_meter()
    global _cache_score_histogram, _cache_ttl_counter, _cache_hybrid_counter

    if _cache_score_histogram is None:
        _cache_score_histogram = meter.create_histogram(
            name="web_search_semantic_cache_score_distribution",
            description="Similarity scores for semantic cache lookups",
            unit="1",
            explicit_bucket_boundaries_advisory=[
                0.7,
                0.75,
                0.8,
                0.82,
                0.85,
                0.9,
                0.95,
                1.0,
            ],
        )

    if _cache_ttl_counter is None:
        _cache_ttl_counter = meter.create_counter(
            name="web_search_semantic_cache_ttl_used",
            description="TTL assigned to cached entries",
            unit="1",
        )

    if _cache_hybrid_counter is None:
        _cache_hybrid_counter = meter.create_counter(
            name="web_search_semantic_cache_hybrid_search",
            description="Hybrid search method used in semantic cache",
            unit="1",
        )

    return _cache_score_histogram, _cache_ttl_counter, _cache_hybrid_counter


def get_circuit_metrics() -> tuple[metrics.UpDownCounter, metrics.Counter]:
    """Get circuit breaker metrics."""
    meter = get_meter()
    global _circuit_state_gauge, _circuit_event_counter

    if _circuit_state_gauge is None:
        _circuit_state_gauge = meter.create_up_down_counter(
            name="web_search_provider_circuit_state",
            description="Circuit breaker state per provider (0=closed, 1=open, 0.5=half_open)",
            unit="1",
        )

    if _circuit_event_counter is None:
        _circuit_event_counter = meter.create_counter(
            name="web_search_provider_circuit_events",
            description="Circuit breaker state changes",
            unit="1",
        )

    return _circuit_state_gauge, _circuit_event_counter


def get_gemini_metrics() -> metrics.Counter:
    """Get Gemini search metrics."""
    meter = get_meter()
    global _gemini_counter

    if _gemini_counter is None:
        _gemini_counter = meter.create_counter(
            name="mcp_gemini_search_details",
            description="Gemini search specifics (grounding queries, chunks, structured output)",
            unit="1",
        )

    return _gemini_counter


def get_perplexity_metrics() -> metrics.Counter:
    """Get Perplexity search metrics."""
    meter = get_meter()
    global _perplexity_counter

    if _perplexity_counter is None:
        _perplexity_counter = meter.create_counter(
            name="mcp_perplexity_search_details",
            description="Perplexity search specifics (depth, source count, model)",
            unit="1",
        )

    return _perplexity_counter


def get_youtube_metrics() -> tuple[metrics.Counter, metrics.Counter]:
    """Get YouTube metrics."""
    meter = get_meter()
    global _youtube_transcript_counter, _youtube_search_counter

    if _youtube_transcript_counter is None:
        _youtube_transcript_counter = meter.create_counter(
            name="mcp_youtube_transcript_details",
            description="YouTube transcript specifics (format, language, duration)",
            unit="1",
        )

    if _youtube_search_counter is None:
        _youtube_search_counter = meter.create_counter(
            name="mcp_youtube_search_details",
            description="YouTube search specifics",
            unit="1",
        )

    return _youtube_transcript_counter, _youtube_search_counter


def get_query_quality_metrics() -> tuple[metrics.Histogram, metrics.Histogram]:
    """Get query quality metrics (P2-1: query length, P2-2: domain diversity).

    Returns:
        Tuple of (query_length_histogram, domain_diversity_histogram)
    """
    meter = get_meter()
    global _query_length_histogram, _domain_diversity_histogram

    if _query_length_histogram is None:
        _query_length_histogram = meter.create_histogram(
            name="web_search_query_length_chars",
            description="Distribution of query string lengths (detect keyword pile-on)",
            unit="chars",
            # Buckets: short queries (10-50 chars), medium (100 chars), long keyword pile-on (500+)
            explicit_bucket_boundaries_advisory=[10, 20, 50, 100, 200, 500],
        )

    if _domain_diversity_histogram is None:
        _domain_diversity_histogram = meter.create_histogram(
            name="web_search_domain_diversity",
            description="Unique domains in top N results (detect homogeneous results)",
            unit="domains",
            # Buckets: 1 domain (all same), 3-5 (good diversity), 10+ (excellent)
            explicit_bucket_boundaries_advisory=[1, 2, 3, 5, 7, 10, 15],
        )

    return _query_length_histogram, _domain_diversity_histogram


# ============================================================================
# RECORDING FUNCTIONS (Metrics)
# ============================================================================


def record_provider_call(
    provider: str,
    duration_seconds: float,
    result_count: int,
    status_code: int = 200,
    error_type: str | None = None,
) -> None:
    """Record provider call metrics with semantic conventions.

    Args:
        provider: Provider name (searxng, ddg, gemini, tavily, brave, jina)
        duration_seconds: Call duration in seconds
        result_count: Number of results returned
        status_code: HTTP status code (200, 500, 503, etc.)
        error_type: Error type if failed (HTTP_500, TimeoutError, RateLimitError, etc.)
    """
    call_counter, duration_histogram, results_counter = get_provider_metrics()

    # Determine status from HTTP code
    status = STATUS_SUCCESS if status_code < 400 else STATUS_ERROR

    # Record call count with full attributes
    call_counter.add(
        1,
        {
            PROVIDER_NAME: provider,
            HTTP_RESPONSE_STATUS_CODE: status_code,
            PROVIDER_STATUS: status,
            PROVIDER_ERROR_TYPE: error_type or "",
        },
    )

    # Record duration
    duration_histogram.record(
        duration_seconds,
        {
            PROVIDER_NAME: provider,
            HTTP_RESPONSE_STATUS_CODE: status_code,
        },
    )

    # Record results (only meaningful on success)
    if status == STATUS_SUCCESS and result_count > 0:
        results_counter.add(result_count, {PROVIDER_NAME: provider})


def record_cache_lookup(
    cache_type: str, hit: bool, duration_seconds: float | None = None
) -> None:
    """Record cache hit/miss and optional latency.

    Args:
        cache_type: "exact", "semantic", or "page"
        hit: True if cache hit, False if miss
        duration_seconds: Optional lookup duration
    """
    request_counter, duration_histogram = get_cache_metrics()

    request_counter.add(
        1,
        {
            CACHE_TYPE: cache_type,
            CACHE_HIT: str(hit).lower(),
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                CACHE_TYPE: cache_type,
                CACHE_HIT: str(hit).lower(),
            },
        )


def record_search_request(
    providers_used: list[str],
    duration_seconds: float,
    result_count: int,
) -> None:
    """Record complete search operation."""
    total_counter, duration_histogram, _ = get_search_metrics()

    providers_str = str(providers_used)

    total_counter.add(1, {SEARCH_PROVIDERS_USED: providers_str})
    duration_histogram.record(duration_seconds, {SEARCH_PROVIDERS_USED: providers_str})


def record_merge(duration_seconds: float, input_lists: int, output_count: int) -> None:
    """Record RRF merge metrics."""
    _, _, merge_histogram = get_search_metrics()
    merge_histogram.record(
        duration_seconds,
        {
            "merge.input_lists": input_lists,
            "merge.output_count": output_count,
        },
    )


def record_mcp_tool_call(tool_name: str, success: bool) -> None:
    """Record MCP tool invocation."""
    tool_counter, error_counter = get_mcp_metrics()

    status = STATUS_SUCCESS if success else STATUS_ERROR
    tool_counter.add(
        1,
        {
            GEN_AI_TOOL_NAME: tool_name,
            PROVIDER_STATUS: status,
        },
    )

    if not success:
        error_counter.add(
            1,
            {
                GEN_AI_TOOL_NAME: tool_name,
                ERROR_TYPE: "tool_execution_error",
            },
        )


def record_content_resolution(
    stage: str,
    url: str,
    success: bool,
    size_bytes: int | None = None,
    duration_seconds: float | None = None,
    word_count: int | None = None,
    extraction_method: str | None = None,
) -> None:
    """Record content resolution stage."""
    resolution_counter, duration_histogram = get_content_metrics()

    status = STATUS_SUCCESS if success else "fallback"
    resolution_counter.add(
        1,
        {
            CONTENT_STAGE: stage,
            CONTENT_FINAL_STAGE: stage,
            CONTENT_STATUS: status,
            CONTENT_EXTRACTION_METHOD: extraction_method or "",
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                CONTENT_STAGE: stage,
                CONTENT_STATUS: status,
            },
        )


def record_content_fallback(
    stage: str, url: str, from_stage: str | None = None
) -> None:
    """Record a fallback to a later extraction stage."""
    counter = _content_fallback_counter
    if counter is None:
        # Ensure initialized
        get_content_metrics()
        counter = _content_fallback_counter
    if counter:
        counter.add(
            1,
            {
                CONTENT_STAGE: stage,
                "content.from_stage": from_stage or "",
                CONTENT_URL: url[:200] if url else "",
            },
        )


def record_content_error(stage: str, url: str, error_type: str) -> None:
    """Record a hard error during content resolution."""
    counter = _content_error_counter
    if counter is None:
        get_content_metrics()
        counter = _content_error_counter
    if counter:
        counter.add(
            1,
            {
                CONTENT_STAGE: stage,
                ERROR_TYPE: error_type,
                CONTENT_URL: url[:200] if url else "",
            },
        )


def record_rrf_merge(
    input_lists: int,
    input_total: int,
    output_total: int,
    discarded_count: int,
    overlap_rate: float,
    provider_contributions: dict[str, int],
) -> None:
    """Record RRF merge operation details.

    Args:
        input_lists: Number of provider result lists merged
        input_total: Total results before deduplication
        output_total: Final results after merge
        discarded_count: URLs discarded as duplicates
        overlap_rate: Fraction of URLs appearing in multiple lists
        provider_contributions: Dict of provider_name -> count of results in final top-N
    """
    merge_counter, contribution_counter, score_histogram = get_rrf_metrics()

    # Record merge operation
    merge_counter.add(
        1,
        {
            RRF_INPUT_LISTS: input_lists,
            RRF_INPUT_TOTAL: input_total,
            RRF_OUTPUT_TOTAL: output_total,
            RRF_DISCARDED_COUNT: discarded_count,
            RRF_OVERLAP_RATE: round(overlap_rate, 3),
        },
    )

    # Record per-provider contribution
    for provider, count in provider_contributions.items():
        contribution_counter.add(
            count,
            {
                PROVIDER_NAME: provider,
            },
        )


def record_rrf_score(score: float, position: int) -> None:
    """Record individual RRF score for distribution analysis."""
    _, _, score_histogram = get_rrf_metrics()
    score_histogram.record(
        score,
        {
            RESULT_POSITION: position,
        },
    )


def record_query_rewrite(
    policy: str,
    variant_count: int,
    has_precision_signals: bool,
    duration_seconds: float | None = None,
    model: str = "mistral-small-2603",
) -> None:
    """Record query rewrite operation.

    Args:
        policy: "bypass", "light_rewrite", or "expand"
        variant_count: Number of query variants produced (1-3)
        has_precision_signals: True if precision signals detected (code patterns, exact phrases)
        duration_seconds: Duration of rewrite operation
        model: Mistral model used
    """
    rewrite_counter, rewrite_histogram = get_rewrite_metrics()

    rewrite_counter.add(
        1,
        {
            REWRITE_POLICY: policy,
            REWRITE_VARIANT_COUNT: variant_count,
            REWRITE_HAS_PRECISION_SIGNALS: str(has_precision_signals).lower(),
            REWRITE_MODEL: model,
        },
    )

    if duration_seconds is not None:
        rewrite_histogram.record(
            duration_seconds,
            {
                REWRITE_POLICY: policy,
            },
        )


def record_rerank_stage(
    stage: str,
    input_count: int,
    output_count: int,
    duration_seconds: float | None = None,
    relevance_scores: list[float] | None = None,
    model: str | None = None,
) -> None:
    """Record reranking pipeline stage.

    Args:
        stage: "bi_encoder", "jina", or "diversity"
        input_count: Candidates before stage
        output_count: Candidates after stage
        duration_seconds: Stage latency
        relevance_scores: Relevance scores from Jina (for jina stage)
    """
    rerank_counter, duration_histogram, score_histogram, _ = get_rerank_metrics()

    removed_count = input_count - output_count
    rerank_counter.add(
        1,
        {
            RERANK_STAGE: stage,
            RERANK_INPUT_COUNT: input_count,
            RERANK_OUTPUT_COUNT: output_count,
            RERANK_REMOVED_COUNT: removed_count,
            RERANK_MODEL: model or "",
        },
    )

    if duration_seconds is not None:
        duration_histogram.record(
            duration_seconds,
            {
                RERANK_STAGE: stage,
                RERANK_MODEL: model or "",
            },
        )

    # Record individual relevance scores for distribution
    # IMPORTANT: Jina reranker can return negative scores (valid for relevance ranking)
    # To track score distribution properly, we use a two-pronged approach:
    # 1. Shift all scores to be positive (add 1.0 offset) for histogram recording
    # 2. Record raw scores as span events for accurate visibility in Grafana
    if relevance_scores and stage == "jina":
        for score in relevance_scores[:20]:  # Limit to top 20
            # Shift score by +1.0 to ensure histogram receives positive values
            # Jina scores typically range from -1.0 to 1.0, so shifted range is 0.0 to 2.0
            shifted_score = score + 1.0
            score_histogram.record(
                shifted_score,
                {
                    RERANK_STAGE: stage,
                    "rerank.score_shifted": "true",  # Indicate transformation for query interpretation
                },
            )


def record_diversity_removal(
    similarity_score: float,
    threshold: float = 0.85,
) -> None:
    """Record a result removed by diversity pruning."""
    _, _, _, diversity_counter = get_rerank_metrics()
    diversity_counter.add(
        1,
        {
            RERANK_DIVERSITY_THRESHOLD: threshold,
            RERANK_SIMILARITY_SCORE: round(similarity_score, 3),
        },
    )


def record_semantic_cache_lookup(
    similarity_score: float,
    hit: bool,
    content_type: str | None = None,
    ttl_seconds: int | None = None,
    search_type: str | None = None,
    vector_distance: float | None = None,
) -> None:
    """Record semantic cache lookup with similarity score.

    Args:
        similarity_score: Cosine similarity score (0.0-1.0)
        hit: True if cache hit
        content_type: "news", "technical", "faq", or "general"
        ttl_seconds: TTL assigned to entry (if write)
        search_type: "vector", "fts", or "hybrid_rrf"
        vector_distance: Actual vector distance value
    """
    score_histogram, ttl_counter, hybrid_counter = get_semantic_cache_metrics()

    # Record similarity score distribution
    score_histogram.record(
        similarity_score,
        {
            CACHE_HIT: str(hit).lower(),
            CACHE_CONTENT_TYPE: content_type or "general",
        },
    )

    # Record TTL used (for cache writes)
    if ttl_seconds is not None:
        ttl_counter.add(
            1,
            {
                CACHE_CONTENT_TYPE: content_type or "general",
                CACHE_TTL_SECONDS: ttl_seconds,
            },
        )

    # Record hybrid search method
    if search_type is not None:
        hybrid_counter.add(
            1,
            {
                CACHE_SEARCH_TYPE: search_type,
                CACHE_VECTOR_DISTANCE: round(vector_distance or 0.0, 4),
            },
        )


def record_circuit_breaker_state(
    provider: str,
    state: str,
    failure_count: int,
) -> None:
    """Record circuit breaker state for a provider.

    Args:
        provider: Provider name
        state: "closed", "open", or "half_open"
        failure_count: Consecutive failures
    """
    state_gauge, _ = get_circuit_metrics()

    # Map state to numeric value for gauge
    state_value = 0.0 if state == "closed" else (1.0 if state == "open" else 0.5)
    state_gauge.add(
        state_value,
        {
            PROVIDER_NAME: provider,
            CIRCUIT_STATE: state,
            CIRCUIT_FAILURE_COUNT: failure_count,
        },
    )


def record_circuit_breaker_event(
    provider: str,
    event: str,
    failure_threshold: int = 3,
) -> None:
    """Record circuit breaker state change event.

    Args:
        provider: Provider name
        event: "trip", "reset", or "half_open"
        failure_threshold: Threshold that triggered the event
    """
    _, event_counter = get_circuit_metrics()
    event_counter.add(
        1,
        {
            PROVIDER_NAME: provider,
            CIRCUIT_EVENT: event,
            CIRCUIT_FAILURE_THRESHOLD: failure_threshold,
        },
    )


def record_gemini_search(
    grounding_queries: int,
    grounding_chunks: int,
    structured_output: bool,
    duration_seconds: float | None = None,
) -> None:
    """Record Gemini search specifics."""
    gemini_counter = get_gemini_metrics()
    gemini_counter.add(
        1,
        {
            GEMINI_GROUNDING_QUERIES: grounding_queries,
            GEMINI_GROUNDING_CHUNKS: grounding_chunks,
            GEMINI_STRUCTURED_OUTPUT: str(structured_output).lower(),
        },
    )


def record_perplexity_search(
    depth: str,
    source_count: int,
    model: str = "sonar",
    duration_seconds: float | None = None,
) -> None:
    """Record Perplexity search specifics."""
    perplexity_counter = get_perplexity_metrics()
    perplexity_counter.add(
        1,
        {
            PERPLEXITY_DEPTH: depth,
            PERPLEXITY_SOURCE_COUNT: source_count,
            PERPLEXITY_MODEL: model,
        },
    )


def record_youtube_transcript(
    format: str,
    language: str,
    is_translated: bool,
    duration_seconds: int | None = None,
) -> None:
    """Record YouTube transcript specifics."""
    transcript_counter, _ = get_youtube_metrics()
    transcript_counter.add(
        1,
        {
            YOUTUBE_FORMAT: format,
            YOUTUBE_LANGUAGE: language,
            YOUTUBE_IS_TRANSLATED: str(is_translated).lower(),
            YOUTUBE_DURATION_SECONDS: duration_seconds or 0,
        },
    )


def record_youtube_search(
    num_results: int,
    duration_seconds: float | None = None,
) -> None:
    """Record YouTube search specifics."""
    _, search_counter = get_youtube_metrics()
    search_counter.add(
        1,
        {
            SEARCH_NUM_RESULTS_RETURNED: num_results,
        },
    )


def record_query_length(
    query_length: int,
    policy: str,
) -> None:
    """Record query length for detecting keyword pile-on patterns.

    Args:
        query_length: Length of original query in characters
        policy: Rewrite policy mode (bypass, light_rewrite, expand)
    """
    query_length_histogram, _ = get_query_quality_metrics()
    query_length_histogram.record(
        query_length,
        {
            REWRITE_POLICY: policy,
        },
    )


def record_domain_diversity(
    unique_domains: int,
    total_results: int,
    providers_used: list[str],
) -> None:
    """Record domain diversity for detecting homogeneous results.

    Args:
        unique_domains: Number of unique domains in top N results
        total_results: Total number of results returned
        providers_used: List of providers that contributed results
    """
    _, domain_diversity_histogram = get_query_quality_metrics()
    domain_diversity_histogram.record(
        unique_domains,
        {
            SEARCH_NUM_RESULTS_RETURNED: total_results,
            SEARCH_PROVIDERS_USED: str(providers_used),
        },
    )


def record_tool_details(
    tool_name: str,
    input_query_length: int | None = None,
    input_url_count: int | None = None,
    output_result_count: int | None = None,
    output_content_length: int | None = None,
    output_transcript_length: int | None = None,
) -> None:
    """Record detailed MCP tool invocation metrics."""
    tool_counter, _ = get_mcp_metrics()

    attrs = {
        GEN_AI_TOOL_NAME: tool_name,
        GEN_AI_OPERATION_NAME: "execute_tool",
    }

    if input_query_length is not None:
        attrs["tool.input.query_length"] = input_query_length
    if input_url_count is not None:
        attrs["tool.input.url_count"] = input_url_count
    if output_result_count is not None:
        attrs["tool.output.result_count"] = output_result_count
    if output_content_length is not None:
        attrs["tool.output.content_length"] = output_content_length
    if output_transcript_length is not None:
        attrs["tool.output.transcript_length"] = output_transcript_length

    tool_counter.add(1, attrs)


# ============================================================================
# SPAN HELPER FUNCTIONS
# ============================================================================


def create_search_span(
    query: str,
    num_results: int,
    providers_requested: list[str] | None,
) -> trace.Span:
    """Create span for search operation with semantic conventions.

    Use as context manager:
        with create_search_span(query, 10, ["searxng"]) as span:
            results = await search(...)
            span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, len(results))
    """
    tracer = get_tracer()
    return tracer.start_as_current_span(
        "web_search",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
            SEARCH_PROVIDERS_REQUESTED: str(providers_requested or []),
            MCP_SERVER_NAME: "web-search-mcp",
            GEN_AI_SYSTEM: "mcp",
        },
    )


def create_provider_span(
    provider: str,
    query: str,
    num_results: int,
    url: str,
) -> trace.Span:
    """Create span for provider call with HTTP semantic conventions.

    Use as context manager:
        with create_provider_span("searxng", query, 10, "http://localhost:8080/search") as span:
            response = await httpx.get(url)
            span.set_attribute(HTTP_RESPONSE_STATUS_CODE, response.status_code)
            add_results_to_span(span, results)
    """
    tracer = get_tracer()

    # Parse URL for server.address and server.port
    from urllib.parse import urlparse

    parsed = urlparse(url)

    return tracer.start_as_current_span(
        f"provider.{provider}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            PROVIDER_NAME: provider,
            HTTP_REQUEST_METHOD: "GET",
            URL_FULL: url[:500],
            SERVER_ADDRESS: parsed.hostname or "",
            SERVER_PORT: parsed.port or 80,
            SEARCH_QUERY: query[:500],
            SEARCH_NUM_RESULTS_REQUESTED: num_results,
            GEN_AI_SYSTEM: "mcp",
        },
    )


def create_mcp_tool_span(
    tool_name: str,
    method: str = "tools/call",
    session_id: str | None = None,
) -> trace.Span:
    """Create span for MCP tool invocation.

    Use at server entry point for each tool call.
    """
    tracer = get_tracer()
    attributes = {
        MCP_METHOD_NAME: method,
        MCP_SERVER_NAME: "web-search-mcp",
        GEN_AI_TOOL_NAME: tool_name,
        GEN_AI_OPERATION_NAME: "execute_tool",
        RPC_SYSTEM: "jsonrpc",
        RPC_JSONRPC_VERSION: "2.0",
    }
    if session_id:
        attributes[MCP_SESSION_ID] = session_id

    return tracer.start_as_current_span(
        f"{method} {tool_name}",
        kind=trace.SpanKind.SERVER,
        attributes=attributes,
    )


def create_content_span(
    stage: str,
    url: str,
) -> trace.Span:
    """Create span for content resolution stage."""
    tracer = get_tracer()

    from urllib.parse import urlparse

    parsed = urlparse(url)

    return tracer.start_as_current_span(
        f"content.{stage}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            CONTENT_STAGE: stage,
            CONTENT_URL: url[:500],
            URL_FULL: url[:500],
            SERVER_ADDRESS: parsed.hostname or "",
            SERVER_PORT: parsed.port or 443 if parsed.scheme == "https" else 80,
        },
    )


def create_merge_span(input_lists: int, total_input: int) -> trace.Span:
    """Create span for RRF merge operation."""
    tracer = get_tracer()
    return tracer.start_as_current_span(
        "rrf_merge",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_MERGE_ALGORITHM: "rrf_k60",
            "merge.input_lists": input_lists,
            "merge.input_total": total_input,
        },
    )


def create_query_rewrite_span(
    query: str,
    policy: str,
) -> trace.Span:
    """Create span for query rewrite operation."""
    tracer = get_tracer()
    return tracer.start_as_current_span(
        "query.rewrite",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            SEARCH_QUERY: query[:500],
            REWRITE_POLICY: policy,
            REWRITE_MODEL: "cascade",
            MCP_SERVER_NAME: "web-search-mcp",
        },
    )


def create_rerank_span(
    stage: str,
    input_count: int,
) -> trace.Span:
    """Create span for reranking stage.

    Args:
        stage: "bi_encoder", "jina", or "diversity"
        input_count: Number of candidates to rerank
    """
    tracer = get_tracer()
    return tracer.start_as_current_span(
        f"rerank.{stage}",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            RERANK_STAGE: stage,
            RERANK_INPUT_COUNT: input_count,
            RERANK_MODEL: "jina-reranker-v3" if stage == "jina" else "bi-encoder",
        },
    )


def create_circuit_breaker_span(
    provider: str,
    state: str,
) -> trace.Span:
    """Create span for circuit breaker state change."""
    tracer = get_tracer()
    return tracer.start_as_current_span(
        f"circuit.{provider}",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            PROVIDER_NAME: provider,
            CIRCUIT_STATE: state,
            MCP_SERVER_NAME: "web-search-mcp",
        },
    )


def create_cache_span(
    cache_type: str,
    query: str | None = None,
    url: str | None = None,
) -> trace.Span:
    """Create span for cache operation."""
    tracer = get_tracer()
    attributes = {
        CACHE_TYPE: cache_type,
        MCP_SERVER_NAME: "web-search-mcp",
    }
    if query:
        attributes[SEARCH_QUERY] = query[:500]
    if url:
        attributes[URL_FULL] = url[:500]

    return tracer.start_as_current_span(
        f"cache.{cache_type}",
        kind=trace.SpanKind.INTERNAL,
        attributes=attributes,
    )


# ============================================================================
# SPAN ENHANCEMENT FUNCTIONS
# ============================================================================


def add_results_to_span(
    span: trace.Span,
    results: list[Any],
    max_results: int = 10,
    include_rrf_details: bool = False,
) -> None:
    """Add search results to span as events for Grafana trace view.

    Each result becomes a span event with title/link visible in Grafana.
    When include_rrf_details=True, also includes RRF score and provider sources.

    Args:
        span: The span to add events to
        results: List of result objects (must have title, link attributes)
        max_results: Maximum number of results to add as events (default 10)
        include_rrf_details: If True, include RRF score and providers for each result
    """
    from urllib.parse import urlparse

    span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, len(results))

    for i, r in enumerate(results[:max_results]):
        title = (
            getattr(r, "title", str(r))[:200] if hasattr(r, "title") else str(r)[:200]
        )
        link = getattr(r, "link", "") if hasattr(r, "link") else ""
        snippet_len = len(getattr(r, "snippet", "")) if hasattr(r, "snippet") else 0

        # Extract domain
        domain = ""
        if link:
            try:
                parsed = urlparse(link)
                domain = parsed.hostname or ""
            except Exception:
                pass

        # Base attributes
        attrs = {
            RESULT_TITLE: title,
            RESULT_URL: link,
            "result.snippet_length": snippet_len,
            RESULT_POSITION: i + 1,
            RESULT_HAS_SNIPPET: str(snippet_len > 0).lower(),
            RESULT_DOMAIN: domain,
        }

        # RRF details if available
        if include_rrf_details:
            rrf_score = getattr(r, "score", None)
            providers = getattr(r, "providers", None)
            provider_count = len(providers) if providers else 0

            if rrf_score is not None:
                attrs[RESULT_RRF_SCORE] = round(float(rrf_score), 4)
                # Record individual score for distribution analysis
                record_rrf_score(float(rrf_score), i + 1)

            if providers is not None:
                attrs[RESULT_PROVIDER_SOURCES] = str(providers)
                attrs[RESULT_PROVIDER_COUNT] = provider_count

        span.add_event(f"result.{i}", attributes=attrs)

    if len(results) > max_results:
        span.add_event(
            "results_truncated",
            attributes={"total_results": len(results)},
        )


def add_query_rewrite_variants_to_span(
    span: trace.Span,
    variants: list[Any],
) -> None:
    """Add query rewrite variants to span as events.

    Each variant becomes a span event visible in Grafana trace view.

    Args:
        span: The span to add events to
        variants: List of variant objects with type and text attributes
    """
    for i, v in enumerate(variants[:5]):  # Limit to 5 variants
        variant_type = getattr(v, "type", "unknown")
        variant_text = getattr(v, "text", getattr(v, "query", str(v)))

        span.add_event(
            f"rewrite.variant.{i}",
            attributes={
                REWRITE_VARIANT_TYPE: variant_type,
                REWRITE_VARIANT_TEXT: variant_text[:200],
            },
        )


def add_rrf_merge_details_to_span(
    span: trace.Span,
    provider_counts: dict[str, int],
    discarded_urls: list[str],
    overlapping_urls: list[str],
) -> None:
    """Add RRF merge details to span as events.

    Args:
        span: The span to add events to
        provider_counts: Dict of provider_name -> result count in final output
        discarded_urls: URLs that were discarded as duplicates
        overlapping_urls: URLs that appeared in multiple provider lists
    """
    # Provider contribution summary
    for provider, count in provider_counts.items():
        span.add_event(
            f"rrf.provider.{provider}",
            attributes={
                PROVIDER_NAME: provider,
                RRF_PROVIDER_CONTRIBUTION: count,
            },
        )

    # Discard summary
    span.add_event(
        "rrf.discards",
        attributes={
            RRF_DISCARDED_COUNT: len(discarded_urls),
            "rrf.discarded_urls_sample": str(discarded_urls[:3])
            if discarded_urls
            else "",
        },
    )

    # Overlap summary
    span.add_event(
        "rrf.overlap",
        attributes={
            "rrf.overlapping_count": len(overlapping_urls),
            "rrf.overlap_urls_sample": str(overlapping_urls[:3])
            if overlapping_urls
            else "",
        },
    )


def add_rerank_scores_to_span(
    span: trace.Span,
    scores: list[float],
    stage: str,
) -> None:
    """Add rerank relevance scores to span as events.

    Args:
        span: The span to add events to
        scores: List of relevance scores (0.0-1.0)
        stage: "bi_encoder" or "jina"
    """
    # Add top scores as individual events
    for i, score in enumerate(scores[:10]):
        span.add_event(
            f"rerank.score.{i}",
            attributes={
                RERANK_STAGE: stage,
                RERANK_RELEVANCE_SCORE: round(score, 4),
                RESULT_POSITION: i + 1,
            },
        )

    # Summary event
    if scores:
        span.add_event(
            f"rerank.{stage}.summary",
            attributes={
                RERANK_STAGE: stage,
                "rerank.min_score": round(min(scores), 4),
                "rerank.max_score": round(max(scores), 4),
                "rerank.avg_score": round(sum(scores) / len(scores), 4),
                RERANK_INPUT_COUNT: len(scores),
            },
        )


def set_span_error(
    span: trace.Span, error: Exception, error_type: str | None = None
) -> None:
    """Record exception on span with proper error attributes."""
    span.record_exception(error)
    span.set_attribute(ERROR_TYPE, error_type or type(error).__name__)
    span.set_status(trace.StatusCode.ERROR, str(error)[:200])


def set_span_success(span: trace.Span, result_count: int | None = None) -> None:
    """Mark span as successful."""
    span.set_status(trace.StatusCode.OK)
    if result_count is not None:
        span.set_attribute(SEARCH_NUM_RESULTS_RETURNED, result_count)


# ============================================================================
# PUBLIC API EXPORTS
# ============================================================================

__all__ = [
    # Initialization
    "init_telemetry",
    "get_tracer",
    "get_meter",
    "get_search_total_metric",
    # Basic metrics recording
    "record_provider_call",
    "record_search_request",
    "record_merge",
    "record_cache_lookup",
    "record_mcp_tool_call",
    "record_content_resolution",
    "record_content_fallback",
    "record_content_error",
    # RRF merge metrics (NEW)
    "record_rrf_merge",
    "record_rrf_score",
    # Query rewrite metrics (NEW)
    "record_query_rewrite",
    # Reranking metrics (NEW)
    "record_rerank_stage",
    "record_diversity_removal",
    # Semantic cache metrics (NEW)
    "record_semantic_cache_lookup",
    # Circuit breaker metrics (NEW)
    "record_circuit_breaker_state",
    "record_circuit_breaker_event",
    # Tool-specific metrics (NEW)
    "record_gemini_search",
    "record_perplexity_search",
    "record_youtube_transcript",
    "record_youtube_search",
    "record_tool_details",
    # Query quality metrics (Phase 2)
    "record_query_length",
    "record_domain_diversity",
    # Span creation
    "create_search_span",
    "create_provider_span",
    "create_mcp_tool_span",
    "create_content_span",
    "create_merge_span",
    "create_query_rewrite_span",
    "create_rerank_span",
    "create_circuit_breaker_span",
    "create_cache_span",
    # Span enhancement
    "add_results_to_span",
    "add_query_rewrite_variants_to_span",
    "add_rrf_merge_details_to_span",
    "add_rerank_scores_to_span",
    "set_span_error",
    "set_span_success",
    # Semantic convention constants
    "HTTP_REQUEST_METHOD",
    "URL_FULL",
    "SERVER_ADDRESS",
    "SERVER_PORT",
    "HTTP_RESPONSE_STATUS_CODE",
    "HTTP_RESPONSE_BODY_SIZE",
    "ERROR_TYPE",
    "NETWORK_PROTOCOL_VERSION",
    "MCP_METHOD_NAME",
    "MCP_SERVER_NAME",
    "MCP_SESSION_ID",
    "MCP_RESOURCE_URI",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_SYSTEM",
    "RPC_SYSTEM",
    "RPC_JSONRPC_VERSION",
    "PROVIDER_NAME",
    "PROVIDER_STATUS",
    "PROVIDER_RESULT_COUNT",
    "PROVIDER_DURATION_MS",
    "PROVIDER_ERROR_TYPE",
    "SEARCH_QUERY",
    "SEARCH_NUM_RESULTS_REQUESTED",
    "SEARCH_NUM_RESULTS_RETURNED",
    "SEARCH_PROVIDERS_REQUESTED",
    "SEARCH_PROVIDERS_USED",
    "SEARCH_MERGE_ALGORITHM",
    "CACHE_TYPE",
    "CACHE_HIT",
    "CACHE_LOOKUP_DURATION_MS",
    "CACHE_SIMILARITY_SCORE",
    "CACHE_CONTENT_TYPE",
    "CACHE_TTL_SECONDS",
    "CACHE_SEARCH_TYPE",
    "CACHE_VECTOR_DISTANCE",
    "CONTENT_STAGE",
    "CONTENT_STATUS",
    "CONTENT_SIZE_BYTES",
    "CONTENT_URL",
    "CONTENT_WORD_COUNT",
    "CONTENT_EXTRACTION_METHOD",
    "CONTENT_FINAL_STAGE",
    "CONTENT_FALLBACK_COUNT",
    "RRF_INPUT_LISTS",
    "RRF_INPUT_TOTAL",
    "RRF_OUTPUT_TOTAL",
    "RRF_DISCARDED_COUNT",
    "RRF_OVERLAP_RATE",
    "RRF_PROVIDER_CONTRIBUTION",
    "RRF_PROVIDER_WEIGHT",
    "RRF_SCORE",
    "RRF_BEST_RANK",
    "RRF_PROVIDERS",
    "REWRITE_POLICY",
    "REWRITE_VARIANT_COUNT",
    "REWRITE_HAS_PRECISION_SIGNALS",
    "REWRITE_MODEL",
    "REWRITE_VARIANT_TYPE",
    "REWRITE_VARIANT_TEXT",
    "RERANK_STAGE",
    "RERANK_INPUT_COUNT",
    "RERANK_OUTPUT_COUNT",
    "RERANK_REMOVED_COUNT",
    "RERANK_RELEVANCE_SCORE",
    "RERANK_MODEL",
    "RERANK_DIVERSITY_THRESHOLD",
    "RERANK_SIMILARITY_SCORE",
    "RERANK_BI_ENCODER_SCORE",
    "CIRCUIT_STATE",
    "CIRCUIT_FAILURE_COUNT",
    "CIRCUIT_LAST_FAILURE_TIME",
    "CIRCUIT_EVENT",
    "CIRCUIT_FAILURE_THRESHOLD",
    "RESULT_POSITION",
    "RESULT_PROVIDER_COUNT",
    "RESULT_RRF_SCORE",
    "RESULT_PROVIDER_SOURCES",
    "RESULT_HAS_SNIPPET",
    "RESULT_DOMAIN",
    "RESULT_TITLE",
    "RESULT_URL",
    "GEMINI_GROUNDING_QUERIES",
    "GEMINI_GROUNDING_CHUNKS",
    "GEMINI_STRUCTURED_OUTPUT",
    "PERPLEXITY_DEPTH",
    "PERPLEXITY_SOURCE_COUNT",
    "PERPLEXITY_MODEL",
    "YOUTUBE_FORMAT",
    "YOUTUBE_LANGUAGE",
    "YOUTUBE_IS_TRANSLATED",
    "YOUTUBE_DURATION_SECONDS",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "STATUS_TIMEOUT",
    "PROVIDER_SEARXNG",
    "PROVIDER_DDG",
    "PROVIDER_GEMINI",
    "PROVIDER_TAVILY",
    "PROVIDER_BRAVE",
    "PROVIDER_JINA",
    "CONTENT_STAGE_STACKEXCHANGE",
    "CONTENT_STAGE_GITHUB",
    "CONTENT_STAGE_WIKIPEDIA",
    "CONTENT_STAGE_ARXIV",
    "CONTENT_STAGE_HTTP_EXTRACT",
    "CONTENT_STAGE_NODRIVER",
]
