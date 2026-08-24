"""GitHub Issues/Discussions channel for ``mode='issues'``.

Ported from the retired ``search/providers/github.py`` web adapter: two
authenticated GraphQL searches (``type: ISSUE`` and ``type: DISCUSSION``) run
concurrently and merge into provider-neutral evidence hits with honest
location metadata (URL precision — no file paths exist on conversations).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .github import _diagnostic, _graphql_error_message, _headers, _json, _token
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    ProviderResponse,
    build_location_metadata,
)
from .query import QueryPlan

LOGGER = logging.getLogger(__name__)

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_ISSUE_QUERY_MAX_CHARS = 256
# Code-scoped qualifiers have no meaning in GitHub's issue/discussion search;
# strip them while preserving conversational qualifiers (repo:, org:, user:,
# is:, state:, author:, label:, sort:, ...).
_CODE_ONLY_QUALIFIERS = re.compile(
    r"\b(?:-?(?:path|file|filename|extension|lang|language|rev|revision|patternType|select)):\S+",
    re.IGNORECASE,
)
_REGEX_TOKEN = re.compile(r"(?:^|\s)/(?:[^/\\\r\n]|\\.)+/(?:[imsu]*)(?=$|\s)")

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


def compile_issues_query(plan: QueryPlan, request: CodeSearchRequest) -> str:
    """Compile one conversation-search query from the parsed plan.

    Keeps free-text terms and conversation qualifiers, drops code-only
    qualifiers and regex tokens, and injects ``repo:`` scopes from explicit
    request repositories when the text does not already scope them.
    """

    text = plan.original_query or plan.search_text or request.query
    text = _REGEX_TOKEN.sub(" ", text)
    text = _CODE_ONLY_QUALIFIERS.sub(" ", text)
    text = " ".join(text.split())
    if request.repositories and not re.search(r"\brepo:\S+", text, re.IGNORECASE):
        scopes = " ".join(f"repo:{repo.strip('/')}" for repo in request.repositories[:3])
        text = f"{text} {scopes}".strip()
    if len(text) > _ISSUE_QUERY_MAX_CHARS:
        text = text[:_ISSUE_QUERY_MAX_CHARS].rsplit(" ", 1)[0].strip()
    return text


def _node_repository(node: dict[str, Any]) -> str | None:
    repository = node.get("repository")
    if isinstance(repository, dict):
        name = repository.get("nameWithOwner")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _parse_issue_node(
    node: dict[str, Any], *, result_type: str, query_variant: str, rank: int
) -> CodeSearchHit | None:
    title = node.get("title")
    url = node.get("url")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.strip():
        return None

    repository = _node_repository(node)
    author = node.get("author")
    author_login = author.get("login") if isinstance(author, dict) else None
    comments = node.get("comments")
    comment_count = (
        comments.get("totalCount")
        if isinstance(comments, dict) and isinstance(comments.get("totalCount"), int)
        else None
    )
    state = node.get("state") if isinstance(node.get("state"), str) else None
    upvotes = node.get("upvoteCount") if isinstance(node.get("upvoteCount"), int) else None
    updated = node.get("updatedAt") if isinstance(node.get("updatedAt"), str) else None

    snippet_parts = [f"{result_type} #{node.get('number')}"]
    if state:
        snippet_parts.append(f"state: {state}")
    if author_login:
        snippet_parts.append(f"author: {author_login}")
    if upvotes is not None:
        snippet_parts.append(f"upvotes: {upvotes}")
    if comment_count is not None:
        snippet_parts.append(f"comments: {comment_count}")

    hit = CodeSearchHit(
        location=build_location_metadata(
            repository=repository, path=None, url=url.strip()
        ),
        repository=repository,
        url=url.strip(),
        provider="github",
        query_variant=query_variant,
        search_rank=rank,
        evidence_role=result_type,
        title=title.strip(),
        snippet=" | ".join(snippet_parts),
        published_date=updated,
        source_metadata={
            "result_type": result_type,
            "number": node.get("number"),
            "state": state,
            "author": author_login,
            "upvotes": upvotes,
            "comment_count": comment_count,
        },
    )
    return hit


def _parse_search_edges(
    payload: dict[str, Any], *, result_type: str, query_variant: str, max_results: int
) -> list[CodeSearchHit]:
    data_block = payload.get("data")
    search_block = data_block.get("search") if isinstance(data_block, dict) else None
    edges = search_block.get("edges") if isinstance(search_block, dict) else None
    if not isinstance(edges, list):
        return []
    hits: list[CodeSearchHit] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict):
            continue
        hit = _parse_issue_node(
            node, result_type=result_type, query_variant=query_variant, rank=len(hits)
        )
        if hit is not None:
            hits.append(hit)
        if len(hits) >= max_results:
            break
    return hits


async def _search_variant(
    http_client: httpx.AsyncClient,
    token: str,
    graphql_query: str,
    variables: dict[str, Any],
    *,
    result_type: str,
    query_variant: str,
    max_results: int,
    query_for_diagnostics: str,
) -> tuple[list[CodeSearchHit], list[Diagnostic]]:
    try:
        response = await http_client.post(
            _GITHUB_GRAPHQL_URL,
            json={"query": graphql_query, "variables": variables},
            headers=_headers(token),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        LOGGER.warning(
            "GitHub %s search failed with HTTP %s", result_type, exc.response.status_code
        )
        return [], [
            _diagnostic(
                f"GitHub {result_type} search returned HTTP {exc.response.status_code}",
                failure_kind="provider",
                query=query_for_diagnostics,
                response=exc.response,
            )
        ]
    except httpx.HTTPError as exc:
        LOGGER.warning("GitHub %s search transport failure: %s", result_type, exc)
        return [], [
            _diagnostic(
                f"GitHub {result_type} search failed: {type(exc).__name__}",
                failure_kind="network",
                query=query_for_diagnostics,
            )
        ]

    payload = _json(response)
    if payload is None:
        return [], [
            _diagnostic(
                f"GitHub {result_type} search returned invalid JSON",
                failure_kind="provider",
                query=query_for_diagnostics,
            )
        ]
    errors = payload.get("errors")
    if errors:
        return [], [
            _diagnostic(
                _graphql_error_message(payload),
                failure_kind="provider",
                query=query_for_diagnostics,
            )
        ]
    hits = _parse_search_edges(
        payload, result_type=result_type, query_variant=query_variant, max_results=max_results
    )
    return hits, []


async def search_github_issues(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    """Search GitHub Issues and Discussions with bounded concurrent variants."""

    token = _token()
    compiled_queries: list[str] = []
    if not token:
        return ProviderResponse(
            provider="github",
            diagnostics=[
                _diagnostic(
                    "GITHUB_TOKEN or GH_TOKEN is required for GitHub Issues/Discussions search",
                    failure_kind="auth",
                    query=request.query,
                )
            ],
        )

    query_text = compile_issues_query(plan, request)
    first = min(max(request.max_results, 1), 20)
    compiled_queries = [
        f"type:ISSUE {query_text}",
        f"type:DISCUSSION {query_text}",
    ]

    issue_results, discussion_results = await asyncio.gather(
        _search_variant(
            http_client,
            token,
            _ISSUE_QUERY,
            {"q": query_text, "first": first},
            result_type="issue",
            query_variant=query_text,
            max_results=request.max_results,
            query_for_diagnostics=request.query,
        ),
        _search_variant(
            http_client,
            token,
            _DISCUSSION_QUERY,
            {"q": query_text, "first": first},
            result_type="discussion",
            query_variant=query_text,
            max_results=request.max_results,
            query_for_diagnostics=request.query,
        ),
        return_exceptions=True,
    )

    hits: list[CodeSearchHit] = []
    diagnostics: list[Diagnostic] = []
    successful_branches = 0
    for result in (issue_results, discussion_results):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            LOGGER.warning("GitHub issues-mode branch failed: %s", result)
            diagnostics.append(
                _diagnostic(
                    f"GitHub issues-mode branch failed ({type(result).__name__})",
                    query=request.query,
                )
            )
            continue
        branch_hits, branch_diagnostics = result
        successful_branches += 1
        diagnostics.extend(branch_diagnostics)
        hits.extend(branch_hits)

    seen_urls: set[str] = set()
    deduped: list[CodeSearchHit] = []
    for hit in hits:
        key = hit.url.casefold()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(hit)

    merged = deduped[: request.max_results]
    if successful_branches == 0:
        raise RuntimeError("GitHub issues-mode branches all failed") from None
    return ProviderResponse(
        provider="github",
        hits=merged,
        diagnostics=diagnostics,
        request_count=successful_branches,
        metadata={
            "compiled_queries": compiled_queries,
            "engine": "github_graphql_conversations",
        },
    )


__all__ = ["compile_issues_query", "search_github_issues"]
