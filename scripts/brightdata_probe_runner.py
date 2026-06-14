from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from scripts._env_loader import load_repo_env


@dataclass(frozen=True)
class Attempt:
    label: str
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class DirectAttempt:
    label: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class EndpointVariant:
    label: str
    endpoint: str


def _build_endpoint() -> str:
    token = (os.environ.get("BRIGHTDATA_API_KEY") or "").strip()
    if not token:
        raise RuntimeError("BRIGHTDATA_API_KEY is not set")
    return f"https://mcp.brightdata.com/mcp?token={token}"


def _build_endpoint_variants() -> list[EndpointVariant]:
    base = _build_endpoint()
    return [
        EndpointVariant("base", base),
        EndpointVariant("pro=1", f"{base}&pro=1"),
        EndpointVariant("groups=advanced_scraping", f"{base}&groups=advanced_scraping"),
        EndpointVariant("groups=research", f"{base}&groups=research"),
        EndpointVariant("groups=code", f"{base}&groups=code"),
    ]


def _summarize_text(text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"text_length": len(text), "preview": text[:240]}
    stripped = text.strip()
    if not stripped:
        summary["parsed_type"] = "empty"
        summary["result_count"] = 0
        return summary

    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError, json.JSONDecodeError):
        summary["parsed_type"] = "markdown_or_plaintext"
        summary["result_count"] = 0
        return summary

    if isinstance(parsed, dict) and isinstance(parsed.get("status_code"), int):
        summary["parsed_type"] = "brightdata_envelope"
        summary["result_count"] = 0
        summary["upstream_status_code"] = parsed["status_code"]
        headers = parsed.get("headers")
        if isinstance(headers, dict):
            summary["upstream_error_code"] = headers.get("x-brd-err-code")
            summary["upstream_error_message"] = (
                headers.get("x-brd-err-msg")
                or headers.get("proxy-status")
                or headers.get("content-type")
            )
        body = parsed.get("body")
        if isinstance(body, str):
            summary["upstream_body_preview"] = body[:240]
            body_stripped = body.strip()
            if body_stripped:
                summary["body_summary"] = _summarize_text(body_stripped)
        return summary

    if isinstance(parsed, dict):
        organic = parsed.get("organic") or parsed.get("results") or parsed.get("items") or []
        summary["parsed_type"] = "dict"
        summary["keys"] = list(parsed.keys())[:12]
        summary["current_page"] = parsed.get("current_page")
        summary["result_count"] = len(organic) if isinstance(organic, list) else 0
        return summary

    if isinstance(parsed, list):
        inner_counts: list[int] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            inner = item.get("result")
            if not isinstance(inner, dict):
                continue
            organic = inner.get("organic") or inner.get("results") or inner.get("items") or []
            if isinstance(organic, list):
                inner_counts.append(len(organic))
        summary["parsed_type"] = "list"
        summary["batch_items"] = len(parsed)
        summary["inner_result_counts"] = inner_counts[:12]
        summary["result_count"] = sum(inner_counts)
        return summary

    summary["parsed_type"] = type(parsed).__name__
    summary["result_count"] = 0
    return summary


def _summarize_http_response(response: httpx.Response) -> dict[str, Any]:
    summary = _summarize_text(response.text)
    summary["http_status"] = response.status_code
    summary["response_bytes"] = len(response.content)
    summary["content_type"] = response.headers.get("content-type")
    return summary


def _extract_text(result: Any) -> str:
    blocks = getattr(result, "content", []) or []
    texts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def _build_attempts(query: str) -> list[Attempt]:
    return [
        Attempt("search_engine/google", "search_engine", {"query": query, "engine": "google"}),
        Attempt("search_engine/google+geo=us", "search_engine", {"query": query, "engine": "google", "geo_location": "us"}),
        Attempt("search_engine/google+cursor=0", "search_engine", {"query": query, "engine": "google", "cursor": "0"}),
        Attempt("search_engine/bing", "search_engine", {"query": query, "engine": "bing"}),
        Attempt("search_engine/yandex", "search_engine", {"query": query, "engine": "yandex"}),
        Attempt("search_engine_batch/google", "search_engine_batch", {"queries": [{"query": query, "engine": "google"}]}),
        Attempt("search_engine_batch/mixed", "search_engine_batch", {"queries": [{"query": query, "engine": "google"}, {"query": query, "engine": "bing"}, {"query": query, "engine": "yandex"}]}),
    ]


def _build_direct_attempts(query: str) -> list[DirectAttempt]:
    search_url = f"https://www.google.com/search?q={query}&start=0"
    return [
        DirectAttempt("direct/mcp_unlocker raw+parsed_light", {"zone": "mcp_unlocker", "url": f"{search_url}&brd_json=1", "format": "raw", "data_format": "parsed_light"}),
        DirectAttempt("direct/mcp_unlocker json+brd_json", {"zone": "mcp_unlocker", "url": f"{search_url}&brd_json=1", "format": "json"}),
    ]


async def _run_mcp_attempt(session: ClientSession, attempt: Attempt) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await session.call_tool(attempt.tool, attempt.arguments)
    except Exception as exc:  # noqa: BLE001 - diagnostic probe
        return {
            "label": attempt.label,
            "tool": attempt.tool,
            "arguments": attempt.arguments,
            "status": "error",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    summary = _summarize_text(_extract_text(result))
    return {
        "label": attempt.label,
        "tool": attempt.tool,
        "arguments": attempt.arguments,
        "status": "ok" if not getattr(result, "isError", False) else "tool_error",
        "is_error": bool(getattr(result, "isError", False)),
        "content_blocks": len(getattr(result, "content", []) or []),
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        **summary,
    }


async def _run_direct_attempt(attempt: DirectAttempt) -> dict[str, Any]:
    token = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.post(
            "https://api.brightdata.com/request",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=attempt.payload,
        )
    return {"label": attempt.label, "status": "ok", "duration_ms": round((time.perf_counter() - started) * 1000.0, 3), **_summarize_http_response(response)}


def _emit(payload: dict[str, Any], fields: list[str]) -> None:
    print(json.dumps({field: payload.get(field) for field in fields}, ensure_ascii=False))


async def run_probe(queries: list[str]) -> list[dict[str, Any]]:
    load_repo_env()
    results: list[dict[str, Any]] = []

    print("BrightData endpoint: https://mcp.brightdata.com/mcp?token=***REDACTED***")
    print("BrightData API token: SET")
    print()

    for endpoint_variant in _build_endpoint_variants():
        print(f"=== MCP endpoint: {endpoint_variant.label} ===")
        print(f"URL: https://mcp.brightdata.com/mcp?token=***REDACTED*** ({endpoint_variant.label})")
        async with streamablehttp_client(endpoint_variant.endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                print(f"Available BrightData tools: {', '.join(tool_names) or 'none'}")

                for query in queries:
                    print(f"--- Query: {query!r} ---")
                    for attempt in _build_attempts(query):
                        if attempt.tool not in tool_names:
                            continue
                        outcome = await _run_mcp_attempt(session, attempt)
                        results.append(outcome)
                        _emit(outcome, ["label", "tool", "status", "duration_ms", "result_count", "text_length", "parsed_type", "upstream_status_code", "upstream_error_code", "upstream_error_message", "error_type", "error_message", "preview"])
                        if outcome.get("result_count", 0):
                            print("First non-empty result found; stopping early for this query.")
                            break
                    if "discover" in tool_names:
                        discover_outcome = await _run_mcp_attempt(session, Attempt("discover/basic", "discover", {"query": query}))
                        results.append(discover_outcome)
                        _emit(discover_outcome, ["label", "tool", "status", "duration_ms", "result_count", "text_length", "parsed_type", "error_type", "error_message", "preview"])
                    if "scrape_as_markdown" in tool_names:
                        scrape_outcome = await _run_mcp_attempt(session, Attempt("scrape_as_markdown/example.com", "scrape_as_markdown", {"url": "https://example.com/"}))
                        results.append(scrape_outcome)
                        _emit(scrape_outcome, ["label", "tool", "status", "duration_ms", "result_count", "text_length", "parsed_type", "error_type", "error_message", "preview"])
                    print()

    print("=== Direct API probe ===")
    for query in queries[:1]:
        for attempt in _build_direct_attempts(query):
            outcome = await _run_direct_attempt(attempt)
            results.append(outcome)
            _emit(outcome, ["label", "status", "duration_ms", "http_status", "response_bytes", "content_type", "result_count", "parsed_type", "upstream_status_code", "upstream_error_code", "upstream_error_message", "upstream_body_preview", "error_type", "error_message", "preview"])
        print()

    return results
