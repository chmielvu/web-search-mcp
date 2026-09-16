"""Core pipeline: search -> dive -> distill, cached. Framework-free on purpose,
so scripts and tests can drive it without MCP framing."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import httpx

from . import distill, searx
from .cache import Cache
from .config import Config
from .crawler import Crawler, Page
from .services import ensure_running, stop_native


class RobotsRefusal(Exception):
    """Target host disallows this path for crawlers in its robots.txt."""


def _ch(chars: int) -> str:
    """Honest character count — one scale for every footer field (contract K1 fix)."""
    return str(chars)


# Fallback engine sets for empty-result retries (F-101). Ordered by independence
# from big-scraping backends; rotation always happens WITH backoff pauses.
_RETRY_ENGINE_SETS: list[str | None] = [
    None,  # as requested / instance defaults
    # live-audited 2026-09-09: mwmbl+mojeek+brave = 40+ results, zero captchas;
    # duckduckgo/qwant/startpage were CAPTCHA'd at audit time — demoted to last.
    "brave,mwmbl,mojeek,wikipedia",
    "google cse,brave,mwmbl,mojeek",
    "duckduckgo,bing,qwant,startpage",
]


@dataclass
class ReadResult:
    page: Page
    distilled: str
    cache_hit: bool


class Engine:
    """Everything the tools need; one instance per server process."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cache = Cache(cfg.cache_dir / "cache.db")
        self.crawler = Crawler(cfg)
        self.http: httpx.AsyncClient | None = None
        self._searx_ok = False
        self._search_ts = 0.0
        self._pace_lock = asyncio.Lock()
        self._engine_fails: dict[str, int] = {}
        self._dive_sem = asyncio.Semaphore(cfg.dive_concurrency)
        self._robots: dict[str, "object | None"] = {}  # host -> RobotFileParser | None (None = allow)
        self._metrics_path = cfg.data_dir / "metrics.jsonl"
        try:
            cfg.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------ metrics ---

    def _log_metrics(self, tool: str, *, cache: str | None = None, chars_in: int = 0,
                     chars_out: int = 0, secs: float = 0.0, ok: bool = True,
                     error: str | None = None, url: str | None = None,
                     q: str | None = None, engine_set: str | None = None) -> None:
        """F-304: local JSONL journal (schema — docs/operations/metrics.md §3.2).
        Metrics must never break a tool: swallow everything."""
        if not self.cfg.metrics:
            return
        import datetime
        import hashlib

        def h(v: str | None) -> str | None:
            return hashlib.sha256(v.encode()).hexdigest()[:12] if v else None

        event = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "cache": cache,
            "chars_in": chars_in,
            "chars_out": chars_out,
            "secs": secs,
            "ok": ok,
            "error_class": error,
            "url_hash": h(url),
            "q_hash": h(q),
            "engine_set": engine_set,
        }
        try:
            import json as _json
            with open(self._metrics_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- robots ---

    async def _robots_allowed(self, url: str) -> bool:
        """F-303: respect robots.txt for our direct page dives; fail-open on
        missing/unreachable robots (standard robots semantics). Search itself
        is not crawling — engines fetch, we only query them."""
        if not self.cfg.respect_robots:
            return True
        from urllib import robotparser
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = (parts.scheme, parts.netloc)
        if host in self._robots:
            rp = self._robots[host]
        else:
            rp = None
            if self.http is not None and parts.netloc:
                try:
                    resp = await self.http.get(
                        f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=5.0)
                    if resp.status_code == 200:
                        parser = robotparser.RobotFileParser()
                        parser.parse(resp.text.splitlines())
                        rp = parser
                except (httpx.HTTPError, ValueError):
                    rp = None  # unreachable robots -> allow
            self._robots[host] = rp
        if rp is None:
            return True
        return rp.can_fetch("*", parts.path or "/")

    async def start(self) -> None:
        self.http = httpx.AsyncClient(follow_redirects=True, timeout=self.cfg.search_timeout)

    async def stop(self) -> None:
        await self.crawler.stop()
        await stop_native()
        if self.http:
            await self.http.aclose()

    # ------------------------------------------------------------ search ----

    async def _polite_pace(self) -> None:
        """F-301: keep a configurable minimum interval between backend searches."""
        async with self._pace_lock:
            wait = self.cfg.search_min_interval - (time.monotonic() - self._search_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._search_ts = time.monotonic()

    def _record_health(self, outcome: searx.SearchOutcome) -> None:
        """F-102: consecutive-failure streak per engine; any hit resets it.
        v0.9: failure reasons are now TYPED (borrowed from pi-web-access's
        fallbackOn design) — captcha engines get a longer memory than timeout
        engines, because captchas don't heal in seconds."""
        for e in outcome.unresponsive:
            parts = e.split(":", 1)
            name = parts[0]
            reason = (parts[1] if len(parts) > 1 else "").lower()
            if "captcha" in reason or "suspended" in reason:
                weight = 3  # captcha: sticky condition, counts triple
            elif "timeout" in reason:
                weight = 1  # timeout: transient, counts as-is
            else:
                weight = 2  # quota/network/unknown
            self._engine_fails[name] = self._engine_fails.get(name, 0) + weight
        for h in outcome.hits:
            for name in h.engines:
                self._engine_fails.pop(name, None)

    def _healthy_fallback(self, engine_set: str) -> str:
        bad = {e for e, c in self._engine_fails.items() if c >= 3}
        if not bad:
            return engine_set
        kept = [e for e in engine_set.split(",") if e.strip() and e.strip() not in bad]
        return ",".join(kept) if kept else engine_set

    def bad_engines(self) -> list[str]:
        """Engines with 3+ consecutive empty/unresponsive streaks (for doctor/diagnostics)."""
        return sorted(e for e, c in self._engine_fails.items() if c >= 3)

    async def _search_outcome(self, query: str, *, max_results: int, category: str | None,
                              engines: str | None, language: str | None, time_range: str | None,
                              refresh: bool = False):
        max_results = max(1, min(20, max_results))
        if time_range not in (None, "", "day", "week", "month", "year"):
            time_range = None
        ck = Cache.key("search", query, max_results, category, engines, language, time_range)
        if not refresh:
            got, stored = self.cache.get(ck)
            if got:
                return stored, True
        http = await self._backend()
        # F-101: on an empty outcome retry with independent engine sets + backoff.
        plan: list[str | None] = ([engines] if engines else []) + _RETRY_ENGINE_SETS
        seen_sets: set[str] = set()
        attempts = 0
        outcome: searx.SearchOutcome | None = None
        for engine_set in plan:
            key = engine_set or ""
            if key in seen_sets:
                continue
            seen_sets.add(key)
            if attempts > self.cfg.search_retries:
                break
            if engine_set and attempts:
                engine_set = self._healthy_fallback(engine_set)
            await self._polite_pace()
            try:
                outcome = await searx.search(
                    self.cfg, http, query,
                    categories=category, engines=engine_set, language=language, time_range=time_range,
                )
            except searx.SearxError:
                self._searx_ok = False
                raise
            self._record_health(outcome)
            attempts += 1
            if outcome.hits or outcome.answers:
                break
            if attempts <= self.cfg.search_retries:
                await asyncio.sleep(1.5 * attempts)  # growing pause: rotation never without a delay
        assert outcome is not None
        stored = {
            "hits": [
                {"title": h.title, "url": h.url, "snippet": h.snippet, "engines": h.engines,
                 "score": h.score, "published": h.published}
                for h in outcome.hits
            ],
            "answers": outcome.answers,
            "suggestions": outcome.suggestions,
            "seconds": outcome.seconds,
            "raw_chars": outcome.raw_chars,
            "retries": attempts - 1,
            "unresponsive": outcome.unresponsive,
        }
        # empty outcomes are real answers too — but cache them briefly so a
        # broken minute doesn't shadow an hour of retries
        ttl = self.cfg.search_ttl if (outcome.hits or outcome.answers) else min(self.cfg.search_ttl, 600)
        self.cache.set(ck, stored, ttl)
        return stored, False

    async def search(self, query: str, *, max_results: int = 8, category: str | None = None,
                     engines: str | None = None, language: str | None = None,
                     time_range: str | None = None, refresh: bool = False,
                     as_json: bool = False) -> str:
        started = time.monotonic()
        try:
            stored, cached = await self._search_outcome(
                query, max_results=max_results, category=category,
                engines=engines, language=language, time_range=time_range, refresh=refresh,
            )
        except Exception as e:
            self._log_metrics("web_search", secs=round(time.monotonic() - started, 1),
                              ok=False, error=e.__class__.__name__, q=query)
            raise
        secs = round(time.monotonic() - started, 1)
        hits = stored["hits"][: max(1, min(20, max_results))]
        # Junk-gate (QA-audit finding): engines answer gibberish queries with
        # fuzzy noise ("Profile / X" for "qwjk") that pollutes context and
        # masks the honest No-results path. A hit is junk when NONE of the
        # query's meaningful terms appear in its TITLE or SNIPPET — the URL is
        # deliberately excluded: username echoes (x.com/qwjk, facebook.com/
        # qwjk.qwjk) live there and are the main false-positive source.
        # If junk filters everything out, the empty-body path takes over
        # (and F-101 retries get a chance).
        q_terms = [w for w in re.findall(r"[a-zа-яё0-9]+", query.lower()) if len(w) > 2]
        if q_terms:
            # Junk-gate v4 (QA-audit finding): a valid hit must cover a
            # meaningful SHARE of the query's terms in title+snippet (URL
            # excluded — username echoes live there). Gibberish queries
            # ("zzqqxxwvyu nonterm qwjk") get username-echo pages covering
            # 1-of-3 terms -> junk; real pages cover the core naturally.
            need = max(1, (len(q_terms) + 1) // 2)  # >=50% of terms
            def _is_junk(h: dict) -> bool:
                hay = (str(h.get("title", "")) + " "
                       + str(h.get("snippet", ""))).lower()
                covered = sum(1 for w in q_terms if w in hay)
                return covered < need
            hits = [h for h in hits if not _is_junk(h)]
        lines: list[str] = []
        if stored["answers"]:
            lines.append("Answer: " + stored["answers"][0])
        for i, h in enumerate(hits, 1):
            date = f" [{h['published']}]" if h["published"] else ""
            lines.append(f"{i}. {h['title']}\n   {h['url']}\n   {h['snippet']}{date}")
        if stored["suggestions"]:
            lines.append("Refine: " + ", ".join(stored["suggestions"]))
        body = "\n".join(lines) if lines else self._empty_body(query, stored)
        if as_json:
            # F-204: machine-readable mode — pure JSON, no footer (metrics go to
            # metrics.jsonl; footer would break strict json.loads consumers).
            import json as _json
            payload = {
                "query": query,
                "count": len(hits),
                "hits": [
                    {"title": h["title"], "url": h["url"], "snippet": h["snippet"],
                     "engines": h["engines"], "published": h["published"] or None}
                    for h in hits
                ],
            }
            if stored["answers"]:
                payload["answer"] = stored["answers"][0]
            out = _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self._log_metrics("web_search", cache="HIT" if cached else "MISS",
                              chars_in=stored["raw_chars"], chars_out=len(out),
                              secs=secs, q=query)
            return out
        footer = (
            f"[bathys: {len(hits)} hits · cache HIT · {secs}s · searx json "
            f"{_ch(stored['raw_chars'])} ch → {_ch(len(body))} ch]"
            if cached else
            f"[bathys: {len(hits)} hits · {secs}s · searx json "
            f"{_ch(stored['raw_chars'])} ch → {_ch(len(body))} ch]"
        )
        self._log_metrics("web_search", cache="HIT" if cached else "MISS",
                          chars_in=stored["raw_chars"], chars_out=len(body),
                          secs=secs, q=query,
                          engine_set=("default" if stored.get("retries", 0) == 0
                                      else f"retry#{stored['retries']}"))
        return body + "\n" + footer

    @staticmethod
    def _empty_body(query: str, stored: dict) -> str:
        body = f"No results for: {query}"
        notes: list[str] = []
        if stored.get("retries"):
            notes.append(f"tried {stored['retries'] + 1} engine sets")
        if stored.get("unresponsive"):
            notes.append("unresponsive: " + ", ".join(stored["unresponsive"][:5]))
        return body + (" · " + " · ".join(notes) if notes else "")

    # -------------------------------------------------------------- read ----

    async def _read(self, url: str, *, query: str | None, max_chars: int,
                    refresh: bool = False) -> ReadResult:
        max_chars = max(300, min(50_000, max_chars))
        pk = Cache.key("page", url)
        got, stored = (False, None) if refresh else self.cache.get(pk)
        if got and "error" in stored:
            raise RuntimeError(stored["error"])
        if got:
            page = Page(**stored)
            hit = True
        else:
            if not await self._robots_allowed(url):
                err = f"robots.txt disallows this path: {url}"
                self.cache.set(pk, {"error": err}, ttl=max(3600, self.cfg.page_ttl // 24))
                raise RobotsRefusal(err)
            try:
                page = await self.crawler.fetch(url, http=self.http)
            except Exception as e:
                err = f"{e.__class__.__name__}: {e}"
                self.cache.set(pk, {"error": err}, ttl=max(3600, self.cfg.page_ttl // 24))
                raise RuntimeError(err) from e
            self.cache.set(pk, {"url": page.url, "status": page.status, "title": page.title,
                                "text": page.text, "raw_chars": page.raw_chars}, self.cfg.page_ttl)
            hit = False
        slim = distill.slim_markdown(page.text)
        distilled = distill.passages(slim, query, max_chars)
        return ReadResult(page=page, distilled=distilled, cache_hit=hit)

    def _find_in_cache(self, url: str, pattern: str, *, limit: int = 3,
                       context: int = 160) -> str:
        """find-text over the cached RAW page (borrowed from pi-web-access's
        get_search_content/findText): exact substring search, no network,
        match counters and local context. Cache miss -> honest error."""
        pk = Cache.key("page", url)
        got, stored = self.cache.get(pk)
        if not got:
            return f"[bathys: find — нет кэша для {url}; сначала read_url]"
        if "error" in stored:
            return f"[bathys: find — в кэше ошибка: {stored['error']}]"
        text = stored.get("text", "")
        low, pl = text.lower(), pattern.lower()
        positions: list[int] = []
        i = low.find(pl)
        while i != -1 and len(positions) < 50:
            positions.append(i)
            i = low.find(pl, i + 1)
        if not positions:
            return (f"[bathys: find '{pattern}' — 0 совпадений в кэше "
                    f"({len(text)} ch); попробуй read_url с query]")
        total = len(positions)
        shown = positions[:limit]
        blocks = []
        for n, pos in enumerate(shown, 1):
            lo = max(0, pos - context)
            hi = min(len(text), pos + len(pattern) + context)
            frag = ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")
            frag = re.sub(r"\s+", " ", frag)
            blocks.append(f"{n}. @{pos}: …{frag}…")
        return (f"[bathys: find '{pattern}' — {total} совпадений(я), показано {len(shown)} "
                f"(кэш сырца, без сети)\n" + "\n".join(blocks))

    async def read(self, url: str, *, query: str | None = None, max_chars: int = 8000,
                   refresh: bool = False, find: str | None = None) -> str:
        if find is not None:
            out = self._find_in_cache(url, find)
            self._log_metrics("read_url", cache="HIT", chars_in=0,
                              chars_out=len(out), secs=0.0, url=url)
            return out
        started = time.monotonic()
        try:
            res = await self._read(url, query=query, max_chars=max_chars, refresh=refresh)
        except RobotsRefusal as e:
            secs = round(time.monotonic() - started, 1)
            self._log_metrics("read_url", cache="MISS", secs=secs, ok=False,
                              error="robots", url=url, q=query)
            body = f"# {url}\n{url}\n\n(not fetched — {e})"
            footer = f"[bathys: page 0 ch → 0 ch · robots-refused · cache MISS · {secs}s]"
            return body + "\n\n" + footer
        except Exception as e:
            self._log_metrics("read_url", cache="MISS", secs=round(time.monotonic() - started, 1),
                              ok=False, error=e.__class__.__name__, url=url, q=query)
            raise
        secs = round(time.monotonic() - started, 1)
        p = res.page
        mode = "query-distilled" if query else "head-trimmed"
        footer = (
            f"[bathys: page {_ch(p.raw_chars)} ch → {_ch(len(res.distilled))} ch · "
            f"{mode} · cache HIT · {secs}s]"
            if res.cache_hit else
            f"[bathys: page {_ch(p.raw_chars)} ch → {_ch(len(res.distilled))} ch · "
            f"{mode} · cache MISS · {secs}s]"
        )
        header = f"# {p.title or url}\n{p.url}\n"
        self._log_metrics("read_url", cache="HIT" if res.cache_hit else "MISS",
                          chars_in=p.raw_chars, chars_out=len(res.distilled),
                          secs=secs, url=url, q=query)
        return header + "\n" + res.distilled + "\n\n" + footer

    # ----------------------------------------------------------- research ----

    async def research(self, query: str, *, max_sources: int = 3, max_results: int = 10,
                       per_source_chars: int = 3500, category: str | None = None,
                       engines: str | None = None, language: str | None = None,
                       time_range: str | None = None, refresh: bool = False) -> str:
        started = time.monotonic()
        max_sources = max(1, min(6, max_sources))
        per_source_chars = max(300, min(8000, per_source_chars))
        stored, _ = await self._search_outcome(
            query, max_results=max_results, category=category,
            engines=engines, language=language, time_range=time_range, refresh=refresh,
        )
        raw_total_hits = len(stored["hits"])
        hits = stored["hits"][: max(1, min(20, max_results))]
        top = hits[:max_sources]
        sem = self._dive_sem

        async def dive(h):
            async with sem:
                try:
                    return h, await self._read(h["url"], query=query, max_chars=per_source_chars,
                                               refresh=refresh), None
                except Exception as e:
                    return h, None, f"{e.__class__.__name__}: {e}"

        results = await asyncio.gather(*(dive(h) for h in top))

        sections: list[str] = [f"# Bathys research: {query!r}"]
        if stored["answers"]:
            sections.append("Answer: " + stored["answers"][0])
        raw_total = out_total = 0
        for i, (hit, res, err) in enumerate(results, 1):
            meta_bits = [hit["url"]]
            if hit.get("engines"):
                meta_bits.append("engines: " + ", ".join(hit["engines"][:4]))
            if hit.get("published"):
                meta_bits.append(hit["published"])
            head = f"## {i}. {hit['title']}\n" + " · ".join(meta_bits)
            if res is None:
                sections.append(f"{head}\n\n(not fetched — {err}; snippet: {hit['snippet']})")
                continue
            raw_total += res.page.raw_chars
            out_total += len(res.distilled)
            sections.append(f"{head}\n\n{res.distilled}")
        rest = hits[max_sources:max_sources + 5]
        if rest:
            sections.append("More hits (not fetched):\n" + "\n".join(
                f"- {h['title']} — {h['url']}" for h in rest))
        secs = round(time.monotonic() - started, 1)
        sections.append(
            f"[bathys: {raw_total_hits} raw hits, top {len(hits)} considered · "
            f"dove {len(top)} pages · "
            f"{_ch(raw_total)} ch fetched → {_ch(out_total)} ch returned · {secs}s]"
        )
        self._log_metrics("deep_research", chars_in=raw_total, chars_out=out_total,
                          secs=secs, q=query)
        return "\n\n".join(sections)

    # ----------------------------------------------------------- backend ----

    async def _backend(self) -> httpx.AsyncClient:
        if not self._searx_ok:
            await ensure_running(self.cfg, self.http)
            self._searx_ok = True
        return self.http
