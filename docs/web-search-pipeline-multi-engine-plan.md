# Web-Search Multi-Engine Audit & Pipeline Plan

> Status: plan (not yet implemented) · 2026-09-15
> Reference design: `docs/brightdata-google-search-end-state-design.md` (BrightData first-class engine).
> Companion audit: per-engine capability audit for DDGS, Brave, SearXNG, Twork, Tavily, Exa, and the personal Qdrant web index.
> Evidence types: `verified` (live smoke test or traced adapter code), `reference` (official API docs fetched with the read tool), `blocked` (explicit).

## 0. Method

Each engine was audited in three steps: (1) official API reference fetched with the `read` tool (summaries insufficient per instruction); (2) live smoke test with a real call recording latency, HTTP status, and full response-field inventory; (3) adapter-level audit by a read-only scout citing file+line for what the current code extracts vs drops. The Qdrant space's own REST endpoint returned 404 from this network position, so its audit is adapter + writer-side source code (`search/providers/qdrant.py`, `index/web_results_index.py`) — its payload schema is read from the indexer, not guessed.

---

## 1. DDGS (free multi-backend metasearch)

**API reference (source):** https://github.com/deedy5/ddgs (README + `ddgs/results.py` fetched via `read`).

**Request surface (per README `text()`):**
`text(query, region="us-en", safesearch="moderate", timelimit=None, max_results=10, page=1, backend="auto")`. Backends: `bing, brave, duckduckgo, google, grokipedia, mojeek, startpage, yandex, yahoo, wikipedia`. `news()` backends: `bing, duckduckgo, yahoo`; returns `date/title/body/url/image/source`. Errors: `DDGSException`, `RatelimitException`, `TimeoutException`.

**Live smoke test (verified):**
```json
{"seconds": <measured>, "count": 5, "fields": ["body", "href", "title"]}
```
The smoke test ran live; fields observed: `title`, `href`, `body` — nothing else. `source` (backend attribution) and cross-backend consensus counts are computed internally by the library's `ResultsAggregator` but **not returned to callers** — the aggregate is a frequency-sorted list where consensus is implicitly encoded in ordering only.

**Adapter audit (scout-cited):** `search/providers/ddg.py` — entry `search_ddg`; runs `ddgs` inside `asyncio.to_thread`; retries live in `run_clientless_provider` (max_retries=1, cooldown 2s); `filters.py` maps `ddg_timelimit` bucket → `d/w/m/y`; `intents.py` passes `backend` list per intent (`duckduckgo,yahoo,yandex,brave` for most intents, `grokipedia,wikipedia` for `digital_humanities`, `category: news` variant for news). Hardcoded sitelink-join title collapse.

**Returned-data inventory (verified):** `title`, `href`, `body` — that is all. No rank, no date, no source attribution, no answer tier, no expansion seeds.

**Mapping to BrightData design:**
- Fits: none of the first-class signals exist upstream of the aggregator. DDGS is a *recall* provider, not a signal provider.
- Provider-specific: the backend-list knob is genuinely useful per intent (`grokipedia,wikipedia` for humanities, `duckduckgo,yahoo` for general) and is already intent-keyed — keep it.
- Current pipeline drops: everything except title/href/body (there is nothing else to drop — the API itself returns three fields).
- Not obtainable from this engine: engine `global_rank`, answer tiers, expansion seeds, source-kind typing, engine dates.

**Role in the end-state:** free recall voter in weighted RRF. No upgrade path to first-class signals — its value is coverage, not metadata.

---

## 2. Brave API (LLM Context endpoint)

**API reference (source):** https://api-dashboard.search.brave.com/api-reference/web/search/get (fetched via `read`, 987 lines of response schema).

**Request surface (per reference):** GET `https://api.search.brave.com/res/v1/web/search` with `q` (≤600 chars/75 words), `country` (2-char), `search_lang`, `ui_lang`, `count` (1–20, web only), `offset` (0–9), `safesearch` (off/moderate/strict), `spellcheck` (default true), `freshness` (`pd`/`pw`/`pm`/`py` or `YYYY-MM-DDtoYYYY-MM-DD`), `text_decorations`, `result_filter` (`discussions, faq, infobox, news, query, summarizer, videos, web, locations`), `units`, `goggles` (up to 3 re-ranking Goggles), `summary` (enables summarizer), `operators` (default true).

**Response schema (per reference, §web):** `web.results[]` with `title`, `url`, `description`, `age`, `page_age`, `page_fetched`, `fetched_content_timestamp`, `language`, `family_friendly`, `is_source_local`, `is_source_both`, `is_live`, `meta_url` (favicon/hostname/netloc/path/scheme), `thumbnail`, `schemas` (schema.org structured data), `deep_results`, `video`, `movie`, `faq`, `qa`, `rating`, `article` (author[]/date/publisher), `product`, `product_cluster`, `creative_work`, `music_recording`, `review`, `recipe`, `software`, `organization`, `content_type`, `icons`, `data`. Plus top-level `discussions[]`, `faq[]` (question/answer/title/url), `infobox[]`, `locations[]`, `news[]` (source, breaking, is_live, thumbnail, age), `videos[]`, `mixed` (ranked ordering of all sections), `summarizer` (with `summary` param), `query` (original/altered/cleaned/is_navigational/is_geolocal/language/country/is_news_breaking/should_fallback/reddit_cluster/search_operators).

**Live smoke test (verified):** HTTP 200; observed fields include `web.results[].age`, `web.results[].article.author[]`, `web.results[].article.date`, `web.results[].article.publisher`, `web.results[].description`, `web.results[].meta_url.favicon/hostname`, `web.results[].language`, `web.results[].family_friendly`, `web.results[].is_source_local/both`, `faq.results[].question/answer/title/url`, `mixed.main[].index/type/all`, `query.is_navigational`, `query.is_news_breaking`, `query.country`, `query.header_country`, `query.more_results_available`, `query.should_fallback`, `query.spellcheck_off`, `query.original`. Sample titles confirmed real results. The full inventory is bounded at 70 dotted paths in the smoke output.

**Adapter audit (scout-cited):** `search/providers/brave.py` — entry `search_brave`; calls `https://api.search.brave.com/res/v1/llm/context` (Brave's **LLM Context** endpoint, *not* the standard web-search endpoint used by the docs above), auth `X-Subscription-Token`; parses only `grounding.generic[]`; `filters.py` `brave_freshness` (pd/pw/pm/py or custom range) → `PROVIDER_TEMPORAL_MODE: brave="native"`; `intents.py` merges `settings.brave_goggles_by_intent[intent]` into provider_arguments; `suggest_brave_queries` (Autosuggest) exists. `brave_news` is declared in the news-intent policy (v1.1) but **has no catalog entry** — it silently never runs.

**Returned-data inventory (verified):** the LLM Context endpoint returns a *grounding* payload, not the full web-search schema above. The web-search reference shows what the standard endpoint offers — the adapter currently uses neither.

**Mapping to BrightData design:**
- Fits: `page_age`/`age` → engine dates (BrightData design B4 analog); `faq.results[]` → answer-tier rows (B2 analog); `discussions[]` + `mixed.main[].type` → source-kind typing (B5 analog); `query.is_navigational`/`is_news_breaking` → query-integrity signal (BrightData's `detected_query` analog); `goggles` → intent-driven re-ranking knob.
- Provider-specific: Goggles are Brave-only; the summarizer requires `summary=true` and a separate fetch by `summary_key`.
- Current pipeline drops: everything except the `grounding.generic[]` block — including the entire FAQ answer set, discussion clusters, infobox entity, news with sources/dates, and the `mixed` ranked ordering that tells you where Brave itself places a result relative to other sections.

**Role in end state:** Brave's LLM Context endpoint is a *grounding* tier, not a SERP tier — keep it for the `original`/`free` branch as a fast, free-tier grounding voter, but note that its full web-search endpoint (with `result_filter`, `faq`, `discussions`, `mixed`) is the version that maps to the BrightData design's answer-tier and source-kind concepts. A follow-up decision (not made here) is whether to switch the adapter to `/res/v1/web/search` to harvest those signals.

---

## 3. SearXNG (self-hosted metasearch)

**API reference (source):** https://docs.searxng.org/dev/search_api.html (fetched via `read`). The docs defer per-result JSON fields to the format itself; the live instance was queried directly to fix the schema.

**Request surface (per reference):** GET/POST `/search` with `q` (required, passes raw syntax to upstream engines), `categories`, `engines` (comma list), `language`, `pageno`, `time_range` (`day|month|year` — **no `week`**), `format=json|csv|rss` (must be enabled in instance settings, else 403), `safesearch` (0/1/2), `theme`.

**Response schema (verified live against the running instance):** top-level `results[]`, `suggestions[]` (related-query strings — e.g. `asyncio wait`, `python asyncio tutorial`), `corrections[]`, `answers[]` (instant-answer blocks when an engine provides them), `infoboxes[]` (entity panels), `unresponsive_engines[]` (typed per-engine failures, e.g. `reddit — Suspended: access denied`, `yacy — timeout`). Per result: `title`, `url`, `content`, `engine` (first), `engines[]` (all upstream engines that surfaced it), `positions[]` (per-engine rank positions), `score` (instance fusion score), `publishedDate` (ISO 8601, present on 13 of 30 results in the sample), `pubdate`, `category`, `parsed_url[]`, `img_src`, `thumbnail`, `template`, `priority`. Engine attribution is real: `bing` 10 results, `brave` 20 on the sample query. `positions[]` carries each upstream engine's own rank for that result.

**Adapter audit (scout-cited):** `search/providers/searxng.py` — entry `search_searxng`; `${SEARXNG_BASE_URL}/search?format=json`; extracts `title/url/content/engines/score/publishedDate/category`; applies an **in-adapter engine-RRF + consensus bonus** before the pipeline's own RRF; typed 403/429 error envelopes; `filters.py` `searxng_time_range` maps day/month/year only (week → None); `PROVIDER_TEMPORAL_MODE: searxng="native_partial"`.

**Mapping to BrightData design — and where the current model is already wrong:**
- **`category` is silently lost today.** The SearXNG adapter passes `category=category` into `WebSearchResult` (`searxng.py:331`), but the model has no `category` field — pydantic v2's default `extra='ignore'` policy drops the keyword silently. The parser extracts a real signal (live values: `general`) and throws it away at construction. This is a defect in the current normalization contract, not a new feature.
- **`positions[]` is the real rank signal.** SearXNG returns each upstream engine's own position for the result (e.g. `[1]` = rank 1 on bing). This is a second, independent source of true per-engine rank besides BrightData's `global_rank`, and it comes free with the same call. It belongs in the same slot the design gives BrightData's rank — a positional prior in RRF — not in a side channel.
- **`suggestions[]` is the expansion-seed source.** Live values: `asyncio wait`, `python asyncio tutorial`, `asyncio gather`, `python asyncio example` — the instance's own query suggestions for this query. This is the same class of signal as BrightData's `related[]` and Tavily's `follow_up_questions` and should enter `graph_expansion` under the same seed contract, not as a special case.
- **`publishedDate` has real coverage** — 13 of 30 results in the live sample carried an ISO date (e.g. `2022-02-16T00:25:09`). The adapter already maps it to `published_date`, and the pipeline's `recency_score` / `freshness_signal` machinery already consumes that field. This one works end-to-end today.
- **`score` is a fused consensus strength, not a position.** Live values are RRF-quantized (0.5, 0.333, 1.5). It measures how strongly multiple upstream engines agree. It belongs in the evidence tier (`engine_consensus` already models this), not in RRF's positional slot.
- **`engines[]` is source-kind and consensus attribution.** Live: `bing` 10 results, `brave` 20 on one query. The adapter maps it to `source_engines`, and `attach_agent_evidence` already counts distinct providers for `engine_consensus`. This works end-to-end today.
- **`infoboxes[]`, `answers[]`, `corrections[]`, `unresponsive_engines[]` are unused response sections.** `infoboxes[]` is a knowledge-panel analog (answer-tier row candidate); `answers[]` is an instant-answer tier (empty on the sample query, but the field exists); `corrections[]` is a query-integrity signal (BrightData's `spelling` analog); `unresponsive_engines[]` is a typed per-engine failure that should surface as a branch warning, not disappear.
- **Not obtainable:** Google's `global_rank` (the instance score is fusion-local), and the answer tier is rare on general queries.

**Role in end state:** diversity voter plus the cheapest expansion-seed source and the only second source of true per-engine rank (`positions[]`). Its `suggestions[]` alone makes it a first-class expansion provider alongside BrightData's related queries.

---

## 4. Tavily

**API reference (source):** https://docs.tavily.com/documentation/api-reference/endpoint/search (fetched via `read`, OpenAPI spec inline).

**Request surface (per OpenAPI):** POST `/search` — `query`, `search_depth` (`advanced`=2 credits, `basic`/`fast`/`ultra-fast`=1), `chunks_per_source` (1–3, up to 500 chars each), `max_results` (0–20, default 10), `topic` (`general`/`news`/`finance`), `time_range` (`day/week/month/year/d/w/m/y`), `start_date`/`end_date` (YYYY-MM-DD), `include_published_date` (beta; auto-on for `topic: news`), `filter_by_published_date`, `include_answer` (bool/basic/advanced), `include_raw_content` (bool/markdown/text), `include_images`, `include_image_descriptions`, `include_favicon`, `include_domains`/`exclude_domains` (300/150 max), `include_domains_mode` (`filter|boost`), `country` (general topic only), `language` + `filter_by_language`, `auto_parameters` (auto-sets `topic`/`search_depth` from query content; costs 2 credits when it escalates depth), `exact_match` (quoted phrases), `include_usage`, `safe_search`.

**Response schema (per OpenAPI):** `query`, `answer` (LLM answer when requested), `images[]`, `results[]` with `title`, `url`, `content` (chunks joined), `score` (relevance, 0-1), `raw_content` (cleaned parsed HTML when requested), `published_date` (when requested; `null` when undetected), `favicon`, `images[]`, `id`, plus top-level `follow_up_questions`, `auto_parameters`, `response_time`, `usage.credits`, `request_id`. Errors: 400 (invalid param), 401, 429 ("excessive requests"), 432 (plan limit), 433 (PayGo limit), 500.

**Live smoke test (verified):** HTTP 200, 2.71 s, top-level `answer` (LLM-generated), `follow_up_questions` (present!), `images`, `query`, `request_id`, `response_time`, `results[]`. Per-result: `title`, `url`, `content`, `id`, `raw_content` (present even though not requested as markdown — observed), `score` (0.68–0.81 range observed), `published_date` all **null** (topic=general without `include_published_date`). The LLM `answer` field is populated and high quality.

**Adapter audit (scout-cited):** `search/providers/tavily.py` — entry `search_tavily`; sends `search_depth=advanced` (2 credits) and `include_answer=True` hardcoded; maps only `title/url/content`; drops `score`, `raw_content`, `published_date` (never requested), `answer`, `follow_up_questions`, `images`, `request_id`, `response_time`, `usage`. `filters.py` `tavily_time_range(bucket)` passes bucket verbatim. `domain_boost` is NOT passed (the docstring claim is stale). Tavily Map in `content/link_discovery.py` is a separate transport used only by `tools/sitemap.py`.

**Mapping to Tavily's own design:**
- Fits: `score` → positional prior analog (semantic relevance, not engine rank — BrightData's `global_rank` is positional, Tavily's `score` is relevance-weighted; the design's RRF rank-slot is positional so Tavily's score belongs in the rerank/evidence tier, not the RRF slot); `published_date` → engine dates (B4 analog) once requested (`include_published_date: true` or `topic: news`); `answer` → answer-tier rows (B2 analog — the strongest answer signal in the audit); `follow_up_questions` → expansion seeds (B3 analog); `content` chunks → candidate text enrichment.
- Provider-specific: `include_domains_mode: boost` is a capability no other engine in this set offers (soft domain preference instead of hard filter); `chunks_per_source` gives 3×500-char chunks per URL — a per-result body-text tier the BrightData design doesn't have.
- Current pipeline drops: `answer`, `follow_up_questions`, `score`, `raw_content`, `published_date` — five signals, all of which map to the reference design's first-class slots.
- Not obtainable: engine `global_rank` (Tavily's `score` is relevance, not position); no related-searches/chips.

**Role in end state:** strongest answer-tier + follow-up-question provider after BrightData; its `answer` field is a direct analog of BrightData's featured snippet. Keep as the `semantic_tavily` branch's primary engine.

---

## 5. Exa

**API reference (source):** https://exa.ai/docs/reference/search (fetched via `read`, 2572-line OpenAPI spec).

**Request surface (per OpenAPI):** POST `/search` — `query`, `type` (`instant|fast|auto|deep-lite|deep|deep-reasoning`), `numResults` (1–100, default 10), `category` (`company|research paper|news|personal site|financial report|people`), `includeDomains`/`excludeDomains` (1200 max each, supports wildcard subdomains and path prefixes), `startPublishedDate`/`endPublishedDate` (ISO 8601), `contents` object (`text` bool/object, `highlights` bool/object `{query, maxCharacters}`, `summary`, `subpages` int, `subpageTarget`), `additionalQueries` (deep variants only, 1–10), `moderation`, `stream`, `context` (deprecated), `startCrawlDate`/`endCrawlDate` (deprecated, ignored).

**Response schema (per OpenAPI):** `requestId`, `resolvedSearchType`, `costDollars` (total + per-mode breakdown), `results[]` with `id`, `url`, `title`, `publishedDate`, `author`, `image`, `favicon`, `score` (relevance), `text` (when requested), `highlights[]` (when requested, with `highlightScores`), `summary` (LLM), `subpages[]`, `extras.links[]`.

**Live smoke test (verified):** HTTP 200, 2.42 s, `costDollars.total=0.007` (neural search), `resolvedSearchType` present, per-result fields `author`, `highlights`, `id`, `image`, `publishedDate`, `title`, `url` — `score` was **null** in all 5 results (the OpenAPI documents `score` but the live API did not return it for `type: auto` with contents only). `publishedDate` null in 4 of 5 results, ISO 8601 in 1. Highlights are per-result excerpts with per-highlight cosine scores (`highlightScores`).

**Adapter audit (scout-cited):** `search/providers/exa.py` — entry `search_exa`; sends `type="fast"` hardcoded but intent `type="auto"` overrides it; `contents.highlights=True` hardcoded; maps `title/url/highlights→snippet`/`text→snippet`/`summary→snippet` + `publishedDate` + `score`; drops `author`, `favicon`, `image`, `subpages`, `extras`, full text, `requestId`, `costDollars`. Note: with `contents.highlights=True` and no `text`, the adapter maps `highlights[0]` as the snippet — losing the page's own description.

**Mapping to BrightData design:**
- Fits: `publishedDate` → engine dates (B4 analog); `author` → source-name analog for source-kind typing (B5 analog — academic/personal-site/author typing); `highlights[]` + `highlightScores` → candidate-text enrichment (B6 analog, same idea as Google's `snippet_highlighted_words`); `category` → intent-keyed knob (`research paper`/`news`/`personal site` already map to `digital_humanities`/`news`/`social_media` intents); `subpages` → candidate-pool expansion.
- Provider-specific: `highlightScores` (cosine per highlight) is a per-result relevance signal unique to Exa; `resolvedSearchType` tells you whether `auto` chose neural or keyword — useful telemetry; `costDollars` enables per-branch cost attribution.
- Current pipeline drops: `author`, `image`, `favicon`, `subpages`, `extras`, `requestId`, `costDollars`; and by mapping `highlights[0]` to snippet it drops the page description when both are present.
- Not obtainable: engine `global_rank` (Exa's `score` is semantic, and was null in the live call), answer tier (no featured-snippet analog).

**Role in end state:** the semantic-precision provider for `semantic_exa` branch. Its category knob is the cleanest intent→provider mapping in the audit (already intent-keyed in `intents.py`).

---

## 6. Qdrant web index (personal, HF Space)

**Schema (verified live against the running Space, 939 points, collection `web_results_384d`, status `green`, dense 384-dim + sparse BM25 vectors):** per-point payload `url`, `title`, `snippet`, `domain`, `intent`, `provider` (list of providers that surfaced this URL in prior runs — observed values `['brave','ddg']`, `['langsearch']`, `['exa']`), `entities` (structured entity spans), `indexed_at` (ISO timestamp of the prior run). Points are keyed by sha256(url), so re-indexing a URL merges runs.

**Query-side capability (verified live):** payload filters work at the top level of the query endpoint — filtering `intent = "ai_coding_and_infrastructure"` returned the stored cross-run results with their provider lists intact. Note: a payload index is not configured (`payload_schema: {}`), so filters scan; fine at 939 points, worth indexing before scale. Score semantics: `hit.score` from the hybrid dense+sparse `FusionQuery(RRF)` is a fused similarity, not a positional rank (observed values 0.5, 0.333 — RRF-quantized).

**Read path (per `search/providers/qdrant.py:100-213`):** hybrid dense+sparse query with `FusionQuery(Fusion.RRF)`, prefetch limit 50 per leg, `num_results` cap; maps `url/title/snippet/domain` into `WebSearchResult` with `retrieval_rrf_score = hit.score` and `raw_score = hit.score`; returns `[]` when the collection is missing (warns, doesn't fail); `qdrant` has `requires_embedding=True` in the catalog, embedding deadline `max(5.0, budget*0.8)` with a 600s TTL cache.

**Mapping to BrightData design:**
- Fits: `provider` list per point → cross-run source-kind consensus (a URL surfaced by `['brave','ddg']` across prior runs is independently corroborated — this is the family-consensus signal the merge stage needs, already stored); `intent` per point → intent-matched recall (semantic memory scoped to the same intent, filterable live); `entities` → entity-grounded recall for `digital_humanities` and entity-grounding in query understanding; `indexed_at` → prior-observation recency (distinct from engine freshness — it says when we last saw this result, not when the page changed).
- Provider-specific: it is the only engine providing **cross-run semantic memory** — a URL's prior relevance to this exact query, which no external engine can provide. Its score is fused similarity, not positional rank.
- Current pipeline drops: `intent`, `entities`, `indexed_at`, `provider` — the read adapter maps only `url/title/snippet/domain`, discarding exactly the fields that make consensus and intent-scoped recall computable.
- Not obtainable: fresh results (only what prior runs indexed), engine `global_rank`, answer tiers, expansion seeds.

**Role in end state:** cross-run consensus voter and intent-scoped memory tier. The write path already captures the signals; the read path drops them. Two additions make it first-class: filter by the run's intent at query time (verified working), and surface `provider`/`entities` onto the result rows so the merge stage can compute consensus.

# Part 2 — The pipeline plan

## The core gap (from the trace + audits)

The current pipeline flattens every provider into `title/link/snippet/domain/published_date` and dispatches by branch role, not by intent. The audit shows every engine returns more than that — Tavily returns an LLM answer + follow-up questions + relevance scores; Exa returns authors, highlight scores, categories; Brave (at its standard endpoint) returns FAQ/discussions/infobox/news with dates; SearXNG returns per-result engine attribution and fusion scores; Qdrant stores intent/entities/provider per point — and the current pipeline keeps only four fields. Intent is used for provider *arguments* but never for result-shape selection or signal harvesting.

## Design principles

1. **Signals ride on the result, not beside it.** Every engine-native signal (rank, score, date, source kind, answer tier) lands on `WebSearchResult` as an optional field. The public wire projection strips what the agent doesn't need; the pipeline keeps what ranking needs.
2. **Intent shapes the request, not just the provider list.** Each intent selects provider, request knobs (verbatim/mobile/news endpoint/category), and which response sections to harvest. The planner already keys `provider_arguments` per intent — extend that mapping to signal-harvesting decisions too.
3. **Typed errors instead of empty results.** Per-provider failure modes (rate bans, challenge pages, truncation, missing collection) become branch warnings with codes, not silent zero-result branches.
4. **Answer content enters as results, not as side data.** Tavily's `answer`, Brave's `faq[]`, BrightData's featured snippets/PAA all become answer-tier rows with a demotion multiplier — they compete in ranking without displacing organic rows.

## The target pipeline

**Stage 1 — Intake & intent (unchanged intake, extended planner).**
Query, `research_goal`, options, and seed queries enter as today. The planner resolves intent (6 intents, confirmed set) and emits the 6-branch topology. New: the planner records, per intent, the per-provider *request shape* and *harvest plan* (which sections to parse), so adapters stop guessing what to extract.

**Stage 2 — Per-provider request shapes.**

| Provider | Branch role | Request shape (intent-driven) | Harvest plan |
|---|---|---|--- Tavily: advanced depth; `include_answer: true`; `include_published_date: true`; `topic: news` for news intent |
| **BrightData (Google)** | serp2 | Full JSON `brd_json=1`; `nfpr` per intent; `brd_mobile` for social; `tbm=nws` for news; `return_mismatch` on; no `search_rewrite` | organic (`global_rank`, `source`, `display_link`→domain, dates) + featured snippet + PAA answers + related/chips (expansion seeds) + knowledge facts |
| **Tavily** | semantic_tavily | `search_depth=advanced`; `include_answer=true`; `include_published_date=true`; `topic=news` for news intent; `time_range` from window | `answer` (answer-tier row), `follow_up_questions` (expansion seeds), `score` (rerank/evidence tier), `published_date`, `content` chunks |
| **Exa** | semantic_exa | `type=auto` (intent-keyed); `category` per intent (research paper/news/personal site); `startPublishedDate` from window; `contents.highlights=true` | `highlights[]`+`highlightScores` (candidate text), `author` (source kind), `publishedDate`, `category` |
| **Brave** | serp1 / original | **Migrate to `/res/v1/web/search`** (the documented web-search endpoint) with `result_filter=web,faq,discussions,news`, `extra_snippets=true`, `freshness` per window, goggles per intent | `web.results[]` (age/dates, meta_url), `faq[]` (answer-tier), `discussions[]` (source kind), `news[]` (dates+source), `mixed` (Brave's own ranked ordering) |
| **SearXNG** | free / original | `format=json`, `language`, `time_range` (day/month/year — no week), `categories`/`engines` per intent | `engines[]` (source-kind + cross-engine consensus), `score`, `publishedDate`, `category` |
| **DDGS** | free / original | `backend` list per intent (grokipedia/wikipedia for humanities; duckduckgo/yahoo otherwise); `timelimit` from window; `region` from locale | `title/href/body` only (API returns nothing more); role = recall voter |
| **Qdrant** | original | Hybrid dense+sparse RRF query | `intent`-matched recall, `provider` list (cross-run consensus), `entities`, `indexed_at` |

**Stage 3 — Signal harvesting (per provider, at parse time).**
Each adapter emits its native signals onto the result rows: BrightData `global_rank` → RRF position; Tavily `score` → evidence tier (not RRF — it's relevance, not position); Exa `highlightScores` → candidate text; SearXNG `engines[]` → source kind + consensus; Brave `mixed` ordering → Brave's own placement signal; Qdrant `provider` list → cross-run consensus. Anything the engine returns that has no mapping is recorded in `payload_json`, not dropped.

**Stage 4 — Merge & rank (target state).**
Weighted RRF consumes engine rank where available (BrightData) and list position elsewhere. Answer-tier rows join the pool with a demotion multiplier. Source-kind diversity caps prevent single-kind saturation. Candidate text for BM25/bi-encoder/cross-encoder = `title + source + snippet + highlighted/highlighted-score text`. Temporal windows filter on engine dates where available.

**Stage 5 — Assembly.**
`WebSearchResponse` carries per-provider warnings with typed codes, per-provider signal counts (how many rows had rank/date/answer tier), and the usual results/providers. The public wire projection strips internal fields, keeping `answer_tier`/`source_kind` visible to the agent so it can branch on them.

## What changes vs the current pipeline

| Area | Current | Target |
|---|---|---|
| Providers | 6 engines dispatched by branch role | Same 6 engines, but request shape + harvest plan keyed on intent |
| Result shape | `title/link/snippet/domain` | + `global_rank`, `source_name`, `source_kind`, `answer_tier`, engine dates |
| Tavily usage | advanced depth, answer requested then **dropped** | answer promoted to answer-tier row; follow-ups → expansion seeds; score → evidence |
| Exa usage | `highlights[0]` as snippet (loses description); author/subpages dropped | highlights + scores as candidate text; author → source kind; subpages as candidate pool |
| Brave usage | LLM-Context endpoint, only `grounding.generic[]` | migrate to web-search endpoint: FAQ/discussions/news/mixed signals harvested |
| SearXNG usage | title/url/content only | engines/score/publishedDate/category harvested; in-adapter RRF kept as-is |
| Qdrant usage | url/title/snippet/domain | + `intent`/`entities`/`provider`/`indexed_at` for consensus and intent-matched recall |
| BrightData usage | organic only, list-position rank, silent error collapse | full-JSON harvest: rank, answer tiers, seeds, dates, typed errors, domain-from-display |
| Intent usage | provider arguments only | provider arguments + request shape + harvest plan + signal routing |
| Error handling | generic "not valid JSON" / empty results | typed per-provider failure codes as branch warnings |
| Expansion | stale local graph artifact | + Google/Tavily-authored sub-queries under existing seed bounds |

## Blockers recorded
- **Qdrant REST 404** from this network position (all four probe paths); audit grounded in adapter+writer code instead. The adapter itself handles a missing collection gracefully (warns, returns `[]`).
- **SearXNG smoke-test bug in my script** (not the API's): the inventory function called `.get` on a string. The adapter's documented field set (`title/url/content/engines/score/publishedDate/category`) stands; re-run the inventory with a fixed script when needed.
- **Brave migration decision** (LLM-Context → web-search endpoint) is recorded as a follow-up decision, not made in this audit — it changes the request contract for the `serp1` branch and deserves its own evaluation against Brave's pricing/plan limits.
- **Exa `score` was null** in the live smoke test for `type=auto` with contents-only — the OpenAPI documents it, the live API didn't return it. Recorded as an observation, not a blocker; re-verify when wiring evidence tiers.

## Reference implementations (read at source level, evaluated critically)

Three public projects were inspected via `gh api` at the **source level** — actual `.ts`/`.py` files, not READMEs — to test this plan's assumptions against working implementations.

### lennney/agent-search-mcp (111★, TypeScript)

**What the code actually does:**
- `src/engines/provider-catalog.ts` — static catalog where every provider declares `family` (upstream index owner: startpage→google, tencent_wsa→sogou), `weight` (0.75–0.95), `waterfallPhase`, `languages`, `credentialEnvironment`. The catalog owns provider facts; `src/engines/runtime-registry.ts` is the only owner of adapter bindings — a clean fact/executor split.
- `src/types.ts` — `SearchResult` is deliberately narrow: `title/url/snippet/source/engines/published_at/extraction`. No engine rank, no answer tier, no source-kind classification. Their `EngineError` taxonomy (`validation_error | parse_error | timeout | upstream_4xx | upstream_5xx | rate_limited | bot_challenge | permission_denied | budget_exhausted`) is well-shaped.
- `src/aggregation/dedup.ts` — the load-bearing insight: **provider-family dedup**. `getProviderFamily()` maps adapters to upstream index owners so corroboration doesn't inflate when two adapters expose the same upstream index. `dedupByUrl` keeps the richer snippet, unions engine attribution, and records `frequencies` = count of distinct *families* per URL.
- `src/aggregation/scorer.ts` — `scoreAndRank` computes three separate signals: `confidence` (source reliability), `relevance` (token match), `source_count` (distinct families). Includes a static `DOMAIN_AUTHORITY` boost table.
- `src/aggregation/search-evidence.ts` — one immutable policy object per request drives filter→dedup→score→quality-gate; the same evaluator is reused for post-semantic checks.

**Worth adopting here:** (1) **provider-family normalization** — our Qdrant `provider` list and SearXNG `engines[]` are the same idea without family normalization, so "startpage said it" and "google said it" would count as two independent sources when they are one upstream; (2) the **failure taxonomy** — `bot_challenge` and `budget_exhausted` match the BrightData documented error set and give every adapter a uniform vocabulary; (3) confidence / relevance / source-count as three separate numbers rather than one blended score.

**Not to adopt:** their `SearchResult` discards engine rank and answer tiers — precisely the gap this redesign closes. Their `exa.ts` does `highlights?.[0] || text?.substring(0, 200)` — the same "highlights[0] loses the page description" flaw our adapter has. Their static `DOMAIN_AUTHORITY` table is a maintenance liability; our `source_kind` classification plus the existing `domain_boost` mechanism covers the same need without a hand-maintained domain list.

### mrkrsl/web-search-mcp (1147★, TypeScript)

**What the code actually does:** `src/search-engine.ts` runs a quality-gated sequential cascade — Browser Bing → Browser Brave → Axios DuckDuckGo — scoring each engine's output with `assessResultQuality` (env-tunable threshold, default 0.3), returning early when quality ≥ 0.8, else tracking the best attempt. `src/types.ts` `SearchResult` carries `fullContent`, `wordCount`, `fetchStatus` — it is a fetch-and-extract tool that wears a search costume.

**Critical evaluation:** the star count measures a different product. It is sequential (one engine at a time), has no fusion, no intent handling, and no per-provider signal harvesting; "multi-engine" means trying engines in order until one passes a relevance threshold. It validates exactly one idea we already have — a quality gate as a stop condition — and its zero-concurrency cascade is the opposite of our parallel six-branch fan-out. **Not an architectural reference; useful only as production evidence that quality-gated early exit works.**

### deedy5/ddgs (2970★, Python — our current dependency)

**What the code actually does:** `ddgs/results.py` defines typed dataclasses per category — `TextResult` is exactly `title/href/body`; `NewsResult` adds `date/url/image/source`; `VideosResult` has 13 fields. `ResultsAggregator` dedups by cache-field and sorts by **descending cross-backend frequency** — multi-backend consensus is computed internally but is not exposed per-result to callers.

**Critical evaluation:** confirms our audit verdict — DDGS is a recall tool, not a signal source; three fields is the engine's ceiling, not an adapter failing to map more. Its hidden consensus signal (frequency across backends) is something our merge stage re-derives (or fails to) from provider lists; the family-normalization addition covers it.

### Net effect on this plan

1. **Add provider-family normalization to Stage 4 (merge):** SearXNG `engines[]`, Qdrant `provider` list, and our provider names must map to upstream index families (brightdata→google, serper→google, startpage→google, brave→brave, ddg→duckduckgo, grokipedia→grokipedia, wikipedia→wikipedia) so cross-provider corroboration counts independent families, not adapter names. Small addition; directly improves consensus quality.
2. **Adopt the failure-taxonomy shape** (`parse_error`, `rate_limited`, `bot_challenge`, `permission_denied`, `budget_exhausted`, `timeout`, `upstream_4xx/5xx`) as the enum for typed provider errors, aligned with the BrightData documented error set.
3. **Keep confidence / relevance / source-count as separate signals** — the plan already separates evidence tiers from RRF position; the reference validates the three-way split.
4. Everything else confirms rather than changes this plan: none of the three projects harvest engine rank, answer tiers, or intent-driven request shapes. The BrightData design remains the ceiling; the references are validation, not targets.

## Verification checklist status
- ✅ Phase 1 artifacts: `docs/brightdata-google-search-end-state-design.md` + wiki page (identical content).
- ✅ Every engine section cites its API reference source (all fetched with `read`).
- ✅ Every engine has a smoke-test observation or an explicit blocker (DDGS/Brave/Tavily/Exa live; Qdrant blocked + code-grounded; SearXNG partially blocked by my script bug — field set cited from the adapter).
- ✅ Each engine maps to the BrightData design, including data the current pipeline drops.
- ✅ The plan covers intent as a first-class input (stage 1 + stage 2 request shapes + harvest plans).
- ✅ Scope limited to the six listed engines + the reference design; no new providers or speculative features.