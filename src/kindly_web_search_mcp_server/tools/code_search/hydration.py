"""Canonical source file hydration using GraphQL with unauthenticated REST fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict

import httpx

from ...settings import settings
from .models import CodeSearchHit, Diagnostic
from .windows import FileSource

LOGGER = logging.getLogger(__name__)

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_GITHUB_API_VERSION = "2022-11-28"
_DEFAULT_MAX_FILES = 25
_DEFAULT_MAX_CHARS_PER_FILE = 200_000


def _token() -> str | None:
    """Retrieve GitHub token from environment."""
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if value:
        return value
    value = os.environ.get("GH_TOKEN", "").strip()
    return value or None


def _headers(
    token: str | None, *, accept: str = "application/vnd.github.v3+json"
) -> dict[str, str]:
    """Build GitHub API request headers."""
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "web-search-mcp/code-search",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_graphql_query(group: list[CodeSearchHit]) -> tuple[str, dict[str, str]]:
    """Construct a batch GraphQL query with correctly-typed variables."""
    variables: dict[str, str] = {}
    declarations: list[str] = []
    fields: list[str] = []

    for index, hit in enumerate(group):
        repository = hit.repository or ""
        owner, _, repo_name = repository.partition("/")
        variables[f"owner{index}"] = owner
        variables[f"repo{index}"] = repo_name
        declarations.append(f"$owner{index}: String!, $repo{index}: String!")

        if hit.sha and re.match(r"^[0-9a-f]{40}$", hit.sha, re.I):
            variables[f"oid{index}"] = hit.sha
            declarations.append(f"$oid{index}: GitObjectID!")
            fields.append(
                f"  f{index}: repository(owner: $owner{index}, name: $repo{index}) {{\n"
                f"    object(oid: $oid{index}) {{ oid ... on Blob {{ byteSize isBinary text }} }}\n"
                f"  }}"
            )
        else:
            commit = hit.commit_oid or "HEAD"
            path = hit.path or ""
            variables[f"expr{index}"] = f"{commit}:{path}"
            declarations.append(f"$expr{index}: String!")
            fields.append(
                f"  f{index}: repository(owner: $owner{index}, name: $repo{index}) {{\n"
                f"    object(expression: $expr{index}) {{ oid ... on Blob {{ byteSize isBinary text }} }}\n"
                f"  }}"
            )

    query = f"query HydrateSources({', '.join(declarations)}) {{\n" + "\n".join(fields) + "\n}"
    return query, variables


async def _hydrate_via_rest(
    hit: CodeSearchHit,
    *,
    http_client: httpx.AsyncClient,
    token: str | None,
    max_chars_per_file: int,
) -> tuple[FileSource | None, Diagnostic | None]:
    """Fetch raw file content via the GitHub Contents API as fallback."""
    if not hit.repository or not hit.path:
        return None, None

    owner, _, repo_name = hit.repository.partition("/")
    if not owner or not repo_name:
        return None, None

    ref = hit.commit_oid or hit.sha
    params = {"ref": ref} if ref else {}
    url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{hit.path}"
    headers = _headers(token, accept="application/vnd.github.raw+json")

    try:
        response = await http_client.get(
            url,
            headers=headers,
            params=params,
            timeout=settings.search_retrieve_budget_seconds,
        )
    except Exception as exc:
        diag = Diagnostic(
            message=f"REST hydration network failure for {hit.repository}:{hit.path}: {exc}",
            outcome="partial",
            failure_kind="network",
            provider="github",
            query=hit.repository,
        )
        return None, diag

    if response.status_code == 404:
        diag = Diagnostic(
            message=f"File not found in GitHub contents: {hit.repository}:{hit.path}",
            outcome="partial",
            failure_kind="not_found",
            provider="github",
            query=hit.repository,
        )
        return None, diag

    if response.status_code != 200:
        diag = Diagnostic(
            message=f"GitHub REST hydration returned HTTP {response.status_code} for {hit.repository}:{hit.path}",
            outcome="partial",
            failure_kind="auth" if response.status_code in {401, 403} else "provider",
            provider="github",
            query=hit.repository,
        )
        return None, diag

    raw_text = response.text
    if len(raw_text) > max_chars_per_file:
        raw_text = raw_text[:max_chars_per_file]
        diag = Diagnostic(
            message=f"Truncated large file {hit.repository}:{hit.path} to {max_chars_per_file} chars",
            outcome="partial",
            failure_kind="budget",
            provider="github",
            query=hit.repository,
        )
    else:
        diag = None

    source = FileSource(repository=hit.repository, path=hit.path, text=raw_text)
    return source, diag


async def hydrate_sources(
    candidates: list[CodeSearchHit],
    *,
    http_client: httpx.AsyncClient,
    token: str | None = None,
    max_files: int = _DEFAULT_MAX_FILES,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS_PER_FILE,
) -> tuple[dict[tuple[str, str], FileSource], list[Diagnostic]]:
    """Fetch canonical file content across all candidate hits."""
    if not candidates:
        return {}, []

    auth_token = token or _token()
    diagnostics: list[Diagnostic] = []
    sources: dict[tuple[str, str], FileSource] = {}

    # Target deduplication: unique (repository, path) capped at max_files
    seen_targets: set[tuple[str, str]] = set()
    selected_hits: list[CodeSearchHit] = []
    for hit in candidates:
        if not hit.repository or not hit.path:
            continue
        key = (hit.repository.casefold(), hit.path.replace("\\", "/").casefold())
        if key in seen_targets:
            continue
        seen_targets.add(key)
        selected_hits.append(hit)
        if len(selected_hits) >= max_files:
            break

    pending_rest: list[CodeSearchHit] = []

    if auth_token:
        groups: dict[str, list[CodeSearchHit]] = defaultdict(list)
        for hit in selected_hits:
            groups[hit.repository or ""].append(hit)

        for repository, group in groups.items():
            query, variables = _build_graphql_query(group)
            try:
                response = await http_client.post(
                    _GITHUB_GRAPHQL_URL,
                    headers=_headers(auth_token),
                    json={"query": query, "variables": variables},
                    timeout=settings.search_retrieve_budget_seconds,
                )
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        message=f"GitHub GraphQL hydration error: {exc}",
                        outcome="partial",
                        failure_kind="network",
                        provider="github",
                        query=repository,
                    )
                )
                pending_rest.extend(group)
                continue

            if response.status_code in {401, 403}:
                diagnostics.append(
                    Diagnostic(
                        message=f"GitHub GraphQL returned HTTP {response.status_code}; falling back to REST",
                        outcome="partial",
                        failure_kind="auth" if response.status_code == 401 else "rate_limit",
                        provider="github",
                        query=repository,
                    )
                )
                pending_rest.extend(group)
                continue

            if response.status_code != 200:
                diagnostics.append(
                    Diagnostic(
                        message=f"GitHub GraphQL returned HTTP {response.status_code}",
                        outcome="partial",
                        failure_kind="provider",
                        provider="github",
                        query=repository,
                    )
                )
                pending_rest.extend(group)
                continue

            try:
                payload = response.json()
            except Exception:
                diagnostics.append(
                    Diagnostic(
                        message="Invalid JSON from GitHub GraphQL hydration",
                        outcome="partial",
                        failure_kind="provider",
                        provider="github",
                        query=repository,
                    )
                )
                pending_rest.extend(group)
                continue

            data = payload.get("data") if isinstance(payload, dict) else None
            for index, hit in enumerate(group):
                node = data.get(f"f{index}") if isinstance(data, dict) else None
                blob = node.get("object") if isinstance(node, dict) else None
                if not isinstance(blob, dict):
                    pending_rest.append(hit)
                    continue
                if blob.get("isBinary"):
                    diagnostics.append(
                        Diagnostic(
                            message=f"Skipping binary file {hit.repository}:{hit.path}",
                            outcome="partial",
                            failure_kind="provider",
                            provider="github",
                            query=repository,
                            details={"binary": True},
                        )
                    )
                    continue

                raw_text = blob.get("text")
                if not isinstance(raw_text, str):
                    pending_rest.append(hit)
                    continue

                if len(raw_text) > max_chars_per_file:
                    raw_text = raw_text[:max_chars_per_file]
                    diagnostics.append(
                        Diagnostic(
                            message=f"Truncated large file {hit.repository}:{hit.path} to {max_chars_per_file} chars",
                            outcome="partial",
                            failure_kind="budget",
                            provider="github",
                            query=repository,
                        )
                    )

                source = FileSource(
                    repository=hit.repository or "",
                    path=hit.path or "",
                    text=raw_text,
                )
                sources[source.key] = source
    else:
        pending_rest.extend(selected_hits)

    # Hydrate unauthenticated or GraphQL-failed files via REST fallback
    if pending_rest:
        semaphore = asyncio.Semaphore(5)

        async def fetch_one(hit: CodeSearchHit) -> tuple[FileSource | None, Diagnostic | None]:
            async with semaphore:
                return await _hydrate_via_rest(
                    hit,
                    http_client=http_client,
                    token=auth_token,
                    max_chars_per_file=max_chars_per_file,
                )

        results = await asyncio.gather(
            *(fetch_one(hit) for hit in pending_rest), return_exceptions=True
        )
        for res in results:
            if isinstance(res, Exception):
                diagnostics.append(
                    Diagnostic(
                        message=f"Hydration task raised exception: {res}",
                        outcome="partial",
                        failure_kind="network",
                        provider="github",
                    )
                )
            elif isinstance(res, tuple):
                source, diag = res
                if diag:
                    diagnostics.append(diag)
                if source:
                    sources[source.key] = source

    return sources, diagnostics
