"""Telemetry internal constants and availability flags."""

from __future__ import annotations

import importlib.util
import logging
import os

_OTEL_SDK_AVAILABLE = importlib.util.find_spec("opentelemetry.sdk") is not None
LOGS_AVAILABLE = importlib.util.find_spec("opentelemetry.sdk._logs") is not None

_initialized: bool = False
_otel_logging_handler: logging.Handler | None = None
_OTLP_EXPORT_TIMEOUT_SECONDS: int = int(os.environ.get("OTLP_EXPORT_TIMEOUT_SECONDS", "10"))

__all__ = [
    "LOGS_AVAILABLE",
]
