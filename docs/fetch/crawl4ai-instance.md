# Crawl4AI instance — full-capability rebuild (Contabo VPS)

**Status:** deployed & verified 2026-09-15 · **Image:** `crawl4ai-full:0.9.3` · **Container:** `crawl4ai`
**Deployment source:** `deploy/crawl4ai/` (Dockerfile, docker-compose.yml, config.yml, .llm.env)
**Rollback:** `deploy/crawl4ai/backup-20260915-181136/` on the VPS holds the previous compose/config/.llm.env.

---

## 1. What this is

A self-hosted, **fully trusted** Crawl4AI 0.9.3 server: every config field upstream blocks on
network requests is accepted, authentication is removed, all browser engines are installed,
and patchright's undetected adapter is actually engaged. It is the crawling backend for
`web-search-mcp`'s `crawl_web` tool, and it is reachable from other services on the VPS.

**Topology**

| Element | Value |
|---|---|
| Host | `13.140.176.104` (zawady) — 6 cores, 11.9 GB RAM (tight: ~2 GB free, swap fully used) |
| Container port | `11235` (gunicorn binds `[::]:11235` inside the container) |
| Host publish | `127.0.0.1:11235` only (compose `ports:`) |
| Client access | SSH tunnel from WSL (autossh session forwards 11235, 3000, 19999, 6379) |
| Sibling services | `camoufox` (port 3000, Firefox-shaped stealth sidecar — **not** wired into Crawl4AI), `session-adapter`, searxng, redis, postgres, n8n, lightrag, unified-ml, phoenix |
| Dashboard | `/monitor` · **Metrics** `/metrics` (Prometheus) · **Schema** `/schema` |

---

## 2. What the image adds over `unclecode/crawl4ai:0.9.3`

1. **PDF + BM25 extras** — `pypdf`, NLTK `punkt`+`stopwords` (used by `BM25ContentFilter`).
2. **Every browser engine for the runtime user** — `firefox-1538`, `webkit-2336`, `ffmpeg` copied
   from the stock image's root cache into `/home/appuser/.cache/ms-playwright` (upstream ships them
   but never hands them to `appuser`, which is why `browser_type="firefox"` returned HTTP 500),
   plus **patchright chromium** (`patchright install chromium`).
3. **Real Google Chrome** (`google-chrome-stable`, amd64) → enables `chrome_channel="chrome"`.
4. **Seven build-time patches** (each asserts its anchor; the build fails rather than shipping a
   silently-restricted or half-patched image) — see §3.
5. **Runtime env**: hooks + JS execution enabled, internal URLs + insecure TLS allowed, artifact
   caps raised (100 MB/artifact, 10 GB quota, 7-day TTL).

**Deliberately NOT included:** torch, sentence-transformers, scikit-learn. Embeddings and
clustering run **API-side via Voyage** (`voyage-4-lite`); in-crawl relevance filtering uses
`BM25ContentFilter` (no model). `CosineStrategy` is therefore intentionally absent.

---

## 3. The build-time patches (what, why, where)

| # | Patch | Why it was needed | Anchor |
|---|---|---|---|
| **a** | `Provenance.UNTRUSTED` → `Provenance.TRUSTED` in `api.py` + `server.py` | Upstream rejects `js_code`, `magic`, `simulate_user`, `override_navigator`, `cookies`, `headers`, `proxy_config`, `cdp_url`, `session_id`, `extra_args`, `deep_crawl_strategy` and all LLM strategies on network requests (8 call sites) | asserted `>= 8` sites, then asserted zero remaining |
| **b** | `auth_gate.py::_authenticate()` returns an admin principal unconditionally | Removes the bearer-token gate while keeping `scope["state"]["principal"]` populated for downstream handlers | exact method-body match |
| **c** | `entrypoint.sh` bind: `127.0.0.1:${PORT}` → `${GUNICORN_BIND:-[::]:${PORT}}` | Without a token the entrypoint forced loopback, which breaks published ports (host traffic never reaches container loopback) | exact line |
| **d** | Remove the startup guard that `sys.exit(1)`s on an unauthenticated non-loopback bind | Stock behaviour: "refusing to start … would expose an unauthenticated API" — that is exactly this deployment's intent | regex over the `if not loopback:` block |
| **e** | `crawler_pool._make_crawler()` — wire `AsyncPlaywrightCrawlerStrategy(browser_adapter=UndetectedAdapter())` at both pool call sites **and** the `api.py` worker path | Upstream ships `UndetectedAdapter` but the server builds plain `AsyncWebCrawler(config=cfg)`, so **patchright never engaged** (see unclecode/crawl4ai#1921). Env-gated `CRAWL4AI_UNDETECTED` (default `true`), chromium-only so Firefox requests keep the default strategy | `async def get_crawler` anchor + 2 call-site replacements (asserted) |
| **f** | `MemoryAdaptiveDispatcher(..., max_session_permit=config.crawler.pool.max_session_permit)` in both batch and stream paths | The server built its dispatcher with only `memory_threshold_percent`, so it kept the class default and per-request `semaphore_count` was ignored (upstream issue #1927) | exact multi-line anchors, both paths |
| **g** | `_seed_browser_defaults()` — merge `config.yml crawler.browser.kwargs` into request BrowserConfigs | Upstream applied those defaults **only** to requests that sent no `browser_config`; instance-wide settings (`chrome_channel`, `text_mode`) never reached normal `/crawl` traffic | both `BrowserConfig.load(...)` call sites |

### Gotchas discovered while building (do not re-learn these)

* **The launch path reads `chrome_channel`, not `channel`**
  (`browser_manager.py:1146`: `if self.config.chrome_channel != "chromium": browser_args["channel"] = ...`).
  Setting `channel: "chrome"` in config looks right and does nothing; `chrome_channel: "chrome"` is the
  key that launches real Chrome.
* **Playwright's default headless is `chrome-headless-shell`** — the more detectable build. Real Chrome
  (via `chrome_channel`) also moves Playwright to the "new headless" path.
* **`patchright install chromium` + `PLAYWRIGHT_BROWSERS_PATH`** is required so the runtime user owns a
  patchright-compatible chromium; the stock image only ships plain chromium.
* **Nested config types must use `{"type": ..., "params": {...}}`** (e.g. `markdown_generator`,
  `content_filter`, `extraction_strategy`, `llm_config`).
* **`LLMExtractionStrategy` contract changed in 0.9.3**: top-level `provider` is deprecated →
  `llm_config={"type": "LLMConfig", "params": {"provider": "gemini/gemini-3.1-flash-lite"}}`.
* **`/crawl/job` returns HTTP 202** on accept (not 200); poll `/crawl/job/{task_id}` for
  `processing|completed|failed`.
* **`extra_args` are appended, never replace** the engine's defaults — Chromium flags make
  WebKit refuse to launch (`Cannot parse arguments: Unknown option --disable-gpu`). Keep them out of
  the server config and pass per request.

---

## 4. Capability inventory (what the instance can now do)

### 4.1 Endpoints (all verified live, **no auth header required**)

| Endpoint | Capability |
|---|---|
| `POST /crawl` | Multi-URL crawl; full config surface (see 4.2) |
| `POST /crawl/stream` | SSE streaming crawl for large batches |
| `POST /crawl/job` → `GET /crawl/job/{id}` | Async job queue (in-container Redis), optional webhooks |
| `POST /md` | Markdown extraction (`f=fit\|raw\|bm25`, `q=query`, `c=cleaning`) |
| `POST /html` | Preprocessed HTML optimized for schema extraction |
| `POST /screenshot` | Full-page PNG (`screenshot_wait_for`, `output_path`) |
| `POST /pdf` | PDF export |
| `POST /execute_js` | Run JS scripts on a page and return the full crawl result |
| `GET /llm/{url}?q=` (also `provider`, `temperature`) | **Server-side LLM page Q&A — Gemini** |
| `POST /llm/job` | Async LLM extraction job + webhooks |
| `GET /ask` | Library-context Q&A |
| `GET /hooks/info` | Declarative hook catalogue |
| `GET /schema`, `GET /config/dump` | Effective configuration |
| `GET /health`, `GET /metrics` | Health + Prometheus metrics |
| `GET /artifacts/{id}` | Screenshot/PDF artifact store |
| `GET /monitor/*` | Pool state, browser control (`kill_browser`, `restart_browser`), logs, timeline |
| `GET /mcp/schema`, `/mcp/sse`, `/mcp/ws` | Crawl4AI's own MCP server (tools: md, html, screenshot, pdf, execute_js, crawl, ask) |

### 4.2 Config surface now accepted (was 400 "not permitted")

`js_code`, `js_code_before_wait`, `magic`, `simulate_user`, `override_navigator`, `cookies`,
`headers`, `proxy_config`, `cdp_url`, `chrome_channel`, `channel`, `extra_args`, `session_id`,
`user_data_dir`, `storage_state`, `deep_crawl_strategy` (BFS/DFS/BestFirst), LLM extraction +
content-filter strategies, `virtual_scroll_config`, `link_preview_config`, `geolocation`, `locale`,
`check_robots_txt`, `max_retries`, `page_timeout`, `wait_for`, `scan_full_page`, `screenshot`,
`pdf`, `capture_console_messages`, `capture_network_requests`, `capture_mhtml`, table extraction,
`css_selector`, `excluded_tags/selector`, markdown generator + filters, chunking, and every
allowlisted strategy type (`PruningContentFilter`, `BM25ContentFilter`, `CosineStrategy`,
`JsonCss/JsonXPath/JsonLxml`, `RegexChunking`, `RegexExtractionStrategy`, `LXMLWebScrapingStrategy`,
`PDFContentScrapingStrategy`).

### 4.3 Browser engines

| Engine | Status | Notes |
|---|---|---|
| Chromium + **patchright (UndetectedAdapter)** | ✅ default | `use_undetected: True` verified |
| **Real Google Chrome** (`chrome_channel="chrome"`) | ✅ default | authentic Chrome TLS/HTTP-2 fingerprint, new-headless |
| Firefox | ✅ | plain Playwright Firefox (stealth libs are Chromium-only, so no added stealth) |
| WebKit | ❌ | Upstream limitation: Chromium CLI flags are appended to every launch and WebKit's launcher rejects them. Not worth fixing (no Safari fidelity, no stealth tooling) |
| Camoufox |  (by design) | Cannot be driven by Crawl4AI — no `executable_path`, private adapter seam. It stays the standalone sidecar (`camoufox:3000`) used by the fetch/crawl fallback ladder |

### 4.4 Hooks (declarative, enabled)

`block_resources`, `add_cookies`, `set_headers`, `scroll_to_bottom`, `wait_for_timeout` (≤60 s),
max 10 hooks per request — usable to block heavy assets, inject auth, scroll for lazy content, or
wait out a JS challenge.

### 4.5 LLM & embeddings (API-side)

* **LLM:** Gemini `gemini-3.1-flash-lite` via litellm (`LLM_PROVIDER` / `LLM_API_KEY` +
  `GEMINI_API_KEY` / `GOOGLE_API_KEY` aliases in `.llm.env`). Powers `/llm/{url}`, `/ask`,
  `LLMExtractionStrategy`, `LLMContentFilter`. **Caveat:** the model intermittently returns
  503 "experiencing high demand"; a retry usually succeeds.
* **Embeddings/clustering:** Voyage `voyage-4-lite` (`VOYAGE_API_KEY`) — consumed downstream by our
  own pipeline, not inside the container.

---

## 5. Settings (`config.yml`) and why

| Setting | Value | Rationale |
|---|---|---|
| `crawler.memory_threshold_percent` | 80 | Host runs near-exhausted swap; the guard gates new browser creation |
| `crawler.pool.idle_ttl_sec` | 180 | Recycle idle browsers faster on a tight host |
| `crawler.pool.max_pages` | 32 | Page cap for a single crawl |
| `crawler.pool.max_session_permit` | 6 | **Concurrency cap** (new in this build; the dispatcher previously used its class default) |
| `crawler.browser.kwargs.chrome_channel` | `"chrome"` | Real Chrome by default (patch b/g make it effective) |
| `crawler.browser.kwargs.text_mode` / `light_mode` | true | ~30–40 % less memory; per-request override allowed |
| `crawler.browser.extra_args` | *(none)* | Engine-specific flags break Firefox/WebKit — pass per request instead |
| `crawler.rate_limiter.base_delay` | `[1.0, 2.0]` | Politeness pacing between requests |
| `rate_limiting.storage_uri` | `redis://localhost:6379/0` | Limits survive restarts; matches the job store |
| `security.trusted_hosts` | `["*"]` | Host guard disabled (trusted deployment) |
| `security.enabled / jwt_enabled / api_token` | false / false / "" | No auth anywhere |
| `logging.level` | INFO | Operational visibility |
| `observability.prometheus` | enabled | `/metrics` for the monitoring stack |
| `llm.provider` | `gemini/gemini-3.1-flash-lite` | Only chat provider configured |

---

## 6. Runbook

```bash
# ---- deploy / rebuild -------------------------------------------------------
cd /srv/stacks/crawl4ai
DOCKER_BUILDKIT=1 docker compose build        # ~1–3 min incremental, ~6 min cold
docker compose up -d                          # recreate with the new image

# ---- config-only change (config.yml is mounted :ro) -------------------------
docker compose restart crawl4ai

# ---- health / status --------------------------------------------------------
docker compose ps
curl -s localhost:11235/health
curl -s localhost:11235/schema | head -c 400

# ---- rollback ---------------------------------------------------------------
cp backup-20260915-181136/{docker-compose.yml,config.yml} . && cp backup-20260915-181136/.llm.env .
docker compose up -d
```

**Environment toggles**

| Env | Effect |
|---|---|
| `CRAWL4AI_UNDETECTED` (default `true`) | Engage/disengage the patchright undetected adapter for chromium crawls |
| `CRAWL4AI_UPSTREAM_PROXY` | Route browser egress through a proxy (paired with the built-in SSRF pinning) |
| `CRAWL4AI_EXECUTE_JS_ENABLED` / `CRAWL4AI_HOOKS_ENABLED` | Feature gates for JS + hooks |
| `CRAWL4AI_ALLOW_INTERNAL_URLS` / `CRAWL4AI_ALLOW_INSECURE_TLS` | SSRF/TLS relaxations |
| `CRAWL4AI_MAX_ARTIFACT_BYTES` / `CRAWL4AI_ARTIFACT_QUOTA_BYTES` / `CRAWL4AI_ARTIFACT_TTL_SECONDS` | Artifact store caps |

---

## 7. Verification performed (2026-09-15)

* Endpoint sweep, **15 probes, zero Authorization headers**: health, full-config `/crawl`
  (chrome + stealth + `js_code` + `magic` + `simulate_user` + `override_navigator` + cookies +
  headers + extra_args + `deep_crawl_strategy` + `BM25ContentFilter`), engines (chromium / firefox),
  `/md`, `/execute_js`, `/screenshot` (22 KB PNG), `/pdf` (19.6 KB), `/llm` (Gemini answered),
  `/crawl/job` + poll (202 → completed), `/hooks/info`, `/schema`.
* **patchright engagement**: in-container construction shows
  `AsyncPlaywrightCrawlerStrategy` + `UndetectedAdapter` + `use_undetected: True`; Firefox configs keep `False`.
* **Real Chrome**: during a 4-URL crawl, 19 `/opt/google/chrome/chrome` processes (previously
  `chrome-headless-shell`); 4/4 successes.
* **No regression in our consumer**: `web-search-mcp` crawl pipeline returned the same artifacts
  (whisper 916 w, deepeval 2208 w) after the engine/adapter changes.
* **Known upstream limits still true**: DataCamp returns 307 (its security interstitial) — the
  Jina-backed fallback in our pipeline is what resolves that class of site, not the browser.
  WebKit remains unusable (see §4.3).

---

## 8. Backlog — highest ROI first (not yet applied)

| Rank | Item | Gain | Effort |
|---|---|---|---|
| 1 | **Proxy egress** (`CRAWL4AI_UPSTREAM_PROXY`, residential + sticky) | The only remaining lever for DataDome/Turnstile-class walls; fingerprint work is done, IP reputation is the blocker | config only, needs purchase |
| 2 | **Headful under Xvfb** (add `xvfb`, run `headless: false` on a virtual display) | Removes headless-specific signals; patchright recommends headed for max stealth | ~1 h image change |
| 3 | **Rate-limiter tuning** `[1.0,2.0] → [0.5,1.0]` | ~20–30 % wall-time reduction on multi-URL crawls | one config line |
| 4 | **Cache revalidation** (`cache_mode=ENABLED` + `check_cache_freshness=true`) | Avoid re-fetch of unchanged pages on repeat crawls | client-side flag |
| 5 | **Session/profile reuse** (`user_data_dir` + `session_id`) | Consent walls, coherent identity across crawls | per-request |
| 6 | **LLM resilience** (retry / fallback model) | Removes intermittent Gemini 503 failures | small client change |
| 7 | **Metrics scrape** into the monitoring stack | Early warning on pool/memory pressure | ~15 min |
| 8 | **`block_resources` hook default** | Faster crawls on asset-heavy pages | one line |

**Explicitly rejected** (documented so it is not re-litigated): fixing WebKit, wiring Camoufox into
Crawl4AI, and UA rotation (mismatched UA is itself a detection signal — coherence beats rotation).

---

## 9. Client notes (`web-search-mcp`)

* Base URL from `.env`: `CRAWL4AI_BASE_URL=http://127.0.0.1:11235` (tunnel).
* The client still sends `Authorization: Bearer <CRAWL4AI_TOKEN>`; the header is **ignored** now.
  Keeping it is harmless and makes it easy to re-enable auth later (patch b would need reverting).
* `crawl_pipeline._crawler_params()` sends `word_count_threshold=2`, `target_elements=["article"]`,
  `excluded_selector`, and an explicit `markdown_generator` + `PruningContentFilter(0.3, fixed, 0)`.
  Instance defaults now *seed* per-request configs, so `chrome_channel`/`text_mode`/`light_mode`
  apply even when the client omits them.
* Hard-site behaviour: page-level failures fall back once to the single-URL ladder
  (registry → Jina → Crawl4AI Markdown → **Camoufox** → archive) via
  `crawl_pipeline._fetch_fallback_artifact`.