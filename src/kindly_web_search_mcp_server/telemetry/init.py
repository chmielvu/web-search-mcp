"""Phoenix-first OpenTelemetry lifecycle — LLM-only spans.

Only spans with ``openinference.span.kind`` of ``LLM`` are forwarded to
Phoenix. Generic pipeline spans (CHAIN, RETRIEVER, TOOL, RERANKER) and
raw HTTP instrumentation are dropped.
"""

from __future__ import annotations

import os
import contextlib
import sys
import logging
from typing import Any

from ..settings import settings

LOGGER = logging.getLogger(__name__)
_provider: Any | None = None
_initialized = False
_shutdown = False


@contextlib.contextmanager
def _redirect_stdout_to_stderr():
    old_stdout = sys.stdout
    old_sys_stdout = getattr(sys, "__stdout__", None)
    sys.stdout = sys.stderr
    sys.__stdout__ = sys.stderr
    has_dup = False
    saved_fd = -1
    try:
        try:
            saved_fd = os.dup(1)
            os.dup2(2, 1)
            has_dup = True
        except Exception:
            pass
        try:
            yield
        finally:
            if has_dup and saved_fd != -1:
                os.dup2(saved_fd, 1)
                os.close(saved_fd)
    finally:
        sys.stdout = old_stdout
        if old_sys_stdout is not None:
            sys.__stdout__ = old_sys_stdout


def init_telemetry(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    """Initialize Phoenix tracing with LLM-only span filtering."""
    del service_name, service_version, prometheus_port
    global _initialized, _provider, _shutdown
    if _initialized:
        return
    if not settings.otel_enabled:
        LOGGER.info("OTEL_ENABLED=false — telemetry initialization skipped")
        return
    with _redirect_stdout_to_stderr():
        from ._internal import _OpenInferenceFilteringSpanExporter
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from phoenix.otel import register

        _provider = register(
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_collector_endpoint,
            headers=settings.phoenix_client_headers,
            batch=True,
            auto_instrument=False,
            set_global_tracer_provider=True,
        )

        # Wrap the OTLP exporter so only LLM/RERANKER spans reach Phoenix.
        active = _provider._active_span_processor
        # register() creates a single BatchSpanProcessor; unwrap its exporter.
        exporter = getattr(active, "span_exporter", None)
        if exporter is not None:
            active.span_exporter = _OpenInferenceFilteringSpanExporter(exporter)
            LOGGER.info("Phoenix LLM-only filter applied (LLM + RERANKER only)")
        OpenAIInstrumentor().instrument(tracer_provider=_provider)
        _initialized = True
    _shutdown = False


def init_telemetry_background(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    """Compatibility name; initialization is deliberately synchronous."""
    init_telemetry(service_name, service_version, prometheus_port)


def shutdown_telemetry(timeout_millis: int = 10_000) -> None:
    global _shutdown
    if _provider is None or _shutdown:
        return
    _provider.force_flush(timeout_millis=timeout_millis)
    _provider.shutdown()
    _shutdown = True


__all__ = [
    "init_telemetry",
    "init_telemetry_background",
    "shutdown_telemetry",
]
