"""Composio SDK adapter for deterministic direct tool execution."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from .settings import settings

COMPOSIO_SEARCH_TOOLKIT = "composio_search"


class ComposioClientError(RuntimeError):
    """Base Composio adapter error."""


class ComposioConfigError(ComposioClientError):
    """Raised when Composio credentials/configuration are missing."""


class ComposioToolError(ComposioClientError):
    """Raised when Composio tool execution fails or returns malformed data."""


_CLIENT_LOCK = threading.Lock()
_CACHED_CLIENT: Any | None = None
_CACHED_CLIENT_CONFIG: tuple[str, float, int, str] | None = None


def _require_composio_config() -> tuple[str, str]:
    api_key = settings.composio_api_key.strip()
    user_id = settings.composio_user_id.strip()
    if not api_key:
        raise ComposioConfigError("COMPOSIO_API_KEY is not set.")
    if not user_id:
        raise ComposioConfigError("KINDLY_COMPOSIO_USER_ID is not set.")
    return api_key, user_id


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return _to_plain(value.model_dump())
    if hasattr(value, "dict"):
        return _to_plain(value.dict())
    if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
        return _to_plain(vars(value))
    return value


def _execute_sync(slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
    _, user_id = _require_composio_config()
    client = _get_composio_client()
    result = client.tools.execute(slug, user_id=user_id, arguments=arguments)
    payload = _to_plain(result)
    if not isinstance(payload, dict):
        raise ComposioToolError(f"{slug} returned a non-object response.")
    if payload.get("successful") is False:
        error = payload.get("error") or "Composio tool execution failed."
        raise ComposioToolError(f"{slug}: {error}")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ComposioToolError(f"{slug} returned non-object data.")
    return data


def _get_composio_client() -> Any:
    api_key, _ = _require_composio_config()
    config = (
        api_key,
        settings.composio_timeout_seconds,
        settings.composio_max_retries,
        settings.composio_search_toolkit_version,
    )
    global _CACHED_CLIENT
    global _CACHED_CLIENT_CONFIG
    with _CLIENT_LOCK:
        if _CACHED_CLIENT is not None and _CACHED_CLIENT_CONFIG == config:
            return _CACHED_CLIENT
        try:
            from composio import Composio
        except ImportError as exc:
            raise ComposioConfigError(
                "Install the `composio` package to use Composio tools."
            ) from exc
        _CACHED_CLIENT = Composio(
            api_key=api_key,
            max_retries=settings.composio_max_retries,
            timeout=settings.composio_timeout_seconds,
            toolkit_versions={
                COMPOSIO_SEARCH_TOOLKIT: settings.composio_search_toolkit_version,
            },
        )
        _CACHED_CLIENT_CONFIG = config
        return _CACHED_CLIENT


async def execute_composio_tool(
    slug: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Execute a Composio tool through the Python SDK with an async timeout."""
    if not slug.strip():
        raise ComposioToolError("Composio tool slug is required.")
    effective_timeout = (
        settings.composio_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    timeout = max(1.0, effective_timeout)
    return await asyncio.wait_for(
        asyncio.to_thread(_execute_sync, slug, arguments),
        timeout=timeout,
    )
