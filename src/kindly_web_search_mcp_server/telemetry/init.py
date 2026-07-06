"""OpenTelemetry initialization for the web-search-mcp server."""
from __future__ import annotations

import json
import logging
import os
import platform
import socket
import threading
from typing import Any
from urllib.parse import urlparse

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

try:
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
except ImportError:  # pragma: no cover
    set_logger_provider = None
    LoggerProvider = None
    BatchLogRecordProcessor = None
    OTLPLogExporter = None
    LoggingHandler = None
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import (
    Resource,
    SERVICE_NAME,
    SERVICE_NAMESPACE,
    SERVICE_VERSION,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from .constants import (
    LOGS_AVAILABLE,
    _OTEL_SDK_AVAILABLE,
    _OTLP_EXPORT_TIMEOUT_SECONDS,
    _initialized,
    _otel_logging_handler,
)
from ._internal import (
    _LoggingExporterProxy,
    _OpenInferenceFilteringSpanProcessor,
    _get_prometheus_metric_reader,
    _otel_sdk_version,
    _probe_otlp_endpoint,
    _resolve_otlp_headers,
    _resolve_otlp_signal_endpoint,
    _resolve_phoenix_headers,
    _service_version,
    build_grafana_cloud_headers,
)

# ============================================================================
# INITIALIZATION
# ============================================================================




def init_telemetry(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    """Initialize OpenTelemetry SDK with Grafana Cloud export.

    This MUST be called at server startup before any HTTP operations.
    Auto-instruments httpx for automatic HTTP span creation.

    Preferred configuration (standard OTEL):
        OTEL_EXPORTER_OTLP_ENDPOINT + OTEL_EXPORTER_OTLP_HEADERS (Basic auth)

    Windows / convenience path (recommended in this repo):
        GRAFANA_CLOUD_INSTANCE_ID + GRAFANA_CLOUD_API_KEY + GRAFANA_CLOUD_OTLP_ENDPOINT
        (or the * equivalents). These are automatically turned into the
        correct Authorization header.

    Sampling is controlled via OTEL_SAMPLING_RATIO (default 0.15 in Settings).

    Optional:
        PROMETHEUS_PORT / PROMETHEUS_ENABLED
        OTEL_SERVICE_NAME / OTEL_SERVICE_NAMESPACE
        DEPLOYMENT_ENV

    Set OTEL_ENABLED=false to skip telemetry initialization entirely.
    Set OTLP_EXPORT_TIMEOUT_SECONDS to control per-exporter connect timeout
    (default 10s).  This prevents the ~70s hang when the OTLP endpoint is unreachable.
    """
    global _initialized
    if _initialized:
        logging.debug("Telemetry already initialized, skipping")
        return

    if os.environ.get("OTEL_ENABLED", "true").lower() not in (
        "true",
        "1",
        "yes",
    ):
        logging.info("OTEL_ENABLED=false — telemetry initialization skipped")
        return

    # Allow overrides from env
    service_name = os.environ.get("OTEL_SERVICE_NAME", service_name)
    service_version = os.environ.get("OTEL_SERVICE_VERSION", service_version or _service_version())

    try:
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
                endpoint = gcloud_endpoint or "https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
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

        trace_endpoint = _resolve_otlp_signal_endpoint("traces", base_endpoint=endpoint)
        trace_headers = _resolve_otlp_headers(signal="traces", grafana_headers=headers)
        metrics_endpoint = _resolve_otlp_signal_endpoint("metrics", base_endpoint=endpoint)
        metrics_headers = _resolve_otlp_headers(signal="metrics", grafana_headers=headers)
        logs_endpoint = _resolve_otlp_signal_endpoint("logs", base_endpoint=endpoint)
        logs_headers = _resolve_otlp_headers(signal="logs", grafana_headers=headers)

        if trace_endpoint and not _probe_otlp_endpoint(
            trace_endpoint, trace_headers, signal="traces"
        ):
            return

        # Allow port override from env
        if prometheus_port is None:
            port_env = os.environ.get("PROMETHEUS_PORT", "0")
            prometheus_port = int(port_env) if port_env else None

        # === RESOURCE (Grafana Cloud Application Observability) ===
        hostname = socket.gethostname()
        pid = os.getpid()

        resource_attrs = {
            SERVICE_NAME: service_name,
            SERVICE_NAMESPACE: service_name,
            SERVICE_VERSION: service_version,
            "service.instance.id": f"{hostname}-{pid}",
            "deployment.environment": os.environ.get(
                "DEPLOYMENT_ENV",
                os.environ.get("OTEL_ENVIRONMENT", "development"),
            ),
            "host.name": hostname,
            "host.arch": "amd64",
            "host.os.type": os.environ.get("HOST_OS_TYPE", "windows"),
            "process.pid": pid,
            "process.executable.name": "python",
            "process.runtime.name": "cpython",
            "process.runtime.version": os.environ.get("PYTHON_VERSION", platform.python_version()),
            "telemetry.sdk.language": "python",
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.version": _otel_sdk_version(),
        }
        resource = Resource.create(resource_attrs)

        # === SAMPLING (head-based, configurable) ===
        sampling_ratio = float(
            os.environ.get(
                "OTEL_SAMPLING_RATIO",
                os.environ.get("OTEL_TRACES_SAMPLER_ARG", "0.15"),
            )
        )
        sampler = ParentBased(TraceIdRatioBased(sampling_ratio))

        # === TRACES ===
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)

        trace_exporter = _LoggingExporterProxy(
            OTLPSpanExporter(
                endpoint=trace_endpoint,
                headers=trace_headers,
                timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
            ),
            signal_name="traces",
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                trace_exporter,  # type: ignore[arg-type]
                max_queue_size=2048,
                schedule_delay_millis=5000,
                max_export_batch_size=512,
            )
        )
        trace.set_tracer_provider(tracer_provider)

        # === PHOENIX OTLP (Arize Phoenix on HF Spaces) ===
        try:
            from ..settings import settings as s

            phoenix_endpoint = getattr(s, "phoenix_collector_endpoint", "")
            if phoenix_endpoint:
                phoenix_exporter = _LoggingExporterProxy(
                    OTLPSpanExporter(
                        endpoint=phoenix_endpoint,
                        headers=_resolve_phoenix_headers(),
                        timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
                    ),
                    signal_name="phoenix",
                )
                phoenix_processor = BatchSpanProcessor(
                    phoenix_exporter,  # type: ignore[arg-type]
                    max_queue_size=2048,
                    schedule_delay_millis=5000,
                    max_export_batch_size=512,
                )
                tracer_provider.add_span_processor(
                    _OpenInferenceFilteringSpanProcessor(phoenix_processor)
                )
                logging.info(
                    "Phoenix OTLP span processor added (OpenInference-only, endpoint: %s)",
                    phoenix_endpoint,
                )
        except Exception as exc:  # pragma: no cover - best effort
            logging.warning("Failed to add Phoenix OTLP processor: %s", exc)

        # === AUTO-INSTRUMENTATION ===
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            logging.info("HTTPX auto-instrumentation enabled - all HTTP calls traced")
        except ImportError:
            logging.info(
                "opentelemetry-instrumentation-httpx not installed - "
                "HTTP calls not auto-traced. Install: uv pip install opentelemetry-instrumentation-httpx"
            )

        try:
            from openinference.instrumentation.litellm import LiteLLMInstrumentor

            LiteLLMInstrumentor().instrument(skip_dep_check=True)
            logging.info("LiteLLM auto-instrumentation enabled - all LLM calls traced to Phoenix")
        except ImportError:
            logging.info(
                "openinference-instrumentation-litellm not installed - "
                "LLM calls not auto-traced. Install: uv pip install openinference-instrumentation-litellm"
            )

        # === METRICS ===
        metric_readers: list[Any] = []

        prometheus_metric_reader = _get_prometheus_metric_reader() if prometheus_port else None
        if prometheus_port and prometheus_metric_reader is not None:
            prometheus_reader = prometheus_metric_reader(port=prometheus_port)
            metric_readers.append(prometheus_reader)
            logging.info(f"Prometheus metrics endpoint started on port {prometheus_port}")
        else:
            metric_exporter = _LoggingExporterProxy(
                OTLPMetricExporter(
                    endpoint=metrics_endpoint,
                    headers=metrics_headers,
                    timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
                ),
                signal_name="metrics",
            )
            metric_reader = PeriodicExportingMetricReader(
                exporter=metric_exporter,  # type: ignore[arg-type]
                export_interval_millis=60000,
            )
            metric_readers.append(metric_reader)

        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
        )
        metrics.set_meter_provider(meter_provider)

        # === LOGS (experimental) ===
        if (
            LOGS_AVAILABLE
            and OTLPLogExporter is not None
            and LoggerProvider is not None
            and BatchLogRecordProcessor is not None
            and set_logger_provider is not None
            and LoggingHandler is not None
        ):
            try:
                log_exporter = _LoggingExporterProxy(
                    OTLPLogExporter(
                        endpoint=logs_endpoint,
                        headers=logs_headers,
                        timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
                    ),
                    signal_name="logs",
                )
                logger_provider = LoggerProvider(resource=resource)
                logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))  # type: ignore[arg-type]
                set_logger_provider(logger_provider)

                global _otel_logging_handler
                if _otel_logging_handler is None:
                    _otel_logging_handler = LoggingHandler(
                        level=logging.NOTSET,
                        logger_provider=logger_provider,
                    )
                    setattr(_otel_logging_handler, "_kindly_otlp_handler", True)
                    logging.getLogger().addHandler(_otel_logging_handler)

                logging.info("OTLP log export enabled - standard logging bridged to OpenTelemetry")
            except Exception as e:
                logging.warning(f"Failed to initialize log export: {e}")

        # Configure structlog for Loki JSON format with trace context injection.
        try:
            from ..utils.structured_logging import configure_structlog

            json_logs = os.environ.get("STRUCTURED_LOGGING", "").lower() in (
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
        logging.info(f"OpenTelemetry initialized: service={service_name}, endpoint={endpoint}")
        endpoint_url = urlparse(endpoint)
        logging.info(
            json.dumps(
                {
                    "event": "telemetry.startup",
                    "service_name": service_name,
                    "service_namespace": service_name,
                    "service_version": service_version,
                    "deployment_environment": os.environ.get("DEPLOYMENT_ENV", "development"),
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
    except Exception as exc:
        logging.warning(
            "OpenTelemetry initialization failed (server will continue without telemetry): %s",
            exc,
        )


def init_telemetry_background(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> threading.Thread:
    """Run init_telemetry in a daemon thread so it never blocks startup.

    Use this when the OTLP endpoint might be unreachable (e.g. local dev,
    CI, air-gapped networks) and you need the MCP server / CLI to become
    responsive immediately.  Telemetry becomes available once the background
    init finishes; spans emitted before that point are silently dropped.

    Returns the daemon thread so callers can ``join()`` if they wish.
    """
    thread = threading.Thread(
        target=init_telemetry,
        args=(service_name, service_version, prometheus_port),
        name="otel-init",
        daemon=True,
    )
    thread.start()
    logging.info("Telemetry init dispatched to background thread")
    return thread

__all__ = [
    "init_telemetry",
    "init_telemetry_background",
]
