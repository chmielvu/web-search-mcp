"""Sourcegraph stream-first code-search adapter with GraphQL fallback."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Any

import httpx

from ...settings import settings
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    ProviderResponse,
    TextFragment,
    build_location_metadata,
)
from .query import QueryPlan

_RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NETWORK_EXCEPTIONS = (httpx.TransportError, httpx.TimeoutException, TimeoutError, OSError)


def _resolve_max_retries() -> int:
    try:
        from ...search.providers.base import provider_retry_max_retries

        retries = provider_retry_max_retries("sourcegraph")
        if isinstance(retries, int) and retries >= 0:
            return retries
    except (ImportError, AttributeError):
        pass
    return 1


def _parse_retry_after_header(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        return None
LOGGER = logging.getLogger(__name__)
_SOURCEGRAPH_URL = "https://sourcegraph.com/.api/graphql"
_SOURCEGRAPH_STREAM_URL = "https://sourcegraph.com/.api/search/stream"

_SEARCH_QUERY = """
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
          lineMatches { preview lineNumber offsetAndLengths }
          symbols { name containerName kind url }
        }
      }
    }
  }
}
"""

def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def compile_sourcegraph_query(
    plan: QueryPlan,
    request: CodeSearchRequest,
    variant: str,
    kind: str,
) -> str:
    """Compile one internal variant into Sourcegraph-native syntax."""

    parts = ["type:file", "fork:no", "archived:no"]
    if kind == "regex":
        parts.append(f"content:{variant}")
    elif kind == "symbol":
        parts.append(f"sym:{_quote(variant)}")
    else:
        terms = re.findall(r'"[^"\n]+"|\S+', variant)
        parts.extend(
            f"content:{_quote(term.strip(chr(34)))}" for term in terms if term.strip(chr(34))
        )

    qualifier_map: dict[str, list[str]] = {}
    for key, value in plan.qualifiers:
        qualifier_map.setdefault(key, []).append(value)

    repositories = list(request.repositories) or qualifier_map.get("repo", [])
    for repository in repositories:
        clean_repo = repository.removeprefix("https://").removeprefix("http://").strip("/")
        if not re.match(r"^[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+/", clean_repo):
            clean_repo = f"github.com/{clean_repo}"
        escaped = re.escape(clean_repo)
        parts.append(f"repo:^{escaped}$")

    # Negative repo exclusions
    for neg_repo in qualifier_map.get("-repo", []):
        clean_neg = neg_repo.removeprefix("https://").removeprefix("http://").strip("/")
        if not re.match(r"^[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+/", clean_neg):
            clean_neg = f"github.com/{clean_neg}"
        neg_escaped = re.escape(clean_neg)
        parts.append(f"-repo:^{neg_escaped}$")
    language = request.language or next(iter(qualifier_map.get("language", [])), None)
    path = request.path or next(iter(qualifier_map.get("path", [])), None)
    filename = request.filename or next(iter(qualifier_map.get("filename", [])), None)
    extension = request.extension or next(iter(qualifier_map.get("extension", [])), None)
    revision = next(iter(qualifier_map.get("rev", []) + qualifier_map.get("revision", [])), None)

    if language:
        parts.append(f"lang:{_quote(language)}")
    for neg_lang in qualifier_map.get("-language", []) + qualifier_map.get("-lang", []):
        parts.append(f"-lang:{_quote(neg_lang)}")

    if path:
        parts.append(f"file:{_quote(path)}")
    for neg_path in qualifier_map.get("-path", []):
        parts.append(f"-file:{_quote(neg_path)}")

    if filename:
        parts.append(f"file:{_quote(filename)}")
    for neg_file in qualifier_map.get("-file", []) + qualifier_map.get("-filename", []):
        parts.append(f"-file:{_quote(neg_file)}")

    if extension:
        suffix = extension if extension.startswith(".") else f".{extension}"
        parts.append(f"file:{_quote(suffix + '$')}")

    if revision:
        parts.append(f"rev:{_quote(revision)}")

    return " ".join(parts)


def _token() -> str | None:
    for name in ("SOURCEGRAPH_TOKEN", "SOURCEGRAPH_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _diag(
    message: str,
    *,
    query: str | None = None,
    response: httpx.Response | None = None,
    outcome: str = "error",
    failure_kind: str = "provider",
    retry_after_seconds: float | None = None,
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    if retry_after_seconds is None and response is not None:
        raw_retry_after = response.headers.get("Retry-After")
        if raw_retry_after:
            retry_after_seconds = _parse_retry_after_header(raw_retry_after)
    return Diagnostic(
        provider="sourcegraph",
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=failure_kind,  # type: ignore[arg-type]
        status_code=response.status_code if response is not None else None,
        retry_after_seconds=retry_after_seconds,
        query=query,
        details=details or {},
    )

def _error_text(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return "Sourcegraph GraphQL error"
    messages = [
        item.get("message", "").strip()
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("message"), str)
    ]
    return "; ".join(item for item in messages if item)[:500] or "Sourcegraph GraphQL error"


def _parse_payload(
    payload: dict[str, Any],
    *,
    query_variant: str,
    max_results: int,
) -> tuple[list[CodeSearchHit], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    if payload.get("errors"):
        diagnostics.append(_diag(_error_text(payload), query=query_variant))
        return [], diagnostics
    results_block = ((payload.get("data") or {}).get("search") or {}).get("results")
    if not isinstance(results_block, dict):
        diagnostics.append(
            _diag("Sourcegraph returned no search result block", query=query_variant)
        )
        return [], diagnostics
    if results_block.get("limitHit"):
        diagnostics.append(
            _diag(
                "Sourcegraph hit its search limit",
                query=query_variant,
                outcome="partial",
                failure_kind="incomplete_index",
                details={"limit_hit": True},
            )
        )
    for field_name in ("timedout", "indexUnavailable"):
        if results_block.get(field_name):
            diagnostics.append(
                _diag(
                    f"Sourcegraph reported {field_name}",
                    query=query_variant,
                    outcome="partial",
                    failure_kind="incomplete_index",
                    details={field_name: True},
                )
            )

    matches = results_block.get("results", [])
    if not isinstance(matches, list):
        return [], diagnostics
    hits: list[CodeSearchHit] = []
    for match in matches:
        if not isinstance(match, dict) or match.get("__typename") != "FileMatch":
            continue
        repository = match.get("repository")
        file_info = match.get("file")
        repository_name = repository.get("name") if isinstance(repository, dict) else None
        if isinstance(repository_name, str):
            repository_name = repository_name.removeprefix("github.com/")
        path = file_info.get("path") if isinstance(file_info, dict) else None
        file_url = file_info.get("url") if isinstance(file_info, dict) else None
        if (
            not isinstance(repository_name, str)
            or not repository_name.strip()
            or not isinstance(path, str)
        ):
            continue
        if isinstance(file_url, str) and file_url.startswith("/"):
            file_url = f"https://sourcegraph.com{file_url}"
        if not isinstance(file_url, str) or not file_url.strip():
            file_url = f"https://sourcegraph.com/github.com/{repository_name}/-/blob/{path}"
        fragments: list[TextFragment] = []
        line_start: int | None = None
        line_end: int | None = None
        match_spans: list[dict[str, Any]] = []
        line_matches = match.get("lineMatches")
        if isinstance(line_matches, list):
            for line_match in line_matches:
                if not isinstance(line_match, dict):
                    continue
                preview = line_match.get("preview")
                if not isinstance(preview, str) or not preview.strip():
                    continue
                line_number = line_match.get("lineNumber")
                if not isinstance(line_number, int):
                    line_number = None
                if line_start is None:
                    line_start = line_number
                if line_number is not None:
                    line_end = max(line_end or line_number, line_number)
                offsets = line_match.get("offsetAndLengths")
                if isinstance(offsets, list):
                    for offset in offsets:
                        if isinstance(offset, list) and len(offset) == 2:
                            match_spans.append(
                                {"line": line_number, "column": offset[0], "length": offset[1]}
                            )
                fragments.append(
                    TextFragment(
                        text=preview.rstrip(),
                        line_start=line_number,
                        line_end=line_number,
                        match_metadata={"offsets": line_match.get("offsetAndLengths")},
                    )
                )
        symbols: list[dict[str, Any]] = []
        raw_symbols = match.get("symbols")
        if isinstance(raw_symbols, list):
            for symbol in raw_symbols[:20]:
                if isinstance(symbol, dict) and isinstance(symbol.get("name"), str):
                    symbols.append(
                        {
                            "name": symbol["name"],
                            "kind": symbol.get("kind"),
                            "container": symbol.get("containerName"),
                            "url": symbol.get("url"),
                        }
                    )
        hits.append(
            CodeSearchHit(
                repository=repository_name,
                path=path,
                url=file_url,
                provider="sourcegraph",
                query_variant=query_variant,
                search_rank=len(hits) + 1,
                result_kind="code_match",
                location=build_location_metadata(
                    repository=repository_name,
                    path=path,
                    url=file_url,
                    line_start=line_start,
                    line_end=line_end or line_start,
                    match_data_available=True,
                ),
                fragments=fragments,
                line_start=line_start,
                line_end=line_end or line_start,
                match_spans=match_spans,
                symbols=symbols,
                evidence_role="definition" if symbols else None,
                snippet="\n".join(fragment.text for fragment in fragments) or None,
                score_components={"match_count": float(match.get("matchCount") or 0.0)},
                source_metadata={
                    "symbols": symbols,
                    "repository_url": repository.get("url")
                    if isinstance(repository, dict)
                    else None,
                },
            )
        )
        if len(hits) >= max_results:
            break
    return hits, diagnostics


def _parse_stream_matches(
    data: list[dict[str, Any]],
    *,
    query_variant: str,
    max_results: int,
) -> list[CodeSearchHit]:
    hits: list[CodeSearchHit] = []
    for match in data:
        if not isinstance(match, dict):
            continue
        match_type = match.get("type", "content")
        if match_type not in ("content", "file", "symbol"):
            continue
        repo_raw = match.get("repository", "")
        if not isinstance(repo_raw, str) or not repo_raw.strip():
            continue
        repository_name = repo_raw.removeprefix("github.com/")
        path = match.get("path", "")
        if not isinstance(path, str) or not path.strip():
            continue

        language = match.get("language")
        repo_stars = match.get("repoStars")
        commit = match.get("commit")
        branches = match.get("branches")

        file_url = f"https://sourcegraph.com/github.com/{repository_name}/-/blob/{path}"
        fragments: list[TextFragment] = []
        line_start: int | None = None
        line_end: int | None = None
        match_spans: list[dict[str, Any]] = []

        # 1. Primary: chunkMatches (function/block level multi-line contexts)
        chunk_matches = match.get("chunkMatches")
        if isinstance(chunk_matches, list) and chunk_matches:
            for chunk in chunk_matches:
                if not isinstance(chunk, dict):
                    continue
                content = chunk.get("content", "")
                if not isinstance(content, str) or not content.strip():
                    continue
                content_start = chunk.get("contentStart", {})
                chunk_line = content_start.get("line", 0) + 1 if isinstance(content_start, dict) else 1
                lines = content.split("\n")
                chunk_end = chunk_line + len(lines) - 1
                if line_start is None:
                    line_start = chunk_line
                line_end = max(line_end or chunk_end, chunk_end)

                # Extract ranges inside chunk if present
                ranges = chunk.get("ranges")
                if isinstance(ranges, list):
                    for r in ranges:
                        if isinstance(r, dict):
                            start_pos = r.get("start", {})
                            end_pos = r.get("end", {})
                            if isinstance(start_pos, dict) and isinstance(end_pos, dict):
                                start_line_val = start_pos.get("line")
                                line_no = start_line_val + 1 if isinstance(start_line_val, int) else chunk_line
                                start_col = start_pos.get("column", 0) if isinstance(start_pos.get("column"), int) else 0
                                end_col = end_pos.get("column", 0) if isinstance(end_pos.get("column"), int) else 0
                                end_line_val = end_pos.get("line", start_line_val or 0)
                                if isinstance(end_line_val, int) and isinstance(start_line_val, int) and end_line_val > start_line_val:
                                    span_len = max(1, end_col)
                                else:
                                    span_len = max(1, end_col - start_col)
                                match_spans.append({
                                    "line": line_no,
                                    "column": start_col,
                                    "length": span_len,
                                })
                fragments.append(
                    TextFragment(
                        text=content.rstrip(),
                        line_start=chunk_line,
                        line_end=chunk_end,
                        match_metadata={"chunk": True},
                    )
                )

        # 2. Fallback: lineMatches
        if not fragments:
            line_matches = match.get("lineMatches")
            if isinstance(line_matches, list):
                for line_match in line_matches:
                    if not isinstance(line_match, dict):
                        continue
                    preview = line_match.get("preview")
                    if not isinstance(preview, str) or not preview.strip():
                        continue
                    line_number = line_match.get("lineNumber")
                    if not isinstance(line_number, int):
                        line_number = None
                    if line_start is None:
                        line_start = line_number
                    if line_number is not None:
                        line_end = max(line_end or line_number, line_number)
                    offsets = line_match.get("offsetAndLengths")
                    if isinstance(offsets, list):
                        for offset in offsets:
                            if isinstance(offset, list) and len(offset) == 2:
                                match_spans.append(
                                    {"line": line_number, "column": offset[0], "length": offset[1]}
                                )
                    fragments.append(
                        TextFragment(
                            text=preview.rstrip(),
                            line_start=line_number,
                            line_end=line_number,
                            match_metadata={"offsets": line_match.get("offsetAndLengths")},
                        )
                    )

        symbols: list[dict[str, Any]] = []
        raw_symbols = match.get("symbols")
        if isinstance(raw_symbols, list):
            for symbol in raw_symbols[:20]:
                if isinstance(symbol, dict) and isinstance(symbol.get("name"), str):
                    symbols.append(
                        {
                            "name": symbol["name"],
                            "kind": symbol.get("kind"),
                            "container": symbol.get("containerName"),
                            "url": symbol.get("url"),
                        }
                    )

        source_meta: dict[str, Any] = {
            "symbols": symbols,
            "repository_url": f"https://github.com/{repository_name}",
        }
        if isinstance(language, str) and language.strip():
            source_meta["language"] = language.strip()
        if isinstance(repo_stars, (int, float)):
            source_meta["stars"] = int(repo_stars)
        if isinstance(branches, list):
            source_meta["branches"] = branches

        revision_str = commit if isinstance(commit, str) and commit.strip() else None

        hits.append(
            CodeSearchHit(
                repository=repository_name,
                path=path,
                url=file_url,
                commit_oid=revision_str,
                provider="sourcegraph",
                query_variant=query_variant,
                search_rank=len(hits) + 1,
                result_kind="code_match",
                location=build_location_metadata(
                    repository=repository_name,
                    path=path,
                    url=file_url,
                    line_start=line_start,
                    line_end=line_end or line_start,
                    revision=revision_str,
                    match_data_available=True,
                ),
                fragments=fragments,
                line_start=line_start,
                line_end=line_end or line_start,
                match_spans=match_spans,
                symbols=symbols,
                evidence_role="definition" if symbols else None,
                snippet="\n".join(fragment.text for fragment in fragments) or None,
                score_components={"match_count": float(match.get("matchCount") or len(fragments))},
                source_metadata=source_meta,
            )
        )
        if len(hits) >= max_results:
            break
    return hits


async def _graphql_search_variant(
    http_client: httpx.AsyncClient,
    headers: dict[str, str],
    query_variant: str,
    var_name: str,
    var_kind: str,
    max_results: int,
    *,
    deadline: float | None = None,
) -> tuple[str, list[CodeSearchHit], list[Diagnostic], str]:
    """Fallback to Sourcegraph GraphQL when Stream API is unavailable."""
    if deadline is None:
        deadline = time.monotonic() + settings.search_retrieve_budget_seconds

    pattern_type = "regexp" if var_kind == "regex" else "literal"
    max_retries = _resolve_max_retries()
    last_response: httpx.Response | None = None
    last_exc: Exception | None = None
    attempts_made = 0

    for attempt in range(max_retries + 1):
        attempts_made = attempt + 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if attempt == 0:
                return (
                    query_variant,
                    [],
                    [
                        _diag(
                            "Sourcegraph GraphQL request timed out before execution",
                            query=query_variant,
                            failure_kind="network",
                        )
                    ],
                    "graphql_fallback",
                )
            break

        request_timeout = min(remaining, settings.search_retrieve_budget_seconds, 8.0)
        try:
            response = await http_client.post(
                _SOURCEGRAPH_URL,
                headers=headers,
                json={
                    "query": _SEARCH_QUERY,
                    "variables": {
                        "query": f"{query_variant} count:{max_results}",
                        "patternType": pattern_type,
                    },
                },
                timeout=request_timeout,
            )
            last_response = response
            last_exc = None
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            last_exc = exc
            last_response = None
            is_transport = isinstance(exc, _NETWORK_EXCEPTIONS)
            if is_transport and attempt < max_retries:
                rem_after = deadline - time.monotonic()
                if rem_after <= 0:
                    break
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), rem_after)
                if delay < rem_after and rem_after > 0:
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
            break

        if response.status_code == 200:
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                return (
                    query_variant,
                    [],
                    [_diag("Sourcegraph returned invalid JSON", query=query_variant)],
                    "graphql_fallback",
                )
            if not isinstance(payload, dict):
                return (
                    query_variant,
                    [],
                    [_diag("Sourcegraph returned an invalid payload", query=query_variant)],
                    "graphql_fallback",
                )
            branch_hits, branch_diagnostics = _parse_payload(
                payload,
                query_variant=var_name,
                max_results=max_results,
            )
            return query_variant, branch_hits, branch_diagnostics, "graphql_fallback"

        if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries:
            rem_after = deadline - time.monotonic()
            if rem_after <= 0:
                break
            retry_after = _parse_retry_after_header(response.headers.get("Retry-After"))
            if retry_after is not None:
                delay = retry_after
            else:
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), rem_after)
            if delay < rem_after and rem_after > 0:
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

        break
    if last_exc is not None:
        failure_kind = "network"
        details = {"retries_attempted": attempts_made - 1} if attempts_made > 1 else {}
        return (
            query_variant,
            [],
            [
                _diag(
                    f"Sourcegraph GraphQL fallback failed: {type(last_exc).__name__}: {last_exc}",
                    query=query_variant,
                    failure_kind=failure_kind,
                    details=details,
                )
            ],
            "graphql_fallback",
        )

    if last_response is not None:
        retry_after_val = _parse_retry_after_header(last_response.headers.get("Retry-After"))
        details = {"retries_attempted": attempts_made - 1} if attempts_made > 1 else {}
        return (
            query_variant,
            [],
            [
                _diag(
                    f"Sourcegraph GraphQL returned HTTP {last_response.status_code}",
                    query=query_variant,
                    response=last_response,
                    retry_after_seconds=retry_after_val,
                    details=details,
                )
            ],
            "graphql_fallback",
        )

    return (
        query_variant,
        [],
        [
            _diag(
                "Sourcegraph GraphQL request timed out before execution",
                query=query_variant,
                failure_kind="network",
            )
        ],
        "graphql_fallback",
    )


async def _stream_search_variant(
    http_client: httpx.AsyncClient,
    headers: dict[str, str],
    query_variant: str,
    var_name: str,
    var_kind: str,
    max_results: int,
    *,
    deadline: float | None = None,
) -> tuple[str, list[CodeSearchHit], list[Diagnostic], str]:
    """Primary Sourcegraph retrieval using the SSE Stream API."""
    if deadline is None:
        deadline = time.monotonic() + settings.search_retrieve_budget_seconds

    pattern_type = (
        "regexp"
        if var_kind == "regex"
        else ("keyword" if var_kind == "symbol" else "standard")
    )
    stream_params = {
        "q": f"{query_variant} count:{max_results}",
        "v": "V3",
        "t": pattern_type,
        "cm": "true",
        "cl": "5",
        "max-line-len": "300",
        "display": str(max_results),
    }
    stream_headers = dict(headers)
    stream_headers["Accept"] = "text/event-stream"

    max_retries = _resolve_max_retries()
    last_response: httpx.Response | None = None
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            LOGGER.warning(
                "Sourcegraph Stream API search deadline exceeded before attempt %d",
                attempt,
            )
            break

        request_timeout = min(remaining, settings.search_retrieve_budget_seconds, 10.0)
        try:
            response = await http_client.get(
                _SOURCEGRAPH_STREAM_URL,
                params=stream_params,
                headers=stream_headers,
                timeout=request_timeout,
            )
            last_response = response
            last_exc = None
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            last_exc = exc
            last_response = None
            is_transport = isinstance(exc, _NETWORK_EXCEPTIONS)
            if is_transport and attempt < max_retries:
                rem_after = deadline - time.monotonic()
                if rem_after <= 0:
                    break
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), rem_after)
                if delay < rem_after and rem_after > 0:
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
            break

        if response.status_code == 200:
            hits: list[CodeSearchHit] = []
            diagnostics: list[Diagnostic] = []
            raw_text = response.text
            events = raw_text.split("\n\n")

            for event_str in events:
                if not event_str.strip():
                    continue
                event_type = ""
                data_lines: list[str] = []
                for line in event_str.split("\n"):
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if not event_type or not data_lines:
                    continue
                data_str = "\n".join(data_lines)
                try:
                    event_data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if event_type == "matches" and isinstance(event_data, list):
                    parsed_matches = _parse_stream_matches(
                        event_data, query_variant=var_name, max_results=max_results - len(hits)
                    )
                    hits.extend(parsed_matches)
                elif event_type == "alert" and isinstance(event_data, dict):
                    title = event_data.get("title", "Sourcegraph Alert")
                    desc = event_data.get("description", "")
                    diagnostics.append(
                        _diag(
                            f"Sourcegraph Alert: {title} {desc}".strip(),
                            query=query_variant,
                            outcome="partial",
                            failure_kind="provider",
                            details=event_data,
                        )
                    )
                elif event_type == "progress" and isinstance(event_data, dict):
                    skipped = event_data.get("skipped", [])
                    if isinstance(skipped, list):
                        for item in skipped:
                            if isinstance(item, dict):
                                reason = item.get("reason", "")
                                title = item.get("title", "")
                                msg = item.get("message", "")
                                if reason in ("shard-match-limit", "match-limit"):
                                    diagnostics.append(
                                        _diag(
                                            f"Sourcegraph limit: {title or reason} - {msg}".strip(),
                                            query=query_variant,
                                            outcome="partial",
                                            failure_kind="incomplete_index",
                                            details=item,
                                        )
                                    )
                elif event_type == "done":
                    break

            if not hits and not diagnostics:
                return query_variant, hits, diagnostics, "stream"

            return query_variant, hits, diagnostics, "stream"

        if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries:
            rem_after = deadline - time.monotonic()
            if rem_after <= 0:
                break
            retry_after = _parse_retry_after_header(response.headers.get("Retry-After"))
            if retry_after is not None:
                delay = retry_after
            else:
                base = 0.5 * (2 ** min(attempt, 4))
                delay = min(random.uniform(0.0, base), rem_after)
            if delay < rem_after and rem_after > 0:
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

        break
    if last_exc is not None:
        LOGGER.warning(
            "Sourcegraph Stream API network error (%s), attempting GraphQL fallback",
            last_exc,
        )
    elif last_response is not None:
        LOGGER.warning(
            "Sourcegraph Stream API returned HTTP %s, attempting GraphQL fallback",
            last_response.status_code,
        )
    else:
        LOGGER.warning(
            "Sourcegraph Stream API deadline exceeded, attempting GraphQL fallback"
        )

    return await _graphql_search_variant(
        http_client,
        headers,
        query_variant,
        var_name,
        var_kind,
        max_results,
        deadline=deadline,
    )


async def search_sourcegraph(
    plan: QueryPlan,
    request: CodeSearchRequest,
    *,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    """Search Sourcegraph with stream-first retrieval and GraphQL fallback."""

    token = _token()
    headers = {"Content-Type": "application/json", "User-Agent": "web-search-mcp/code-search"}
    if token:
        headers["Authorization"] = f"token {token}"
    variants = plan.variant_pairs[: request.budget.max_query_variants] or (
        (plan.api_query, "lexical"),
    )
    deadline = time.monotonic() + settings.search_retrieve_budget_seconds

    async def _run_single_variant(
        var_name: str, var_kind: str
    ) -> tuple[str, list[CodeSearchHit], list[Diagnostic], str]:
        query_variant = compile_sourcegraph_query(plan, request, var_name, var_kind)
        if not query_variant:
            return "", [], [], ""
        return await _stream_search_variant(
            http_client,
            headers,
            query_variant,
            var_name,
            var_kind,
            request.max_results,
            deadline=deadline,
        )

    results = await asyncio.gather(
        *(_run_single_variant(v, k) for v, k in variants),
        return_exceptions=True,
    )
    all_hits: list[CodeSearchHit] = []
    diagnostics: list[Diagnostic] = []
    compiled_queries: list[str] = []
    transports: list[str] = []
    request_count = 0
    for res in results:
        if isinstance(res, BaseException):
            continue
        q_var, b_hits, b_diags, transport = res
        transports.append(transport)
        if q_var:
            compiled_queries.append(q_var)
            request_count += 1
        all_hits.extend(b_hits)
        diagnostics.extend(b_diags)
    return ProviderResponse(
        provider="sourcegraph",
        hits=all_hits,
        diagnostics=diagnostics,
        request_count=request_count,
        metadata={
            "auth_mode": "token" if token else "anonymous",
            "compiled_queries": compiled_queries,
            "transports": transports,
            "transport_summary": {
                "stream": transports.count("stream"),
                "graphql_fallback": transports.count("graphql_fallback"),
            },
        },
    )
