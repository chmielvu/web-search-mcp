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
import re

import httpx

from ..models import WebSearchResult
from .base_provider import run_provider

logger = logging.getLogger(__name__)

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_REPO_HINT_PATTERN = re.compile(r"\brepo:([^\s]+/[^\s]+)\b", re.I)
_ORG_HINT_PATTERN = re.compile(r"\borg:([^\s]+)\b", re.I)
_USER_HINT_PATTERN = re.compile(r"\buser:([^\s]+)\b", re.I)
_BARE_REPO_PATTERN = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")


class GitHubGraphQLError(RuntimeError):
    pass


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
          createdAt
          updatedAt
          state
          author { login }
          repository { nameWithOwner }
          comments { totalCount }
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
          createdAt
          updatedAt
          state
          author { login }
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


def _narrow_query(query: str) -> str:
    """Preserve explicit repo/org/user hints and add a repo qualifier when obvious."""
    normalized = query.strip()
    if not normalized:
        return normalized

    qualifier_parts: list[str] = []
    seen: set[str] = set()

    for repo in _REPO_HINT_PATTERN.findall(normalized):
        qualifier = f"repo:{repo.strip()}"
        if qualifier not in seen:
            qualifier_parts.append(qualifier)
            seen.add(qualifier)

    for org in _ORG_HINT_PATTERN.findall(normalized):
        qualifier = f"org:{org.strip()}"
        if qualifier not in seen:
            qualifier_parts.append(qualifier)
            seen.add(qualifier)

    for user in _USER_HINT_PATTERN.findall(normalized):
        qualifier = f"user:{user.strip()}"
        if qualifier not in seen:
            qualifier_parts.append(qualifier)
            seen.add(qualifier)

    bare_repo = _BARE_REPO_PATTERN.search(normalized)
    if bare_repo and any(
        marker in normalized.casefold()
        for marker in ("github", "issue", "issues", "discussion", "discussions", "repo")
    ):
        qualifier = f"repo:{bare_repo.group(1)}"
        if qualifier not in seen:
            qualifier_parts.append(qualifier)

    if not qualifier_parts:
        return normalized

    return " ".join([normalized, *qualifier_parts])


def _short_date(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:10]


def _author_login(node: dict[str, object]) -> str:
    author = node.get("author")
    if isinstance(author, dict):
        login = author.get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()
    return "(deleted)"


def _format_result_snippet(node: dict[str, object], result_type: str) -> str:
    repo_data = node.get("repository")
    repo = "unknown"
    if isinstance(repo_data, dict):
        repo_name = repo_data.get("nameWithOwner")
        if isinstance(repo_name, str) and repo_name.strip():
            repo = repo_name.strip()

    author = _author_login(node)
    created = _short_date(node.get("createdAt"))
    updated = _short_date(node.get("updatedAt"))
    comments_data = node.get("comments", {})
    total_comments = (
        comments_data.get("totalCount", 0)
        if isinstance(comments_data, dict)
        else 0
    )

    parts = [repo, f"by {author}"]
    if result_type == "discussion":
        upvotes = node.get("upvoteCount", 0)
        parts.append(f"{upvotes} upvotes")
    else:
        state = node.get("state", "")
        if isinstance(state, str) and state:
            parts.append(state.casefold())
    parts.append(f"{total_comments} comments")
    if created:
        parts.append(f"created {created}")
    if updated and updated != created:
        parts.append(f"updated {updated}")
    return " | ".join(parts)


def _format_result_title(node: dict[str, object]) -> str:
    repo_data = node.get("repository")
    repo = "unknown"
    if isinstance(repo_data, dict):
        repo_name = repo_data.get("nameWithOwner")
        if isinstance(repo_name, str) and repo_name.strip():
            repo = repo_name.strip()

    title = node.get("title")
    number = node.get("number")
    if isinstance(title, str) and title.strip() and isinstance(number, int):
        return f"{repo}#{number}: {title.strip()}"
    if isinstance(title, str) and title.strip():
        return f"{repo}: {title.strip()}"
    return repo


def _format_graphql_errors(errors: object) -> str:
    if not isinstance(errors, list):
        return str(errors)
    parts: list[str] = []
    for error in errors:
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                parts.append(message.strip())
    return "; ".join(parts) if parts else str(errors)


async def _search_graphql(
    client: httpx.AsyncClient,
    query: str,
    num_results: int,
    graphql_query: str,
    token: str,
    result_type: str,
) -> list[WebSearchResult]:
    """Execute a single GitHub GraphQL search and map results."""
    variables = {"q": _narrow_query(query), "first": min(num_results, 20)}
    payload = {"query": graphql_query, "variables": variables}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        resp = await client.post(_GITHUB_GRAPHQL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("GitHub GraphQL %s search failed: %s", result_type, exc)
        raise GitHubGraphQLError(f"{result_type} search failed") from exc

    if not isinstance(data, dict):
        raise GitHubGraphQLError(f"{result_type} search returned an invalid payload")

    # Check for GraphQL errors
    errors = data.get("errors")
    if errors:
        message = _format_graphql_errors(errors)
        logger.warning("GitHub GraphQL %s returned errors: %s", result_type, message)
        raise GitHubGraphQLError(message or f"{result_type} search returned errors")

    data_block = data.get("data", {})
    if not isinstance(data_block, dict):
        raise GitHubGraphQLError(f"{result_type} search missing data block")

    search_data = data_block.get("search")
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
        url = node.get("url")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(url, str) or not url.strip():
            continue

        snippet = _format_result_snippet(node, result_type)
        full_title = _format_result_title(node)
        results.append(WebSearchResult(title=full_title, link=url, snippet=snippet))

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
            _search_graphql(client, query, num_results, _ISSUE_QUERY, token, "issue"),
            return_exceptions=True,
        )

        discussion_list: list[WebSearchResult] = (
            discussion_results if isinstance(discussion_results, list) else []
        )
        issue_list: list[WebSearchResult] = (
            issue_results if isinstance(issue_results, list) else []
        )
        discussion_failed = isinstance(discussion_results, Exception)
        issue_failed = isinstance(issue_results, Exception)

        for raw in (discussion_results, issue_results):
            if isinstance(raw, asyncio.CancelledError):
                raise raw

        if discussion_failed and issue_failed:
            raise GitHubGraphQLError("Both GitHub GraphQL searches failed")

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

        if discussion_failed or issue_failed:
            logger.warning(
                "GitHub GraphQL returned partial results for query=%r (discussion_failed=%s, issue_failed=%s)",
                query,
                discussion_failed,
                issue_failed,
            )

        return merged[:num_results]

    return await run_provider(
        "github_graphql",
        query,
        num_results,
        request=_run,
        parse_response=lambda results: results,
        http_client=http_client,
        timeout_seconds=30.0,
    )
