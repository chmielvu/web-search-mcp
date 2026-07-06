"""Telemetry internal helpers, exporters, and endpoint resolution."""
from __future__ import annotations
from typing import Any

import logging
import os
from importlib.metadata import PackageNotFoundError, version as _package_version

import httpx

from .attributes import OPENINFERENCE_SPAN_KIND
from typing import TYPE_CHECKING

from .constants import _OTEL_SDK_AVAILABLE

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
elif _OTEL_SDK_AVAILABLE:
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
else:
    SpanProcessor = object  # type: ignore[assignment, misc]
    BatchSpanProcessor = Any  # type: ignore[misc]

def _otel_sdk_version() -> str:
    """Resolve the installed OpenTelemetry SDK version.

    Reads from an explicit env override first, then package metadata, falling
    back to ``"unknown"`` when neither is available.
    """
    override = os.environ.get("OTEL_SDK_VERSION")
    if override:
        return override
    for dist in ("opentelemetry-sdk", "opentelemetry-api"):
        try:
            return _package_version(dist)
        except PackageNotFoundError:
            continue
    return "unknown"


def _service_version() -> str:
    try:
        return _package_version("web-search-mcp")
    except PackageNotFoundError:
        return os.environ.get("WEB_SEARCH_MCP_VERSION", "dev")


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
    return headers


def build_hf_space_headers(*, hf_token: str = "") -> dict[str, str]:
    """Build Authorization headers for a private Hugging Face Space."""
    if not hf_token:
        return {}
    return {"Authorization": f"Bearer {hf_token.strip()}"}


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if not raw.strip():
        return headers
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
        elif ":" in part:
            key, value = part.split(":", 1)
        else:
            continue
        headers[key.strip()] = value.strip().replace("%20", " ")
    return headers


def _resolve_otlp_signal_endpoint(
    signal: str,
    *,
    base_endpoint: str | None,
) -> str | None:
    signal_key = signal.upper()
    per_signal = os.environ.get(f"OTEL_EXPORTER_OTLP_{signal_key}_ENDPOINT", "").strip()
    if per_signal:
        return per_signal.rstrip("/")

    endpoint = (base_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")).strip()
    if not endpoint:
        return None

    normalized = endpoint.rstrip("/")
    signal_suffix = f"/v1/{signal}"
    if normalized.endswith(signal_suffix):
        return normalized
    return f"{normalized}{signal_suffix}"


def _resolve_otlp_headers(
    *,
    signal: str,
    grafana_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = _parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""))
    headers.update(
        _parse_otlp_headers(os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_HEADERS", ""))
    )
    if grafana_headers:
        headers.update(grafana_headers)
    return headers


def _resolve_phoenix_headers() -> dict[str, str]:
    """Resolve OTLP headers for the Phoenix Space exporter."""
    headers = _parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""))
    headers.update(_parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "")))
    if "Authorization" not in headers:
        headers.update(
            build_hf_space_headers(
                hf_token=(
                    os.environ.get("HF_TOKEN", "") or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "")
                )
            )
        )
    return headers


class _LoggingExporterProxy:
    """Log exporter failures instead of letting them disappear in debug noise."""

    def __init__(self, exporter: Any, *, signal_name: str) -> None:
        self._exporter = exporter
        self._signal_name = signal_name

    def export(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._exporter.export(*args, **kwargs)
        except Exception as exc:
            logging.warning("%s OTLP export failed: %s", self._signal_name, exc, exc_info=True)
            raise

        result_name = getattr(result, "name", "")
        if result_name and result_name.upper() not in {"SUCCESS", "SUCCESS_WITH_WARNINGS"}:
            logging.warning(
                "%s OTLP export returned %s",
                self._signal_name,
                result_name,
            )
        return result

    def shutdown(self, **kwargs: Any) -> Any:
        return self._exporter.shutdown(**kwargs)

    def force_flush(self, **kwargs: Any) -> Any:
        return self._exporter.force_flush(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._exporter, name)


class _OpenInferenceFilteringSpanProcessor(SpanProcessor):
    """Forward only OpenInference-classified spans to the wrapped processor."""

    def __init__(self, processor: BatchSpanProcessor) -> None:
        self._processor = processor

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        return None

    def on_end(self, span: Any) -> None:
        attributes = getattr(span, "attributes", None) or {}
        if attributes.get(OPENINFERENCE_SPAN_KIND):
            self._processor.on_end(span)

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis=timeout_millis)


def _probe_otlp_endpoint(endpoint: str, headers: dict[str, str], *, signal: str) -> bool:
    """Return False when the configured endpoint looks like an HTML page."""

    try:
        with httpx.Client(timeout=3.0, follow_redirects=True) as client:
            response = client.get(
                endpoint,
                headers={**headers, "Accept": "text/html,application/json,*/*"},
            )
    except Exception as exc:
        logging.warning(
            "OTLP %s endpoint probe could not reach %s: %s",
            signal,
            endpoint,
            exc,
        )
        return True

    content_type = response.headers.get("content-type", "").casefold()
    body = response.text.lstrip().casefold() if response.text else ""
    if "text/html" in content_type or body.startswith("<!doctype html") or body.startswith("<html"):
        logging.warning(
            "OTLP %s endpoint looks like HTML (%s %s); telemetry export disabled",
            signal,
            response.status_code,
            content_type or "unknown",
        )
        return False
    return True

__all__ = [
    "_LoggingExporterProxy",
    "_OpenInferenceFilteringSpanProcessor",
    "_get_prometheus_metric_reader",
    "_otel_sdk_version",
    "_probe_otlp_endpoint",
    "_resolve_otlp_headers",
    "_resolve_otlp_signal_endpoint",
    "_resolve_phoenix_headers",
    "_service_version",
    "build_grafana_cloud_headers",
    "build_hf_space_headers",
]
