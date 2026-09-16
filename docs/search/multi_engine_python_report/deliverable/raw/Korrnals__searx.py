"""SearXNG client. Expects JSON format enabled on the backend."""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import Config

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DROP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ref", "referrer", "irclickid", "irgwc",
}


class SearxError(RuntimeError):
    pass


def clean_text(s: str, limit: int) -> str:
    s = html.unescape(_TAG_RE.sub(" ", s or ""))
    s = _WS_RE.sub(" ", s).strip()
    if len(s) > limit:
        s = s[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return s


def normalize_url(u: str) -> str:
    try:
        parts = urlsplit(u or "")
    except ValueError:
        return u or ""
    if not parts.scheme:
        return u or ""
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _DROP_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    engines: list[str] = field(default_factory=list)
    score: float = 0.0
    published: str = ""

    @property
    def dedupe_key(self) -> str:
        p = urlsplit(self.url)
        return (p.netloc.removeprefix("www.") + p.path.rstrip("/")).lower()


@dataclass
class SearchOutcome:
    query: str
    hits: list[SearchHit]
    answers: list[str]
    suggestions: list[str]
    seconds: float
    raw_chars: int
    unresponsive: list[str] = field(default_factory=list)


def _answer_text(a: object) -> str:
    if isinstance(a, dict):
        return str(a.get("answer") or a.get("title") or "")
    return str(a)


async def search(
    cfg: Config,
    http: httpx.AsyncClient,
    query: str,
    *,
    categories: str | None = None,
    engines: str | None = None,
    language: str | None = None,
    time_range: str | None = None,
    safesearch: int = 1,
) -> SearchOutcome:
    started = time.monotonic()
    params: dict[str, str | int] = {"q": query, "format": "json", "safesearch": safesearch}
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range
    try:
        resp = await http.get(cfg.searxng_url + "/search", params=params)
    except httpx.HTTPError as e:
        raise SearxError(
            f"searxng unreachable at {cfg.searxng_url}: {e.__class__.__name__}: {e}"
        ) from e
    if resp.status_code != 200:
        hint = ""
        if resp.status_code == 403:
            hint = " — enable JSON in searxng settings: search.formats: [html, json]"
        raise SearxError(f"searxng http {resp.status_code}{hint}")

    data = resp.json()
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for item in data.get("results", []):
        url = normalize_url(item.get("url", ""))
        if not url or url.startswith(("mailto:", "javascript:")):
            continue
        hit = SearchHit(
            title=clean_text(item.get("title", ""), 140),
            url=url,
            snippet=clean_text(item.get("content", ""), 280),
            engines=[str(e) for e in item.get("engines", [])],
            score=float(item.get("score", 0) or 0),
            published=str(item.get("publishedDate") or "")[:10],
        )
        dk = hit.dedupe_key
        if dk in seen:
            continue
        seen.add(dk)
        hits.append(hit)
    hits.sort(key=lambda h: -h.score)
    unresponsive: list[str] = []
    for item in data.get("unresponsive_engines", []):
        if isinstance(item, (list, tuple)) and item:
            unresponsive.append(f"{item[0]}:{item[1]}" if len(item) > 1 else str(item[0]))
        elif item:
            unresponsive.append(str(item))
    return SearchOutcome(
        query=query,
        hits=hits,
        answers=[clean_text(_answer_text(a), 300) for a in data.get("answers", []) if _answer_text(a)],
        suggestions=[str(s) for s in data.get("suggestions", [])][:6],
        seconds=round(time.monotonic() - started, 2),
        raw_chars=len(resp.text),
        unresponsive=unresponsive,
    )
