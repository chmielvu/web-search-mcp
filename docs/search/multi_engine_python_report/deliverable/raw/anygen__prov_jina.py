"""Jina s.jina.ai search + r.jina.ai reader. Docs: https://jina.ai/reader/"""
from __future__ import annotations

from typing import Any

from hsearch.models import SearchResult
from hsearch.providers.base import SearchProvider

SEARCH_ENDPOINT = "https://s.jina.ai/"
READER_BASE = "https://r.jina.ai/"
GROUNDING_ENDPOINT = "https://g.jina.ai/"


class JinaProvider(SearchProvider):
    name = "jina"
    requires_env = ["JINA_API_KEY"]
    supports_extract = True

    def _headers(self, *, json_resp: bool = True, no_content: bool = False) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Accept": "application/json" if json_resp else "text/plain",
        }
        if no_content:
            h["X-Respond-With"] = "no-content"
        return h

    async def _search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        # Default: fetch snippets/descriptions (not full content, but not no-content either)
        no_content = kwargs.get("no_content", False)
        headers = self._headers(json_resp=True, no_content=no_content)
        headers["Content-Type"] = "application/json"
        if kwargs.get("site"):
            headers["X-Site"] = str(kwargs["site"])
        if kwargs.get("engine"):
            headers["X-Engine"] = str(kwargs["engine"])
        if kwargs.get("locale"):
            headers["X-Locale"] = str(kwargs["locale"])
        if kwargs.get("no_cache"):
            headers["X-No-Cache"] = "true"
        if kwargs.get("jina_timeout"):
            headers["X-Timeout"] = str(int(kwargs["jina_timeout"]))
        if kwargs.get("max_tokens"):
            headers["X-Max-Tokens"] = str(int(kwargs["max_tokens"]))
        if kwargs.get("cache_tolerance"):
            headers["X-Cache-Tolerance"] = str(int(kwargs["cache_tolerance"]))
        if kwargs.get("respond_with"):
            headers["X-Respond-With"] = str(kwargs["respond_with"])
        if kwargs.get("target_selector"):
            headers["X-Target-Selector"] = str(kwargs["target_selector"])
        if kwargs.get("wait_for_selector"):
            headers["X-Wait-For-Selector"] = str(kwargs["wait_for_selector"])
        if kwargs.get("remove_selector"):
            headers["X-Remove-Selector"] = str(kwargs["remove_selector"])
        if kwargs.get("preset"):
            headers["X-Preset"] = str(kwargs["preset"])
        if kwargs.get("retain_images"):
            headers["X-Retain-Images"] = str(kwargs["retain_images"])
        if kwargs.get("with_generated_alt"):
            headers["X-With-Generated-Alt"] = "true"
        payload = {"q": query}
        resp = await self._request("POST", SEARCH_ENDPOINT, headers=headers, json=payload)
        body = resp.json()
        # Jina JSON shape: { "code": 200, "data": [ { "title", "url", "description", ... }, ... ] }
        items = body.get("data") or []
        if isinstance(items, dict):
            items = items.get("results") or []

        out: list[SearchResult] = []
        for r in items[:count]:
            if not isinstance(r, dict):
                continue
            out.append(
                SearchResult(
                    url=r.get("url", "") or r.get("link", ""),
                    title=r.get("title", "") or "",
                    snippet=r.get("description", "")
                    or (r.get("content", "") or "")[:500],
                    provider=self.name,
                    published=r.get("date"),
                    raw=r,
                )
            )
        return out

    async def _extract(self, url: str) -> str | None:
        headers = {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Accept": "application/json",
            "X-Return-Format": "markdown",
        }
        resp = await self._request("GET", f"{READER_BASE}{url}", headers=headers)
        try:
            body = resp.json()
            return ((body.get("data") or {}).get("content")) or None
        except Exception:
            return resp.text or None

    async def ground(self, statement: str, **kwargs: Any) -> dict[str, Any]:
        """Call Jina Grounding API (g.jina.ai) — fact-check a statement against the web.

        Grounding scrapes multiple reference pages server-side and runs an LLM
        synthesis; typical wall time is 30–90 s. We pass an explicit long
        timeout (default 180 s, overridable via ``HSEARCH_GROUND_TIMEOUT``) so
        the default 15 s search timeout doesn't kill the request.
        """
        if not self.is_configured():
            from hsearch.providers.base import ProviderAuthError
            raise ProviderAuthError(f"{self.name}: missing env {','.join(self.requires_env)}")
        headers = {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if kwargs.get("no_cache"):
            headers["X-No-Cache"] = "true"
        payload: dict[str, Any] = {"statement": statement}
        import os
        ground_timeout = float(os.environ.get("HSEARCH_GROUND_TIMEOUT", "180"))
        resp = await self._request(
            "POST",
            GROUNDING_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=ground_timeout,
        )
        return resp.json()
