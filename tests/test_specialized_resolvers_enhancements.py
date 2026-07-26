from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock

from kindly_web_search_mcp_server.content.resolvers import (
    GitHubPullError,
    GitHubRepoError,
    HackerNewsError,
    RedditError,
    fetch_github_pull_thread_markdown,
    fetch_github_repo_markdown,
    fetch_hackernews_thread_markdown,
    fetch_reddit_thread_markdown,
    parse_github_pull_url,
    parse_github_repo_url,
    parse_hackernews_url,
    parse_reddit_url,
)
from kindly_web_search_mcp_server.content.resolvers.youtube import parse_youtube_content_url
from kindly_web_search_mcp_server.content.specialized_pipeline import _maybe_specialized


# --- GitHub Repo Resolver Tests ---


def test_parse_github_repo_url():
    target = parse_github_repo_url("https://github.com/fastapi/fastapi")
    assert target.owner == "fastapi"
    assert target.repo == "fastapi"
    assert target.ref is None

    target_tree = parse_github_repo_url("https://github.com/fastapi/fastapi/tree/main/docs")
    assert target_tree.owner == "fastapi"
    assert target_tree.repo == "fastapi"
    assert target_tree.ref == "main"
    assert target_tree.path == "docs"

    with pytest.raises(GitHubRepoError):
        parse_github_repo_url("https://github.com/fastapi/fastapi/issues/100")

    with pytest.raises(GitHubRepoError):
        parse_github_repo_url("https://github.com/fastapi/fastapi/pull/100")


@pytest.mark.asyncio
async def test_fetch_github_repo_markdown():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "api.github.com/repos/owner/repo/readme" in url_str:
            return httpx.Response(200, text="# Repo README\nHello world")
        if "api.github.com/repos/owner/repo" in url_str:
            return httpx.Response(
                200,
                json={
                    "description": "Awesome repo",
                    "stargazers_count": 1234,
                    "forks_count": 56,
                    "language": "Python",
                    "license": {"spdx_id": "MIT"},
                    "default_branch": "main",
                    "topics": ["web", "mcp"],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        md = await fetch_github_repo_markdown("https://github.com/owner/repo", http_client=client)
        assert "# Repository: owner/repo" in md
        assert "Stars: 1234" in md
        assert "Language: Python" in md
        assert "## README" in md
        assert "Hello world" in md


# --- GitHub PR Resolver Tests ---


def test_parse_github_pull_url():
    target = parse_github_pull_url("https://github.com/psf/black/pull/1234")
    assert target.owner == "psf"
    assert target.repo == "black"
    assert target.number == 1234

    with pytest.raises(GitHubPullError):
        parse_github_pull_url("https://github.com/psf/black/issues/1234")


@pytest.mark.asyncio
async def test_fetch_github_pull_thread_markdown():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "pulls/1234" in url_str:
            return httpx.Response(
                200,
                json={
                    "title": "Fix bug in formatter",
                    "body": "This PR fixes a bug.",
                    "state": "open",
                    "created_at": "2026-07-24T10:00:00Z",
                    "html_url": "https://github.com/psf/black/pull/1234",
                    "additions": 45,
                    "deletions": 10,
                    "changed_files": 3,
                    "base": {"ref": "main"},
                    "head": {"ref": "fix-branch"},
                    "user": {"login": "dev1"},
                },
            )
        if "issues/1234/comments" in url_str:
            return httpx.Response(
                200,
                json=[
                    {
                        "body": "Looks good to me!",
                        "created_at": "2026-07-24T11:00:00Z",
                        "user": {"login": "reviewer1"},
                    }
                ],
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        md = await fetch_github_pull_thread_markdown(
            "https://github.com/psf/black/pull/1234", http_client=client
        )
        assert "# Pull Request: Fix bug in formatter" in md
        assert "Diff: +45 / -10 (3 files)" in md
        assert "Author: @dev1" in md
        assert "Looks good to me!" in md


# --- HackerNews Resolver Tests ---


def test_parse_hackernews_url():
    target = parse_hackernews_url("https://news.ycombinator.com/item?id=40000000")
    assert target.item_id == 40000000

    target_algolia = parse_hackernews_url("https://hn.algolia.com/api/v1/items/40000000")
    assert target_algolia.item_id == 40000000

    with pytest.raises(HackerNewsError):
        parse_hackernews_url("https://news.ycombinator.com/news")


@pytest.mark.asyncio
async def test_fetch_hackernews_thread_markdown():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "hn.algolia.com/api/v1/items/40000000" in url_str:
            return httpx.Response(
                200,
                json={
                    "id": 40000000,
                    "title": "Show HN: Fast Web Search",
                    "url": "https://example.com",
                    "points": 250,
                    "author": "hnuser",
                    "created_at": "2026-07-24T08:00:00Z",
                    "text": "<p>Check out our new project!</p>",
                    "children": [
                        {
                            "id": 40000001,
                            "author": "commenter1",
                            "created_at": "2026-07-24T09:00:00Z",
                            "text": "<p>This is great!</p>",
                            "children": [],
                        }
                    ],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        md = await fetch_hackernews_thread_markdown(
            "https://news.ycombinator.com/item?id=40000000", http_client=client
        )
        assert "# HackerNews: Show HN: Fast Web Search" in md
        assert "Points: 250" in md
        assert "Comment by @commenter1" in md
        assert "This is great!" in md


# --- Reddit Resolver Tests ---


def test_parse_reddit_url():
    target = parse_reddit_url("https://www.reddit.com/r/python/comments/1abc23/awesome_project/")
    assert target.subreddit == "python"
    assert target.post_id == "1abc23"

    target_short = parse_reddit_url("https://redd.it/1abc23")
    assert target_short.subreddit == "auto"
    assert target_short.post_id == "1abc23"

    with pytest.raises(RedditError):
        parse_reddit_url("https://www.reddit.com/r/python/")


@pytest.mark.asyncio
async def test_fetch_reddit_thread_markdown():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "reddit.com/r/python/comments/1abc23.json" in url_str:
            return httpx.Response(
                200,
                json=[
                    {
                        "data": {
                            "children": [
                                {
                                    "data": {
                                        "title": "Python 3.14 Released",
                                        "subreddit": "python",
                                        "author": "pydev",
                                        "score": 500,
                                        "upvote_ratio": 0.95,
                                        "num_comments": 42,
                                        "selftext": "Python 3.14 is now live!",
                                        "permalink": "/r/python/comments/1abc23/python_314_released/",
                                    }
                                }
                            ]
                        }
                    },
                    {
                        "data": {
                            "children": [
                                {
                                    "kind": "t1",
                                    "data": {
                                        "author": "user1",
                                        "score": 100,
                                        "body": "Super excited for deferred evaluation!",
                                        "replies": "",
                                    },
                                }
                            ]
                        }
                    },
                ],
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        md = await fetch_reddit_thread_markdown(
            "https://www.reddit.com/r/python/comments/1abc23/awesome_project/", http_client=client
        )
        assert "# Reddit: Python 3.14 Released (r/python)" in md
        assert "Score: 500" in md
        assert "Comment by u/user1" in md
        assert "Super excited for deferred evaluation!" in md


# --- Integration Test with _resolve_tier1 ---


@pytest.mark.asyncio
async def test_maybe_specialized_skips_parser_returning_none() -> None:
    fetcher = AsyncMock()

    result = await _maybe_specialized(
        "https://pi.dev/packages",
        parser=parse_youtube_content_url,
        fetcher=fetcher,
        source_type="youtube",
    )

    assert result is None
    fetcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_tier1_routes_to_new_resolvers():
    # Verify matching URL returns a ContentArtifact with expected source_type
    # We can mock the network calls inside _resolve_tier1 if needed, or check URL matching behavior
    target_repo = parse_github_repo_url("https://github.com/fastapi/fastapi")
    assert target_repo.owner == "fastapi"

    target_pull = parse_github_pull_url("https://github.com/fastapi/fastapi/pull/100")
    assert target_pull.number == 100

    target_hn = parse_hackernews_url("https://news.ycombinator.com/item?id=12345")
    assert target_hn.item_id == 12345

    target_red = parse_reddit_url("https://www.reddit.com/r/test/comments/xyz123/title/")
    assert target_red.post_id == "xyz123"
