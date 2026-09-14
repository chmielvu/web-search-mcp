"""One shared GitHub GraphQL + REST paged transport.

The previous per-resolver GraphQL implementations (issues, pulls, discussions,
repo) are now consolidated here. Producers borrow :class:`FetchContext`'s client
and may attach the bearer token only for the lifetime of this request.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from .http_utils import raise_for_status, request_with_redirect_validation
from .models import AcquisitionError, FetchContext, ResolverTarget

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_API_BASE_URL = "https://api.github.com"


def resolve_github_token() -> str | None:
    """Return the configured bearer token when present."""

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return token or None


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kindly-web-search/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def github_graphql(
    ctx: FetchContext,
    *,
    query: str,
    variables: dict[str, Any],
    token: str | None,
    endpoint: str = GITHUB_GRAPHQL_URL,
) -> dict[str, Any]:
    """Run a single GraphQL query through the borrowed client."""

    headers = {"Content-Type": "application/json", **_auth_headers(token)}
    body = {"query": query, "variables": variables}
    response = await request_with_redirect_validation(
        ctx,
        endpoint,
        method="POST",
        headers=headers,
        follow_redirects=False,
        json_body=body,
    )
    if response.status_code == 401:
        raise AcquisitionError(
            code="unauthorized", message="GitHub rejected the supplied token", retryable=False
        )
    if response.status_code == 403:
        body_text = response.text or ""
        if (
            "rate limit" in body_text.lower()
            or response.headers.get("x-ratelimit-remaining") == "0"
        ):
            raise AcquisitionError(
                code="ratelimit", message="GitHub GraphQL rate limit hit", retryable=True
            )
        raise AcquisitionError(
            code="forbidden", message="GitHub GraphQL forbidden", retryable=False
        )
    raise_for_status(response, what="GitHub GraphQL")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AcquisitionError(
            code="graphql_unexpected",
            message=f"Unexpected GraphQL payload: {type(payload).__name__}",
        )
    if payload.get("errors"):
        first = payload["errors"][0]
        message = first.get("message") if isinstance(first, dict) else str(first)
        raise AcquisitionError(code="graphql_error", message=str(message)[:500])
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AcquisitionError(code="graphql_data_missing", message="GraphQL response missing data")
    return data


async def rest_get(
    ctx: FetchContext,
    *,
    path: str,
    token: str | None,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    base_url: str = GITHUB_API_BASE_URL,
) -> Any:
    """Perform one REST GET against the GitHub HTTP API."""

    headers = _auth_headers(token)
    if accept:
        headers["Accept"] = accept
    response = await request_with_redirect_validation(
        ctx,
        f"{base_url.rstrip('/')}/{path.lstrip('/')}",
        headers=headers,
        params=params,
        follow_redirects=True,
    )
    if response.status_code == 304:
        return None
    if response.status_code == 404:
        raise AcquisitionError(code="not_found", message=f"GitHub 404 for {path}", retryable=False)
    raise_for_status(response, what=f"GitHub REST {path}")
    if not response.content:
        return None
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


async def graphql_paginate_comments(
    ctx: FetchContext,
    *,
    query: str,
    token: str,
    variables: dict[str, Any],
    resource: str,
    resource_label: str,
    cursor_variable: str = "cursor",
    comments_key: str = "comments",
    max_pages: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Fetch one GraphQL resource and walk its paginated ``comments`` connection.

    Returns ``(resource_payload, comment_nodes, total_count)``. Each hop
    re-runs the query with the connection's ``endCursor``; the loop stops
    when ``hasNextPage`` is false, the cursor is missing, or the comment
    block is absent. ``cursor_variable`` names the query's cursor variable
    (GitHub uses ``cursor`` for issues/discussions and ``commentsCursor``
    for pull requests).
    """
    nodes: list[dict[str, Any]] = []
    total = 0
    payload: dict[str, Any] = {}
    cursor: str | None = None
    for _ in range(max_pages):
        data = await github_graphql(
            ctx,
            query=query,
            variables={**variables, cursor_variable: cursor},
            token=token,
        )
        repo_block = data.get("repository")
        if not isinstance(repo_block, dict):
            raise AcquisitionError(
                code="graphql_data_missing",
                message=f"GitHub {resource_label}: repository block missing",
            )
        raw_resource: Any = repo_block.get(resource)
        payload = raw_resource if isinstance(raw_resource, dict) else {}
        if not payload:
            raise AcquisitionError(
                code="graphql_data_missing",
                message=f"GitHub {resource_label}: {resource} block missing",
            )
        comments_block = payload.get(comments_key)
        if not isinstance(comments_block, dict):
            break
        total = int(comments_block.get("totalCount") or 0)
        if isinstance(nodes_list := comments_block.get("nodes"), list):
            nodes.extend(node for node in nodes_list if isinstance(node, dict))
        page_info = comments_block.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    return payload, nodes, total


async def fetch_readme_markdown(
    ctx: FetchContext, *, owner: str, repo: str, token: str | None
) -> str:
    """Return the decoded README of a repository (base64-decoded when needed)."""

    payload: Any = await rest_get(
        ctx,
        path=f"repos/{owner}/{repo}/readme",
        token=token,
        accept="application/vnd.github.raw",
    )
    if payload is None:
        # Fall back to JSON envelope and base64-decode the content field.
        json_payload: Any = await rest_get(
            ctx,
            path=f"repos/{owner}/{repo}/readme",
            token=token,
            accept="application/vnd.github+json",
        )
        if isinstance(json_payload, dict):
            encoded = json_payload.get("content")
            if isinstance(encoded, str):
                cleaned = encoded.replace("\n", "").strip()
                try:
                    return base64.b64decode(cleaned, validate=False).decode(
                        "utf-8", errors="replace"
                    )
                except Exception as exc:
                    raise AcquisitionError(code="readme_decode", message=str(exc)) from exc
        raise AcquisitionError(code="missing_readme", message="Repository has no README")
    return str(payload)


def repo_values(target: ResolverTarget) -> tuple[str, str, str | None]:
    """Pull ``owner``, ``repo``, optional ``ref`` from a resolver target values dict."""

    owner = target.values.get("owner") or ""
    repo = target.values.get("repo") or target.values.get("name") or ""
    ref = target.values.get("ref")
    if not owner or not repo:
        raise AcquisitionError(
            code="bad_target", message=f"Repository target missing owner/repo: {target}"
        )
    return owner, repo, ref


def thread_values(target: ResolverTarget) -> tuple[str, str, int]:
    """Pull ``owner``, ``repo``, and numeric ``number`` from a thread target."""

    owner = target.values.get("owner") or ""
    repo = target.values.get("repo") or ""
    try:
        number = int(target.values.get("number") or "")
    except ValueError as exc:
        raise AcquisitionError(code="bad_target", message=str(exc)) from exc
    if not owner or not repo:
        raise AcquisitionError(
            code="bad_target", message=f"Thread target missing owner/repo: {target}"
        )
    return owner, repo, number


__all__ = [
    "GITHUB_GRAPHQL_URL",
    "GITHUB_API_BASE_URL",
    "resolve_github_token",
    "github_graphql",
    "graphql_paginate_comments",
    "rest_get",
    "fetch_readme_markdown",
    "repo_values",
    "thread_values",
]
