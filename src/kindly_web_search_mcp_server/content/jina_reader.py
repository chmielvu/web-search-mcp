"""Jina Reader HTTP client driven by DOM route decisions.

Every request carries an explicit :class:`JinaRoute` from
:mod:`kindly_web_search_mcp_server.content.dom_detector`. Route selection
owns engine choice, preset choice, and render timing; this module only
translates the decision into Jina protocol headers and normalizes the
ReaderLM JSON/SSE transports into one response model.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..settings import get_env_value, settings
from .dom_detector import JinaRoute

JinaEngine = Literal["reader", "readerlm-v2", "browser"]
JinaResponseFormat = Literal["frontmatter", "json", "sse", "plain"]

_ROUTE_ENGINES: dict[JinaRoute, JinaEngine] = {
    "agent": "reader",
    "research": "reader",
    "readerlm-v2": "readerlm-v2",
    "readerlm-research": "readerlm-v2",
    "research+browser-timing": "browser",
    "browser": "browser",
}

_ROUTE_TIMEOUTS: dict[JinaRoute, float] = {
    "agent": 25.0,
    "research": 30.0,
    "readerlm-v2": 60.0,
    "readerlm-research": 60.0,
    "research+browser-timing": 40.0,
    "browser": 40.0,
}


@dataclass(frozen=True, slots=True)
class JinaReaderResponse:
    """Normalized response returned by the Jina Reader API."""

    content: str
    engine: JinaEngine
    route: JinaRoute
    response_format: JinaResponseFormat
    title: str | None = None
    description: str | None = None
    fetched_url: str | None = None
    warning: str | None = None
    usage: dict[str, Any] | None = None
    chunks: tuple[str, ...] = ()


class JinaReaderError(RuntimeError):
    """Raised when Jina cannot return a usable content response."""


class _JinaCircuit:
    """Circuit breaker opening after repeated Jina failures."""

    def __init__(self, threshold: int = 3, recovery_seconds: float = 60.0) -> None:
        self._threshold = threshold
        self._recovery = recovery_seconds
        self._failures = 0
        self._last_failure = 0.0

    def is_open(self) -> bool:
        if self._failures >= self._threshold:
            if time.monotonic() - self._last_failure < self._recovery:
                return True
            self._failures = 0
        return False

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()

    def record_success(self) -> None:
        self._failures = 0


_CIRCUIT = _JinaCircuit()
_JINA_ENDPOINT = "https://r.jina.ai/"


def _api_key() -> str:
    return get_env_value("JINA_API_KEY", settings.jina_api_key).strip()


def default_timeout_for_route(route: JinaRoute) -> float:
    """Return the per-route Jina timeout honoring reader-side latency."""
    return _ROUTE_TIMEOUTS[route]


def _request_headers(
    route: JinaRoute,
    *,
    accept: str | None,
    api_key: str,
    target_selector: str | None,
    wait_for_selector: str | None,
    remove_selector: str | None,
    inject_page_scripts: Sequence[str],
    respond_timing: str | None,
    no_cache: bool,
    max_tokens: int | None,
    token_budget: int | None,
    markdown_chunking: str | None,
    with_iframe: bool,
    with_shadow_dom: bool,
) -> dict[str, str]:
    engine = _ROUTE_ENGINES[route]
    if route == "readerlm-research":
        headers = {
            "Accept": accept or "application/json",
            "X-Engine": "readerlm-v2",
            "X-Base": "final",
            "X-Retain-Links": "text",
            "X-Markdown-Chunking": markdown_chunking or "h3",
            "X-With-Links-Summary": "true",
        }
    elif route == "readerlm-v2":
        headers = {
            "Accept": accept or "application/json",
            "X-Engine": "readerlm-v2",
            "X-Base": "final",
        }
    elif route == "research":
        headers = {
            "Accept": accept or "text/plain",
            "X-Respond-With": "frontmatter",
            "X-Preset": "research",
            "X-Retain-Links": "text",
            "X-With-Links-Summary": "true",
            "X-Base": "final",
        }
    elif route == "research+browser-timing":
        headers = {
            "Accept": accept or "text/plain",
            "X-Engine": "browser",
            "X-Respond-With": "frontmatter",
            "X-Preset": "research",
            "X-Retain-Links": "text",
            "X-With-Links-Summary": "true",
            "X-Base": "final",
            "X-Respond-Timing": respond_timing or "mutation-idle",
        }
    elif route == "browser":
        headers = {
            "Accept": accept or "text/plain",
            "X-Engine": "browser",
            "X-Respond-With": "frontmatter",
            "X-Preset": "agent",
            "X-Retain-Links": "all",
            "X-Retain-Images": "none",
            "X-Base": "final",
            "X-Respond-Timing": respond_timing or "mutation-idle",
        }
    else:
        headers = {
            "Accept": accept or "text/plain",
            "X-Respond-With": "frontmatter",
            "X-Preset": "agent",
            "X-Retain-Links": "all",
            "X-Retain-Images": "none",
            "X-Base": "final",
        }

    if engine == "readerlm-v2":
        if not api_key:
            raise JinaReaderError(f"Route {route!r} requires JINA_API_KEY")
        headers["Authorization"] = f"Bearer {api_key}"
    elif route == "agent":
        # Agent stays on the key-free tier even when a key is configured;
        # paid quota is reserved for research/readerlm/browser profiles.
        headers.pop("Authorization", None)
    elif api_key and route in {"research", "research+browser-timing", "browser"}:
        headers["Authorization"] = f"Bearer {api_key}"
    if target_selector:
        headers["X-Target-Selector"] = target_selector
    if wait_for_selector:
        headers["X-Wait-For-Selector"] = wait_for_selector
    if remove_selector:
        headers["X-Remove-Selector"] = remove_selector
    if respond_timing and "X-Respond-Timing" not in headers:
        headers["X-Respond-Timing"] = respond_timing
    elif inject_page_scripts and "X-Respond-Timing" not in headers:
        headers["X-Respond-Timing"] = "mutation-idle"
    if no_cache:
        headers["X-No-Cache"] = "true"
    if max_tokens is not None:
        headers["X-Max-Tokens"] = str(max_tokens)
    if token_budget is not None:
        headers["X-Token-Budget"] = str(token_budget)
    if markdown_chunking and "X-Markdown-Chunking" not in headers:
        headers["X-Markdown-Chunking"] = markdown_chunking
    if with_iframe:
        headers["X-With-Iframe"] = "true"
    if with_shadow_dom:
        headers["X-With-Shadow-Dom"] = "true"
    return headers


async def _send_request(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    inject_page_scripts: Sequence[str],
) -> httpx.Response:
    if inject_page_scripts:
        return await client.post(
            _JINA_ENDPOINT,
            headers=headers,
            json={"url": url, "injectPageScript": list(inject_page_scripts)},
        )
    return await client.get(f"{_JINA_ENDPOINT}{url}", headers=headers)


def _sse_payloads(text: str) -> list[str]:
    payloads: list[str] = []
    data_lines: list[str] = []
    for line in text.splitlines():
        if not line:
            if data_lines:
                payloads.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        payloads.append("\n".join(data_lines))
    return payloads


def _record_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _parse_sse_record(text: str) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    for payload_text in _sse_payloads(text):
        if payload_text.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise JinaReaderError("Jina Reader returned malformed SSE JSON") from exc
        record = _record_from_payload(payload)
        if record is not None and isinstance(record.get("content"), str):
            # ReaderLM emits cumulative snapshots; only the final
            # content-bearing event is the complete result.
            latest = record
    if latest is None:
        raise JinaReaderError("Jina Reader returned no content-bearing SSE event")
    return latest


def _parse_json_record(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JinaReaderError("Jina Reader returned malformed JSON") from exc
    record = _record_from_payload(payload)
    if record is None or not isinstance(record.get("content"), str):
        raise JinaReaderError("Jina Reader JSON response did not contain content")
    return record


def _response_format(response: Any, accept: str | None, engine: JinaEngine) -> JinaResponseFormat:
    headers = getattr(response, "headers", None)
    content_type = str(headers.get("content-type", "") if headers is not None else "").lower()
    if "event-stream" in content_type or accept == "text/event-stream":
        return "sse"
    if "json" in content_type or accept in {"application/json", "text/json"}:
        return "json"
    return "frontmatter" if engine in {"reader", "browser"} else "plain"


def _string_field(record: dict[str, Any], name: str) -> str | None:
    value = record.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalized_response(
    response: Any,
    *,
    route: JinaRoute,
    engine: JinaEngine,
    accept: str | None,
) -> JinaReaderResponse:
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise JinaReaderError("Jina Reader returned empty content")

    response_format = _response_format(response, accept, engine)
    if response_format == "sse":
        record = _parse_sse_record(text)
    elif response_format == "json":
        record = _parse_json_record(text)
    else:
        return JinaReaderResponse(
            content=text,
            engine=engine,
            route=route,
            response_format=response_format,
        )

    raw_chunks = record.get("chunks")
    chunks = (
        tuple(item for item in raw_chunks if isinstance(item, str))
        if isinstance(raw_chunks, list)
        else ()
    )
    usage = record.get("usage")
    return JinaReaderResponse(
        content=str(record["content"]).strip(),
        engine=engine,
        route=route,
        response_format=response_format,
        title=_string_field(record, "title"),
        description=_string_field(record, "description"),
        fetched_url=_string_field(record, "url"),
        warning=_string_field(record, "warning"),
        usage=usage if isinstance(usage, dict) else None,
        chunks=chunks,
    )


async def fetch_with_jina_reader_response(
    url: str,
    *,
    route: JinaRoute,
    timeout_seconds: float | None = None,
    accept: str | None = None,
    target_selector: str | None = None,
    wait_for_selector: str | None = None,
    remove_selector: str | None = None,
    inject_page_scripts: Sequence[str] = (),
    respond_timing: str | None = None,
    no_cache: bool = False,
    max_tokens: int | None = None,
    token_budget: int | None = None,
    markdown_chunking: str | None = None,
    with_iframe: bool = False,
    with_shadow_dom: bool = False,
) -> JinaReaderResponse:
    """Fetch one URL with an explicit DOM route decision.

    Args:
        url: Target page URL.
        route: Route from :func:`dom_detector.classify_route`.
        timeout_seconds: Override for the per-route default timeout.

    Returns:
        Normalized :class:`JinaReaderResponse` for the pipeline.
    """
    if _CIRCUIT.is_open():
        raise JinaReaderError("Jina Reader circuit breaker is open")
    engine = _ROUTE_ENGINES[route]
    scripts = tuple(script.strip() for script in inject_page_scripts if script.strip())
    headers = _request_headers(
        route,
        accept=accept,
        api_key=_api_key(),
        target_selector=target_selector,
        wait_for_selector=wait_for_selector,
        remove_selector=remove_selector,
        inject_page_scripts=scripts,
        respond_timing=respond_timing,
        no_cache=no_cache,
        max_tokens=max_tokens,
        token_budget=token_budget,
        markdown_chunking=markdown_chunking,
        with_iframe=with_iframe,
        with_shadow_dom=with_shadow_dom,
    )
    selected_accept = headers.get("Accept")
    timeout = httpx.Timeout(
        timeout_seconds if timeout_seconds is not None else default_timeout_for_route(route)
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await _send_request(
                client,
                url,
                headers=headers,
                inject_page_scripts=scripts,
            )
            response.raise_for_status()
            result = _normalized_response(
                response,
                route=route,
                engine=engine,
                accept=selected_accept,
            )
            _CIRCUIT.record_success()
            return result
    except (
        JinaReaderError,
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.ConnectError,
        httpx.HTTPStatusError,
        httpx.RequestError,
    ):
        _CIRCUIT.record_failure()
        raise
