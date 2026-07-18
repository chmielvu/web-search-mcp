"""Jina Reader HTTP client (free + authenticated fallback).

https://r.jina.ai/http://example.com
"""

from __future__ import annotations

import time

import httpx

from ..settings import get_env_value, settings


class JinaReaderError(RuntimeError):
    pass


class _JinaCircuit:
    """Simple module-level circuit breaker for Jina Reader.

    Opens after ``threshold`` consecutive failures and stays open for
    ``recovery_seconds``. It intentionally ignores 4xx vs 5xx distinction;
    a burst of 404s from Jina usually indicates service-side instability.
    """

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


async def fetch_with_jina_reader(url: str, *, timeout_seconds: float = 25.0) -> str:
    if _CIRCUIT.is_open():
        raise JinaReaderError("Jina Reader circuit breaker is open")

    endpoint = f"https://r.jina.ai/{url}"
    # Modern Jina Reader defaults tuned for downstream LLM consumption.
    # - frontmatter: returns YAML frontmatter (title/url) + clean markdown body.
    # - research preset: keeps links/images/media inline and chunks at h3.
    # - retain-links/images none: drops noisy embedded markup from the body.
    base_headers = {
        "X-Respond-With": "frontmatter",
        "X-Preset": "research",
        "X-Retain-Links": "none",
        "X-Retain-Images": "none",
        "X-No-Cache": "true",
    }

    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1) First attempt: no API key (preferred free path)
            response = await client.get(endpoint, headers=base_headers)
            if response.status_code == 429:
                # 2) Retry with API key only when rate-limited
                api_key = get_env_value("JINA_API_KEY", settings.jina_api_key).strip()
                if not api_key:
                    _CIRCUIT.record_failure()
                    raise JinaReaderError(
                        "Jina Reader rate-limited and JINA_API_KEY is not configured"
                    )
                auth_headers = dict(base_headers)
                auth_headers["Authorization"] = f"Bearer {api_key}"
                response = await client.get(endpoint, headers=auth_headers)

            response.raise_for_status()
            text = response.text.strip()
            if not text:
                _CIRCUIT.record_failure()
                raise JinaReaderError("Jina Reader returned empty content")
            _CIRCUIT.record_success()
            return text
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.ConnectError,
        httpx.HTTPStatusError,
        httpx.RequestError,
    ):
        # Record the failure for the circuit breaker, then re-raise the raw
        # httpx exception so the stage-level retry helper can inspect it.
        _CIRCUIT.record_failure()
        raise
