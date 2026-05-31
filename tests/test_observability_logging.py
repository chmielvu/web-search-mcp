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
    monkeypatch.setenv("KINDLY_OBSERVABILITY_MAX_TEXT_CHARS", "200")

    logger = logging.getLogger("test.observability")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = _ListHandler()
    logger.addHandler(handler)

    emit_observability_event(
        logger,
        "tool.perplexity_search.response",
        query="fastmcp middleware best practices",
        answer="Concrete answer text",
        sources=["https://example.com/a", "https://example.com/b"],
    )

    assert len(handler.records) == 1
    record = handler.records[0]
    payload = json.loads(record.getMessage())

    assert payload["event"] == "tool.perplexity_search.response"
    assert payload["query"] == "fastmcp middleware best practices"
    assert payload["answer"] == "Concrete answer text"
    assert payload["sources"] == ["https://example.com/a", "https://example.com/b"]
    assert record.kindly_event == "tool.perplexity_search.response"
    assert record.kindly_query == "fastmcp middleware best practices"


def test_emit_tool_observability_event_adds_fingerprint_and_bounds_payload(monkeypatch) -> None:
    monkeypatch.setenv("KINDLY_OBSERVABILITY_MAX_TEXT_CHARS", "50")

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
    assert handler.records[1].kindly_event == "tool.get_content.request"


def test_preview_text_truncates() -> None:
    value = "x" * 12
    assert preview_text(value, limit=10) == ("x" * 10) + "…"


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
            if getattr(handler, "_kindly_structlog_stream_handler", False)
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
        os.environ.pop("LOG_LEVEL", None)
