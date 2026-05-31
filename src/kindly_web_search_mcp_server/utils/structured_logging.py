"""Structured logging with OpenTelemetry trace context injection for Grafana Loki.

Loki expects JSON logs with trace_id/span_id fields for log-trace correlation.
This module configures structlog to:
1. Output JSON format (Loki native format)
2. Inject trace_id and span_id from current OTEL span context
3. Bridge existing Python logging to structlog

USAGE:
    Set KINDLY_STRUCTURED_LOGGING=true to enable JSON output.
    Default: plain text logs (for local development)

GRAFANA LOGQL CORRELATION:
    {job="web-search-mcp"} | json | trace_id="<TRACE_ID>"
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from opentelemetry import trace

_STRUCTLOG_STREAM_HANDLER_ATTR = "_kindly_structlog_stream_handler"


def add_trace_context(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor: inject trace_id/span_id from current OTEL span.

    This enables Loki log-trace correlation:
    - trace_id: 32-char hex (OTEL standard, matches Tempo)
    - span_id: 16-char hex

    Format matches Grafana Tempo expectations for correlation.
    """
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_structlog(json_output: bool = True) -> None:
    """Configure structlog for Loki-compatible JSON logging with trace context.

    Args:
        json_output: True for JSON (Loki), False for plain text (local dev)
    """
    # Determine log level from environment
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and log_level > logging.INFO:
        log_level = logging.INFO

    # Shared processors for both modes
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_context,  # OTEL trace injection
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # JSON output for Loki
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),  # Loki expects JSON
        ]
    else:
        # Plain text for local development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=False),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging to use structlog while preserving any
    # pre-existing non-stream handlers such as the OpenTelemetry LoggingHandler.
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in root_logger.handlers[:]:
        if getattr(handler, _STRUCTLOG_STREAM_HANDLER_ATTR, False):
            root_logger.removeHandler(handler)

    # Emit local logs to stderr so stdio MCP traffic on stdout remains clean.
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _STRUCTLOG_STREAM_HANDLER_ATTR, True)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processors=processors))
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers
    noisy_loggers = (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "nodriver",
        "undetected_chromedriver",
    )
    for name in noisy_loggers:
        level = logging.ERROR if name == "asyncio" else logging.WARNING
        logging.getLogger(name).setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger with optional name binding.

    Args:
        name: Logger name (defaults to calling module name)

    Returns:
        Bound structlog logger with trace context injection
    """
    return structlog.get_logger(name)


# Convenience function for logging within spans
def log_with_span(
    event: str,
    **kwargs: Any,
) -> None:
    """Log an event with automatic trace context and optional attributes.

    Example:
        log_with_span("search.started", query="Python tutorial", num_results=10)
        # Output: {"event": "search.started", "query": "Python tutorial",
        #          "num_results": 10, "trace_id": "...", "span_id": "..."}
    """
    logger = get_logger()
    logger.info(event, **kwargs)
