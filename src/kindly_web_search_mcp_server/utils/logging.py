from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    """
    Configure logging defaults for both local runs and MCP stdio hosts.

    Goals:
    - Avoid noisy third-party logs during tool execution (especially `httpx` request logs).
    - Keep configuration idempotent so hosts can override it safely.
    """
    root = logging.getLogger()
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and level > logging.INFO:
        level = logging.INFO

    # Only set up basicConfig if nothing configured yet (common for scripts).
    if not root.handlers:
        logging.basicConfig(level=level)
    root.setLevel(level)

    # Silence common noisy libraries unless the host explicitly configures them.
    noisy_loggers = (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "nodriver",
        "undetected_chromedriver",
    )
    for name in noisy_loggers:
        # `asyncio` can emit noisy warnings about slow callbacks in some environments.
        level = logging.ERROR if name == "asyncio" else logging.WARNING
        logging.getLogger(name).setLevel(level)
