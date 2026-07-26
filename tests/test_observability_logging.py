from __future__ import annotations

import json
import logging
import os
import sys

from kindly_web_search_mcp_server.utils.observability import (
    emit_observability_event,
    emit_tool_observability_event,
    preview_text,
)
from kindly_web_search_mcp_server.utils.logging import configure_logging
from kindly_web_search_mcp_server.utils.structured_logging import configure_structlog


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_emit_observability_event_includes_hard_values(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_MAX_TEXT_CHARS", "200")

    logger = logging.getLogger("test.observability")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = _ListHandler()
    logger.addHandler(handler)

    emit_observability_event(
        logger,
        "tool.grok_search.response",
        query="fastmcp middleware best practices",
        answer="Concrete answer text",
        sources=["https://example.com/a", "https://example.com/b"],
    )

    assert len(handler.records) == 1
    record = handler.records[0]
    payload = json.loads(record.getMessage())

    assert payload["event"] == "tool.grok_search.response"
    assert payload["query"] == "fastmcp middleware best practices"
    assert payload["answer"] == "Concrete answer text"
    assert payload["sources"] == ["https://example.com/a", "https://example.com/b"]
    assert record.obs_event == "tool.grok_search.response"
    assert record.obs_query == "fastmcp middleware best practices"


def test_emit_tool_observability_event_adds_fingerprint_and_bounds_payload(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_MAX_TEXT_CHARS", "50")

    logger = logging.getLogger("test.tool_observability")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = _ListHandler()
    logger.addHandler(handler)

    emit_tool_observability_event(
        logger,
        "get_content",
        "response",
        page_content="x" * 120,
        metadata={
            "title": "Example",
            "description": "Long description",
            "canonical_url": "https://example.com",
            "domain": "example.com",
            "extra": "ignored",
        },
        links=[
            {
                "url": "https://example.com/next",
                "text": "Next",
                "domain": "example.com",
                "internal": True,
            }
        ],
        error={"code": "timeout", "message": "too slow"},
    )

    assert len(handler.records) == 1
    payload = json.loads(handler.records[0].getMessage())
    assert payload["event"] == "tool.get_content.response"
    assert payload["page_content"].endswith("…")
    assert payload["metadata"]["title"] == "Example"
    assert payload["links"][0]["url"] == "https://example.com/next"
    assert payload["error"]["code"] == "timeout"

    emit_tool_observability_event(
        logger,
        "get_content",
        "request",
        url="https://example.com",
        char_length=20,
    )
    assert len(handler.records) == 2
    request_payload = json.loads(handler.records[1].getMessage())
    assert request_payload["event"] == "tool.get_content.request"
    assert "request_fingerprint" in request_payload
    assert request_payload["request_fingerprint"]
    assert handler.records[1].obs_event == "tool.get_content.request"


def test_preview_text_truncates() -> None:
    value = "x" * 12
    assert preview_text(value, limit=10) == ("x" * 10) + "…"


def test_emit_tool_observability_event_persists_correlated_typed_rows(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def capture(*, db_path=None, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.duckdb_store.insert_tool_call_event",
        capture,
    )
    logger = logging.getLogger("test.typed_tool_observability")
    logger.handlers = []
    logger.addHandler(logging.NullHandler())

    emit_tool_observability_event(
        logger,
        "web_search",
        "request",
        query="typed telemetry",
        Authorization="Bearer should-not-persist",
    )
    emit_tool_observability_event(
        logger,
        "web_search",
        "response",
        query="typed telemetry",
        results=[],
        duration_ms=12.5,
    )

    assert len(captured) == 2
    assert captured[0]["tool_call_id"] == captured[1]["tool_call_id"]
    assert captured[0]["status"] == "started"
    assert captured[1]["status"] == "empty"
    assert captured[1]["duration_ms"] == 12.5
    assert "authorization" not in str(captured[0]["payload_json"]).lower()


def test_configure_structlog_preserves_existing_handlers(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    try:
        root.handlers = []
        sentinel = _ListHandler()
        root.addHandler(sentinel)

        configure_structlog(json_output=True)

        assert sentinel in root.handlers
        structlog_handlers = [
            handler
            for handler in root.handlers
            if getattr(handler, "_otel_structlog_stream_handler", False)
        ]
        assert len(structlog_handlers) == 1
        assert getattr(structlog_handlers[0], "stream", None) is sys.stderr
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
        os.environ.pop("LOG_LEVEL", None)


def test_configure_logging_applies_info_level_with_existing_handlers(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    try:
        root.handlers = []
        sentinel = _ListHandler()
        root.addHandler(sentinel)
        root.setLevel(logging.WARNING)

        configure_logging()

        assert sentinel in root.handlers
        assert root.level == logging.INFO
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_configure_logging_accepts_an_explicit_level(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    try:
        root.handlers = []
        sentinel = _ListHandler()
        root.addHandler(sentinel)
        root.setLevel(logging.WARNING)

        configure_logging(level=logging.DEBUG)

        assert sentinel in root.handlers
        assert root.level == logging.DEBUG
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
        os.environ.pop("LOG_LEVEL", None)


def test_configure_logging_silences_tls_and_http2_chatter(monkeypatch) -> None:
    """rustls / h2 / hyper_util / cookie_store must be WARNING-or-higher by default
    so DEBUG runs don't drown in TLS handshake and HTTP/2 frame logs.
    """
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.utils.logging._install_process_logging",
        lambda: None,
    )

    previous_levels = {
        name: logging.getLogger(name).level
        for name in ("rustls", "h2", "hyper_util", "cookie_store")
    }

    try:
        configure_logging(level=logging.DEBUG)

        for name in ("rustls", "h2", "hyper_util", "cookie_store"):
            assert logging.getLogger(name).level >= logging.WARNING, (
                f"{name} logger must be silenced to WARNING-or-higher; "
                f"got level={logging.getLogger(name).level}"
            )
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)
        os.environ.pop("LOG_LEVEL", None)
