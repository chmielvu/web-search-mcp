from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_build_langfuse_litellm_kwargs_includes_metadata(monkeypatch) -> None:
    from kindly_web_search_mcp_server.llm.langfuse_tracing import (
        LangfuseTraceContext,
        build_langfuse_litellm_kwargs,
    )

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.llm.langfuse_tracing.resolve_langfuse_credentials",
        lambda **kwargs: ("pk-test", "sk-test", "https://cloud.langfuse.com"),
    )

    kwargs = build_langfuse_litellm_kwargs(
        generation_name="query_understanding",
        trace_context=LangfuseTraceContext(
            trace_name="query_understanding",
            session_id="session-123",
            user_id="user-456",
            tags=("search", "llm"),
            metadata={"run_key": "run-789"},
        ),
    )

    assert kwargs["langfuse_public_key"] == "pk-test"
    assert kwargs["langfuse_secret_key"] == "sk-test"
    assert kwargs["langfuse_host"] == "https://cloud.langfuse.com"
    assert kwargs["metadata"]["generation_name"] == "query_understanding"
    assert kwargs["metadata"]["trace_name"] == "query_understanding"
    assert kwargs["metadata"]["session_id"] == "session-123"
    assert kwargs["metadata"]["trace_user_id"] == "user-456"
    assert kwargs["metadata"]["tags"] == ["search", "llm"]
    assert kwargs["metadata"]["trace_metadata"] == {"run_key": "run-789"}


def test_ensure_langfuse_litellm_callbacks_installs_callbacks(monkeypatch) -> None:
    import litellm

    from kindly_web_search_mcp_server.llm import langfuse_tracing

    monkeypatch.setattr(
        langfuse_tracing,
        "_CALLBACKS_CONFIGURED",
        False,
    )
    monkeypatch.setattr(
        langfuse_tracing,
        "resolve_langfuse_credentials",
        lambda **kwargs: ("pk-test", "sk-test", "https://cloud.langfuse.com"),
    )
    litellm.success_callback = ["other"]
    litellm.failure_callback = []

    assert langfuse_tracing.ensure_langfuse_litellm_callbacks()
    assert "langfuse" in litellm.success_callback
    assert "other" in litellm.success_callback
    assert litellm.failure_callback == ["langfuse"]
