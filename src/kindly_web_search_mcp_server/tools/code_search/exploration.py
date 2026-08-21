"""Commit-pinned GitHub file and repository-tree continuation tool."""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import BaseModel, Field

from ...settings import settings
from ...utils.http_client import get_http_client
from ...utils.github import normalize_github_repository
from ...cache.code_search import get_code_search_cache, is_immutable_revision
from .github import _headers, _retry_after, _token

LOGGER = logging.getLogger(__name__)
_GITHUB_API_URL = "https://api.github.com"
_GITHUB_GRAPHQL_URL = f"{_GITHUB_API_URL}/graphql"

_FETCH_FILE_QUERY = """
query FetchCode($owner: String!, $repo: String!, $expression: String!) {
  repository(owner: $owner, name: $repo) {
    object(expression: $expression) {
      oid
      ... on Blob { byteSize isBinary text }
    }
  }
}
"""


class CodeFetchTreeEntry(BaseModel):
    path: str
    type: Literal["blob", "tree", "commit"] | str
    mode: str | None = None
    sha: str | None = None
    size: int | None = None
    url: str | None = None


class CodeFetchResponse(BaseModel):
    action: Literal["file", "tree"]
    outcome: Literal["ok", "partial", "error"]
    repository: str
    path: str | None = None
    ref: str | None = None
    commit_oid: str | None = None
    content: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    total_lines: int | None = None
    source_chars: int | None = None
    truncated: bool = False
    has_more: bool = False
    next_start_line: int | None = None
    tree: list[CodeFetchTreeEntry] = Field(default_factory=list)
    error: str | None = None
    retry_after_seconds: float | None = None


def _normalize_repository(repository: str) -> str:
    return normalize_github_repository(repository)


def _slice_lines(
    text: str,
    *,
    start_line: int | None,
    end_line: int | None,
    max_chars: int,
) -> tuple[str, int, int, bool]:
    lines = text.replace("\r\n", "\n").splitlines()
    total = len(lines)
    start = max(1, start_line or 1)
    end = min(total, max(start, end_line or total))
    content = "\n".join(lines[start - 1 : end])
    truncated = end < total
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
        returned_lines = max(1, content.count("\n") + 1)
        end = min(end, start + returned_lines - 1)
    return content, start, end, truncated


def _response_error(
    action: Literal["file", "tree"],
    repository: str,
    message: str,
    *,
    ref: str | None = None,
    response: httpx.Response | None = None,
) -> CodeFetchResponse:
    return CodeFetchResponse(
        action=action,
        outcome="error",
        repository=repository,
        ref=ref,
        error=message[:500],
        retry_after_seconds=_retry_after(response) if response is not None else None,
    )


async def code_fetch(
    repository: Annotated[
        str,
        Field(description="GitHub owner/name repository, for example prefecthq/fastmcp."),
    ],
    path: Annotated[
        str | None,
        Field(description="Repository-relative file path. Omit for a repository tree."),
    ] = None,
    ref: Annotated[
        str | None,
        Field(description="Branch, tag, or immutable commit SHA. Defaults to HEAD."),
    ] = None,
    action: Annotated[
        Literal["file", "tree"],
        Field(description="Fetch one file or list the repository tree."),
    ] = "file",
    start_line: Annotated[int | None, Field(description="One-based first line to return.")] = None,
    end_line: Annotated[int | None, Field(description="One-based last line to return.")] = None,
    max_chars: Annotated[int, Field(description="Maximum returned file characters.")] = 200_000,
    max_entries: Annotated[int, Field(description="Maximum tree entries.")] = 2_000,
    ctx: Context | None = CurrentContext(),
) -> CodeFetchResponse:
    """Read commit-pinned public GitHub code or list a repository tree.

    This is a continuation for ``code_search`` results. It does not issue a
    second code-search request and returns immutable revision/blob metadata when
    GitHub provides it.
    """
    try:
        normalized_repository = _normalize_repository(repository)
    except ValueError as exc:
        return _response_error(action, repository.strip(), str(exc), ref=ref)

    if action == "file" and not path:
        return _response_error("file", normalized_repository, "path is required for action='file'", ref=ref)
    if action == "tree" and path:
        return _response_error("tree", normalized_repository, "path must be omitted for action='tree'", ref=ref)

    token = _token()
    if not token:
        return _response_error(
            action,
            normalized_repository,
            "GITHUB_TOKEN or GH_TOKEN is required for code_fetch",
            ref=ref,
        )

    safe_max_chars = max(1, min(max_chars, 200_000))
    safe_max_entries = max(1, min(max_entries, 10_000))
    normalized_ref = (ref or "HEAD").strip() or "HEAD"
    client = await get_http_client()
    if ctx is not None:
        await ctx.report_progress(progress=10, total=100, message="Reading GitHub continuation...")

    if action == "tree":
        owner, repo = normalized_repository.split("/", 1)
        tree_url = f"{_GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(normalized_ref, safe='')}"
        try:
            response = await client.get(
                tree_url,
                params={"recursive": "1"},
                headers=_headers(token),
                timeout=settings.search_retrieve_budget_seconds,
            )
        except httpx.HTTPError as exc:
            return _response_error("tree", normalized_repository, f"GitHub tree request failed: {exc}", ref=ref)
        if response.status_code != 200:
            return _response_error(
                "tree",
                normalized_repository,
                f"GitHub tree returned HTTP {response.status_code}",
                ref=ref,
                response=response,
            )
        try:
            payload = response.json()
        except ValueError:
            return _response_error("tree", normalized_repository, "GitHub tree returned invalid JSON", ref=ref)
        raw_entries = payload.get("tree") if isinstance(payload, dict) else None
        if not isinstance(raw_entries, list):
            return _response_error("tree", normalized_repository, "GitHub tree payload omitted tree entries", ref=ref)
        entries: list[CodeFetchTreeEntry] = []
        for raw in raw_entries[:safe_max_entries]:
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                continue
            raw_type = raw.get("type")
            entry_type: str = raw_type if isinstance(raw_type, str) else "blob"
            entries.append(
                CodeFetchTreeEntry(
                    path=raw["path"],
                    type=entry_type,
                    mode=raw.get("mode") if isinstance(raw.get("mode"), str) else None,
                    sha=raw.get("sha") if isinstance(raw.get("sha"), str) else None,
                    size=raw.get("size") if isinstance(raw.get("size"), int) else None,
                    url=raw.get("url") if isinstance(raw.get("url"), str) else None,
                )
            )
        return CodeFetchResponse(
            action="tree",
            outcome="partial" if len(raw_entries) > len(entries) else "ok",
            repository=normalized_repository,
            ref=ref,
            tree=entries,
            truncated=len(raw_entries) > len(entries),
            has_more=len(raw_entries) > len(entries),
        )

    normalized_path = (path or "").strip().strip("/")
    cache = get_code_search_cache()
    cached = await cache.lookup_hydration(normalized_repository, normalized_path, normalized_ref)
    if cached is not None:
        cached_metadata = cached.get("metadata")
        metadata = cached_metadata if isinstance(cached_metadata, dict) else {}
        text = cached["text"]
        content, returned_start, returned_end, truncated = _slice_lines(
            text,
            start_line=start_line,
            end_line=end_line,
            max_chars=safe_max_chars,
        )
        return CodeFetchResponse(
            action="file",
            outcome="ok",
            repository=normalized_repository,
            path=normalized_path,
            ref=ref,
            commit_oid=(
                metadata.get("blob_oid")
                if isinstance(metadata.get("blob_oid"), str)
                else None
            ),
            content=content,
            line_start=returned_start,
            line_end=returned_end,
            total_lines=len(text.splitlines()),
            source_chars=len(text),
            truncated=truncated,
            has_more=truncated,
            next_start_line=returned_end + 1 if truncated else None,
        )

    owner, repo = normalized_repository.split("/", 1)
    try:
        response = await client.post(
            _GITHUB_GRAPHQL_URL,
            headers=_headers(token),
            json={
                "query": _FETCH_FILE_QUERY,
                "variables": {
                    "owner": owner,
                    "repo": repo,
                    "expression": f"{normalized_ref}:{normalized_path}",
                },
            },
            timeout=settings.search_retrieve_budget_seconds,
        )
    except httpx.HTTPError as exc:
        return _response_error("file", normalized_repository, f"GitHub file request failed: {exc}", ref=ref)
    if response.status_code != 200:
        return _response_error(
            "file",
            normalized_repository,
            f"GitHub file returned HTTP {response.status_code}",
            ref=ref,
            response=response,
        )
    try:
        payload = response.json()
    except ValueError:
        return _response_error("file", normalized_repository, "GitHub file returned invalid JSON", ref=ref)
    if not isinstance(payload, dict) or payload.get("errors"):
        return _response_error("file", normalized_repository, "GitHub file lookup failed", ref=ref)
    repository_payload = payload.get("data", {}).get("repository") if isinstance(payload.get("data"), dict) else None
    blob = repository_payload.get("object") if isinstance(repository_payload, dict) else None
    if not isinstance(blob, dict):
        return _response_error("file", normalized_repository, "GitHub file was not found", ref=ref)
    if blob.get("isBinary"):
        return _response_error("file", normalized_repository, "GitHub file is binary", ref=ref)
    text = blob.get("text")
    if not isinstance(text, str):
        return _response_error("file", normalized_repository, "GitHub file has no textual content", ref=ref)

    blob_oid = blob.get("oid") if isinstance(blob.get("oid"), str) else None
    if blob_oid and len(text) <= 1_000_000:
        await cache.store_hydration(
            normalized_repository,
            normalized_path,
            blob_oid,
            text,
            metadata={"blob_oid": blob_oid},
        )
        if is_immutable_revision(normalized_ref) and normalized_ref != blob_oid:
            await cache.store_hydration(
                normalized_repository,
                normalized_path,
                normalized_ref,
                text,
                metadata={"blob_oid": blob_oid},
            )
    content, returned_start, returned_end, truncated = _slice_lines(
        text,
        start_line=start_line,
        end_line=end_line,
        max_chars=safe_max_chars,
    )
    return CodeFetchResponse(
        action="file",
        outcome="ok",
        repository=normalized_repository,
        path=normalized_path,
        ref=ref,
        commit_oid=blob_oid,
        content=content,
        line_start=returned_start,
        line_end=returned_end,
        total_lines=len(text.splitlines()),
        source_chars=len(text),
        truncated=truncated,
        has_more=truncated,
        next_start_line=returned_end + 1 if truncated else None,
    )


__all__ = ["CodeFetchResponse", "CodeFetchTreeEntry", "code_fetch"]
