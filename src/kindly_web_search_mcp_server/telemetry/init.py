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
        except (OSError, AttributeError):
            pass
        try:
            yield
        finally:
            if has_dup and saved_fd != -1:
                try:
                    os.dup2(saved_fd, 1)
                except OSError:
                    pass
                try:
                    os.close(saved_fd)
                except OSError:
                    pass
    finally:
        sys.stdout = old_stdout
        if old_sys_stdout is not None:
            sys.__stdout__ = old_sys_stdout


def init_telemetry(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    """Initialize OTLP tracing with LLM-only span filtering.

    Sets up a ``TracerProvider`` with a ``BatchSpanProcessor`` wrapping an
    ``OTLPSpanExporter`` directly — no ``phoenix.otel`` import.  The Phoenix
    library's ``register()`` convenience pulls in ``sklearn``/``scipy`` C
    extensions that hold the GIL for 25-30 s; under MCP stdio that GIL hold
    delays transport startup past client connect timeouts.
    """
    global _initialized, _provider, _shutdown
    if _initialized:
        return
    if not settings.otel_enabled:
        LOGGER.info("OTEL_ENABLED=false — telemetry initialization skipped")
        _initialized = True
        return

    with _redirect_stdout_to_stderr():
        from opentelemetry import trace, metrics as otel_metrics
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPOTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        from ._internal import (
            _OpenInferenceFilteringSpanExporter,
            _resolve_otlp_headers,
            _resolve_otlp_signal_endpoint,
        )

        svc_name = service_name or settings.otel_service_name
        attrs: dict[str, str] = {"service.name": svc_name}
        if service_version:
            attrs["service.version"] = service_version
        resource = Resource.create(attrs)

        provider = TracerProvider(resource=resource)

        endpoint = _resolve_otlp_signal_endpoint("traces", base_endpoint=None)
        headers = _resolve_otlp_headers(signal="traces")

        if endpoint:
            exporter = HTTPOTLPSpanExporter(
                endpoint=endpoint,
                headers=headers,
            )
            filtered_exporter = _OpenInferenceFilteringSpanExporter(exporter)
            processor = BatchSpanProcessor(filtered_exporter)
            provider.add_span_processor(processor)
            LOGGER.info(
                "OTLP span exporter configured (endpoint=%s, LLM+RERANKER only)",
                endpoint,
            )
        else:
            LOGGER.warning("No OTLP traces endpoint resolved; spans will be dropped")

        trace.set_tracer_provider(provider)
        _provider = provider
        _initialized = True
        _shutdown = False

        # --- Metrics export to Grafana Cloud ---
        grafana_metrics_endpoint = getattr(settings, "grafana_cloud_otlp_endpoint", "")
        metrics_readers: list[Any] = []

        if grafana_metrics_endpoint:
            try:
                metrics_reader = PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=grafana_metrics_endpoint,
                    ),
                )
                metrics_readers.append(metrics_reader)
                LOGGER.info(
                    "OTLP metrics exporter configured (endpoint=%s)",
                    grafana_metrics_endpoint,
                )
            except Exception as exc:
                LOGGER.warning("Failed to configure OTLP metrics exporter: %s", exc)

        if metrics_readers:
            meter_provider = MeterProvider(
                metric_readers=metrics_readers,
                resource=resource,
            )
            otel_metrics.set_meter_provider(meter_provider)
            LOGGER.info("MeterProvider configured with %d readers", len(metrics_readers))

        # OpenAI auto-instrumentation is optional and does not import
        # sklearn/scipy; load it lazily on first use instead of at init.
        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor

            OpenAIInstrumentor().instrument(tracer_provider=provider)
        except Exception:
            LOGGER.debug("OpenAI auto-instrumentation skipped", exc_info=True)


def init_telemetry_background(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    """Compatibility name; initialization launches a lazy background thread."""
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
