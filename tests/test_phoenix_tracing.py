from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_openinference_context_attributes_serializes_context() -> None:
    from kindly_web_search_mcp_server.telemetry.phoenix_tracing import (
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
    from unittest.mock import patch
    from contextlib import contextmanager

    import kindly_web_search_mcp_server.telemetry.phoenix_tracing as tracing_mod
    from kindly_web_search_mcp_server.telemetry.phoenix_tracing import (
        LLMTraceContext,
        current_openinference_attributes,
        openinference_context_scope,
    )

    # openinference.instrumentation is optional; when absent the fallbacks
    # are no-ops that return {}.  Mock them so context round-trips actually
    # store and retrieve attributes, matching the real library's key mapping.
    stored: dict[str, object] = {}
    _KEY_MAP = {
        "session_id": "session.id",
        "user_id": "user.id",
        "tags": "tag.tags",
        "metadata": "metadata",
    }

    @contextmanager
    def _mock_using_attributes(**attrs):
        for k, v in attrs.items():
            mapped_key = _KEY_MAP.get(k, k)
            # Match real openinference behaviour: metadata becomes a JSON string
            if mapped_key == "metadata" and isinstance(v, dict):
                stored[mapped_key] = json.dumps(v, sort_keys=True, default=str)
            else:
                stored[mapped_key] = v
        yield

    def _mock_get_attributes():
        return dict(stored)

    ctx = LLMTraceContext(
        trace_name="entity_extraction",
        session_id="session-abc",
        user_id="user-def",
        tags=("phoenix",),
        metadata={"run_key": "run-123"},
    )

    with (
        patch.object(tracing_mod, "using_attributes", _mock_using_attributes),
        patch.object(tracing_mod, "get_attributes_from_context", _mock_get_attributes),
    ):
        with openinference_context_scope(ctx):
            attrs = current_openinference_attributes()

    assert attrs["session.id"] == "session-abc"
    assert attrs["user.id"] == "user-def"
    assert attrs["tag.tags"] == ["phoenix"]
    assert json.loads(attrs["metadata"]) == {
        "run_key": "run-123",
        "trace_name": "entity_extraction",
    }
