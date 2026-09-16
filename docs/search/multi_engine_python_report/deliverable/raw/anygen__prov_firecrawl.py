"""Firecrawl v2 search + scrape. Docs: https://docs.firecrawl.dev/api-reference/endpoint/search"""
from __future__ import annotations

from typing import Any

from hsearch.models import SearchResult
from hsearch.providers.base import SearchProvider

SEARCH_ENDPOINT = "https://api.firecrawl.dev/v2/search"
SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"

_VALID_SOURCES = {"web", "news", "images"}


class FirecrawlProvider(SearchProvider):
    name = "firecrawl"
    requires_env = ["FIRECRAWL_API_KEY"]
    supports_extract = True

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

    async def _search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        # ---- sources -------------------------------------------------------
        raw_sources = kwargs.get("sources")
        if isinstance(raw_sources, str):
            raw_sources = [s.strip() for s in raw_sources.split(",") if s.strip()]
        if not raw_sources:
            sources = ["web"]
        else:
            sources = [s for s in raw_sources if s in _VALID_SOURCES] or ["web"]

        payload: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(count, 100)),
            "sources": [{"type": s} for s in sources],
        }
        if kwargs.get("categories"):
            cats = kwargs["categories"]
            if isinstance(cats, str):
                cats = [c.strip() for c in cats.split(",") if c.strip()]
            # v2 API now expects [{"type": "github"}] objects; older string form
            # ["github"] is still accepted for backward compatibility but the
            # docs steer toward the object shape, so normalize on send.
            normalized: list[dict[str, Any]] = []
            for c in cats:
                if isinstance(c, dict):
                    normalized.append(c)
                elif isinstance(c, str) and c:
                    normalized.append({"type": c})
            if normalized:
                payload["categories"] = normalized
        if kwargs.get("country"):
            payload["country"] = kwargs["country"]
        if kwargs.get("location"):
            payload["location"] = kwargs["location"]
        if kwargs.get("tbs"):
            payload["tbs"] = kwargs["tbs"]
        if kwargs.get("timeout"):
            try:
                payload["timeout"] = int(kwargs["timeout"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("ignore_invalid_urls"):
            payload["ignoreInvalidURLs"] = True
        if kwargs.get("include_domains"):
            payload["includeDomains"] = kwargs["include_domains"]
        if kwargs.get("exclude_domains"):
            payload["excludeDomains"] = kwargs["exclude_domains"]
        if kwargs.get("enterprise"):
            ent = kwargs["enterprise"]
            payload["enterprise"] = [ent] if isinstance(ent, str) else list(ent)

        # ---- scrapeOptions -------------------------------------------------
        # NOTE (2026-08-14, live-verified): Firecrawl /v2/search scrapeOptions
        # accepts ONLY the enum documented at api-reference/endpoint/search —
        # `highlights` is NOT in it and returns HTTP 400 `invalid_union`, in
        # BOTH the plain-string and {"type": ...} object shapes. v0.5.0 briefly
        # re-enabled it after the vendor docs implied support; the vendor has
        # since walked that back. `highlights` is an Exa-only concept here, so
        # drop it silently rather than letting a multi-provider mode (recall)
        # 400 the whole Firecrawl leg. See tests/test_v090_drift.py.
        scrape_opts: dict[str, Any] = {"onlyMainContent": True}
        formats: list[Any] = []
        if kwargs.get("with_content"):
            formats.append("markdown")
        if kwargs.get("summary"):
            formats.append("summary")
        if kwargs.get("question"):
            q: dict[str, Any] = {"type": "question", "question": kwargs["question"]}
            formats.append(q)
        if formats:
            scrape_opts["formats"] = formats
        if kwargs.get("lang"):
            scrape_opts["location"] = {
                "country": kwargs.get("country") or "US",
                "languages": [kwargs["lang"]],
            }
        if kwargs.get("scrape_timeout") is not None:
            try:
                scrape_opts["timeout"] = int(kwargs["scrape_timeout"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("wait_for") is not None:
            try:
                scrape_opts["waitFor"] = int(kwargs["wait_for"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("mobile"):
            scrape_opts["mobile"] = True
        if kwargs.get("only_clean_content"):
            scrape_opts["onlyCleanContent"] = True
        if kwargs.get("max_age") is not None:
            try:
                scrape_opts["maxAge"] = int(kwargs["max_age"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("min_age") is not None:
            try:
                scrape_opts["minAge"] = int(kwargs["min_age"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("block_ads") is not None:
            scrape_opts["blockAds"] = bool(kwargs["block_ads"])
        if kwargs.get("remove_base64_images") is not None:
            scrape_opts["removeBase64Images"] = bool(kwargs["remove_base64_images"])
        if kwargs.get("proxy"):
            scrape_opts["proxy"] = kwargs["proxy"]
        if kwargs.get("parsers"):
            # e.g. ["pdf"] — enables PDF parsing in scraped results (v2.5).
            parsers = kwargs["parsers"]
            if isinstance(parsers, str):
                parsers = [p.strip() for p in parsers.split(",") if p.strip()]
            scrape_opts["parsers"] = list(parsers)
        if kwargs.get("redact_pii") is not None:
            scrape_opts["redactPII"] = bool(kwargs["redact_pii"])
        if kwargs.get("store_in_cache") is not None:
            scrape_opts["storeInCache"] = bool(kwargs["store_in_cache"])
        if kwargs.get("lockdown") is not None:
            scrape_opts["lockdown"] = bool(kwargs["lockdown"])
        if kwargs.get("zero_data_retention") is not None:
            scrape_opts["zeroDataRetention"] = bool(kwargs["zero_data_retention"])
        if kwargs.get("skip_tls_verification") is not None:
            scrape_opts["skipTlsVerification"] = bool(kwargs["skip_tls_verification"])
        if formats or any(k in scrape_opts for k in ("location", "timeout", "waitFor", "mobile", "onlyCleanContent", "maxAge", "minAge", "blockAds", "removeBase64Images", "proxy", "parsers", "redactPII", "storeInCache", "lockdown", "zeroDataRetention", "skipTlsVerification")):
            payload["scrapeOptions"] = scrape_opts

        resp = await self._request(
            "POST", SEARCH_ENDPOINT, headers=self._headers(), json=payload
        )
        data = resp.json()
        block = data.get("data") or {}

        out: list[SearchResult] = []
        per_source_cap = max(1, count)
        # iterate in user-specified order so first source wins relative ranking
        for src in sources:
            items = block.get(src) or []
            if not isinstance(items, list):
                continue
            for r in items[:per_source_cap]:
                if not isinstance(r, dict):
                    continue
                url = r.get("url") or r.get("link") or r.get("imageUrl") or ""
                if not url:
                    continue
                summary_val = r.get("summary")
                content_val = r.get("markdown") or r.get("content")
                out.append(
                    SearchResult(
                        url=url,
                        title=r.get("title", "") or url,
                        snippet=r.get("description", "") or r.get("snippet", "") or "",
                        provider=self.name,
                        score=float(r.get("score") or 0.0),
                        published=r.get("date") or r.get("published"),
                        summary=summary_val if isinstance(summary_val, str) else None,
                        content=content_val if isinstance(content_val, str) else None,
                        raw=r,
                    )
                )
        # Honor the global cap.
        return out[:count] if count else out

    async def _extract(self, url: str) -> str | None:
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        resp = await self._request("POST", SCRAPE_ENDPOINT, headers=self._headers(), json=payload)
        data = resp.json()
        return ((data.get("data") or {}).get("markdown")) or None
