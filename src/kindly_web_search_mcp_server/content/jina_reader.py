from __future__ import annotations

import os

import httpx


class JinaReaderError(RuntimeError):
    pass


async def fetch_with_jina_reader(url: str, *, timeout_seconds: float = 25.0) -> str:
    endpoint = f"https://r.jina.ai/{url}"
    base_headers = {
        "Accept": "text/plain",
        "x-no-cache": "true",
    }

    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) First attempt: no API key (preferred free path)
        response = await client.get(endpoint, headers=base_headers)
        if response.status_code == 429:
            # 2) Retry with API key only when rate-limited
            api_key = (os.environ.get("JINA_API_KEY") or "").strip()
            if not api_key:
                raise JinaReaderError(
                    "Jina Reader rate-limited and JINA_API_KEY is not configured"
                )
            auth_headers = dict(base_headers)
            auth_headers["Authorization"] = f"Bearer {api_key}"
            response = await client.get(endpoint, headers=auth_headers)

        response.raise_for_status()
        text = response.text.strip()
        if not text:
            raise JinaReaderError("Jina Reader returned empty content")
        return text
