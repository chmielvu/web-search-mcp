from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..settings import get_env_value, settings


class TavilyMapError(RuntimeError):
    """Raised when a Tavily Map request fails or returns invalid data."""


class TavilyMapConfigError(TavilyMapError):
    """Raised when Tavily Map is requested without an API key."""


@dataclass(frozen=True)
class TavilyMapConfig:
    """Tavily Map request configuration."""

    instructions: str | None = None
    max_depth: int = 1
    max_breadth: int = 20
    limit: int = 50
    select_paths: list[str] | None = None
    select_domains: list[str] | None = None
    exclude_paths: list[str] | None = None
    exclude_domains: list[str] | None = None
    allow_external: bool = False
    timeout: float = 150.0


def _get_tavily_api_key() -> str:
    api_key = get_env_value("TAVILY_API_KEY", settings.tavily_api_key).strip()
    if not api_key:
        raise TavilyMapConfigError(
            "TAVILY_API_KEY is not set. Configure it in your runtime settings."
        )
    return api_key


def _map_payload(
    url: str,
    *,
    config: TavilyMapConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": url,
        "max_depth": int(config.max_depth),
        "max_breadth": int(config.max_breadth),
        "limit": int(config.limit),
        "allow_external": bool(config.allow_external),
    }
    if config.instructions:
        payload["instructions"] = config.instructions
    if config.select_paths:
        payload["select_paths"] = config.select_paths
    if config.select_domains:
        payload["select_domains"] = config.select_domains
    if config.exclude_paths:
        payload["exclude_paths"] = config.exclude_paths
    if config.exclude_domains:
        payload["exclude_domains"] = config.exclude_domains
    return payload


def extract_map_urls(response: dict[str, Any]) -> list[str]:
    raw_results = response.get("results", [])
    if not isinstance(raw_results, list):
        raise TavilyMapError("Tavily Map response missing `results` list.")

    urls: list[str] = []
    seen: set[str] = set()
    for item in raw_results:
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, dict):
            raw_url = item.get("url")
            candidate = raw_url.strip() if isinstance(raw_url, str) else ""
        else:
            candidate = ""
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls


async def map_site(
    url: str,
    *,
    instructions: str | None = None,
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    select_paths: list[str] | None = None,
    select_domains: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    allow_external: bool = False,
    timeout: float = 150.0,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Call Tavily Map and return the raw response payload."""
    api_key = _get_tavily_api_key()
    config = TavilyMapConfig(
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths,
        select_domains=select_domains,
        exclude_paths=exclude_paths,
        exclude_domains=exclude_domains,
        allow_external=allow_external,
        timeout=timeout,
    )
    payload = _map_payload(url, config=config)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    endpoint = "https://api.tavily.com/map"

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyMapError("Tavily Map response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise TavilyMapError("Tavily Map response was not a JSON object.")
        return data

    if http_client is not None:
        return await _do_request(http_client)

    timeout_config = httpx.Timeout(timeout, connect=min(10.0, timeout))
    async with httpx.AsyncClient(timeout=timeout_config, follow_redirects=True) as client:
        return await _do_request(client)
