from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.mark.asyncio
async def test_sourcegraph_literal_search_maps_line_matches() -> None:
    from kindly_web_search_mcp_server.search.providers.sourcegraph import search_sourcegraph

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "search": {
                        "results": {
                            "results": [
                                {
                                    "__typename": "FileMatch",
                                    "file": {
                                        "path": "src/main.py",
                                        "url": "/github.com/acme/demo/-/blob/main/src/main.py",
                                    },
                                    "repository": {"name": "github.com/acme/demo"},
                                    "lineMatches": [{"lineNumber": 7, "preview": "needle = 1"}],
                                }
                            ]
                        }
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await search_sourcegraph("needle", num_results=1, http_client=client)

    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["variables"]["patternType"] == "literal"
    assert results[0].title == "github.com/acme/demo: src/main.py:7"
    assert results[0].snippet == "needle = 1"


@pytest.mark.asyncio
async def test_sourcegraph_regexp_uses_graphql_pattern_variable() -> None:
    from kindly_web_search_mcp_server.search.providers.sourcegraph import search_sourcegraph

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"search": {"results": {"matchCount": 0, "limitHit": False, "results": []}}}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await search_sourcegraph(
            "needle", num_results=2, http_client=client, pattern_type="regexp"
        )

    assert results == []
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["variables"]["patternType"] == "regexp"
    assert "patternType:regexp" not in payload["variables"]["query"]


@pytest.mark.asyncio
async def test_gitlab_blob_search_maps_project_and_line() -> None:
    from kindly_web_search_mcp_server.search.providers.gitlab import search_gitlab

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["scope"] == "blobs"
        return httpx.Response(
            200,
            json=[
                {
                    "project_id": 42,
                    "filename": "main.py",
                    "path": "src/main.py",
                    "ref": "main",
                    "startline": 9,
                    "data": "needle = 1",
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await search_gitlab("needle", num_results=1, http_client=client)

    assert results[0].title == "GitLab Project 42: main.py:9"
    assert "/projects/42/-/blob/main/src/main.py#L9" in results[0].link
    assert results[0].snippet == "needle = 1"


@pytest.mark.asyncio
async def test_gitlab_unauthorized_is_structured_error() -> None:
    from kindly_web_search_mcp_server.search.providers.base import ProviderRequestError
    from kindly_web_search_mcp_server.search.providers.gitlab import search_gitlab

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError) as exc_info:
            await search_gitlab("needle", num_results=1, http_client=client)

    metadata = exc_info.value.metadata
    assert metadata.http_status == 401
    assert metadata.result_class == "error"
    assert metadata.auth_mode == "anonymous"


@pytest.mark.asyncio
async def test_github_search_uses_text_match_code_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import kindly_web_search_mcp_server.search.providers.github as github

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/code"
        assert request.headers["Accept"] == "application/vnd.github.text-match+json"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "path": "src/main.py",
                        "html_url": "https://github.com/acme/demo/blob/main/src/main.py",
                        "repository": {"full_name": "acme/demo"},
                        "text_matches": [{"fragment": "needle = 1"}],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await github.search_github("needle", num_results=1, http_client=client)

    assert results[0].title == "acme/demo: src/main.py"
    assert results[0].snippet == "needle = 1"
