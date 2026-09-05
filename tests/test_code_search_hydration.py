"""Unit tests for the canonical source hydration module."""

from __future__ import annotations

import httpx
import pytest

from kindly_web_search_mcp_server.tools.code_search.hydration import (
    _build_graphql_query,
    hydrate_sources,
)
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchHit


def test_graphql_query_typing():
    # Hit with 40-char blob SHA must use GitObjectID!
    sha_hit = CodeSearchHit(
        repository="owner/repo",
        path="src/main.py",
        sha="a" * 40,
        provider="github",
    )
    # Hit without SHA must use String! expression
    expr_hit = CodeSearchHit(
        repository="owner/repo",
        path="src/helper.py",
        commit_oid="main",
        provider="github",
    )

    query, variables = _build_graphql_query([sha_hit, expr_hit])
    # Verify variable typing
    assert "$oid0: GitObjectID!" in query
    assert "$expr1: String!" in query
    assert "$oid0: String!" not in query

    assert variables["oid0"] == "a" * 40
    assert variables["expr1"] == "main:src/helper.py"


@pytest.mark.asyncio
async def test_unauthenticated_rest_fallback():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "contents/src/main.py" in str(request.url):
            assert request.headers.get("accept") == "application/vnd.github.raw+json"
            return httpx.Response(200, text="def hello(): pass\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        hit = CodeSearchHit(
            repository="owner/repo",
            path="src/main.py",
            provider="github",
            url="https://github.com/owner/repo/blob/main/src/main.py",
        )
        sources, diagnostics = await hydrate_sources([hit], http_client=client, token=None)

        key = ("owner/repo", "src/main.py")
        assert key in sources
        assert sources[key].text == "def hello(): pass\n"
        assert sources[key].repository == "owner/repo"


@pytest.mark.asyncio
async def test_graphql_error_falls_back_to_rest():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "graphql" in str(request.url):
            return httpx.Response(401, text="Unauthorized")
        if "contents/src/lib.rs" in str(request.url):
            return httpx.Response(200, text="pub fn solve() {}\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        hit = CodeSearchHit(
            repository="owner/repo",
            path="src/lib.rs",
            provider="github",
            url="https://github.com/owner/repo/blob/main/src/lib.rs",
        )
        sources, diagnostics = await hydrate_sources(
            [hit], http_client=client, token="invalid_or_expired_token"
        )

        key = ("owner/repo", "src/lib.rs")
        assert key in sources
        assert sources[key].text == "pub fn solve() {}\n"
        # Diagnostics should mention fallback
        assert any("falling back to REST" in d.message for d in diagnostics)


@pytest.mark.asyncio
async def test_large_file_truncation():
    large_content = "x" * 250_000

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=large_content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        hit = CodeSearchHit(
            repository="owner/repo",
            path="src/large.py",
            provider="github",
            url="https://github.com/owner/repo/blob/main/src/large.py",
        )
        sources, diagnostics = await hydrate_sources(
            [hit],
            http_client=client,
            token=None,
            max_chars_per_file=200_000,
        )

        key = ("owner/repo", "src/large.py")
        assert key in sources
        assert len(sources[key].text) == 200_000
        assert any(d.failure_kind == "budget" for d in diagnostics)
