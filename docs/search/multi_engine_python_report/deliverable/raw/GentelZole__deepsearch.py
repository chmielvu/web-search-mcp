#!/usr/bin/env python3
"""
deepsearch.py — Multi-engine web search CLI (v2, 2026-09-09).

Usage:
    python3 deepsearch.py "query" -E ENGINE -n 10 [-o json|text] [-s 3]
    python3 deepsearch.py "query" -E all        # parallel fan-out + RRF fusion
    python3 deepsearch.py --read URL            # keyless full-page reader
    python3 deepsearch.py --health              # probe every engine, print table

Engines (validated live 2026-09-09 against research/free-search-engines-2026.md):
  keyed/self-hosted: brave, serpapi, firecrawl (search+scrape, local)
  keyless general:   searxng (self-hosted :8899, JSON enabled), ddg (POST-only),
                     marginalia (API v2), bing (HTML, datacenter-safe), 4get
  keyless vertical:  wikipedia, openalex, arxiv, hn (Algolia), gnews (Google News RSS),
                     bnews (Bing News RSS), stackexchange, wayback
Fusion: -E all runs every AVAILABLE engine concurrently, URL-canonical dedup,
Reciprocal Rank Fusion  score = Σ 1/(60+rank)  (Cormack et al. SIGIR 2009).

Reader fallback chain: firecrawl → trafilatura → stdlib html2text-ish strip.
"""

import argparse, base64, json, os, re, sys, time, gzip, io, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------- keys/env
# API keys: export BRAVE_API_KEY / SERPAPI_KEY (optional — keyless engines cover general search)
BRAVE_KEY   = os.environ.get("BRAVE_API_KEY", "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
FC_URL   = os.environ.get("FIRECRAWL_URL",   "http://localhost:3002")
SX_URL   = os.environ.get("SEARXNG_URL",     "http://localhost:8899")
FC_VER   = "v1"
UA_CHROME = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
UA_HONEST = {"User-Agent": "deepsearch/2.0 (research CLI; keyless engines; low volume)"}
TIMEOUT = 25

def _req(method, url, headers=None, timeout=TIMEOUT, **kw):
    if requests is None: raise RuntimeError("requests not installed")
    h = dict(UA_CHROME); h.update(headers or {})
    r = requests.request(method, url, headers=h, timeout=timeout,
                         allow_redirects=True, **kw)
    if r.headers.get("Content-Encoding", "") == "gzip" and isinstance(r.content, bytes) and not hasattr(r, "_decoded"):
        pass  # requests decodes gzip transparently
    return r

def _strip(html):
    html = re.sub(r'(?is)<(script|style|noscript).*?</\1>', ' ', html)
    return re.sub(r'\s+', ' ', re.sub(r'(?s)<[^>]+>', ' ', html)).replace('&nbsp;', ' ').replace('&amp;', '&').replace('&#x27;', "'").strip()

# ---------------------------------------------------------------- keyed engines
def search_brave(q, n):
    if not BRAVE_KEY: return []
    r = _req("GET", "https://api.search.brave.com/res/v1/web/search",
             headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY},
             params={"q": q, "count": n}, timeout=15)
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")}
            for i in r.json().get("web", {}).get("results", [])]

def search_serpapi(q, n):
    if not SERPAPI_KEY: return []
    r = _req("GET", "https://serpapi.com/search",
             params={"q": q, "num": n, "api_key": SERPAPI_KEY, "engine": "google"}, timeout=20)
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("link", ""), "snippet": i.get("snippet", "")}
            for i in r.json().get("organic_results", [])]

def _fc_ok():
    try: return _req("GET", FC_URL + "/", timeout=3).status_code == 200
    except Exception: return False

def search_firecrawl(q, n):
    if not _fc_ok(): return []
    try:
        r = _req("POST", f"{FC_URL}/{FC_VER}/search",
                 json={"query": q, "limit": n, "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True}},
                 timeout=90)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data and data[0].get("markdown"):
            return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", ""),
                     "content": i.get("markdown", "")[:4000]} for i in data]
    except Exception:
        pass
    r = _req("POST", f"{FC_URL}/{FC_VER}/search", json={"query": q, "limit": n}, timeout=60)
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")}
            for i in r.json().get("data", [])]

# ---------------------------------------------------------------- keyless general
def search_searxng(q, n):
    """Self-hosted metasearch (docker searxng on SX_URL with json format enabled)."""
    r = _req("GET", SX_URL + "/search", headers=UA_HONEST,
             params={"q": q, "format": "json", "engines": "google,duckduckgo,brave,bing,marginalia"}, timeout=15)
    if r.status_code != 200: return []
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
            for i in r.json().get("results", [])[:n]]

def search_ddg(q, n):
    """DDG HTML — POST ONLY (GET is bot-challenged 2026)."""
    r = _req("POST", "https://html.duckduckgo.com/html/", data={"q": q}, timeout=20)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        url, title = m.group(1), _strip(m.group(2))
        if "uddg=" in url:
            url = urllib.parse.unquote(url.split("uddg=")[1].split("&")[0])
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', r.text[m.end():m.end()+2500], re.S)
        out.append({"title": title, "url": url, "snippet": _strip(sm.group(1)) if sm else ""})
        if len(out) >= n: break
    return out

def _bing_unwrap(href):
    href = href.replace("&amp;", "&")
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m: return None
    try:
        pad = lambda t: t + "=" * (-len(t) % 4)
        return base64.urlsafe_b64decode(pad(m.group(1))).decode("utf-8", "replace")
    except Exception:
        return None

def search_bing(q, n):
    """Bing HTML 2026 layout: results wrapped in /ck/a?u=a1<base64> redirect links."""
    r = _req("GET", "https://www.bing.com/search", params={"q": q, "count": n}, timeout=20)
    out = []
    for chunk in r.text.split('class="b_algo"')[1:]:
        hrefs = re.findall(r'<a[^>]+href="(https://www\.bing\.com/ck/a[^"]+)"', chunk)
        title_m = re.search(r'<a[^>]+href="https://www\.bing\.com/ck/a[^"]*"[^>]*>(.*?)</a>', chunk, re.S)
        url = None
        for h in hrefs:
            url = _bing_unwrap(h)
            if url and url.startswith("http"): break
        if not url: continue
        title = _strip(title_m.group(1)) if title_m else ""
        snip_m = re.search(r'<p[^>]*>(.*?)</p>', chunk, re.S)
        out.append({"title": title, "url": url, "snippet": _strip(snip_m.group(1))[:300] if snip_m else ""})
        if len(out) >= n: break
    return out

def search_marginalia(q, n):
    """Marginalia: keyless public JSON endpoint first, api2 (public key) fallback."""
    try:
        r = _req("GET", "https://api.marginalia.nu/public/search/" + urllib.parse.quote(q),
                 headers=UA_HONEST, timeout=20)
        if r.status_code == 200:
            out = [{"title": i.get("title", ""), "url": i.get("url", ""),
                    "snippet": (i.get("description") or "").strip()}
                   for i in r.json().get("results", [])[:n]]
            if out: return out
    except Exception:
        pass
    return search_marginalia_html(q, n)

def search_marginalia_html(q, n):
    """Marginalia HTML fallback (api2 QPM exhausted) — parse result links."""
    r = _req("GET", "https://search.marginalia.nu/search", params={"query": q, "profile": "default"},
             headers=UA_HONEST, timeout=20)
    if r.status_code != 200: return []
    out = []
    for m in re.finditer(r'<a[^>]+href="(https?://(?!www\.marginalia|search\.marginalia)[^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        url, title = m.group(1), _strip(m.group(2))
        if not title or len(out) >= n: continue
        out.append({"title": title[:120], "url": url, "snippet": "marginalia (indie web)"})
    return out

# ---------------------------------------------------------------- keyless verticals
def search_wikipedia(q, n):
    r = _req("GET", "https://en.wikipedia.org/w/api.php",
             params={"action": "query", "list": "search", "srsearch": q, "srlimit": n, "format": "json"},
             headers=UA_HONEST, timeout=15)
    return [{"title": i["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(i["title"].replace(" ", "_")),
             "snippet": _strip(i["snippet"])[:300]} for i in r.json().get("query", {}).get("search", [])]

def search_openalex(q, n):
    r = _req("GET", "https://api.openalex.org/works",
             params={"search": q, "per-page": n, "select": "id,title,doi,publication_year,cited_by_count,authorships"},
             headers=UA_HONEST, timeout=20)
    out = []
    for w in r.json().get("results", []):
        url = w.get("doi") or w.get("id", "")
        if url.startswith("https://doi.org/") is False and url.startswith("https://openalex.org/"): url = w["id"]
        out.append({"title": w.get("title", ""), "url": url,
                    "snippet": f"OpenAlex · {w.get('publication_year','')} · cites:{w.get('cited_by_count',0)}"})
    return out

def search_arxiv(q, n):
    r = _req("GET", "https://export.arxiv.org/api/query", params={"search_query": f"all:{q}", "max_results": n},
             headers=UA_HONEST, timeout=20)
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", r.text, re.S):
        e = m.group(1)
        t = re.search(r"<title>(.*?)</title>", e, re.S); u = re.search(r"<id>(.*?)</id>", e)
        s = re.search(r"<summary>(.*?)</summary>", e, re.S)
        if t and u:
            out.append({"title": _strip(t.group(1)), "url": u.group(1).strip(), "snippet": _strip(s.group(1))[:300] if s else ""})
    return out

def search_hn(q, n):
    r = _req("GET", "https://hn.algolia.com/api/v1/search",
             params={"query": q, "hitsPerPage": n}, headers=UA_HONEST, timeout=15)
    out = []
    for h in r.json().get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append({"title": h.get("title") or "", "url": url,
                    "snippet": f"HN · {h.get('points',0)} pts · {h.get('num_comments',0)} comments"})
    return out

def search_gnews(q, n):
    r = _req("GET", "https://news.google.com/rss/search", params={"q": q}, headers=UA_HONEST, timeout=15)
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", r.text, re.S):
        e = m.group(1)
        t = re.search(r"<title>(.*?)</title>", e, re.S); l = re.search(r"<link>(.*?)</link>", e)
        p = re.search(r"<pubDate>(.*?)</pubDate>", e)
        if t and l:
            out.append({"title": _strip(t.group(1)), "url": l.group(1).strip(),
                        "snippet": f"news · {p.group(1).strip() if p else ''}"})
        if len(out) >= n: break
    return out

def search_bnews(q, n):
    r = _req("GET", "https://www.bing.com/news/search", params={"q": q, "format": "rss"}, headers=UA_CHROME, timeout=15)
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", r.text, re.S):
        e = m.group(1)
        t = re.search(r"<title>(.*?)</title>", e, re.S); l = re.search(r"<link>(.*?)</link>", e)
        if t and l: out.append({"title": _strip(t.group(1)), "url": l.group(1).strip(), "snippet": "bing news"})
        if len(out) >= n: break
    return out

def search_stackexchange(q, n):
    r = _req("GET", "https://api.stackexchange.com/2.3/search/advanced",
             params={"order": "desc", "sort": "relevance", "q": q, "site": "stackoverflow", "pagesize": n, "filter": "!nNPvSNdWme"},
             headers=UA_HONEST, timeout=15)
    if r.status_code != 200: return []
    try:
        # handle gzip if requests didn't
        if not r.text.strip().startswith("{") and r.content[:2] == b"\x1f\x8b":
            j = json.loads(gzip.decompress(r.content))
        else:
            j = r.json()
    except Exception: return []
    return [{"title": i.get("title", ""), "url": i.get("link", ""), "snippet": f"SE/{i.get('site','')} · score:{i.get('score',0)}"}
            for i in j.get("items", [])]

def search_wayback(q, n):
    """Not a web search: finds archived snapshots of domains matching the query token."""
    dom = re.search(r"(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})", q, re.I)
    if not dom: return []
    r = _req("GET", "https://web.archive.org/cdx/search/cdx",
             params={"url": dom.group(1), "output": "json", "limit": n, "collapse": "urlkey",
                     "filter": "statuscode:200", "from": "2023"}, headers=UA_HONEST, timeout=20)
    try: rows = r.json()
    except Exception: return []
    out = []
    for row in rows[1:] if rows else []:
        out.append({"title": f"Wayback {row[2]}", "url": f"https://web.archive.org/web/{row[1]}/{row[2]}",
                    "snippet": f"archived {row[1][:8]}"})
    return out

# ---------------------------------------------------------------- registry
ENGINES = {
    "searxng":    search_searxng,     "ddg": search_ddg,
    "marginalia": search_marginalia,  "bing": search_bing,
    "wikipedia":  search_wikipedia,   "openalex": search_openalex, "arxiv": search_arxiv,
    "hn":         search_hn,          "gnews": search_gnews, "bnews": search_bnews,
    "stackexchange": search_stackexchange, "wayback": search_wayback,
    "brave": search_brave, "serpapi": search_serpapi, "firecrawl": search_firecrawl,
}
GENERAL = ["searxng", "ddg", "bing", "marginalia", "brave", "serpapi", "firecrawl"]

# ---------------------------------------------------------------- fusion
def canon(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = re.sub(r"^(www|m|mobile|amp|light)\d*\.", "", p.netloc.lower())
        path = p.path.rstrip("/") or "/"
        return host + path
    except Exception:
        return url

def rrf_merge(per_engine, limit=30):
    scores, best = {}, {}
    for eng, results in per_engine.items():
        for rank, item in enumerate(results, 1):
            key = canon(item.get("url", ""))
            if not key: continue
            scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
            prev = best.get(key)
            item = dict(item); item.setdefault("engines", [])
            if prev is None:
                item["engines"] = [eng]; best[key] = item
            else:
                if eng not in prev["engines"]: prev["engines"].append(eng)
    out = sorted(best.values(), key=lambda x: -scores[canon(x["url"])])
    for x in out:
        x["rrf"] = round(scores[canon(x["url"])], 4)
        x["consensus"] = len(x["engines"])
    return out[:limit]

def run_engine(eng, q, n):
    t0 = time.time()
    try:
        res = ENGINES[eng](q, n)
        return eng, res, None, round(time.time() - t0, 1)
    except Exception as e:
        return eng, [], str(e)[:120], round(time.time() - t0, 1)

def fanout(q, n, engines=None):
    engines = engines or list(ENGINES)
    per, errs, timing = {}, {}, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_engine, e, q, n): e for e in engines}
        for f in as_completed(futs, timeout=120):
            eng, res, err, dt = f.result()
            timing[eng] = dt
            if err: errs[eng] = err
            if res: per[eng] = res
    merged = rrf_merge(per)
    return {"query": q, "engines_ok": sorted(per), "engines_failed": errs, "timing_s": timing,
            "results": merged, "counts": {e: len(r) for e, r in per.items()}}

# ---------------------------------------------------------------- reader
def read_url(url):
    """Full-page extraction: firecrawl → trafilatura → requests+strip."""
    if _fc_ok():
        try:
            r = _req("POST", f"{FC_URL}/{FC_VER}/scrape",
                     json={"url": url, "formats": ["markdown"], "onlyMainContent": True}, timeout=90)
            d = r.json().get("data", {})
            if d.get("markdown"): return {"url": url, "engine": "firecrawl", "content": d["markdown"][:20000]}
        except Exception: pass
    try:
        import trafilatura
        html = trafilatura.fetch_url(url)
        txt = trafilatura.extract(html, include_links=False) if html else None
        if txt: return {"url": url, "engine": "trafilatura", "content": txt[:20000]}
    except Exception: pass
    try:
        r = _req("GET", url, headers=UA_CHROME, timeout=25)
        return {"url": url, "engine": "raw", "content": _strip(r.text)[:20000]}
    except Exception as e:
        return {"url": url, "error": str(e)[:200]}

# ---------------------------------------------------------------- health
def health():
    probes = {
        "firecrawl": lambda: _fc_ok(),
        "searxng":   lambda: _req("GET", SX_URL + "/search", params={"q": "x", "format": "json"}, headers=UA_HONEST, timeout=8).status_code == 200,
        "ddg":       lambda: len(search_ddg("test", 3)) > 0,
        "bing":      lambda: len(search_bing("test", 3)) > 0,
        "marginalia":lambda: len(search_marginalia("test", 3)) > 0,
        "brave":     lambda: len(search_brave("test", 3)) > 0,
        "serpapi":   lambda: len(search_serpapi("test", 3)) > 0,
        "wikipedia": lambda: len(search_wikipedia("test", 3)) > 0,
        "openalex":  lambda: len(search_openalex("test", 2)) > 0,
        "arxiv":     lambda: len(search_arxiv("test", 2)) > 0,
        "hn":        lambda: len(search_hn("test", 3)) > 0,
        "gnews":     lambda: len(search_gnews("test", 3)) > 0,
        "bnews":     lambda: len(search_bnews("test", 3)) > 0,
        "stackexchange": lambda: len(search_stackexchange("test", 3)) > 0,
    }
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fn): name for name, fn in probes.items()}
        for f in as_completed(futs, timeout=60):
            name = futs[f]
            try: out[name] = bool(f.result())
            except Exception: out[name] = False
    return out

# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?")
    p.add_argument("-E", "--engine", default="all")
    p.add_argument("-n", "--max-results", type=int, default=10)
    p.add_argument("-s", "--scrape-top", type=int, default=0)
    p.add_argument("--extract", type=str, default=None)
    p.add_argument("-o", "--output", choices=["json", "text"], default="json")
    p.add_argument("--read", type=str, default=None, help="full-page reader for a URL")
    p.add_argument("--health", action="store_true")
    a = p.parse_args()

    if a.health:
        h = health()
        print(json.dumps(h, indent=1))
        ok = sum(h.values())
        print(f"# {ok}/{len(h)} engines alive", file=sys.stderr)
        return

    if a.read:
        print(json.dumps(read_url(a.read), ensure_ascii=False, indent=1)); return

    if not a.query:
        p.error("query required (or use --read URL / --health)")

    if a.engine == "all":
        res = fanout(a.query, a.max_results)
    else:
        eng, res_l, err, dt = run_engine(a.engine, a.query, a.max_results)
        if err and not res_l:
            print(json.dumps({"engine": eng, "results": [], "error": err}), file=sys.stderr); sys.exit(1)
        res = {"engine": eng, "results": res_l, "error": err, "seconds": dt}

    if a.scrape_top > 0:
        scraped = []
        for item in res.get("results", [])[:a.scrape_top]:
            if item.get("url"): scraped.append(read_url(item["url"]))
            time.sleep(0.2)
        res["scraped"] = scraped

    if a.output == "json":
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for i, x in enumerate(res.get("results", []), 1):
            src = ",".join(x.get("engines", [res.get("engine", "")]))
            print(f"{i}. {x.get('title','')} [{src}] rrf={x.get('rrf','')}")
            print(f"   {x.get('url','')}")
            if x.get("snippet"): print(f"   {x['snippet'][:200]}")
            print()

if __name__ == "__main__":
    main()
