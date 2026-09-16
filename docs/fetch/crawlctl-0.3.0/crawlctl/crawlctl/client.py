"""Async REST client for a self-hosted Crawl4AI server.

Verified against server sources v0.6.3 / v0.7.5 / v0.8.0 / v0.9.3
(deploy/docker/server.py). Key facts driving this design:

* ``GET /health`` is the only reliably public endpoint on every version and
  reports ``{"status": "ok", "version": "x.y.z", ...}``.
* ``GET /schema`` returns dumped default Browser/Crawler configs. On open
  servers (<= 0.8.9 default posture) it is unauthenticated; on 0.9.x it
  requires a Bearer token -> an unauthenticated 401 is itself a version signal.
* ``/token.json`` and ``/config.json`` DO NOT EXIST (common misconception).
  Real endpoints: ``POST /token`` (JWT mint), ``POST /config/dump`` (config
  validation probe), ``GET /schema``.
* ``POST /crawl`` takes ``{urls[1..100], browser_config?, crawler_config?}``
  and returns ``{success, results[], server_processing_time_s, ...}``. Each
  result carries ``markdown`` as a 5-key dict
  ``{raw_markdown, markdown_with_citations, references_markdown, fit_markdown,
  fit_html}`` where ``fit_markdown`` is only populated when a content filter
  is configured.
* 0.9.x runs an UNTRUSTED config gate: it rejects (HTTP 400) configs
  containing ``js_code``, ``session_id``, ``deep_crawl_strategy``,
  ``proxy_config``, ``magic``, ``base_url``, ``simulate_user``, cookies,
  headers and all LLM*/Proxy*/DeepCrawl* strategy types, silently drops
  unknown fields, and clamps ``page_timeout`` <= 60000 ms. Deep crawling via
  the REST API is therefore impossible on 0.9.x -> crawlctl always does
  client-side BFS over ``result.links.internal`` instead.
* ``POST /md`` is the cheap path: ``{url, f: "raw"|"fit"|"bm25"|"llm", q?, c?}``
  -> ``{url, filter, query, cache, markdown, success}``. ``fit`` applies
  server-default PruningContentFilter, ``bm25`` requires ``q``. Not tunable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

DEFAULT_BASE_URL = os.getenv("CRAWL4AI_BASE_URL", "http://localhost:11235")
DEFAULT_TIMEOUT = float(os.getenv("CRAWL4AI_TIMEOUT", "180"))
MAX_RETRIES = int(os.getenv("CRAWL4AI_MAX_RETRIES", "2"))

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _vt(version: str) -> Tuple[int, int, int]:
    m = _VERSION_RE.search(version or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


@dataclass
class ServerProfile:
    """What the client learned about the server at connect time.

    ``capabilities`` flags gate config building so one client works against
    any server build from ~0.6.x to current without per-version branches at
    call sites.
    """

    base_url: str = DEFAULT_BASE_URL
    version: str = "unknown"
    version_tuple: Tuple[int, int, int] = (0, 0, 0)
    health: Dict[str, Any] = field(default_factory=dict)
    auth_required: bool = False
    token_configured: bool = False
    schema_ok: bool = False
    schema: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "version": self.version,
            "auth_required": self.auth_required,
            "token_configured": self.token_configured,
            "schema_ok": self.schema_ok,
            "capabilities": self.capabilities,
            "health": self.health,
        }


class Crawl4AIError(RuntimeError):
    """Raised for server-side failures with actionable messages."""


class Crawl4AIClient:
    """HTTP client with runtime capability auto-detection.

    Auth model: bearer-optional. ``Authorization: Bearer <token>`` is attached
    only when a token is configured (``CRAWL4AI_API_TOKEN`` or constructor
    arg). Health checks never send the header (keeps detection clean).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or os.getenv("CRAWL4AI_API_TOKEN") or None
        self.timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self.profile: Optional[ServerProfile] = None

    # ------------------------------------------------------------- plumbing
    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    def _headers(self, *, auth: bool = True) -> Dict[str, str]:
        if auth and self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        auth: bool = True,
        retries: int = MAX_RETRIES,
    ) -> httpx.Response:
        """POST/GET with retry + backoff on transient failures.

        4xx responses are returned as-is (callers decide); 5xx and network
        timeouts are retried with exponential backoff.
        """
        http = await self._http()
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                resp = await http.request(
                    method, path, json=json_body, headers=self._headers(auth=auth)
                )
                if resp.status_code >= 500:
                    last_exc = Crawl4AIError(
                        f"{method} {path} -> HTTP {resp.status_code} (server error)"
                    )
                else:
                    return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
            if attempt < retries:
                await asyncio.sleep(0.5 * (2**attempt))
        raise Crawl4AIError(
            f"{method} {path} failed after {retries + 1} attempts: {last_exc}"
        ) from last_exc

    @staticmethod
    def _error_for(resp: httpx.Response, context: str) -> Crawl4AIError:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("detail") if isinstance(body, dict) else str(body)
        except Exception:
            detail = resp.text[:300]
        if resp.status_code == 401:
            return Crawl4AIError(
                f"{context}: server requires authentication (HTTP 401). "
                "Set CRAWL4AI_API_TOKEN (env) or pass token= to Crawl4AIClient."
            )
        if resp.status_code == 400:
            return Crawl4AIError(
                f"{context}: server rejected the config (HTTP 400): {detail}. "
                "On Crawl4AI >= 0.9 the REST API forbids js_code, session_id, "
                "deep_crawl_strategy, proxy_config and LLM strategies; crawlctl "
                "strips those automatically — you may be passing unsupported "
                "extra config fields."
            )
        return Crawl4AIError(f"{context}: HTTP {resp.status_code}: {detail}")

    # ------------------------------------------------------------ discovery
    async def detect(self) -> ServerProfile:
        """Auto-detect server version, auth posture and capability flags.

        Recipe (verified against 0.6.3/0.7.5/0.8.0/0.9.3):
          1. GET /health (no auth)  -> version string.
          2. GET /schema (no auth)  -> 200: open server; 401: authed server.
          3. GET /schema (authed)   -> capability introspection.
        Never uses /openapi.json (401-gated on 0.9.x).
        """
        profile = ServerProfile(base_url=self.base_url, token_configured=bool(self.token))

        try:
            health_resp = await self._request("GET", "/health", auth=False, retries=1)
        except Crawl4AIError:
            raise Crawl4AIError(
                f"GET /health failed. Is the Crawl4AI server running at "
                f"{self.base_url}? (docker: unclecode/crawl4ai, default port 11235; "
                "verify CRAWL4AI_BASE_URL)"
            )
        if health_resp.status_code != 200:
            raise Crawl4AIError(
                f"GET /health -> HTTP {health_resp.status_code}. Is the Crawl4AI "
                f"server running at {self.base_url}? (docker: unclecode/crawl4ai, "
                "default port 11235)"
            )
        try:
            profile.health = health_resp.json() or {}
        except Exception:
            profile.health = {}
        profile.version = str(profile.health.get("version") or "unknown")
        profile.version_tuple = _vt(profile.version)
        v = profile.version_tuple

        # Auth posture via /schema
        schema_resp = await self._request("GET", "/schema", auth=False, retries=0)
        if schema_resp.status_code == 200:
            profile.schema = schema_resp.json() or {}
            profile.schema_ok = True
        elif schema_resp.status_code in (401, 403):
            profile.auth_required = True
            if self.token:
                authed = await self._request("GET", "/schema", auth=True, retries=0)
                if authed.status_code == 200:
                    profile.schema = authed.json() or {}
                    profile.schema_ok = True
        else:
            # unusual: treat as reachable but schema-less
            profile.schema_ok = False

        profile.capabilities = {
            # 0.9.x "untrusted" gate: rejects power fields, clamps timeouts
            "untrusted_gate": v >= (0, 9, 0),
            # content filter moved inside markdown_generator in 0.7+
            "generator_style_filter": v >= (0, 7, 0) or v == (0, 0, 0),
            # target_elements[] param appeared around 0.8
            "target_elements": v >= (0, 8, 0) or v == (0, 0, 0),
            # cheap /md endpoint
            "md_endpoint": v >= (0, 6, 0) or v == (0, 0, 0),
            # result.tables field
            "tables_field": v >= (0, 6, 1) or v == (0, 0, 0),
            # NDJSON streaming crawl
            "stream": True,
            # per-URL crawler_configs (0.8.7+)
            "per_url_configs": v >= (0, 8, 7),
            # deep-crawl strategies over REST (blocked by the 0.9 gate)
            "server_deep_crawl": (0, 6, 0) <= v < (0, 9, 0),
        }
        self.profile = profile
        return profile

    async def ensure_profile(self) -> ServerProfile:
        if self.profile is None:
            await self.detect()
        assert self.profile is not None
        return self.profile

    # ------------------------------------------------------------- endpoints
    async def health(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/health", auth=False)
        if resp.status_code != 200:
            raise self._error_for(resp, "GET /health")
        return resp.json() or {}

    async def schema(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/schema", auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, "GET /schema")
        return resp.json() or {}

    async def validate_config(self, crawler_config: dict) -> Dict[str, Any]:
        """POST /config/dump round-trip: server-normalized dump or error detail.

        Useful as a pre-flight probe; on 0.9.x the 400 detail names exactly
        which config fields were rejected.
        """
        payload: Dict[str, Any] = {"type": "CrawlerRunConfig", "params": crawler_config}
        resp = await self._request("POST", "/config/dump", json_body=payload, auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, "POST /config/dump")
        return resp.json() or {}

    async def md(self, url: str, f: str = "fit", q: Optional[str] = None,
                 cached: bool = False) -> Dict[str, Any]:
        """Cheap single-URL markdown via POST /md.

        f: raw | fit | bm25 | llm. bm25 requires q. Returns
        {url, filter, query, cache, markdown, success}.
        """
        payload: Dict[str, Any] = {"url": url, "f": f, "c": "1" if cached else "0"}
        if q is not None:
            payload["q"] = q
        resp = await self._request("POST", "/md", json_body=payload, auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, f"POST /md ({url})")
        data = resp.json() or {}
        if not data.get("success", True):
            raise Crawl4AIError(f"POST /md ({url}) reported failure: {data}")
        return data

    async def html(self, url: str) -> Dict[str, Any]:
        """Schema-preprocessed HTML via POST /html -> {html, url, success}."""
        resp = await self._request("POST", "/html", json_body={"url": url}, auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, f"POST /html ({url})")
        return resp.json() or {}

    async def crawl(
        self,
        urls: List[str],
        browser_config: Optional[dict] = None,
        crawler_config: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """POST /crawl with 1..100 urls -> list of CrawlResult dicts."""
        if not urls:
            return []
        payload: Dict[str, Any] = {"urls": list(urls)[:100]}
        if browser_config:
            payload["browser_config"] = browser_config
        if crawler_config:
            payload["crawler_config"] = crawler_config
        resp = await self._request("POST", "/crawl", json_body=payload, auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, f"POST /crawl ({len(urls)} urls)")
        data = resp.json() or {}
        results = data.get("results") or []
        if not results and not data.get("success", True):
            raise Crawl4AIError(f"POST /crawl reported failure: {data}")
        return results

    async def crawl_stream(
        self,
        urls: List[str],
        browser_config: Optional[dict] = None,
        crawler_config: Optional[dict] = None,
    ):
        """POST /crawl/stream (NDJSON). Yields one CrawlResult dict per line;
        a final line {"status": "completed"} terminates the stream."""
        payload: Dict[str, Any] = {"urls": list(urls)[:100]}
        if browser_config:
            payload["browser_config"] = browser_config
        if crawler_config:
            payload["crawler_config"] = crawler_config
        http = await self._http()
        async with http.stream(
            "POST", "/crawl/stream", json=payload, headers=self._headers()
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise Crawl4AIError(
                    f"POST /crawl/stream -> HTTP {resp.status_code}: {body[:300]}"
                )
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("status") == "completed":
                    break
                yield item

    # --------------------------------------------------------------- helpers
    async def get_bytes(self, url: str, timeout: float = 30.0) -> bytes:
        """Direct fetch (sitemaps, llms.txt) without the Crawl4AI server."""
        http = await self._http()
        resp = await http.get(url, timeout=timeout)
        if resp.status_code != 200:
            raise Crawl4AIError(f"GET {url} -> HTTP {resp.status_code}")
        return resp.content

    async def metrics(self) -> str:
        resp = await self._request("GET", "/metrics", auth=True)
        if resp.status_code != 200:
            raise self._error_for(resp, "GET /metrics")
        return resp.text


def extract_markdown(result: dict) -> Tuple[str, str]:
    """Return (raw_markdown, fit_markdown) from a CrawlResult dict.

    Handles every observed shape:
    * ``markdown`` as the 5-key dict (0.6+): raw/fit read from sub-keys;
    * ``markdown`` as a plain string (older builds / /md endpoint);
    * missing -> ("", "").
    """
    md = result.get("markdown")
    if isinstance(md, dict):
        raw = md.get("raw_markdown") or md.get("markdown") or ""
        fit = md.get("fit_markdown") or ""
        return raw or "", fit or ""
    if isinstance(md, str):
        return md, ""
    return "", ""


def result_links(result: dict) -> Dict[str, List[str]]:
    """Normalize result.links into {'internal': [href...], 'external': [...]}."""
    out: Dict[str, List[str]] = {"internal": [], "external": []}
    links = result.get("links") or {}
    for key in ("internal", "external"):
        for item in links.get(key) or []:
            if isinstance(item, dict):
                href = item.get("href")
            else:
                href = str(item)
            if href:
                out[key].append(href)
    return out


def monotonic_ts() -> float:
    return time.monotonic()
