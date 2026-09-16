"""Serper.dev (Google SERP). Docs: https://serper.dev/api-key"""
from __future__ import annotations

from typing import Any

from hsearch.models import SearchResult
from hsearch.providers.base import SearchProvider

ENDPOINTS = {
    "search": "https://google.serper.dev/search",
    "news": "https://google.serper.dev/news",
    "scholar": "https://google.serper.dev/scholar",
    "images": "https://google.serper.dev/images",
    "videos": "https://google.serper.dev/videos",
    "shopping": "https://google.serper.dev/shopping",
    "places": "https://google.serper.dev/places",
    "patents": "https://google.serper.dev/patents",
}

# Map search_kind -> serper sub-endpoint
KIND_MAP = {
    "web": "search",
    "news": "news",
    "scholar": "scholar",
    "academic": "scholar",
    "image": "images",
    "images": "images",
    "video": "videos",
    "videos": "videos",
    "shopping": "shopping",
    "places": "places",
    "patent": "patents",
    "patents": "patents",
}


class SerperProvider(SearchProvider):
    name = "serper"
    requires_env = ["SERPER_API_KEY"]

    _last_answer: str | None = None
    _last_knowledge_graph: dict[str, Any] | None = None
    _last_people_also_ask: list[dict[str, Any]] | None = None
    _last_related_searches: list[str] | None = None

    async def _search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        self._last_answer = None
        self._last_knowledge_graph = None
        self._last_people_also_ask = None
        self._last_related_searches = None

        kind = (kwargs.get("search_kind") or kwargs.get("search_type") or kwargs.get("endpoint") or "web").lower()
        endpoint_key = KIND_MAP.get(kind, "search") if kind in KIND_MAP else (kind if kind in ENDPOINTS else "search")
        url = ENDPOINTS.get(endpoint_key, ENDPOINTS["search"])

        payload: dict[str, Any] = {"q": query, "num": max(1, min(count, 20))}
        if kwargs.get("country"):
            payload["gl"] = kwargs["country"]
        if kwargs.get("locale"):
            payload["hl"] = kwargs["locale"]
        if kwargs.get("tbs"):
            payload["tbs"] = kwargs["tbs"]
        if kwargs.get("location"):
            payload["location"] = kwargs["location"]
        if kwargs.get("page") is not None:
            try:
                payload["page"] = max(1, int(kwargs["page"]))
            except (TypeError, ValueError):
                pass
        if kwargs.get("autocorrect") is not None:
            payload["autocorrect"] = bool(kwargs["autocorrect"])

        headers = {
            "X-API-KEY": self.api_key or "",
            "Content-Type": "application/json",
        }
        resp = await self._request("POST", url, headers=headers, json=payload)
        data = resp.json()

        # Extract SERP features for richer recall
        self._extract_serp_features(data)

        out: list[SearchResult] = []
        if endpoint_key == "news":
            items = data.get("news") or []
            for r in items[:count]:
                out.append(
                    SearchResult(
                        url=r.get("link", ""),
                        title=r.get("title", ""),
                        snippet=r.get("snippet", "") or "",
                        provider=self.name,
                        published=r.get("date"),
                        raw=r,
                    )
                )
        elif endpoint_key == "images":
            items = data.get("images") or []
            for r in items[:count]:
                out.append(
                    SearchResult(
                        url=r.get("imageUrl") or r.get("link", ""),
                        title=r.get("title", "") or "",
                        snippet=r.get("source", "") or "",
                        provider=self.name,
                        raw=r,
                    )
                )
        elif endpoint_key == "videos":
            items = data.get("videos") or []
            for r in items[:count]:
                out.append(
                    SearchResult(
                        url=r.get("link", ""),
                        title=r.get("title", "") or "",
                        snippet=r.get("snippet", "") or r.get("source", "") or "",
                        provider=self.name,
                        published=r.get("date"),
                        raw=r,
                    )
                )
        elif endpoint_key == "shopping":
            items = data.get("shopping") or []
            for r in items[:count]:
                price = r.get("price") or ""
                out.append(
                    SearchResult(
                        url=r.get("link", ""),
                        title=r.get("title", "") or "",
                        snippet=f"{price} — {r.get('source', '')}".strip(" —"),
                        provider=self.name,
                        raw=r,
                    )
                )
        elif endpoint_key == "places":
            items = data.get("places") or []
            for r in items[:count]:
                addr = r.get("address", "") or ""
                rating = r.get("rating", "")
                out.append(
                    SearchResult(
                        url=r.get("website") or r.get("link", "") or f"https://maps.google.com/?q={r.get('title','')}",
                        title=r.get("title", "") or "",
                        snippet=(f"{addr} (★{rating})" if rating else addr).strip(),
                        provider=self.name,
                        raw=r,
                    )
                )
        elif endpoint_key == "scholar":
            items = data.get("organic") or []
            for r in items[:count]:
                out.append(
                    SearchResult(
                        url=r.get("link", ""),
                        title=r.get("title", "") or "",
                        snippet=r.get("snippet", "") or r.get("publicationInfo", "") or "",
                        provider=self.name,
                        published=r.get("year"),
                        raw=r,
                    )
                )
        elif endpoint_key == "patents":
            items = data.get("organic") or data.get("patents") or []
            for r in items[:count]:
                out.append(
                    SearchResult(
                        url=r.get("link", "") or r.get("patentUrl", ""),
                        title=r.get("title", "") or "",
                        snippet=r.get("snippet", "") or r.get("assignee", "") or "",
                        provider=self.name,
                        published=r.get("publicationDate") or r.get("date"),
                        raw=r,
                    )
                )
        else:
            # Main organic web results
            items = data.get("organic") or []
            for r in items[:count]:
                # Merge siteLinks into snippet for richer content
                sitelinks = r.get("sitelinks") or []
                sitelink_text = ""
                if isinstance(sitelinks, list) and sitelinks:
                    sl_parts = [sl.get("title", "") for sl in sitelinks[:4] if isinstance(sl, dict) and sl.get("title")]
                    if sl_parts:
                        sitelink_text = " | Related: " + ", ".join(sl_parts)
                snippet = (r.get("snippet", "") or "") + sitelink_text
                out.append(
                    SearchResult(
                        url=r.get("link", ""),
                        title=r.get("title", ""),
                        snippet=snippet,
                        provider=self.name,
                        score=0.0,
                        published=r.get("date"),
                        raw=r,
                    )
                )

            # Append answerBox as a top result if available
            answer_box = data.get("answerBox")
            if isinstance(answer_box, dict) and answer_box.get("answer") or answer_box and answer_box.get("snippet"):
                ab_snippet = answer_box.get("answer") or answer_box.get("snippet") or ""
                ab_title = answer_box.get("title") or "Google Answer"
                ab_url = answer_box.get("link") or ""
                if ab_url and ab_snippet:
                    out.insert(0, SearchResult(
                        url=ab_url,
                        title=f"[Answer] {ab_title}",
                        snippet=ab_snippet,
                        provider=self.name,
                        raw=answer_box,
                    ))

            # Append knowledgeGraph as a result for entity queries
            kg = data.get("knowledgeGraph")
            if isinstance(kg, dict) and kg.get("title"):
                kg_desc = kg.get("description", "") or ""
                kg_type = kg.get("type", "") or ""
                attrs = kg.get("attributes") or {}
                attr_text = " | ".join(f"{k}: {v}" for k, v in attrs.items() if v) if isinstance(attrs, dict) else ""
                kg_snippet = " — ".join(s for s in (kg_type, kg_desc, attr_text) if s)
                kg_url = kg.get("descriptionLink") or kg.get("website") or ""
                if kg_url:
                    out.insert(0, SearchResult(
                        url=kg_url,
                        title=f"[Knowledge] {kg.get('title', '')}",
                        snippet=kg_snippet[:500],
                        provider=self.name,
                        image=kg.get("imageUrl") if isinstance(kg.get("imageUrl"), str) else None,
                        raw=kg,
                    ))

            # Append "People Also Ask" as extra results for recall
            paa = data.get("peopleAlsoAsk") or []
            if isinstance(paa, list):
                for item in paa[:3]:
                    if not isinstance(item, dict):
                        continue
                    paa_url = item.get("link", "")
                    paa_title = item.get("question", "") or item.get("title", "")
                    paa_snippet = item.get("snippet", "") or item.get("answer", "") or ""
                    if paa_url and paa_title:
                        out.append(SearchResult(
                            url=paa_url,
                            title=f"[PAA] {paa_title}",
                            snippet=paa_snippet,
                            provider=self.name,
                            raw=item,
                        ))

        return out

    def _extract_serp_features(self, data: dict[str, Any]) -> None:
        """Stash SERP features on the instance for engine-level consumption."""
        # Answer box
        ab = data.get("answerBox")
        if isinstance(ab, dict):
            self._last_answer = ab.get("answer") or ab.get("snippet")

        # Knowledge graph
        kg = data.get("knowledgeGraph")
        if isinstance(kg, dict) and kg.get("title"):
            self._last_knowledge_graph = kg

        # People Also Ask
        paa = data.get("peopleAlsoAsk")
        if isinstance(paa, list) and paa:
            self._last_people_also_ask = paa

        # Related searches
        rs = data.get("relatedSearches")
        if isinstance(rs, list) and rs:
            self._last_related_searches = [
                item.get("query", "") for item in rs
                if isinstance(item, dict) and item.get("query")
            ]
