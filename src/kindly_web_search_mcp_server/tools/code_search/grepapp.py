"""grep.app adapter: REST-first search with the streamable-HTTP MCP relay as fallback."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import replace
from typing import Any

import httpx

from ...settings import settings
from .models import (
    CodeSearchHit,
    CodeSearchRequest,
    Diagnostic,
    FailureKind,
    ProviderResponse,
    TextFragment,
    build_location_metadata,
)
from .query import QueryPlan

LOGGER = logging.getLogger(__name__)
_GREPP_APP_MCP_URL = "https://mcp.grep.app"
_GREP_APP_REST_URL = "https://grep.app/api/search"
_MCP_ATTEMPTS = 3
_MCP_RETRY_BASE_SECONDS = 0.25


def _diagnostic(
    message: str,
    *,
    query: str | None = None,
    outcome: str = "error",
    details: dict[str, Any] | None = None,
    failure_kind: FailureKind | None = None,
    status_code: int | None = None,
) -> Diagnostic:
    return Diagnostic(
        provider="grep.app",
        outcome=outcome,  # type: ignore[arg-type]
        message=message[:500],
        failure_kind=failure_kind
        or ("network" if "request" in message.casefold() else "provider"),
        query=query,
        details=details or {},
        status_code=status_code,
    )


def _exception_chain(exc: BaseException) -> list[dict[str, Any]]:
    """Flatten task-group failures into bounded, agent-visible diagnostics."""

    pending = [exc]
    failures: list[dict[str, Any]] = []
    while pending and len(failures) < 8:
        current = pending.pop(0)
        if isinstance(current, BaseExceptionGroup):
            pending[0:0] = list(current.exceptions)
            continue
        failure: dict[str, Any] = {
            "type": type(current).__name__,
            "message": str(current)[:300],
        }
        if isinstance(current, httpx.HTTPStatusError):
            failure["status_code"] = current.response.status_code
            failure["url"] = str(current.request.url)
        failures.append(failure)
    return failures


def _text_blocks(result: Any) -> list[str]:
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if not isinstance(content, list):
        return []
    blocks: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if isinstance(text, str) and text.strip():
            blocks.append(text)
    return blocks


async def _call_grepapp_mcp(
    arguments: dict[str, Any], http_client: httpx.AsyncClient
) -> dict[str, Any]:
    """Call grep.app using its stateless JSON-RPC-over-SSE contract."""

    request_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "searchGitHub", "arguments": arguments},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(_MCP_ATTEMPTS):
        try:
            async with http_client.stream(
                "POST",
                _GREPP_APP_MCP_URL,
                headers=headers,
                json=request_body,
                timeout=httpx.Timeout(15.0, connect=5.0),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if not raw:
                        continue
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue
                    error = payload.get("error")
                    if isinstance(error, dict):
                        raise RuntimeError(str(error.get("message") or "grep.app MCP error"))
                    result = payload.get("result")
                    if isinstance(result, dict):
                        return result
                raise ValueError("grep.app MCP stream ended without a result event")
        except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < _MCP_ATTEMPTS:
                await asyncio.sleep(_MCP_RETRY_BASE_SECONDS * (attempt + 1))
    assert last_error is not None
    raise last_error


def parse_grepapp_text(text: str, *, query_variant: str, max_results: int) -> list[CodeSearchHit]:
    """Parse the relay's human-readable Repository/Path/URL/snippet blocks."""

    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip().casefold().startswith("repository:") and current:
            blocks.append(current)
            current = []
        if line.strip():
            current.append(line.rstrip())
    if current:
        blocks.append(current)

    hits: list[CodeSearchHit] = []
    for block in blocks:
        fields: dict[str, str] = {}
        snippet_lines: list[str] = []
        first_line: int | None = None
        last_line: int | None = None
        seen_snippet = False
        for line in block:
            match = re.match(r"^\s*(Repository|Path|URL|License)\s*:\s*(.*?)\s*$", line, re.I)
            if match and not seen_snippet:
                fields[match.group(1).casefold()] = match.group(2).strip()
                continue
            snippet_header = re.match(
                r"^\s*---\s*Snippet\s+\d+\s*\(Line\s+(\d+)\)\s*---\s*$",
                line,
                re.IGNORECASE,
            )
            if snippet_header:
                seen_snippet = True
                header_line = int(snippet_header.group(1))
                if first_line is None:
                    first_line = header_line
                last_line = header_line
                continue
            if line.strip().casefold() == "snippets:":
                seen_snippet = True
                continue
            numbered = re.match(r"^\s*(\d+)\s*(?:\||:)\s?(.*)$", line)
            if numbered:
                seen_snippet = True
                numbered_line = int(numbered.group(1))
                if first_line is None:
                    first_line = numbered_line
                last_line = numbered_line
                snippet_lines.append(numbered.group(2).rstrip())
                continue
            if line.strip() and not line.lstrip().startswith(
                ("Repository:", "Path:", "URL:", "License:")
            ):
                seen_snippet = True
                snippet_lines.append(line.rstrip())
        repository = fields.get("repository")
        path = fields.get("path")
        url = fields.get("url")
        if not repository or not path or not url:
            continue
        snippet = "\n".join(snippet_lines).strip()[:4000]
        hits.append(
            CodeSearchHit(
                repository=repository,
                path=path,
                url=url,
                provider="grep.app",
                query_variant=query_variant,
                search_rank=len(hits) + 1,
                result_kind="code_match",
                fragments=[
                    TextFragment(
                        text=snippet[:50_000],
                        line_start=first_line,
                        line_end=last_line or first_line,
                    )
                ]
                if snippet
                else [],
                line_start=first_line,
                line_end=last_line or first_line,
                location=build_location_metadata(
                    repository=repository,
                    path=path,
                    url=url,
                    line_start=first_line,
                    line_end=last_line or first_line,
                    match_data_available=bool(snippet),
                ),
                snippet=snippet or None,
                source_metadata={"license": fields.get("license")},
            )
        )
        if len(hits) >= max_results:
            break
    return hits


def _raw(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("raw")
        return candidate if isinstance(candidate, str) else None
    return None


def _parse_rest_payload(
    payload: Any, *, query_variant: str, max_results: int
) -> list[CodeSearchHit]:
    raw_hits = ((payload.get("hits") or {}).get("hits")) if isinstance(payload, dict) else None
    if not isinstance(raw_hits, list):
        return []
    hits: list[CodeSearchHit] = []
    for item in raw_hits[:max_results]:
        if not isinstance(item, dict):
            continue
        repository, path = _raw(item.get("repo")), _raw(item.get("path"))
        raw_ref = _raw(item.get("branch"))
        branch = raw_ref or "HEAD"
        content = item.get("content")
        snippet_html = content.get("snippet") if isinstance(content, dict) else ""
        snippet = html.unescape(re.sub(r"<[^>]+>", "", snippet_html or "")).strip()
        if not repository or not path:
            continue
        url = f"https://github.com/{repository}/blob/{branch}/{path}"
        hits.append(
            CodeSearchHit(
                repository=repository,
                path=path,
                url=url,
                provider="grep.app",
                query_variant=query_variant,
                search_rank=len(hits) + 1,
                result_kind="code_match",
                snippet=snippet[:3000] or None,
                fragments=[TextFragment(text=snippet[:1200])] if snippet else [],
                location=build_location_metadata(
                    repository=repository,
                    path=path,
                    url=url,
                    ref=raw_ref,
                    match_data_available=bool(snippet),
                ),
                source_metadata={
                    "transport": "rest",
                    **({"branch": raw_ref} if raw_ref else {}),
                },
            )
        )
    return hits


async def _search_grepapp_single_repo_rest(
    expression: str,
    kind: str,
    repository: str | None,
    language: str | None,
    path: str | None,
    max_results: int,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    params: dict[str, Any] = {"q": expression, "regexp": str(kind == "regex").lower()}
    if language:
        params["f.lang"] = language
    if repository:
        params["f.repo"] = repository
    if path:
        params["f.path"] = path
    last_response: httpx.Response | None = None
    last_exc: BaseException | None = None
    request_count = 0
    for attempt in range(2):
        request_count += 1
        if attempt > 0:
            # grep.app publishes no Retry-After header; use a fixed backoff.
            await asyncio.sleep(2.0)
        try:
            last_response = await http_client.get(
                _GREP_APP_REST_URL,
                params=params,
                timeout=settings.search_retrieve_budget_seconds,
            )
        except (httpx.HTTPError, TimeoutError) as exc:
            last_exc = exc
            break
        if last_response.status_code != 429:
            break
    if last_exc is not None:
        return ProviderResponse(
            provider="grep.app",
            diagnostics=[
                _diagnostic(
                    f"grep.app REST request failed ({type(last_exc).__name__})", query=expression
                )
            ],
            request_count=request_count,
        )
    assert last_response is not None
    if last_response.status_code == 429:
        return ProviderResponse(
            provider="grep.app",
            diagnostics=[
                _diagnostic(
                    "grep.app REST rate-limited (HTTP 429); retry exhausted",
                    query=expression,
                    outcome="partial",
                    failure_kind="rate_limit",
                    status_code=429,
                )
            ],
            request_count=request_count,
        )
    if last_response.status_code != 200:
        return ProviderResponse(
            provider="grep.app",
            diagnostics=[
                _diagnostic(
                    f"grep.app REST returned HTTP {last_response.status_code}", query=expression
                )
            ],
            request_count=request_count,
        )
    try:
        payload = last_response.json()
    except ValueError:
        return ProviderResponse(
            provider="grep.app",
            diagnostics=[_diagnostic("grep.app REST returned invalid JSON", query=expression)],
            request_count=request_count,
        )
    return ProviderResponse(
        provider="grep.app",
        hits=_parse_rest_payload(payload, query_variant=expression, max_results=max_results),
        request_count=request_count,
        metadata={"compiled_queries": [expression], "transport": "rest"},
    )


async def _search_grepapp_single_repo(
    expression: str,
    kind: str,
    repository: str | None,
    request: CodeSearchRequest,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    rest_response = await _search_grepapp_single_repo_rest(
        expression,
        kind,
        repository,
        request.language,
        request.path,
        request.max_results,
        http_client,
    )
    if rest_response.hits:
        return rest_response

    arguments: dict[str, Any] = {
        "query": expression,
        "matchCase": False,
        "matchWholeWords": False,
        "useRegexp": kind == "regex",
    }
    if repository:
        arguments["repo"] = repository
    if request.path:
        arguments["path"] = request.path
    if request.language:
        arguments["language"] = [request.language]

    try:
        result = await _call_grepapp_mcp(arguments, http_client)
    except Exception as exc:
        failures = _exception_chain(exc)
        summary = failures[0] if failures else {"type": type(exc).__name__}
        LOGGER.warning("grep.app MCP request failed: %s", summary)
        rest_response.diagnostics.insert(
            0,
            _diagnostic(
                "grep.app REST returned no hits; MCP fallback was attempted",
                query=expression,
                outcome="partial",
                details={"failures": failures},
            ),
        )
        rest_response.request_count += 1
        rest_response.metadata.setdefault("transport", "rest")
        rest_response.metadata["fallback_from"] = "rest"
        return rest_response

    if getattr(result, "isError", False) or (isinstance(result, dict) and result.get("isError")):
        rest_response.request_count += 1
        return ProviderResponse(
            provider="grep.app",
            diagnostics=list(rest_response.diagnostics)
            + [_diagnostic("grep.app MCP relay returned an error", query=expression)],
            request_count=rest_response.request_count,
            metadata={
                "compiled_queries": [expression],
                "transport": "mcp",
                "fallback_from": "rest",
            },
        )
    blocks = _text_blocks(result)
    text = "\n".join(blocks)
    hits = parse_grepapp_text(text, query_variant=expression, max_results=request.max_results)
    diagnostics: list[Diagnostic] = []
    if not hits and text:
        lowered = text.casefold()
        if not ("no results found" in lowered or "0 results" in lowered or "no hits" in lowered):
            diagnostics.append(
                _diagnostic(
                    "grep.app MCP response did not contain parseable Repository/Path/URL blocks",
                    query=expression,
                    outcome="partial",
                    details={"response_chars": len(text)},
                )
            )
    return ProviderResponse(
        provider="grep.app",
        hits=hits,
        diagnostics=diagnostics,
        request_count=rest_response.request_count + 1,
        metadata={
            "compiled_queries": [expression],
            "transport": "mcp",
            "fallback_from": "rest",
        },
    )


async def _search_grepapp_variant(
    expression: str,
    kind: str,
    request: CodeSearchRequest,
    http_client: httpx.AsyncClient,
) -> ProviderResponse:
    """Search one variant across target repositories over grep.app REST/MCP."""
    target_repos = list(request.repositories) if request.repositories else [None]
    if len(target_repos) == 1:
        return await _search_grepapp_single_repo(
            expression, kind, target_repos[0], request, http_client
        )
    responses = await asyncio.gather(
        *(
            _search_grepapp_single_repo(expression, kind, repo, request, http_client)
            for repo in target_repos
        )
    )
    return ProviderResponse(
        provider="grep.app",
        hits=[hit for resp in responses for hit in resp.hits][: request.max_results],
        diagnostics=[d for resp in responses for d in resp.diagnostics],
        request_count=sum(resp.request_count for resp in responses),
        metadata={
            "compiled_queries": [expression],
            "transport": "mixed",
        },
    )

async def search_grepapp(
    plan: QueryPlan, request: CodeSearchRequest, *, http_client: httpx.AsyncClient
) -> ProviderResponse:
    """Search bounded lexical/symbol/regex variants and retain their provenance."""

    qualifier_map: dict[str, list[str]] = {}
    for key, value in plan.qualifiers:
        qualifier_map.setdefault(key, []).append(value)
    effective_request = replace(
        request,
        repositories=request.repositories or tuple(qualifier_map.get("repo", [])),
        language=request.language or next(iter(qualifier_map.get("language", [])), None),
        path=request.path or next(iter(qualifier_map.get("path", [])), None),
    )
    variants = plan.variant_pairs[: request.budget.max_query_variants]
    if not variants:
        expression = plan.grep_expression()
        variants = (
            ((expression, "regex" if plan.regex_source else "lexical"),) if expression else ()
        )
    unique_variants = list(dict.fromkeys((variant, kind == "regex") for variant, kind in variants))
    responses: list[ProviderResponse] = []
    for variant, use_regex in unique_variants:
        responses.append(
            await _search_grepapp_variant(
                variant,
                "regex" if use_regex else "lexical",
                effective_request,
                http_client,
            )
        )
    return ProviderResponse(
        provider="grep.app",
        hits=[hit for response in responses for hit in response.hits],
        diagnostics=[item for response in responses for item in response.diagnostics],
        request_count=sum(response.request_count for response in responses),
        metadata={
            "compiled_queries": [variant for variant, _ in unique_variants],
            "transports": [
                {
                    "transport": response.metadata.get("transport", "mcp"),
                    "fallback_from": response.metadata.get("fallback_from"),
                }
                for response in responses
            ],
        },
    )
