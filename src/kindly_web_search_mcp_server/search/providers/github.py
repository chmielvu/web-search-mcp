"""GitHub code, Discussions, and Issues search provider.

Code search uses the REST ``/search/code`` endpoint and requests text-match
fragments. Issues and Discussions use GraphQL when ``GITHUB_TOKEN`` is set.
Code search remains available without a token, subject to GitHub's public rate
limits.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import settings
from ...heuristics.query_features import (
    BARE_REPO_PATTERN as _BARE_REPO_PATTERN,
    ORG_HINT_PATTERN as _ORG_HINT_PATTERN,
    REPO_HINT_PATTERN as _REPO_HINT_PATTERN,
    USER_HINT_PATTERN as _USER_HINT_PATTERN,
)
from .base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    run_provider,
    set_provider_request_metadata,
)

logger = logging.getLogger(__name__)

_GITHUB_API_URL = "https://api.github.com"
_GITHUB_GRAPHQL_URL = f"{_GITHUB_API_URL}/graphql"
_GITHUB_CODE_SEARCH_URL = f"{_GITHUB_API_URL}/search/code"
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_TEXT_MATCH_ACCEPT = "application/vnd.github.text-match+json"
_GITHUB_API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    """Raised when a GitHub endpoint returns an unusable response."""


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
          author { login }
          repository { nameWithOwner }
          comments { totalCount }
        }
      }
    }
  }
}
"""

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
}
"""


def _get_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return token if token else None


def _narrow_query(query: str) -> str:
    """Preserve explicit qualifiers and add an obvious repository qualifier."""
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

    return " ".join([normalized, *qualifier_parts]) if qualifier_parts else normalized


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
    total_comments = comments_data.get("totalCount", 0) if isinstance(comments_data, dict) else 0

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
    messages = [
        error.get("message", "").strip()
        for error in errors
        if isinstance(error, dict) and isinstance(error.get("message"), str)
    ]
    messages = [message for message in messages if message]
    return "; ".join(messages) or str(errors)


def _code_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": _GITHUB_TEXT_MATCH_ACCEPT,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _format_code_snippet(item: dict[str, Any]) -> str:
    matches = item.get("text_matches")
    fragments: list[str] = []
    if isinstance(matches, list):
        for match in matches:
            if not isinstance(match, dict):
                continue
            fragment = match.get("fragment")
            if isinstance(fragment, str) and fragment.strip():
                fragments.append(fragment.strip())
            if len(fragments) >= 3:
                break
    if fragments:
        return "\n".join(fragments)[:500]
    return "GitHub code match"


def _parse_code_results(data: object, num_results: int) -> list[WebSearchResult]:
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []

    results: list[WebSearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        html_url = item.get("html_url")
        path = item.get("path")
        repository = item.get("repository")
        repo_name = repository.get("full_name") if isinstance(repository, dict) else None
        if not isinstance(html_url, str) or not html_url.strip():
            continue
        if not isinstance(path, str) or not path.strip():
            path = "unknown file"
        if not isinstance(repo_name, str) or not repo_name.strip():
            repo_name = "unknown repository"
        results.append(
            WebSearchResult(
                title=f"{repo_name}: {path}",
                link=html_url.strip(),
                snippet=_format_code_snippet(item),
            )
        )
        if len(results) >= num_results:
            break
    return results


async def _search_code(
    client: httpx.AsyncClient,
    query: str,
    num_results: int,
    token: str | None,
) -> list[WebSearchResult]:
    response = await client.get(
        _GITHUB_CODE_SEARCH_URL,
        params={"q": query, "per_page": min(num_results, 100)},
        headers=_code_headers(token),
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise GitHubError("code search returned invalid JSON") from exc
    return _parse_code_results(data, num_results)


async def _search_graphql(
    client: httpx.AsyncClient,
    query: str,
    num_results: int,
    graphql_query: str,
    token: str,
    result_type: str,
) -> list[WebSearchResult]:
    variables = {"q": _narrow_query(query), "first": min(num_results, 20)}
    response = await client.post(
        _GITHUB_GRAPHQL_URL,
        json={"query": graphql_query, "variables": variables},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": _GITHUB_ACCEPT,
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        },
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise GitHubError(f"{result_type} search returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise GitHubError(f"{result_type} search returned an invalid payload")
    errors = data.get("errors")
    if errors:
        raise GitHubError(_format_graphql_errors(errors))

    data_block = data.get("data")
    search_data = data_block.get("search") if isinstance(data_block, dict) else None
    edges = search_data.get("edges") if isinstance(search_data, dict) else None
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
        results.append(
            WebSearchResult(
                title=_format_result_title(node),
                link=url.strip(),
                snippet=_format_result_snippet(node, result_type),
            )
        )
    return results


async def search_github(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search GitHub code plus authenticated Issues and Discussions.

    Code search is public and uses REST text-match fragments. A token enables
    parallel GraphQL searches for Issues and Discussions; GraphQL failures do
    not discard successful code results.
    """
    if not query.strip() or num_results < 1:
        return []

    token = _get_token()

    async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
        set_provider_request_metadata(
            ProviderRequestMetadata(
                provider="github",
                endpoint=_GITHUB_CODE_SEARCH_URL,
                auth_mode="token" if token else "anonymous",
                response_meta={"api_version": _GITHUB_API_VERSION, "token_present": bool(token)},
            )
        )
        requests: list[asyncio.Task[list[WebSearchResult]]] = [
            asyncio.create_task(_search_code(client, query, num_results, token))
        ]
        if token:
            requests.extend(
                [
                    asyncio.create_task(
                        _search_graphql(
                            client, query, num_results, _DISCUSSION_QUERY, token, "discussion"
                        )
                    ),
                    asyncio.create_task(
                        _search_graphql(client, query, num_results, _ISSUE_QUERY, token, "issue")
                    ),
                ]
            )

        responses = await asyncio.gather(*requests, return_exceptions=True)
        merged: list[WebSearchResult] = []
        seen_links: set[str] = set()
        errors: list[str] = []
        http_statuses: list[int] = []
        successful_branches = 0
        for response in responses:
            if isinstance(response, asyncio.CancelledError):
                raise response
            if isinstance(response, BaseException):
                errors.append(str(response)[:300])
                if isinstance(response, httpx.HTTPStatusError):
                    http_statuses.append(response.response.status_code)
                elif isinstance(response, ProviderRequestError) and response.metadata.http_status:
                    http_statuses.append(response.metadata.http_status)
                logger.warning("GitHub search branch failed for query=%r: %s", query, response)
                continue
            successful_branches += 1
            for result in response:
                if result.link in seen_links:
                    continue
                seen_links.add(result.link)
                merged.append(result)
                if len(merged) >= num_results:
                    break
        metadata = ProviderRequestMetadata(
            provider="github",
            endpoint=_GITHUB_CODE_SEARCH_URL,
            http_status=http_statuses[0] if http_statuses else 200,
            result_class=(
                "incomplete"
                if errors and merged
                else "error"
                if errors and not merged and successful_branches == 0
                else "nonempty"
                if merged
                else "empty"
            ),
            error_type="partial_provider_failure" if errors and merged else None,
            error_summary="; ".join(errors)[:500] if errors else None,
            auth_mode="token" if token else "anonymous",
            response_meta={
                "api_version": _GITHUB_API_VERSION,
                "token_present": bool(token),
                "successful_branches": successful_branches,
                "error_count": len(errors),
                "code_result_count": len(merged),
            },
        )
        set_provider_request_metadata(metadata)
        if errors and not merged and successful_branches == 0:
            raise ProviderRequestError(
                metadata.error_summary or "GitHub search failed", metadata=metadata
            )
        return merged

    return await run_provider(
        "github",
        query,
        num_results,
        request=_run,
        parse_response=lambda results: results,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
