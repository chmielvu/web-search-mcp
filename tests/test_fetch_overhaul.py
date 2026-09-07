from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.models import FetchResult, FetchWindow
from kindly_web_search_mcp_server.tools.content import (
    _cache_metadata,
    _result_from_artifact,
    fetch,
)


def _artifact(
    *,
    url: str = "https://example.test/article",
    status: str = "success",
    content: str = "Article body.",
    error: dict | None = None,
) -> dict:
    return {
        "input_url": url,
        "normalized_url": url,
        "fetched_url": url,
        "status": status,
        "source_type": "html",
        "fetch_backend": "test",
        "origin_backend": "test",
        "cached": False,
        "content_type": "text/markdown",
        "markdown": content,
        "metadata": {"title": "Internal title"},
        "links": None,
        "error": error,
        "entities": None,
        "llms_txt": None,
        "diagnostics": None,
    }


def test_success_public_result_has_only_contract_fields() -> None:
    result = _result_from_artifact(
        _artifact(content="A usable article body."),
        offset=0,
        max_chars=0,
        include_links=False,
    )

    validated = FetchResult.model_validate(result)
    public = validated.model_dump(exclude_none=True)
    assert set(public) <= {
        "url",
        "status",
        "content",
        "links",
        "window",
        "error",
        "entities",
        "diagnostics",
    }
    assert public["status"] == "success"
    assert public["content"] == "A usable article body."
    assert public["window"].get("has_more") is False
    assert public.get("error") is None


def test_wall_status_replaces_access_signal_field() -> None:
    result = _result_from_artifact(
        _artifact(
            content='<form><input type="password"></form> Sign in to continue.',
        ),
        offset=0,
        max_chars=0,
        include_links=False,
    )

    assert result["status"] == "login"
    assert result["error"] is None
    assert "access_signal" not in result


def test_http_error_has_actionable_typed_envelope() -> None:
    result = _result_from_artifact(
        _artifact(
            url="https://example.test/missing",
            status="error",
            content="",
            error={"code": "http_404", "message": "Not Found", "retryable": False},
        ),
        offset=0,
        max_chars=0,
        include_links=False,
    )

    assert result["status"] == "error"
    error = result["error"]
    assert error is not None
    assert error["code"] == "http_404"
    assert error["category"] == "upstream"
    assert error["http_status"] == 404
    assert error["retryable"] is False
    assert error["resolution"]


def test_window_and_cache_keep_internal_contracts_separate() -> None:
    assert "continuation_notice" not in FetchWindow.model_fields
    cache = _cache_metadata(
        {
            "input_url": "https://example.test/data.json",
            "normalized_url": "https://example.test/data.json",
            "source_type": "json",
            "content_type": "application/json",
        }
    )
    assert cache["__web_fetch__"]["format"] == "json"
    public = _result_from_artifact(
        _artifact(content='{"ok": true}'),
        offset=0,
        max_chars=0,
        include_links=False,
    )
    assert "format" not in public
    assert "continuation_notice" not in public["window"]


def test_fetch_signature_drops_resource_tuning_inputs() -> None:
    parameters = set(inspect.signature(fetch).parameters)
    assert not {"include_metadata", "max_links", "strip_selectors"} & parameters


@pytest.mark.asyncio
async def test_ai_summary_replaces_content_without_public_summary_fields() -> None:
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.report_progress = AsyncMock()
    summary = {
        "summary": "Synthesized answer from the source.",
        "key_points": ["The source has a clear answer."],
        "important_entities": [],
        "verbatim_terms": [],
        "limitations": [],
        "model": "test-model",
        "backend": "test",
    }
    with (
        patch(
            "kindly_web_search_mcp_server.tools.content._fetch_one_artifact",
            new=AsyncMock(return_value=_artifact(content="Original source body.")),
        ),
        patch(
            "kindly_web_search_mcp_server.tools.content.create_summary",
            new=AsyncMock(return_value=summary),
        ),
        patch("kindly_web_search_mcp_server.tools.content.emit_tool_observability_event"),
        patch("kindly_web_search_mcp_server.tools.content._record_tool_success"),
    ):
        response = await fetch(
            url="https://example.test/article",
            ai_summary=True,
            ctx=ctx,
        )

    result = response.model_dump(exclude_none=True)["results"][0]
    assert result["content"] == "Synthesized answer from the source."
    assert "summary" not in result
    assert "usage" not in result
    assert "metadata" not in result
    assert "page_content" not in result
