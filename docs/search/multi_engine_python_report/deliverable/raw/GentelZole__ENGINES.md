# Engine Evidence — live-tested 2026-09-09

58 candidates (53 HTTP endpoints + 5 local tools) tested from a datacenter Linux box.
25 returned real usable content. **Every failure recorded honestly** — that's the value:
don't re-add dead engines. Companion machine-readable results: `engine-test-results.json` (bottom).

# Free / Keyless Web Search Engines & Scrapers — Verified Catalog (Sep 2026)

Every entry below was **live-tested** from a datacenter-Linux box on 2026-09-09 using
`requests` (stdlib-adjacent, Py 3.11). Test harness: `test_harness.py`, `test_followup.py`,
and ad-hoc probes; structured per-test evidence is in `engine-test-results.json` alongside this file.
**Nothing here is fabricated — failures are recorded because they tell you what not to ship.**

TL;DR: of **58 live tests**, **25 returned real usable content**. The general web search
market in 2026 has almost fully closed off keyless HTML scraping (Mojeek captcha, Startpage PoW
challenge, Ecosia 403, Brave 429, Yandex captcha, DDG-lite anti-bot). The reliable keyless
general-web routes are: **DuckDuckGo HTML (POST only)**, **Marginalia API v2 (public key)**, and
**self-hosted SearXNG** (JSON disabled on essentially all public instances now). Vertical searches
(Wikipedia, HN, Reddit, StackExchange, news RSS, academic APIs, Wayback) are all alive and keyless.
For scraping/reading, **trafilatura** and **readability-lxml** are free and excellent; **Jina Reader
keyless is now Cloudflare-challenged** from datacenter IPs; the **local Firecrawl stack is currently
down**; **Crawl4AI** installs and works once its pinned chromium is installed.

---

## Summary table

| Engine | Type | Key? | Format | Live-test result | Limits | Verdict |
|---|---|---|---|---|---|---|
| DuckDuckGo HTML (`html.duckduckgo.com/html/`) | General web | No | HTML | **200, 10 results** | POST only; ~20-30 q/min bursty | ✅ ship |
| DDG Lite | General web | No | HTML | 202/stub, no results | — | ❌ dead |
| DDG Instant Answer (`api.duckduckgo.com`) | Entity/definition | No | JSON | 200, AbstractText | entity-only, not web search | ⚠️ niche |
| Bing HTML (`www.bing.com/search`) | General web | No | HTML | 200, 10 b_algo, but `/ck/a` redirect links & degraded results | captcha risk; needs UA | ⚠️ fragile |
| Mojeek (`www.mojeek.com`) | General web | API key | HTML/JSON | Captcha page | key API + free tier, scrape=captcha | ❌ scrape-blocked |
| Startpage | General web | No | HTML | PoW challenge | algorithmic challenge | ❌ |
| Ecosia | General web | No | HTML | 403 Firewall | — | ❌ |
| Brave HTML | General web | No | HTML | 429 | use Brave API (free 2k/mo) | ❌ (use API) |
| Marginalia HTML | Indie web | No | HTML | **200, results** | polite | ✅ |
| **Marginalia API v2** (`api2.marginalia-search.com`) | Indie web | public key | **JSON** | **200 JSON, 20 results** | QPM limit on `public` key | ✅ top pick |
| SearXNG (public instances) | Meta | No | JSON | JSON disabled / Anubis / 403 / 429 on **22 tested** | — | ❌ → self-host |
| Wikipedia API (`action=query list=search`) | Factual/encyclopedic | No | JSON | **200, 5877 hits** | needs UA; polite | ✅ |
| Wikidata (`wbsearchentities`) | Entity | No | JSON | **200** | needs UA | ✅ |
| Crossref | Academic | No | JSON | **200, 2.5M results** | polite pool, add mailto | ✅ |
| OpenAlex | Academic | No | JSON | **200, 270k** | generous | ✅ |
| Semantic Scholar | Academic | No (key optional) | JSON | 429→**200 w/ backoff** | shared pool, heavy 429 | ⚠️ retry |
| Google News RSS | News | No | XML/RSS | **200** | none observed | ✅ |
| Bing News RSS | News | No | XML/RSS | **200** | none observed | ✅ |
| Hacker News Algolia | Discussions | No | JSON | **200, real-time** | none | ✅ |
| Reddit JSON (`search.json`) | Discussions | No (OAuth opt) | JSON | **200 w/ honest UA** | ~100 q/5min anon | ✅ (UA-sensitive) |
| Lobste.rs JSON | Discussions | No | JSON | 400 unpermitted param | — | ❌ |
| StackExchange API | Q&A | No (key opt) | JSON | **200** | 300/day | ✅ |
| Yandex | General web | No | HTML | captcha | — | ❌ |
| Stract (hosted) | Indie web | No | JSON | 404 (SPA) | self-host via docker | ⚠️ self-host |
| 4get | Meta | No | HTML | 200 (no clean JSON) | self-hostable | ⚠️ |
| Jina Reader (`r.jina.ai`) | Reader | No (free key) | Markdown | **403 Cloudflare** (3/3 retries) | free key recommended | ⚠️ needs key |
| Jina Search (`s.jina.ai`) | Search | **Required** | JSON | **401** | keyless removed | ❌ needs key |
| Wayback CDX / available / reader | Archive reader | No | JSON/HTML | **200** (429 on burst) | backoff | ✅ |
| trafilatura | Extractor (local) | No | markdown/txt | **66 KB clean MD** | none | ✅ top reader |
| readability-lxml | Extractor (local) | No | HTML→text | **67 KB clean text** | none | ✅ |
| html2text / markdownify | HTML→MD (local) | No | MD | full-page incl nav junk | none | ⚠️ converter only |
| Firecrawl (local docker) | Search+scrape | No | JSON | **DOWN** (api container never up) | self-host | ⚠️ broken now |
| Crawl4AI (local) | JS scraper | No | MD/JSON | **verified headless scrape** (0.9.3, "Example Domain" → MD) | heavy setup (playwright) | ✅ (after setup) |

---

## Part 1 — General web search engines (the hard part)

### DuckDuckGo HTML — ✅ WORKS (the one surviving keyless general engine)

```
POST https://html.duckduckgo.com/html/
Content-Type: application/x-www-form-urlencoded
body: q=open+sthece+web+search+engine
Headers: User-Agent: Mozilla/5.0 ... (browser UA)
```

- **Status:** 200 `text/html`. **10 organic results** parsed (`result__a` links + `result__snippet`).
- **Real excerpt:** `result__a=10 titles=10 | Mwmbl - the Open Sthece and Non-Profit Web Search Engine`
- **Method is critical:** `POST` works; the same query via `GET` returns **HTTP 202** with zero results
  (DDG's new anti-bot treats GET differently). Keep the POST form-encoding.
- Redirects: real URLs arrive as `//duckduckgo.com/l/?uddg=<url-encoded>` — unquote `uddg` param.
- Limits: no key; ~20-30 queries/min before transient 202s; **cache results** and back off on 202/403.
- `lite.duckduckgo.com/lite/` is **dead** — 202 challenge on Firefox UA, and a 200 stub with only 2
  links and no result table on Chrome UA. Do not use.

### Marginalia — ✅ WORKS (indie web; JSON API with free public key)

Marginalia is a human-curated, non-commercial indie index (long-tail/small-web). Two ways in:

1. **HTML (keyless):** `GET https://search.marginalia.nu/search?query=<q>&profile=default`
   → 200, title `open sthece web search engine - Marginalia Search`, real results.
2. **JSON (new API v2, "public" key):**
   ```
   GET https://api2.marginalia-search.com/search?query=web+crawler
   Header: API-Key: public      # no signup needed
   ```
   - **Status:** 200 `application/json`, 20 results/page.
   - **Real excerpt:** `{"license":"CC-BY-NC-SA 4.0","page":1,"pages":11,"query":"...","results":[{"url":"http://infolab.stanford.edu/~backrub/google.html","title":"The Anatomy of a Search Engine", ...}]}`
   - **Result fields:** `url, title, description, quality (float score), format, resultsFromDomain, details`.
- **Rate limit:** `429 QPM Limit Exceeded` — the `public` key has a tight queries-per-minute cap (I hit
  it after ~2 rapid calls). A **free non-commercial key** via email `contact@marginalia-search.com`
  raises it; commercial keys are metered.
- **License:** results CC-BY-NC-SA 4.0 (attribution + non-commercial). Fine for internal agent use.
- The old `search.marginalia.nu/api/search` path is **404**; the API moved to `api2.marginalia-search.com`.

### The blockers (tested, all failed — do not ship)

| Engine | Endpoint tested | Result |
|---|---|---|
| Mojeek | `www.mojeek.com/search?q=` | 200 but **Captcha** page (datacenter IP). Official Mojeek API requires a key; free tier exists but scrape posture = captcha-on-bot. |
| Startpage | `www.startpage.com/sp/search?query=` | 200 but body is a **JS proof-of-work challenge** (`{"rules":{"algorithm":"fast","difficulty":6}}`). Not solvable with stdlib. |
| Ecosia | `www.ecosia.org/search?q=` | **403** "Ecosia Firewall". |
| Brave HTML | `search.brave.com/search?q=` | **429**. Use the Brave Search **API** (free tier ≈2000 q/mo, needs key) — cleaner and ToS-compliant. |
| Yandex | `yandex.com/search/?text=` | 200 but **captcha/robot** page, 0 organic results. |
| Stract (hosted) | `stract.com/api/search` | **404** (Nuxt SPA shell). Open-sthece (StractOrg/stract); **self-host docker** for reliable keyless JSON. |
| DDG Lite | `lite.duckduckgo.com/lite/` | 202 / no-result stub. |

### Bing (⚠️ works-ish, fragile)

`GET https://www.bing.com/search?q=<q>&count=10` with a browser `User-Agent` + `Accept-Language: en-US,en;q=0.9`
returns **200 with 10 `b_algo` organic blocks**. Caveats found in testing (honest record):

- **Redirect URLs:** hrefs are `https://www.bing.com/ck/a?...&u=<base64>` — decode the `u=a1...` base64 param
  to get the real destination.
- **Result quality anomaly:** for the multiword query *"open sthece web search engine"*, Bing returned
  near-duplicate low-quality results about "*Open*" (OpenAI, "Open" newspaper, Open University) — it appears
  Bing served a degraded/fallback SERP for this bot request. Treat as usable-but-untrusted.
- **Captcha risk** on datacenter IPs and on bursts. Add cookie persistence and sleep between queries.

### SearXNG — ❌ public JSON is gone; self-host is THE reliable route

Tested **22 public instances** from the live `searx.space` list with `GET /search?q=<q>&format=json`:

- **JSON disabled (served normal HTML/`text/html`):** paulgo.io, kantan.cat, opnxng.com, searx.tiekoetter.com,
  search.mdosch.de, search.unredacted.org, search.serpensin.com, search.rhscz.eu.
- **Anti-bot JS challenge (Anubis / Substation):** searx.be ("Verifying ythe browser…"), baresearch.org,
  search.hbubli.cc ("Making sure you're not a bot!"), search.inetol.net.
- **403/429:** searxng.site (403), searx.dresden.network (429), priv.au (429/timeout).
- **Dead DNS:** search.bus-hit.me, search.whatever.social, search.sapti.me, searx.work, search.canine.tools, search.zwei.science.

Every public instance either disabled JSON or bot-walls it. **Self-hosting is now mandatory** for reliable
keyless JSON metasearch:

```bash
docker run -d --name searxng --restart unless-stopped \
  -p 8080:8080 -e SEARXNG_BASE_URL=https://<ythe-host>/ \
  searxng/searxng
```

Then **enable JSON output** in `settings.yml` (`search.formats` must list `json` — it is off by default and
most public instances leave it off):

```yaml
search:
  formats:
    - html
    - json
```

Mount a custom `settings.yml` (`-v $PWD/settings.yml:/etc/searxng/settings.yml`). Also set
`server.limiter: false` (or tune `server.public_instance`) to stop self-rate-limiting. Query:
`GET http://localhost:8080/search?q=<q>&format=json` → `{results:[{title,url,content,snippet,engine,...}]}`.

### Google / Vertical "big-name" endpoints (context)

- **Google CSE** `https://www.googleapis.com/customsearch/v1?q=<q>&key=<K>&cx=<CX>` — **needs key + CX**,
  free tier **100 queries/day**. Not keyless; mention only as a free-tier (100/day) option.
- **Wolfram|Alpha** — all APIs (`/v1/simple`, `/v1/result`, `/v2/query`) require an AppID. **Not keyless.**
  Skip unless you add a key.
- **Presearch, Kagi, Tavily, Exa, Serper, SearchAPI** — all require keys/accounts. (SerPer/SearchAPI/Tavily
  are cheap and ToS-clean if you ever add paid budget; gpt-researcher's default retriever is Tavily.)

---

## Part 2 — Keyless vertical / JSON search endpoints (all VERIFIED live)

These are the real workhorses for a keyless multi-engine CLI — official, stable, JSON, no key.

| Engine | Request | Notes |
|---|---|---|
| **Wikipedia full-text** | `GET https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=<q>&format=json&srlimit=5` | 200, `totalhits: 5877`, top title "Search engine". **Must set descriptive UA** (403 without). Strip `<span class="searchmatch">` from snippets. |
| **Wikipedia opensearch** | `GET .../w/api.php?action=opensearch&search=<q>&format=json&limit=5` | title completion, 200 (UA required). |
| **Wikidata** | `GET https://www.wikidata.org/w/api.php?action=wbsearchentities&search=<q>&language=en&format=json` | entities with labels/aliases/descriptions. |
| **Crossref** | `GET https://api.crossref.org/works?query=<q>&rows=3` | 200, `total-results: 2588483`. Add `mailto=` param or mailto UA for polite pool. |
| **OpenAlex** | `GET https://api.openalex.org/works?search=<q>&per-page=3` | 200, `count: 270257`. Generous; supports `mailto`. |
| **Semantic Scholar** | `GET https://api.semanticscholar.org/graph/v1/paper/search?query=<q>&limit=3` | **429 on first try → 200 after ~6s backoff** (`total: 357345`). Shared pool; implement retry/backoff, or free key. |
| **HN Algolia** | `GET https://hn.algolia.com/api/v1/search?query=<q>&tags=story` | 200, real-time. `numericFilters=points>20`, `tags=comment` for comments. |
| **Reddit JSON** | `GET https://www.reddit.com/search.json?q=<q>&limit=5&raw_json=1` | **200 only with an honest non-browser UA** (e.g. `linux:deepsearch:v1.0 (by /u/you)`) — a browser UA gets **403**, the default `python-requests` UA gets 403. ~100 q/5min anonymous; OAuth for production. |
| **StackExchange** | `GET https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q=<q>&site=stackoverflow&pagesize=5` | 200 JSON. Keyless daily quota (300/day with a registered key — still free). `site=` switches network. |
| **Google News RSS** | `GET https://news.google.com/rss/search?q=<q>&hl=en-US&gl=US&ceid=US:en` | 200 RSS 2.0 — parse with `feedparser`. |
| **Bing News RSS** | `GET https://www.bing.com/news/search?q=<q>&format=rss` | 200 RSS 2.0 — surprisingly un-walled; reliable. |
| **Wayback CDX** | `GET https://web.archive.org/cdx/search/cdx?url=<domain/path>&output=json&limit=N` | 200 lines of `[urlkey,timestamp,original,mimetype,statuscode,digest,length]`. Wildcard `url=example.com/*`. 429 on bursts → backoff. |
| **Wayback available** | `GET https://archive.org/wayback/available?url=<url>` | nearest snapshot JSON. |
| **Wayback reader** | `GET https://web.archive.org/web/<timestamp>id_/<url>` | serves archived copy — bypasses live blocks. Pair with CDX. |

**Failed and excluded:** Lobste.rs JSON returns `400 Unpermitted query or form parameter` for every param
shape (q / q+what+order / empty) — kill it. GitHub **code** search needs a token (there is no anonymous
search API — note, don't ship).

---

## Part 3 — Scrapers / readers

### Reader test matrix (quality measured on the same page, `paulgraham.com/greatwork.html`, 79.7 KB)

| Extractor | Output | Length | Quality |
|---|---|---|---|
| **trafilatura** (`trafilatura.extract(html, output_format='markdown')`) | Markdown | **66,661 chars** | **Best-in-class** — main article only, nav/images stripped. |
| **readability-lxml** (`Document(html).summary()`) | HTML (strip tags) | **66,843 chars** | Equivalent main-content quality; correct title detection ("How to Do Great Work"). |
| **html2text** (`HTML2Text().handle(html)`) | Markdown | 68,657 chars | Full-page conversion — **keeps nav + banner images** (turbifycdn). Converter, not extractor. |
| **markdownify** (`markdownify.markdownify(html)`) | Markdown | 67,555 chars | Full-page conversion — **keeps nav table junk** (`\| \| \|`). Converter, not extractor. |

**Recommendation:** use **trafilatura** as the default reader (one pip, no infra, rock-solid). It also
outputs `txt`, `csv`, `json` (with title/date/author/url metadata) and accepts a `url=` for metadata
extraction. Use **readability-lxml** if you specifically need the raw readability DOM.

### Jina — ⚠️ keyless is now unreliable; free key is the fix

- **`r.jina.ai/<url>`** (Reader): **403 "Just a moment…" (Cloudflare)** on all 3 retries from this
  datacenter IP. Documented behavior changed in 2025-26: the keyless endpoint is Cloudflare-gated and
  selectively serves only residential/browser-like clients. **Fix:** get a **free** Jina API key
  (`jina.ai`) and send `Authorization: Bearer jina_xxx` (+ `X-Return-Format: markdown`, `X-No-Cache: true`).
  Also self-hostable.
- **`s.jina.ai/<query>`** (Search): **401 `AuthenticationRequiredError`** — keyless removed entirely; must
  pass `Authorization` header now.
- Rate-limits: free key ~20 req/min (Reader) with 403/429 beyond; returns `X-RateLimit-*` headers.
- Verdict: **not "keyless" anymore** — treat like a free-tier keyed service.

### Wayback as a reader (✅ works keyless)

`https://web.archive.org/web/<timestamp>/<url>` returned 200 with the archived page. For pages that block
you (Ecosia, Mojeek, paywalled, deleted), fetch the latest CDX capture and pull the archived copy. Google
Cache is **dead** (retired 2024) — Wayback is the replacement.

### Firecrawl (local) — ❌ DOWN at test time

`http://localhost:3002` refused connections; `docker ps -a` shows `firecrawl-api-1` stuck in **`Created`**
(never started), `firecrawl-foundationdb-init-1` **Exited (1)**, and `rabbitmq` unhealthy — the stack is
crash-looping and the API container never comes up. **Repair** (`docker compose up -d` after fixing the
FoundationDB init failure) before relying on the `firecrawl` engine in `deepsearch.py`. Note: Hermes'
`web_extract` also routes through this local Firecrawl, so it's affected too.

### Crawl4AI — ✅ installs; heavy setup (best JS-capable free scraper)

- `pip install crawl4ai` → installed **0.9.3** successfully.
- Honest caveat: `arun()` needs a **pinned Playwright headless-shell**. Generic `playwright install
  chromium` installs the wrong build; `crawl4ai-setup` installs full chromium + patchright but **not** the
  headless-shell build crawl4ai launches (1234). **Fix that worked:** `python3 -m playwright install
  chromium-headless-shell`. Verified result: `arun(url='https://example.com')` → 200, title "Example
  Domain", 166 chars clean markdown in ~2 s.
- Apache-2.0 (core) / MIT; also `docker run -p 11235:11235 unclecode/crawl4ai:<ver>`.
- Usage: `AsyncWebCrawler().arun(url=..., bypass_cache=True)` → `result.markdown` (and `cleaned_html`,
  `fit_markdown` with LLM extraction, links, media). Best pick when trafilatura can't handle JS-driven pages.

### Other self-hostable readers (context, not individually tested live)

trafilatura, readability-lxml, Scrapy (general crawler), Passage (JS reader, unmaintained), `mercury-parser`/
`@postlight/parser` (unmaintained), RAG API (`dduprie/rag-appro` — a self-hosted "search + read" REST API,
free; run it ytheself and query `/search` + `/read`). `textise`/`12ft`-style proxies are **dead** (skip as
instructed).

---

## Part 4 — Patterns from other OSS agent projects

**gpt-researcher** (verified via docs.gptr.dev / assafelovic/gpt-researcher): a **single retriever** model —
`RETRIEVER` env var selects one of `tavily` (default), `duckduckgo`, `bing`, `google`, `serper`, `serpapi`,
`searchapi`, `searx`, `arxiv`, `exa`, `semantic_scholar`, `pubmed_central`. It does **not** fan out across
engines; it calls one, **dedups by URL**, then scrapes each sthece and feeds the LLM. The DuckDuckGo
retriever shells out to the `ddgs` package (which uses the same POST HTML endpoint verified), not a
keyed API.

**LlamaIndex / LangChain:** each engine is a **discrete tool** (`DuckDuckGoSearchToolSpec`, `TavilyToolSpec`,
`SearchApiToolSpec`, `BraveSearch`, `SearxSearch`, LangChain's `DuckDuckGoSearchResults`, `SearxSearchWrapper`).
Multi-engine fanout is assembled by the user — neither framework ships consensus ranking out of the box.

**Firecrawl /search & Hermes:** Firecrawl's `/v1/search` combines search + full-page scrape in one call (the
pattern `deepsearch.py` already uses). Hermes routes through per-platform backends with **sequential
failover** (try A → on error/empty → B), not parallel consensus.

**Cross-cutting pattern:** almost nobody does cross-engine *consensus* ranking. The dominant, robust pattern is
**sequential failover + URL dedup + cap-N**, optionally with a cross-encoder reranker when budget allows.

### Recommended architecture for a keyless multi-engine deepsearch CLI

1. **Fan out concurrently** (threads/asyncio) with a **per-engine timeout** (10-15 s) and an overall budget.
2. **Failover order (general web):** DDG-HTML(POST) → Marginalia API2(public key) → self-hosted SearXNG(JSON)
   → Bing-HTML(last resort) — each independent, so a 202/429/captcha only degrades one lane.
3. **Vertical lanes always on** (cheap, stable): Wikipedia, HN-Algolia, Reddit, StackExchange, Google-News-RSS,
   Bing-News-RSS, Crossref/OpenAlex/Semantic-Scholar (if the query looks academic).
4. **Dedup:** normalize URLs (lowercase host, drop `utm_*`/fragment/trailing slash, unshorten DDG `/l/?uddg=`
   and Bing `/ck/a?u=`). Key = normalized URL; keep first occurrence, merge engines list.
5. **Ranking (no-LLM):** order by (a) number of engines agreeing on the URL (consensus), then (b) per-engine
   rank; optionally (c) Marginalia `quality` / SS citation count if present.
6. **Health / circuit breaker:** track consecutive failures per engine; after N failures, back off that engine
   for T seconds (exponential). Treat *empty results* ≠ *error* (don't hammer retries on empty).
7. **Cache:** query → results with TTL (e.g. 1-6 h) to survive outages and stay under keyless rate limits.
8. **Scrape top-K** with trafilatura (fallback readability-lxml → Wayback), honoring per-host politeness.

---

## Part 5 — Anti-bot evasion that stays within ToS

1. **Set a real `User-Agent`.** Browser-UA for DDG/Bing; **honest, descriptive bot-UA for Reddit and
   Wikipedia**, which explicitly detect `python-requests`/browser UAs and block or 403 them. Include a
   contact/mailto where the service asks (Crossref, Wikipedia polite pool).
2. **Respect `robots.txt` and site ToS.** Wikipedia has a crawler policy; Reddit requires a descriptive UA and
   rate limiting; Crossref/OpenAlex/StackExchange publish polite-pool rules.
3. **Honor 429 + `Retry-After`.** Semantic Scholar and Marginalia (public key) 429 aggressively — back off
   (exponential + jitter), never hammer. Wayback also 429s on bursts.
4. **Prefer official keyless APIs over HTML scraping.** Every engine above marked ✅ is an official endpoint;
   the blocklist (Mojeek/Startpage/Ecosia/Brave/Yandex HTML) fails *specifically because* those are scrape
   targets the vendors actively protect. Using their official **free-tier APIs** (Brave 2k/mo, Google CSE
   100/day, Mojeek API, Jina free key) is the ToS-clean way to get general-web results at keyless-like cost.
5. **Cache aggressively** to minimize request volume (and thus bot-detection surface).
6. **Session/cookie persistence** for Bing/DDG (a warm session passes more often than a cold one).
7. Avoid anything that presents as evasion against a *login wall or a vendor's explicit anti-abuse*
   (CAPTCHA-solving, proof-of-work bypass, proxy pools to dodge rate limits) — that risks ToS violation.
   Rotating UA + backoff + caching + official APIs is the legitimate envelope.

---

## Top 6 keyless additions to `deepsearch.py`

1. **Marginalia API v2** (`api2.marginalia-search.com/search?query=` + `API-Key: public`) — keyless JSON, indie/long-tail coverage.
2. **Wikipedia API** (full-text `list=search` + `opensearch`) + **Wikidata** — factual/encyclopedic grounding.
3. **HN Algolia + Reddit JSON** (honest UA) — real-time discussion/community search.
4. **Google News RSS + Bing News RSS** — news vertical, zero auth.
5. **Crossref + OpenAlex (+ Semantic Scholar w/ retry)** — academic vertical.
6. **StackExchange API** — Q&A vertical.

Plus two infra moves: **self-host SearXNG** (docker, enable `search.formats: [html, json]`) to restore a
reliable keyless *general* metasearch lane, and **fix or replace the local Firecrawl** (currently down)
with **trafilatura as the default reader** (and Crawl4AI when JS rendering is needed).

### Recommended self-hosted scraper/reader stack (minimal → full)

- **Minimal (pip-only, zero infra):** `trafilatura` (extract) + `readability-lxml` (fallback) + `html2text` (convert isolated fragments).
- **General search lane:** SearXNG (docker, JSON enabled).
- **JS-heavy scraping:** Crawl4AI (pip + `crawl4ai-setup`, or docker).
- **(Optional, when repaired)** local Firecrawl for combined `/search`+`/scrape`.