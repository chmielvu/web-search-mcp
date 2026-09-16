# Crawl4AI instance — improvement backlog

Companion to [`crawl4ai-instance.md`](./crawl4ai-instance.md) (deployed state, capabilities, runbook).
Items are ordered by ROI. Each has an acceptance check so completion is provable.

**Deployment source:** `Contabo-VPS/deploy/crawl4ai/` · **Image:** `crawl4ai-full:0.9.3`

---

## 1. Proxy egress — `CRAWL4AI_UPSTREAM_PROXY`  highest ROI

**Why:** fingerprint work is done (real Chrome + patchright + new-headless). What remains is IP
**reputation** — the one signal no browser-side change can fix. Datacamp/DataDome-class walls answer
307 from this datacenter IP while the same page resolves through Jina.

**Change:** set `CRAWL4AI_UPSTREAM_PROXY=http://user:pass@host:port` in `.llm.env` (server-side env;
the per-request `proxy_config` field is also open now, but the upstream proxy keeps the built-in SSRF
pinning). Prefer **residential with sticky sessions**; do not rotate mid-session.

**Acceptance:** `/crawl` on `https://www.datacamp.com/tutorial/pydantic-ai-guide` returns
`success=true` with the article (not the 307 interstitial), twice in a row with a stable session.

**Cost:** proxy subscription. **Effort:** config only.

---

## 2. Headful under Xvfb

**Why:** patchright's own guidance is that headed Chrome is the strongest configuration; headless is a
detectable property independent of fingerprint patches. A virtual display removes the need for a GPU.

**Change:** add `xvfb` to the image, run the container with `DISPLAY=:99` and a supervisor entry that
starts `Xvfb :99 -screen 0 1920x1080x24`; switch `crawler.browser.kwargs.headless` to `false`.

**Acceptance:** `browser_config.params.headless=false` crawl succeeds; a headless-detection page
(e.g. `https://arh.antoinevastel.com/bots/areyouheadless`) reports "You are not Chrome headless".

**Cost:** ~1 h image change, +~150 MB RAM per browser instance. **Effort:** medium.

---

## 3. Rate-limiter tuning

**Why:** `base_delay: [1.0, 2.0]` inserts ~1–2 s between requests; on a 12-URL crawl that is ~18 s of
pure pacing (measured: 31 s total).

**Change:** `crawler.rate_limiter.base_delay: [0.5, 1.0]`.

**Acceptance:** 12-URL christophergs.com crawl wall-time drop ≥20 % with 12/12 successes; revert if any
target starts returning 429/403.

**Cost:** none (politeness trade-off). **Effort:** one line.

---

## 4. Cache revalidation for repeat crawls

**Why:** our client always sends `cache_mode: bypass`, so re-crawling the same corpus re-fetches
everything. `CacheMode.ENABLED` + `check_cache_freshness: true` revalidates instead.

**Change:** client-side in `crawl_pipeline._crawler_params()` (or per-request opt-in), leaving `bypass`
as the default for freshness-critical calls.

**Acceptance:** second crawl of an unchanged page returns the cached artifact with a documented
freshness check, and a changed page is re-fetched (both observable in `stage_attempts`/timings).

**Cost:** none. **Effort:** small client change.

---

## 5. Session/profile reuse

**Why:** fresh contexts are themselves a signal; consent walls and gated pages need continuity.

**Change:** per-site `user_data_dir` + `session_id` (both now accepted). Start with one consent-walled
target to prove the pattern before generalising.

**Acceptance:** a page behind a consent dialog is fetched without the dialog on the second call using
the same `session_id`; the profile directory is reused (no new browser persona).

**Cost:** disk + pool memory (each profile can spawn its own browser). **Effort:** per-request wiring.

---

## 6. LLM resilience (Gemini 503s)

**Why:** `gemini-3.1-flash-lite` intermittently returns `503 — model is currently experiencing high
demand` (hit live twice; retry succeeded). `/llm/{url}`, `/ask` and `LLMExtractionStrategy` inherit it.

**Change:** retry with backoff on 503 in our client, or add a secondary provider path. (Crawl4AI has no
native fallback chain; `LLMConfig` provider is single-valued.)

**Acceptance:** 10 consecutive `/llm` calls on a fixed URL succeed 10/10 (with retries invisible to the
caller).

**Cost:** none. **Effort:** small client change.

---

## 7. Observability wiring

**Why:** pool growth / memory pressure on a host whose swap is already exhausted.

**Change:** scrape `http://127.0.0.1:11235/metrics` into the existing monitoring stack; alert on
container RSS and on `/monitor/browsers` count growth.

**Acceptance:** metrics visible in the dashboard with at least one alert rule on memory and pool size.

**Cost:** ~15 min. **Effort:** low.

---

## 8. `block_resources` hook as default

**Why:** asset-heavy pages spend most of their time downloading images/fonts that markdown output never
uses (`text_mode` already covers much of this, so measure before keeping).

**Change:** add `block_resources` to the default hooks payload for large crawls.

**Acceptance:** measurable wall-time drop on an asset-heavy target with identical markdown output.

**Effort:** one line; keep only if the measurement holds.

---

## Explicitly rejected (do not re-litigate)

| Item | Reason |
|---|---|
| **Fix WebKit** | Playwright's WebKit is a Linux build — no Safari fidelity (TLS/JA3 is WebKit's own stack, not Safari's); no stealth tooling exists for it; the fix touches the shared launch path used by the working engines. Also runs *against* the 2026 practitioner consensus, which is Chromium-based stealth. |
| **Wire Camoufox into Crawl4AI** | No `executable_path` in `BrowserConfig`; the only seams are a process-wide `PLAYWRIGHT_BROWSERS_PATH` substitution (brittle, version-stamped) or an undocumented adapter interface. Camoufox's stealth lives in its own client, so its binary under vanilla Playwright loses the point. It stays the standalone sidecar (`camoufox:3000`) used by the fetch/crawl fallback ladder. |
| **UA rotation / `user_agent_mode: random`** | Playwright reports the *host's* real values; a Windows UA on a Linux container is a detectable mismatch. Use a single coherent identity (Chrome/Linux UA + matching locale/timezone) instead. |

---

## Verification checklist for any future change

1. `docker compose build` — every patch prints `patched: …` or the build fails on its assertion.
2. `docker compose ps` → `healthy`; `curl -s localhost:11235/health`.
3. `/crawl` smoke: 3–4 URLs, `browser_config.params.headless=true`, expect `success=true` per URL.
4. Adapter check (in-container): `_make_crawler(BrowserConfig(headless=True))` →
   `UndetectedAdapter` + `use_undetected: True`; Firefox config → `False`.
5. Engine check: during a crawl, `ps` shows `/opt/google/chrome/chrome` (not `chrome-headless-shell`).
6. Consumer check: `web-search-mcp` crawl pipeline on 2 known URLs — word counts must match the
   documented baseline (whisper 916 w, deepeval 2208 w).