# Changelog

All notable changes to **anygen-search-cli** (`hsearch`) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [SemVer](https://semver.org/).

## [1.0.0] — 2026-08-14

Closes the deferral backlog from the 2026-08-14 drift review. **Every item was
live-probed before any code was written** — which is how half of them got
cancelled instead of built (see "Probed and rejected" below). 303 tests.

### Added
- **`--mode rag` / `--context`** — Exa `contents.context`. Returns ONE
  pre-assembled, LLM-ready context string spanning all results, surfaced as
  `meta.context` (CLI JSON) and `SearchResponse.context` (SDK). The single best
  call for grounding an answer: no stitching snippets together yourself.
  - **`--context-max-chars` (default 12000).** Always bounded on purpose:
    unbounded returned **168,235 chars** live (275,969 with `text=True`), which
    would blow any context window and quietly burn tokens.
  - Distinct from the existing `--mode context` (Brave's LLM Context endpoint,
    per-result grounding snippets). `rag` = one blob; `context` = many snippets.
- **`hsearch research --stream`** — Tavily research over SSE. Prints the report
  as it is written instead of waiting 15-25s for the buffered version. Wire
  format is OpenAI-compatible (`chat.completion.chunk`, deltas at
  `choices[].delta.content`). SDK: `research_streaming()` async generator.
- **`hsearch usage`** — remaining quota/credits per provider. Live: Tavily
  plan + per-capability counts (search/crawl/extract/map/research), Firecrawl
  remaining credits + billing period. Run it before an expensive sweep. SDK:
  `account_usage()` / `account_usage_sync()`; MCP tool `usage`.
  Failures are isolated per provider — one dead endpoint can't break the report.
- MCP server now exposes 12 tools (added `usage`); `hsearch schema` lists 12.

### Probed and rejected (documented so nobody re-researches them)
Vendor docs and sitemaps advertise these; the live API disagrees. Verified
2026-08-14 with real keys:
- **Firecrawl `/v2/ask`** → HTTP 404 (all three body shapes tried).
- **Firecrawl `/v2/monitors`** (GET + POST) → HTTP 404. The whole monitors
  family is in the docs sitemap but not live on v2 for this account tier.
- **Tavily keyless search** → HTTP 401 `missing or invalid API key`, despite a
  documented "Try Tavily Without an API Key" page.
- **Exa top-level `context: true`** → silently ignored, no `context` key in the
  response. Only `contents.context` works. This is the param the 2026-06-25
  review saw marked deprecated and used to drop the feature entirely — two
  different params share the name, one retired, one current.

### Changed
- `SearchResponse` gains a `context` field, mirrored into `meta.context` for
  CLI/JSON parity (same fix pattern as the v0.6.0 `meta.answer` drop).
- Cache TTL for the new `rag` mode: 3600s.

### Tests
- 303 passing (279 → 303; 24 new in `tests/test_v100_context_stream_usage.py`).
- Streaming tests cover malformed frames, empty deltas, HTTP errors and bad
  model names — the SSE parser must never crash a long research run.

## [0.9.0] — 2026-08-14

Provider drift wave (2026-08) + site traversal. Three vendor changes broke or
degraded existing behavior silently; all were caught by live-probing the APIs
rather than reading changelogs, and all now have regression tests.

### Fixed
- **`--mode recall` no longer 400s Firecrawl.** Firecrawl removed `highlights`
  from the `/v2/search` `scrapeOptions.formats` enum again — live-verified
  2026-08-14 that BOTH `["highlights"]` and `[{"type":"highlights"}]` return
  HTTP 400 `invalid_union`. Because `--mode recall` enables `highlights` for
  Exa, the kwarg leaked into Firecrawl and killed the entire Firecrawl leg on
  every recall run. The provider now drops it. (v0.4.0 fixed this once; v0.5.0
  re-enabled it after the vendor docs implied v2 support; the vendor has since
  walked that back. Third time — now covered by tests.)
- **`--mode recall` no longer silently loses Exa to a timeout.** Exa's
  `deep-reasoning` tier (which recall selects) measures ~12s standalone and
  reliably exceeded the 15s global default once six providers competed for
  connections. Slow Exa tiers now get a 45s floor, overridable upward via
  `HSEARCH_TIMEOUT`. Net effect: recall went from 4/6 to **6/6 providers
  contributing, zero errors**.
- **`--mode academic` stopped requesting a retired Exa category.** Exa's
  July-2026 release replaced `research paper` with `publication` (a 350M-entry
  index with structured author/venue/citation metadata) and dropped it from the
  documented enum. Unknown strings are still accepted as loose "category hints",
  so this failed silently as a quality regression rather than an error.

### Added
- **`hsearch map URL`** — Tavily `/map`. Returns a site's complete URL
  inventory without extracting content (live: 20 URLs in 1.5s). The right first
  move for exhaustive page enumeration — marketplace catalogs, docs trees,
  connector listings — where client-side React pagination hides most entries
  and `sitemap.xml` is missing or stale.
- **`hsearch crawl URL`** — Tavily `/crawl`. Traverses a site AND extracts each
  page. `--instructions` is genuine agentic steering that prunes the traversal
  frontier mid-crawl, not a post-filter: live-verified that
  `--instructions "API reference endpoint pages only"` returned only
  `api-reference/endpoint/*` pages.
- Traversal controls shared by both: `--max-depth`, `--max-breadth`, `--limit`,
  `--select-path`, `--exclude-path`, `--allow-external`, `--category`; crawl
  also takes `--extract-depth basic|advanced` and `--content-format`.
  Output formats: `table | json | urls | markdown`.
- SDK: `map_site`, `map_site_sync`, `crawl_site`, `crawl_site_sync`,
  `TraversalResponse` (normalizes /map's `list[str]` and /crawl's
  `list[{url, raw_content}]` into one `pages` shape, plus a `.urls` helper).
- MCP: `map` and `crawl` tools registered (11 tools total).
- Exa `people` category is now documented and usable — live-verified to return
  LinkedIn profiles directly.

### Changed
- Exa category names are normalized in the provider: `research paper` /
  `papers` → `publication`, `linkedin profile` → `people`. Existing scripts and
  muscle memory keep working at full quality. `pdf` / `github` / `tweet` are
  deprecated upstream with no successor and pass through untouched as hints.
- `--category` help text now lists the current enum.
- Version assertions in tests no longer hardcode a literal version string
  (every release used to break them); they assert a floor, and a new test
  checks `pyproject.toml` and `hsearch.__version__` agree.

### Tests
- 279 passing (245 → 279; 34 new in `tests/test_v090_drift.py`).
- Two v0.3.1 Firecrawl `highlights` tests reversed to assert the corrected
  behavior, with the reversal reason documented inline.

## [0.8.0] — 2026-06-25

Provider drift wave (2026-06): Exa Agent API (June 2026 launch), Tavily Extract
endpoint, Firecrawl scrape-control flags.

### Added
- **`hsearch agent "instruction" [--effort minimal|low|medium|high|xhigh|auto]`**
  — new subcommand backed by the Exa Agent API (`POST /agent/runs`, async
  high-compute deep-research / list-building / enrichment agent). Creates a run,
  polls `GET /agent/runs/{id}` until terminal, renders the text or schema-validated
  structured output + cost. Flags: `--schema-file` (JSON Schema → structured
  output), `--previous-run-id` (continue a completed run), `--timeout`,
  `--poll-interval`, `--format json|markdown`. Live-verified: minimal effort
  answered a narrow question in ~9s, cost $0.012.
- **`hsearch extract --provider tavily`** — Tavily Extract endpoint
  (`POST /extract`) as a third extraction provider alongside jina/firecrawl.
  Adds `--query` (rerank extracted chunks by relevance to an intent),
  `--extract-depth basic|advanced` (advanced retrieves tables/embedded content),
  and `--extract-format markdown|text`. Live-verified: query reranking surfaces
  the most relevant passage first.
- **Firecrawl scrape-control flags** — `--firecrawl-store-in-cache` (cache pages
  for reuse), `--firecrawl-lockdown` (hardened scrape mode),
  `--firecrawl-zdr` (zero data retention), `--firecrawl-skip-tls` (ignore TLS
  cert errors). All map onto `scrapeOptions`.
- **SDK**: `agent()` / `agent_sync()` / `AgentResponse` exported from `hsearch`.
- **MCP server**: new `agent` tool; `extract` tool gains tavily `query` /
  `extract_depth` / `extract_format` params.
- **Schema**: `hsearch schema` documents the `agent` subcommand; `extract` tool
  provider enum gains `tavily`.

### Notes
- **Exa search `context` request param NOT wired** — the current Exa docs mark it
  deprecated ("Use highlights or text instead"), so it was deliberately skipped
  (same discipline as the v0.5.0 `startCrawlDate` removal). Use `--highlights` /
  `--text` instead.
- Exa Agent run statuses: `queued → running → completed|failed|cancelled`.
- `test_version_bumped` in older test files de-hardcoded to a well-formed-version
  check so future bumps don't break it; the exact-version assert lives in the
  latest release's test file.

## [0.7.0] — 2026-06-12

Provider drift wave (2026-05/06): Tavily Research API, Exa Company Search,
Firecrawl v2.5 scrapeOptions.

### Added
- **`hsearch research "question" --model mini|pro|auto`** — new subcommand backed
  by Tavily Research API (async deep-research agent). Creates a research request,
  polls until completion, renders a cited report + Sources table. Flags:
  `--timeout`, `--poll-interval`, `--format json|markdown|table`.
  Live-verified: mini model answers in ~10-60s with numbered citations.
- **`--mode company`** — Exa Company Search vertical (`type=auto` +
  `category=company`, Jan 2026 revamp). Returns company entities (homepage URLs),
  cache TTL 4h.
- **`--category <name>`** — generic Exa category filter exposed on `search`
  (pdf / github / company / research paper / financial report / news / tweet /
  personal site / linkedin profile).
- **`--firecrawl-parsers pdf`** — Firecrawl v2.5 `scrapeOptions.parsers` for PDF
  parsing control.
- **`--firecrawl-redact-pii`** — Firecrawl `scrapeOptions.redactPII` (beta).
- **SDK**: `research()` / `research_sync()` / `ResearchResponse` exported from
  `hsearch`.
- **MCP server**: new `research` tool; `search` tool gains `category` param.
- **Schema**: `hsearch schema` documents the `research` subcommand + new params.

### Changed
- Version bumped to `0.7.0` (pyproject + `__init__` + User-Agent).
- Tests: 221 passing (23 new covering research polling/timeout/enum validation,
  company mode routing, category plumbing, Firecrawl parsers/redactPII, MCP +
  schema sync).

## [0.6.0] — 2026-05-21 *(retroactive entry)*

RRF ranking, SERP feature extraction (Serper answerBox/KG/PAA, Brave infobox/FAQ),
provider fallback chains, aggregated answers, broadened mode routing,
`--fanout-timeout`; later commits added `hsearch answer` (Exa /answer),
`hsearch ground` (Jina g.jina.ai), `hsearch similar` (Exa /findSimilar),
`--mode context` (Brave LLM Context), ground timeout fix (`HSEARCH_GROUND_TIMEOUT`).

## [0.5.0] — 2026-05-21

API alignment release: fixes deprecated Exa parameters, adds new Firecrawl/Tavily
features from latest docs, and syncs MCP server with all CLI options.

### Fixed
- **Exa `startCrawlDate`/`endCrawlDate` removed** — these params were deprecated
  and silently ignored since 2026-04-15, fully removed on 2026-05-01. hsearch no
  longer sends them, avoiding false sense of date filtering.
- **User-Agent now dynamic** — `hsearch/X.Y.Z` matches `__version__` instead of
  hardcoded `hsearch/0.3`.
- **Firecrawl `highlights` format is now valid** — the v2 API supports it with an
  optional `query` sub-parameter. Removed incorrect drop logic.

### Added
- **Firecrawl new scrapeOptions**:
  - `--firecrawl-clean-content` — LLM-based boilerplate cleanup (beta `onlyCleanContent`).
  - `--firecrawl-max-age` / `--firecrawl-min-age` — cache freshness control in ms.
  - `--firecrawl-block-ads` / `--firecrawl-no-block-ads` — ad/popup blocking toggle.
  - `--firecrawl-proxy` — proxy tier selection: `basic | enhanced | auto`.
  - `--firecrawl-question` — ask a question about each scraped page.
  - `--highlights-query` — relevance query for Firecrawl/Exa highlights.
  - `enterprise` param support for Zero Data Retention.
- **Tavily new params**:
  - `--safe-search` — filter adult/unsafe content (Enterprise).
  - `--project-id` — `X-Project-ID` header for per-project usage tracking
    (also reads `TAVILY_PROJECT` env var).
- **Exa new params**:
  - `output_schema` support for structured extraction via JSON Schema.
- **MCP server** fully synced with CLI — all Firecrawl, Jina, and Tavily params
  now available via MCP tools.

### Changed
- Version bumped to `0.5.0`.

### Tests
- **123 unit tests passing** (13 new for v0.5.0 features).
- Coverage: Exa deprecation removal, Firecrawl new formats/scrapeOptions,
  Tavily safe_search/project_id/env, User-Agent version match.

## [0.4.0] — 2026-05-20

Optimization release: provider drift follow-up, native MCP server mode, and
adaptive cache TTLs by search mode.

### Added
- **MCP server mode** — `hsearch mcp` starts a stdio MCP server with `search`,
  `extract`, `providers`, and `schema` tools. The optional extra is declared as
  `mcp = ["mcp[cli]>=1.0"]`.
- **MCP docs** — `docs/MCP.md` includes Claude Desktop and Codex config examples.
- **Adaptive cache TTLs** — mode defaults now use 5 minutes for
  `news`/`realtime`, 15 minutes for `finance`/`answer`, 1 hour for
  `general`/`fast`/`recall`, 4 hours for `code`/`deep`, and 24 hours for
  `academic`. JSON output now includes `meta.cache_ttl_seconds`.
- **Provider drift flags**:
  - Tavily `--include-images` and `--include-image-descriptions`.
  - Brave `--goggles` and native Place Search handling for `places`.
  - Serper `--serper-type`, `--page`, `--autocorrect/--no-autocorrect`, and
    patents endpoint support.
  - Firecrawl `--ignore-invalid-urls`, `--firecrawl-scrape-timeout`, and
    `--firecrawl-wait-for`.
  - Jina `--jina-engine`, `--jina-respond-with`, `--jina-target-selector`,
    `--jina-wait-for`, `--jina-remove-selector`, and `--jina-generated-alt`.
- **Provider audit doc** — `docs/PROVIDER-DRIFT-2026-Q4.md` records findings,
  implemented flags, and skipped enterprise/niche items.

### Changed
- Version bumped to `0.4.0`.
- `--cache-ttl` remains the explicit override and now takes precedence over
  mode-derived defaults everywhere in the SDK/CLI.
- `--no-cache` documentation now matches behavior: it bypasses cache reads and
  writes.

### Tests
- **113 unit tests passing**.
- New coverage: MCP tool registration/direct calls, adaptive cache policy and
  engine metadata, Tavily/Brave/Serper/Firecrawl/Jina drift wiring.

### Verified
- `.venv/bin/pytest -q`
- `.venv/bin/hsearch --version`
- `.venv/bin/hsearch mcp --help`

## [0.3.1] — 2026-05-20

API alignment update: verified all 6 providers against their latest official docs (2026-05).

### Fixed
- **Firecrawl sources format** — now sends `[{type: "web"}]` objects per v2 API spec (was incorrectly sending `["web"]` strings).
- **Firecrawl `lang`** — moved from invalid top-level param to correct `scrapeOptions.location.languages` placement.
- **Firecrawl `scrapeOptions.formats`** — now sends plain strings (`"markdown"`, `"summary"`) per v2 docs, not objects.
- **Tavily topic validation** — invalid topics now fall back to `"general"` instead of being forwarded to the API.

### Added
- **`--mode finance`** — new router preset using Tavily `topic=finance` + `search_depth=advanced` + advanced answer.
- **`--answer-depth basic|advanced`** — Tavily answer detail level control (Tavily now accepts string values for `include_answer`).
- **`--moderation`** — Exa content moderation filter for unsafe content.
- **`--livecrawl-timeout`** — Exa livecrawl timeout in milliseconds.
- **Exa new params**: `startCrawlDate`/`endCrawlDate` (crawl date filtering), `contents.text.verbosity`/`includeHtmlTags`/`includeSections`/`excludeSections` (text extraction control), `contents.extras.links`/`imageLinks` (link/image extraction), `contents.livecrawlTimeout`.
- **Exa response fields**: `author` and `image` now captured in `SearchResult`.
- **Brave**: `spellcheck` and `ui_lang` parameters.
- **Jina**: `X-Timeout`, `X-Max-Tokens`, `X-Cache-Tolerance`, `X-Preset`, `X-Target-Selector`, `X-Retain-Images`, `X-Respond-With` headers.
- **Firecrawl**: `timeout` param, `highlights` scrape format.
- **Dedup**: content/summary/favicon/author/image merging across providers; richness-based scoring (results with content/summary/dates rank higher).
- **Recall mode**: now also enables Brave `spellcheck`+`extra_snippets` and Exa `moderation`.
- `SearchResult.author` and `SearchResult.image` fields.

### Changed
- **Tavily `topic`** validated against `{general, news, finance}` with fallback.
- Schema updated with new params, finance example, and recall tips.

### Tests
- **+26 unit tests** (99 total): Firecrawl sources/lang/timeout/highlights, Exa moderation/crawl-dates/livecrawl-timeout/text-verbosity/extras-links/author-image, Tavily finance-topic/invalid-topic/advanced-answer/basic-answer, Brave spellcheck/ui_lang, Jina new headers/respond-with, dedup content-merging/richness-scoring, router finance-mode, CLI answer-depth/moderation/mode-finance/version.

## [0.2.3] — 2026-05-12

High-recall search update based on current Tavily, Exa, Brave, and Firecrawl docs.

### Added
- **`--mode recall`** — broad, recall-first preset over Exa, Tavily, Brave, Serper, Firecrawl, and Jina.
- **Brave LLM Context support** via `search_kind=context`, used automatically by `--mode recall`.
- **`--chunks-per-source`** — Tavily `chunks_per_source` passthrough for high-context advanced/fast search.
- **`--highlights`**, **`--additional-query`**, **`--max-age-hours`** — Exa Search API controls for current `contents.*` parameters.
- **`--context-threshold`** — Brave LLM Context threshold control.

### Changed
- Exa Search now nests content options under `contents`, requests `highlights` by default, and translates legacy `--livecrawl` values to current `contents.maxAgeHours`.
- `--time YYYY-MM-DD..YYYY-MM-DD` now maps to Tavily `start_date` / `end_date`.
- Firecrawl now uses native `includeDomains` / `excludeDomains` filters when only one side is specified.
- Jina `--lang` now maps to `X-Locale`.
- Cache initialization now falls back to `/tmp/hsearch-cache`, and `--no-cache` no longer initializes diskcache.

### Tests
- **73 unit tests passing**.

## [0.2.2] — 2026-05-07

Tracks 2026-04 provider doc updates: Tavily new params, Exa Fast/Instant search types, Firecrawl v2 categories format.

### Added
- **`--exact`** — Tavily `exact_match=True`: quoted phrases must appear verbatim, no synonym expansion. Useful for exact-name lookups.
- **`--depth basic|advanced|fast|ultra-fast`** — Tavily `search_depth` selector. `fast`/`ultra-fast` are new latency-first depths added by Tavily in 2026-04 (sub-second responses).
- **`--exa-type auto|fast|instant|neural|keyword|deep-reasoning`** — explicit Exa `type` selector. `instant` (Exa 2.0) returns in ~400ms; `fast` in ~1s.
- **`--include-favicon`** — Tavily: each result includes its `favicon` URL (now exposed via the new `SearchResult.favicon` field).
- **`--include-usage`** — Tavily: response usage block (`{"credits": N}`) is surfaced in the JSON `meta.usage[provider]`.
- **`--mode fast`** — new latency-first router preset: queries Exa with `type=instant` and Tavily with `search_depth=ultra-fast` in parallel for sub-second multi-provider grounding.
- `SearchResult.favicon: str | None` field.
- Provider `_last_usage` introspection hook (mirrors existing `_last_answer`).

### Changed
- **Firecrawl `categories`** — string array form (`["github"]`) is now auto-normalized to the v2 object form (`[{"type": "github"}]`) before sending. Existing callers continue to work; the wire format matches current Firecrawl docs. Backward-compatible.
- Tavily `search_depth` is validated against `{basic, advanced, fast, ultra-fast}` and falls back to `basic` on unknown values instead of forwarding garbage to the API.

### Tests
- **+21 unit tests** (68 total): Tavily exact_match / include_favicon / include_usage / depth variants + invalid-depth fallback, Exa type passthrough (parametrized over fast/instant/auto/deep-reasoning), router `fast` mode, full CLI flag wiring (`--exact`, `--depth`, `--exa-type`, `--include-favicon`, `--include-usage`, `--mode fast`), Firecrawl categories object-form passthrough.

### Verified live (2026-05-07)
- `--mode fast`: 1.4s for exa+tavily double-fetch with deduped merge.
- `--depth ultra-fast`: 0.97s tavily-only.
- `--exa-type instant`: 0.7s exa-only.
- `--include-favicon`: real favicon URLs returned for Wikipedia + anthropic.com.
- `--include-usage`: surfaces `meta.usage.tavily.credits=1`.
- Firecrawl categories normalized form `[{"type": "github"}]` confirmed on the wire.

## [0.2.1] — 2026-04-30

### Added
- **`search --agent`** — compact agent preset: defaults to structured JSON output and `--top 5` unless the caller explicitly overrides `--format` or `--top`.
- **`search --extract-provider jina|firecrawl`** — lets `--extract-top` use Firecrawl for JS-heavy pages instead of always using Jina.

### Fixed
- Provider key loading is now Hermes-profile-aware: `hsearch` reads `$HERMES_HOME/.env` and global `~/.hermes/.env`, so Telegram/gateway sessions see the active profile's search keys even when `HOME` points at the profile sandbox.

## [0.2.0] — 2026-04-21

### Added — provider feature parity & answer mode
- **`--mode answer`** — Perplexity-style synthesized answer panel (powered by Tavily `include_answer`); printed at the top in `table` and `markdown` output.
- **`--answer / -a`** — explicit flag, equivalent to `--mode answer` but composable with other modes.
- **`--summary`** — request per-result LLM summaries from Exa/Firecrawl (when supported).
- **`--raw`** — fetch full markdown of each Tavily result (`include_raw_content="markdown"`); content is truncated to 2000 chars per result in render to avoid console flooding while remaining grep-friendly.
- **`--livecrawl always|fallback|never`** — control Exa `contents.livecrawl` to force fresh fetches.
- **`--days N`** — restrict Tavily news mode to the last N days.
- **`--auto`** — enable Tavily `auto_parameters=True` (let Tavily auto-pick depth/topic).
- **`--sources web,news,images`** — Firecrawl multi-source merge.
- **`--retries N`** — per-request retry count on 429/5xx with exponential backoff (default 2).
- **17 new unit tests** covering all of the above (40 tests total, all green).

### Changed — output rendering
- Markdown snippet truncated at 500 chars (was full body); prevents 80+ line dumps in `--mode answer`.
- `--raw` content rendered up to 2000 chars per result (was a hidden 200-char clip — now matches the flag's contract).
- `--summary` field surfaced in both `markdown` and `table` outputs.

### Internal
- Provider HTTP layer: shared retry/backoff middleware in `providers/base.py`.
- Cache key params now strip private flags (anything prefixed `_`) so retries don't pollute the cache.

## [0.1.0] — 2026-04 (initial)

- 6 providers wired: **Brave, Serper, Exa, Tavily, Firecrawl, Jina**.
- Routing modes: `default | news | academic | code | general | realtime | shopping | video | images | places | answer | deep`.
- `search` / `extract` / `providers` / `config` / `cache` subcommands.
- Filters: `--time / --lang / --region / --site / --exclude`.
- Output formats: `table | json | jsonl | markdown | urls`.
- Disk cache (diskcache, default 1h TTL).
- Multi-provider parallel mode (`--all`) with cross-provider URL dedup.
- 23 unit tests (mocked HTTP via `respx`).
