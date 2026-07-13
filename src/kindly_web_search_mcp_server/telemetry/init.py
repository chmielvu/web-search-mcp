"""Phoenix-first OpenTelemetry lifecycle."""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings

LOGGER = logging.getLogger(__name__)
_provider: Any | None = None
_initialized = False
_shutdown = False


def sanitize_httpx_request_span(span: Any, request: Any) -> None:
    """Keep HTTP client attributes useful without exporting query data."""
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    url = request.url
    safe_url = f"{url.scheme}://{url.host}{url.path}"
    span.set_attribute("url.full", safe_url)
    span.set_attribute("server.address", url.host or "")


def init_telemetry(
    service_name: str = "web-search-mcp",
    service_version: str | None = None,
    prometheus_port: int | None = None,
) -> None:
    del service_name, service_version, prometheus_port
    global _initialized, _provider, _shutdown
    if _initialized:
        return
    if not settings.otel_enabled:
        LOGGER.info("OTEL_ENABLED=false — telemetry initialization skipped")
        return
    from openinference.instrumentation.litellm import LiteLLMInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from phoenix.otel import register

    _provider = register(
        project_name=settings.phoenix_project_name,
        endpoint=settings.phoenix_collector_endpoint,
        headers=settings.phoenix_client_headers,
        batch=True,
        auto_instrument=False,
        set_global_tracer_provider=True,
    )
    LiteLLMInstrumentor().instrument(tracer_provider=_provider)
    HTTPXClientInstrumentor().instrument(
        tracer_provider=_provider,
        async_request_hook=sanitize_httpx_request_span,
    )
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
    "sanitize_httpx_request_span",
    "shutdown_telemetry",
]
