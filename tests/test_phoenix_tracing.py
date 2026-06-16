from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_openinference_context_attributes_serializes_context() -> None:
    from kindly_web_search_mcp_server.llm.phoenix_tracing import (
        LLMTraceContext,
        openinference_context_attributes,
    )

    attrs = openinference_context_attributes(
        LLMTraceContext(
            trace_name="query_understanding",
            session_id="session-123",
            user_id="user-456",
            tags=("search", "llm"),
            metadata={"run_key": "run-789"},
        )
    )

    assert attrs["session.id"] == "session-123"
    assert attrs["user.id"] == "user-456"
    assert attrs["tag.tags"] == ["search", "llm"]
    assert json.loads(attrs["metadata"]) == {
        "run_key": "run-789",
        "trace_name": "query_understanding",
    }


def test_openinference_context_scope_populates_context_attributes() -> None:
    from kindly_web_search_mcp_server.llm.phoenix_tracing import (
        LLMTraceContext,
        current_openinference_attributes,
        openinference_context_scope,
    )

    ctx = LLMTraceContext(
        trace_name="entity_extraction",
        session_id="session-abc",
        user_id="user-def",
        tags=("phoenix",),
        metadata={"run_key": "run-123"},
    )

    with openinference_context_scope(ctx):
        attrs = current_openinference_attributes()

    assert attrs["session.id"] == "session-abc"
    assert attrs["user.id"] == "user-def"
    assert attrs["tag.tags"] == ["phoenix"]
    assert json.loads(attrs["metadata"]) == {
        "run_key": "run-123",
        "trace_name": "entity_extraction",
    }
