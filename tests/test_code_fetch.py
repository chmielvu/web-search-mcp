from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kindly_web_search_mcp_server.tools.code_search.exploration import code_fetch


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> object:
        return self._payload


class FakeClient:
    def __init__(self, *, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    async def post(self, url: str, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)


@pytest.mark.asyncio
async def test_code_fetch_file_returns_bounded_lines_and_blob_oid() -> None:
    client = FakeClient(
        post_responses=[
            FakeResponse(
                {
                    "data": {
                        "repository": {
                            "object": {
                                "oid": "a" * 40,
                                "isBinary": False,
                                "text": "one\ntwo\nthree\nfour",
                            }
                        }
                    }
                }
            )
        ]
    )
    cache = AsyncMock()
    cache.lookup_hydration.return_value = None

    with (
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration._token",
            return_value="token",
        ),
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration.get_http_client",
            AsyncMock(return_value=client),
        ),
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration.get_code_search_cache",
            return_value=cache,
        ),
    ):
        result = await code_fetch(
            "owner/repo",
            path="src/example.py",
            ref="main",
            start_line=2,
            end_line=3,
            ctx=None,
        )

    assert result.outcome == "ok"
    assert result.content == "two\nthree"
    assert result.line_start == 2
    assert result.line_end == 3
    assert result.commit_oid == "a" * 40
    assert result.source_chars == len("one\ntwo\nthree\nfour")
    assert result.has_more is True
    assert result.next_start_line == 4
    assert "main:src/example.py" in client.post_calls[0][1]["json"]["variables"].values()
    cache.store_hydration.assert_awaited_once()


@pytest.mark.asyncio
async def test_code_fetch_tree_is_bounded() -> None:
    client = FakeClient(
        get_responses=[
            FakeResponse(
                {
                    "tree": [
                        {"path": "README.md", "type": "blob", "sha": "1"},
                        {"path": "src", "type": "tree", "sha": "2"},
                        {"path": "src/app.py", "type": "blob", "sha": "3"},
                    ]
                }
            )
        ]
    )

    with (
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration._token",
            return_value="token",
        ),
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration.get_http_client",
            AsyncMock(return_value=client),
        ),
    ):
        result = await code_fetch(
            "owner/repo",
            action="tree",
            max_entries=2,
            ctx=None,
        )

    assert result.outcome == "partial"
    assert result.truncated is True
    assert [entry.path for entry in result.tree] == ["README.md", "src"]
    assert client.get_calls[0][1]["params"] == {"recursive": "1"}


@pytest.mark.asyncio
async def test_code_fetch_uses_immutable_hydration_cache_without_http() -> None:
    client = FakeClient()
    cache = AsyncMock()
    cache.lookup_hydration.return_value = {
        "text": "cached one\ncached two\ncached three",
        "metadata": {"blob_oid": "b" * 40},
    }

    with (
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration._token",
            return_value="token",
        ),
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration.get_http_client",
            AsyncMock(return_value=client),
        ),
        patch(
            "kindly_web_search_mcp_server.tools.code_search.exploration.get_code_search_cache",
            return_value=cache,
        ),
    ):
        result = await code_fetch(
            "owner/repo",
            path="src/example.py",
            ref="b" * 40,
            start_line=2,
            ctx=None,
        )

    assert result.content == "cached two\ncached three"
    assert result.commit_oid == "b" * 40
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_code_fetch_rejects_invalid_repository_before_auth() -> None:
    with patch(
        "kindly_web_search_mcp_server.tools.code_search.exploration._token",
        return_value=None,
    ):
        result = await code_fetch("not-a-repository", path="README.md", ctx=None)

    assert result.outcome == "error"
    assert "owner/name" in (result.error or "")

def test_code_search_next_points_to_code_fetch_for_repository_hits() -> None:
    from kindly_web_search_mcp_server.tools.code_search.models import (
        CodeSearchHit,
        CodeSearchResultType,
        QueryMetadata,
        Stats,
        to_public_result,
    )
    from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan

    hit = CodeSearchHit(
        repository="owner/repo",
        path="src/example.py",
        commit_oid="c" * 40,
        url="https://github.com/owner/repo/blob/main/src/example.py",
        provider="github",
        line_start=12,
        line_end=15,
        snippet="def example(): pass",
    )
    result = CodeSearchResultType(
        query="example",
        outcome="ok",
        results=[hit],
        repositories=[],
        diagnostics=[],
        stats=Stats(returned_count=1),
        query_metadata=QueryMetadata(original_query="example", anchor_terms=["example"]),
    )

    public = to_public_result(result, plan=build_query_plan("example"))

    assert public.next[0].tool == "code_fetch"
    assert public.next[0].query["repository"] == "owner/repo"
    assert public.next[0].query["ref"] == "c" * 40
