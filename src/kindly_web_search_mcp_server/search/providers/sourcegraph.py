"""Sourcegraph public code search provider.

API: POST https://sourcegraph.com/.api/graphql
Supports literal and regex search across public repositories using GraphQL API v3.
"""

from __future__ import annotations

import logging
import os
from typing import Any

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

_SOURCEGRAPH_URL = "https://sourcegraph.com/.api/graphql"


class SourcegraphError(RuntimeError):
    """Raised when Sourcegraph returns an unusable GraphQL response."""


_SOURCEGRAPH_QUERY = """
query SearchCode($query: String!, $patternType: SearchPatternType!) {
  search(query: $query, version: V3, patternType: $patternType) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          file { name path url }
          repository { name url }
          lineMatches {
            preview
            lineNumber
            offsetAndLengths
          }
        }
      }
    }
  }
}
"""


async def search_sourcegraph(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    token: str | None = None,
    pattern_type: str | None = None,
) -> list[WebSearchResult]:
    """Search public Sourcegraph code with literal or RE2 regexp matching.

    Prefix the query with ``patternType:regexp`` to select Sourcegraph's RE2
    regexp mode; literal matching is the default. ``SOURCEGRAPH_TOKEN`` is
    optional and is used when configured to raise rate limits.
    """
    if not query.strip() or num_results < 1:
        return []

    pattern_type = pattern_type or ("regexp" if "patternType:regexp" in query else "literal")
    if pattern_type not in {"literal", "regexp"}:
        pattern_type = "literal"
    clean_query = query.replace("patternType:regexp", "").strip()
    token = token or os.environ.get("SOURCEGRAPH_TOKEN", "").strip() or None
    set_provider_request_metadata(
        ProviderRequestMetadata(
            provider="sourcegraph",
            endpoint=_SOURCEGRAPH_URL,
            auth_mode="token" if token else "anonymous",
            response_meta={"pattern_type": pattern_type, "token_present": bool(token)},
        )
    )

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"

        payload = {
            "query": _SOURCEGRAPH_QUERY,
            "variables": {
                "query": f"{clean_query} count:{num_results}",
                "patternType": pattern_type,
            },
        }
        resp = await client.post(
            _SOURCEGRAPH_URL,
            json=payload,
            headers=headers,
            timeout=settings.search_retrieve_budget_seconds,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourcegraphError("Sourcegraph returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise SourcegraphError("Sourcegraph returned an invalid payload")
        errors = data.get("errors")
        if errors:
            messages = [
                error.get("message", "").strip()
                for error in errors
                if isinstance(error, dict) and isinstance(error.get("message"), str)
            ]
            metadata = get_provider_request_metadata() or ProviderRequestMetadata("sourcegraph")
            error_summary = "; ".join(message for message in messages if message)
            metadata = ProviderRequestMetadata(
                provider=metadata.provider,
                endpoint=metadata.endpoint,
                http_status=resp.status_code,
                result_class="error",
                error_type="graphql_error",
                error_summary=error_summary[:500],
                auth_mode=metadata.auth_mode,
                response_meta=metadata.response_meta,
            )
            set_provider_request_metadata(metadata)
            raise ProviderRequestError(
                error_summary or "Sourcegraph GraphQL error", metadata=metadata
            )
        results_block = data.get("data", {}).get("search", {}).get("results", {})
        if isinstance(results_block, dict):
            metadata = get_provider_request_metadata() or ProviderRequestMetadata("sourcegraph")
            meta = dict(metadata.response_meta)
            meta.update(
                {
                    "match_count": results_block.get("matchCount"),
                    "limit_hit": results_block.get("limitHit"),
                }
            )
            set_provider_request_metadata(
                ProviderRequestMetadata(
                    provider=metadata.provider,
                    endpoint=metadata.endpoint,
                    http_status=resp.status_code,
                    auth_mode=metadata.auth_mode,
                    response_meta=meta,
                )
            )
        return data

    def _parse(data: dict[str, Any]) -> list[WebSearchResult]:
        results: list[WebSearchResult] = []
        matches = data.get("data", {}).get("search", {}).get("results", {}).get("results", [])
        if not isinstance(matches, list):
            return []

        for match in matches:
            if not isinstance(match, dict) or match.get("__typename") != "FileMatch":
                continue
            repo_info = match.get("repository", {})
            file_info = match.get("file", {})
            repo_name = (
                repo_info.get("name", "unknown") if isinstance(repo_info, dict) else "unknown"
            )
            file_path = (
                file_info.get("path", "unknown") if isinstance(file_info, dict) else "unknown"
            )
            file_url = file_info.get("url", "") if isinstance(file_info, dict) else ""
            if not isinstance(repo_name, str) or not repo_name.strip():
                repo_name = "unknown"
            if not isinstance(file_path, str) or not file_path.strip():
                file_path = "unknown"

            line_matches = match.get("lineMatches", [])
            if isinstance(line_matches, list):
                for line_match in line_matches[:3]:
                    if not isinstance(line_match, dict):
                        continue
                    line_num = line_match.get("lineNumber", 0)
                    if not isinstance(line_num, int):
                        line_num = 0
                    preview_value = line_match.get("preview", "")
                    preview = preview_value.strip()[:300] if isinstance(preview_value, str) else ""
                    results.append(
                        WebSearchResult(
                            title=f"{repo_name}: {file_path}:{line_num}",
                            link=f"https://sourcegraph.com{file_url}#L{line_num}"
                            if file_url
                            else f"https://sourcegraph.com/{repo_name}",
                            snippet=preview,
                        )
                    )
                    if len(results) >= num_results:
                        break
            if len(results) >= num_results:
                break

        metadata = get_provider_request_metadata() or ProviderRequestMetadata("sourcegraph")
        meta = dict(metadata.response_meta)
        meta["parsed_file_match_count"] = len(results)
        set_provider_request_metadata(
            ProviderRequestMetadata(
                provider=metadata.provider,
                endpoint=metadata.endpoint,
                http_status=metadata.http_status,
                auth_mode=metadata.auth_mode,
                response_meta=meta,
            )
        )
        return results

    return await run_provider(
        "sourcegraph",
        query,
        num_results,
        request=_do_request,
        parse_response=_parse,
        http_client=http_client,
        timeout_seconds=settings.search_retrieve_budget_seconds,
    )
