"""GitLab public code search provider.

API: GET https://gitlab.com/api/v4/search
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

from ...models import WebSearchResult
from ...settings import settings
from .base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    get_provider_request_metadata,
    run_provider,
    set_provider_request_metadata,
)

logger = logging.getLogger(__name__)

_GITLAB_SEARCH_URL = "https://gitlab.com/api/v4/search"


class GitLabError(ProviderRequestError):
    """Raised when GitLab returns an unusable search response."""


async def search_gitlab(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    token: str | None = None,
) -> list[WebSearchResult]:
    """Search public GitLab repository blobs.

    ``GITLAB_TOKEN`` is optional; when present it is sent as ``PRIVATE-TOKEN``
    to increase the available API quota.
    """
    if not query.strip() or num_results < 1:
        return []

    token = token or os.environ.get("GITLAB_TOKEN", "").strip() or None

    async def _do_request(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        set_provider_request_metadata(
            ProviderRequestMetadata(
                provider="gitlab",
                endpoint=_GITLAB_SEARCH_URL,
                auth_mode="private-token" if token else "anonymous",
            )
        )
        headers = {"Accept": "application/json"}
        if token:
            headers["PRIVATE-TOKEN"] = token

        params = {
            "scope": "blobs",
            "search": query,
            "per_page": min(num_results, 50),
        }
        resp = await client.get(_GITLAB_SEARCH_URL, params=params, headers=headers, timeout=15.0)
        metadata = get_provider_request_metadata() or ProviderRequestMetadata("gitlab")
        set_provider_request_metadata(
            ProviderRequestMetadata(
                provider=metadata.provider,
                endpoint=str(resp.request.url),
                http_status=resp.status_code,
                auth_mode=metadata.auth_mode,
                response_meta={"scope": "blobs", "token_present": bool(token)},
            )
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise GitLabError("GitLab returned invalid JSON") from exc
        if not isinstance(data, list):
            raise GitLabError("GitLab returned an invalid search payload")
        metadata = get_provider_request_metadata() or ProviderRequestMetadata("gitlab")
        meta = dict(metadata.response_meta)
        meta["parsed_row_count"] = len(data)
        set_provider_request_metadata(
            ProviderRequestMetadata(
                provider=metadata.provider,
                endpoint=metadata.endpoint,
                http_status=metadata.http_status,
                auth_mode=metadata.auth_mode,
                response_meta=meta,
            )
        )
        return data

    def _parse(data: list[dict[str, Any]]) -> list[WebSearchResult]:
        results: list[WebSearchResult] = []
        for item in data[:num_results]:
            if not isinstance(item, dict):
                continue
            project_id = item.get("project_id", "unknown")
            filename = item.get("filename", "unknown")
            path = item.get("path", filename)
            ref = item.get("ref", "main")
            startline = item.get("startline", 1)
            if not isinstance(project_id, (int, str)):
                project_id = "unknown"
            if not isinstance(filename, str) or not filename.strip():
                filename = "unknown"
            if not isinstance(path, str) or not path.strip():
                path = filename
            if not isinstance(ref, str) or not ref.strip():
                ref = "main"
            if not isinstance(startline, int):
                try:
                    startline = int(startline)
                except (TypeError, ValueError):
                    startline = 1
            raw_data = item.get("data", "")
            snippet = raw_data.strip()[:300] if isinstance(raw_data, str) else ""

            link = (
                f"https://gitlab.com/projects/{quote(str(project_id), safe='')}/-/blob/"
                f"{quote(ref, safe='')}/{quote(path, safe='/')}#L{startline}"
            )
            title = f"GitLab Project {project_id}: {filename}:{startline}"

            results.append(WebSearchResult(title=title, link=link, snippet=snippet))

        return results

    return await run_provider(
        "gitlab",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
