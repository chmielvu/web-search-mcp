"""GitHub GraphQL search provider for Discussions and Issues.

Requires GITHUB_TOKEN environment variable (personal access token).
Falls back gracefully to empty list if not configured.

Rate limit: 5,000 points/hour for authenticated users.
Query cost: ~1 point each for Discussion and Issue searches.

Pattern validated by live probes on 2026-04-21 (see plans/GraphQL-tuning.md)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from ..models import WebSearchResult

logger = logging.getLogger(__name__)

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Discussion discovery query (Pattern A from GraphQL-tuning.md)
_DISCUSSION_QUERY = """
query($q: String!, $first: Int!) {
  search(type: DISCUSSION, query: $q, first: $first) {
    edges {
      node {
        ... on Discussion {
          number
          title
          url
          upvoteCount
          repository { nameWithOwner }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
"""

# Issue discovery query
_ISSUE_QUERY = """
query($q: String!, $first: Int!) {
  search(type: ISSUE, query: $q, first: $first) {
    edges {
      node {
        ... on Issue {
          number
          title
          url
          comments { totalCount }
          repository { nameWithOwner }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
"""


def _get_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return token if token else None


async def _search_graphql(
    client: httpx.AsyncClient,
    query: str,
    num_results: int,
    graphql_query: str,
    token: str,
    result_type: str,
) -> list[WebSearchResult]:
    """Execute a single GitHub GraphQL search and map results."""
    variables = {"q": query, "first": min(num_results, 20)}
    payload = {"query": graphql_query, "variables": variables}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        resp = await client.post(
            _GITHUB_GRAPHQL_URL, json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("GitHub GraphQL %s search failed: %s", result_type, exc)
        return []

    if not isinstance(data, dict):
        return []

    # Check for GraphQL errors
    errors = data.get("errors")
    if errors:
        logger.debug("GitHub GraphQL %s returned errors: %s", result_type, errors)
        return []

    search_data = data.get("data", {}).get("search")
    if not isinstance(search_data, dict):
        return []

    edges = search_data.get("edges", [])
    if not isinstance(edges, list):
        return []

    results: list[WebSearchResult] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict):
            continue

        title = node.get("title")
        number = node.get("number")
        url = node.get("url")
        repo_data = node.get("repository")
        repo = "unknown"
        if isinstance(repo_data, dict):
            repo = repo_data.get("nameWithOwner", "unknown")

        if not isinstance(title, str) or not title:
            continue
        if not isinstance(url, str) or not url:
            continue

        if result_type == "discussion":
            upvotes = node.get("upvoteCount", 0)
            snippet = f"{repo} | {upvotes} upvotes"
        else:
            comments_data = node.get("comments", {})
            total_comments = (
                comments_data.get("totalCount", 0)
                if isinstance(comments_data, dict)
                else 0
            )
            snippet = f"{repo} | {total_comments} comments"

        full_title = f"{repo}#{number}: {title}"
        results.append(
            WebSearchResult(title=full_title, link=url, snippet=snippet)
        )

    return results


async def search_github_graphql(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search GitHub Discussions and Issues via GraphQL API.

    Queries both Discussions and Issues in parallel, then merges results.
    Requires GITHUB_TOKEN environment variable.

    Args:
        query: Normalized search query string.
        num_results: Maximum number of results to return.
        http_client: Optional shared httpx client.

    Returns:
        List of WebSearchResult objects (empty on failure or no token).
    """
    if not query.strip() or num_results < 1:
        return []

    token = _get_token()
    if token is None:
        return []

    async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
        discussion_results, issue_results = await asyncio.gather(
            _search_graphql(
                client, query, num_results, _DISCUSSION_QUERY, token, "discussion"
            ),
            _search_graphql(
                client, query, num_results, _ISSUE_QUERY, token, "issue"
            ),
            return_exceptions=True,
        )

        discussion_list: list[WebSearchResult] = (
            discussion_results
            if isinstance(discussion_results, list)
            else []
        )
        issue_list: list[WebSearchResult] = (
            issue_results if isinstance(issue_results, list) else []
        )

        # Interleave discussions and issues for diversity, cap at num_results
        merged: list[WebSearchResult] = []
        max_len = max(len(discussion_list), len(issue_list))
        for i in range(max_len):
            if i < len(discussion_list):
                merged.append(discussion_list[i])
                if len(merged) >= num_results:
                    break
            if i < len(issue_list):
                merged.append(issue_list[i])
                if len(merged) >= num_results:
                    break

        return merged[:num_results]

    try:
        if http_client is not None:
            return await _run(http_client)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                return await _run(client)
    except Exception as exc:
        logger.debug("GitHub GraphQL search failed: %s", exc)
        return []
