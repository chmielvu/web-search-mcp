# Changelog

## [Unreleased]

### Changed - Direct OpenAI-compatible LLM clients
- Replaced LiteLLM with `openai.OpenAI` / `openai.AsyncOpenAI` across runtime and offline judge calls. Provider endpoints retain their configured `base_url`, timeout, and model, while client retries are disabled with `max_retries=0` so 429 `Retry-After: 60` responses cannot reintroduce the orchestration latency spike.
- Replaced LiteLLM OpenInference instrumentation with the OpenAI instrumentor and removed the direct LiteLLM dependency. The optional `mcpevals` extra still brings LiteLLM transitively through DSPy.
- Added `openai/gpt-oss-120b:nscale` through `huggingface_hub.InferenceClient` between Groq and Vercel in the worker ladder. The synchronous Hugging Face call is isolated with `asyncio.to_thread` and bounded by the same per-endpoint timeout/failover loop.

### Changed - Search retrieve budget
- Every planned provider is now attempted without runtime health/cooldown gating. One phase-level retrieve budget preserves completed results for ranking and records budget-exceeded provider tasks as `incomplete` with `error_type="retrieve_budget"`.

### Fixed — Analytics cutover to fixed six-branch model
- **Analytics DuckDB schema aligned to the fixed six-branch topology.** `search_branches` and `provider_calls` now use `branch_role` (not `branch_target`), `support_terms` (not `must_keep_terms`), and no `branch_weight`. `search_quality_scores` no longer has `rewrite_variant_count`; `branch_count` is computed from `search_branches`. Quality metrics query the live unified tables (`search_candidates`, not `merged_candidates`; no `query_rewrites`). Daily summaries read from `search_runs` and `provider_calls` with correct column names (`latency_ms`/`error_type`, not `duration_ms`/`error_code`); `summary_intent_daily` replaces `decomposition_rate`/`fallback_rate`/`avg_rewrite_variants` with `avg_branch_count` (observability invariant, expect 6.0). `summary_rerank_daily` normalizes NULL providers to `'internal'` with `COALESCE` and declares `provider NOT NULL` to match the composite PK. The `_migrate_rerank_stages()` compatibility path is removed; the analytics database is disposable and recreated from fresh DDL with no migration. `reports.candidate_survival` reads from `provider_calls.candidate_urls`, `search_candidates`, and `final_results` (not `merged_candidates`). `vw_branch_summary` includes `support_terms`. `vw_rerank_timeline` uses live rerank stage names. Stale `search_events`/`query_understanding`/`query_rewrites`/`provider_candidates`/`merged_candidates` references removed from analytics AGENTS guide and DuckDB schema docs.
- **Search quality persistence ordering fixed.** Quality metrics now run inside the same dedicated DuckDB worker callback after all unified fact rows have been inserted, eliminating the asynchronous read-before-write race that produced false zero candidate/final-result counts.
- **Shared Qdrant embedding dispatch fixed.** `search_qdrant` now accepts the six-branch service’s precomputed query embedding; the provider adapter no longer raises `TypeError` by injecting an unsupported keyword and no longer recomputes the same Hugging Face embedding.
- **Bright Data Yandex raw-response support added.** Yandex URLs follow the documented `text`/`lr`/`lang` contract without unsupported `brd_json=1`; raw organic result HTML is parsed while advertisement containers are excluded.
- **Bright Data retrieval timeout envelope aligned with provider HTTP budget.** `retrieval._call_provider` no longer wraps Bright Data adapters in a 10s catalog default while `run_provider` allows ~20s × 3 attempts plus backoff; outer timeout now derives from `BRIGHTDATA_GOOGLE_TIMEOUT_SECONDS` / `BRIGHTDATA_BING_TIMEOUT_SECONDS`. Removed redundant `asyncio.wait_for` around Bing sidecar HTTP (httpx timeout only).
### Fixed — Query embedding dropped from analytics when rerank produced a context
- **Query embedding dropped from analytics when rerank produced a context.**
  `ranking.py` now copies `RerankEmbeddingContext.query_embedding` onto
  `DiagnosticsCollector.query_embedding` alongside the candidate copy, so
  `query_embedding_dim` is no longer null and `query_embeddings` persistence
  is no longer skipped. Add a regression test
  (`tests/test_query_embedding_propagation.py`) covering the collector state,
  the `build_diagnostics` projection, and the `persist_search_outcome` write
  dispatcher.

### Removed
- Perplexity search surface removed entirely: `PerplexitySearchResponse` model and `PerplexitySearchResultType` alias from `models.py`, `perplexity_search` from `EXPENSIVE_TOOLS` in `rate_limits.py`, "Perplexity Sonar" steering message replaced with generic expensive-tool guidance in `expensive_tool_protection.py`.
- Perplexity telemetry removed: `record_perplexity_search`, `get_perplexity_metrics`, `PERPLEXITY_DEPTH`/`PERPLEXITY_SOURCE_COUNT`/`PERPLEXITY_MODEL` constants and their re-exports from `telemetry/__init__.py`, `attributes.py`, `metrics.py`, `records_ai.py`.
- `POLLINATIONS_API_KEY` removed from environment docs in `CLAUDE.md`, `README.md`, and `skills/web-search-cli/SKILL.md`.
- `skills/web-search-cli/SKILL.md` fully refreshed to match current CLI shape: added `--debug` global flag, `sitemap generate`, `experiments` group; removed `agent research`; updated `search web` to require `--research-goal`, default `--num-results` 15 (clamped 15–50), added `--diagnostics`, removed `--provider`; added `--summary-mode`/`--focus-query` to `content batch`; added `--backend` to `youtube transcript`.
### Added
- DeGoog search aggregator as free provider alongside SearXNG
- Brave LLM Context replaces the standard Brave web path in `search_brave()` (`/res/v1/llm/context`, `grounding.generic` → `WebSearchResult`).
- New `brave_news` specialized provider for the `news` intent (`/res/v1/news/search`, `page_age` → `published_date`).
- `brave_common.py` centralizes Brave API key, headers, query bounds, and freshness translation across Brave surfaces.
- `BRAVE_GOGGLES_BY_INTENT` settings field (default `{}`) merges intent-configured Goggles into `brave` / `brave_news` provider arguments.
- `ProviderExecutionPlan.specialized_provider_names` and a `specialized_original` branch wire intent-policy specialized providers (e.g. `telegram`, `brave_news`).
- `web-search-cli --debug` to enable DEBUG-level application logging on stderr while keeping command JSON on stdout.
- Strict `WebSearchRequest`/`QueryBranch` contracts, immutable 19-provider metadata registry, `bm25s` lexical scoring, and detached search-outcome lifecycle.
- Camoufox stealth-Firefox sidecar as last-resort browser fallback.
- `CamoufoxClient` / `CamoufoxClientError` in `remote_clients.py` with 503 retry, 8 MiB cap, health cache.
- `_fetch_via_camoufox` stage in `stages.py` (raw HTML -> markdown + metadata + links).
- `specialized_pipeline.py` module extracted for Tier-1 resolver orchestration.
- `CAMOUFOX_BASE_URL`, `CAMOUFOX_TIMEOUT_SECONDS`, `CAMOUFOX_HEALTH_CACHE_SECONDS` settings.
- `CONTENT_STAGE_CAMOUFOX` telemetry attribute.

### Breaking changes
- **2026-07-12 — Shared web-search service cutover.** MCP and CLI now construct the same validated request and call `execute_web_search`; `research_goal` is required and `num_results` accepts only 15–50. Explicit `rewrite=False` retains deterministic keyword/Autosuggest/Spellcheck enrichment instead of literal-syntax auto-bypass.
- **2026-07-10 — Query rewrite and reranking overhaul.** This is a clean break: `original_free` routes the original query to `free` providers, `keyword_refined` routes to keyword/SERP providers, and `neural_refined` routes to neural providers. Literal search syntax bypasses the LLM rewrite. RAKE-NLTK extracts ranked `must_keep_terms` from `research_goal`; Brave Autosuggest uses `rich=true` and the separate `BRAVE_SUGGEST_API_KEY`, while spellcheck uses `BRAVE_API_KEY`. Branch results are filtered through the DuckDB-backed URL blocklist before merge. Merge is pure rank-based RRF with per-intent `rrf_k` and no provider/list weights. The Qwen XML listwise-CoT reranker now escapes untrusted candidate fields, shuffles display IDs and remaps them, parses only `<final_ranking>`, and assigns normalized linear ordinal scores. LLM output is accepted only when error-free with non-empty relevance scores. Bi-encoder and cross-encoder stage multipliers form a monotonic funnel, and diversity is terminal with no tail reattachment.

### Removed
- Removed the experimental LangChain/LangGraph agentic research stack, its `agent` CLI command, tool registration, telemetry, settings, dependencies, and dedicated tests.
- Local `crawl4ai` Python package + transitive `playwright`/`playwright-stealth` deps.
- `legacy_sitemap.py` (Crawl4AI deep-crawl sitemap fallback).
- `CONTENT_STAGE_NODRIVER` telemetry attribute.
- `BROWSER_EXECUTABLE_PATH` env var from README.

### Changed
- Tier-2 order: Jina Reader -> Crawl4AI /md -> local BS4 (conditional) -> Camoufox last-resort.
- `crawl4ai_client.py` renamed to `remote_clients.py`; `Crawl4AIClient.crawl()`/`deep_crawl()` removed.
- `fallback.py` renamed to `stages.py`; `fallback_fetch_content` wrapper removed.
- `fetch_pipeline.py` rewritten to delegate stages to `stages.py` and `specialized_pipeline.py`.
- `batch_orchestrator.py` simplified to per-URL `fetch_content_artifact` only.
- `sitemap.py` simplified to Tavily-only; legacy fallback deleted.
- 6 specialized resolvers moved to `content/resolvers/` subfolder.
- **Phoenix tracing lifecycle** now uses `phoenix.otel.register` with the `WebSearchMCP` project and local SSH-forward endpoint `http://127.0.0.1:6006/v1/traces`; LiteLLM, LangChain, and HTTPX instrumentation share one provider and shutdown follows outcome drain → persistence → HTTP → telemetry.
- **VPS service endpoints corrected** — `.env` now targets SearXNG at `127.0.0.1:8080`, DeGoog at `127.0.0.1:4444`, and Phoenix OTLP HTTP at `127.0.0.1:6006/v1/traces`, matching the SSH-forwarded VPS services; Hermes keepalive job `7acbeb2b3573` now specifies the complete manifest forward list.
- **Crawl4AI remote endpoint enabled** — `.env` now points `CRAWL4AI_BASE_URL` to the manifest-mapped SSH forward `http://127.0.0.1:11235`.
- **Phase Two — Brave retrieval:** `news` intent policy version `1.1` adds `brave_news` via `specialized_original`; BrightData news URLs map freshness to `tbs=qdr:`; cache identity fingerprints per-provider arguments; DDGS documented as peer `free` provider (not fallback).
- **Modularized telemetry package** — split `src/kindly_web_search_mcp_server/telemetry.py` into a focused `telemetry/` package (`attributes.py`, `constants.py`, `init.py`, `metrics.py`, `spans.py`, `span_enhancements.py`, `records_*.py`, `_internal.py`). The public API is preserved via `telemetry/__init__.py` re-exports; all existing imports from `.telemetry` continue to work.
- **Rerank bi-encoder hot path repaired** — the HF bi-encoder now runs for normal overfetch windows by default, embeds bounded title/snippet candidate text (`RERANK_BI_ENCODER_TEXT_MAX_CHARS=384`), keeps normal windows in one batch (`RERANK_BI_ENCODER_BATCH_SIZE=64`), and uses a single latency-sensitive candidate-embedding attempt (`RERANK_BI_ENCODER_TIMEOUT_SECONDS=15.0`). The per-call `AsyncInferenceClient` singleton is reused for connection pooling; concurrency is controlled by the per-caller wrappers (Qdrant `BatchLimitedEmbeddings`, bi-encoder batch semaphore) rather than a process-global gate.
- **Rerank candidate analytics batched per stage** — candidate-survival rows are now inserted with one DuckDB connection/executemany per stage instead of per-candidate writes inside the awaited rerank path.
- **SerpApi default engine switched to Yahoo** — the provider now defaults `SERPAPI_DEFAULT_ENGINE` to `yahoo`, keeping multi-engine support intact while broadening the default non-Google coverage.
- **Grafana dashboards aligned to current telemetry** — the pipeline dashboard now uses `web_search_rrf_provider_contribution`, the content dashboard now tracks `crawl4ai_remote`, and the providers dashboard now includes circuit-state visibility. `grafana/README.md` and the Grafana dashboard regression tests were updated to match.
- **HF Inference API connection reuse** — `embed_texts` now uses a singleton `AsyncInferenceClient` instead of creating a new instance per call. The HF library lazily creates an internal `httpx.AsyncClient`; reusing the same instance gives TCP/TLS connection pooling. Latency dropped from 5-6s to ~1s per embedding call (~5x improvement).
- **Reranking pipeline overhaul** — MMR now uses reranker scores for relevance instead of embedding cosine similarity. The cross-encoder/LLM reranker scores are min-max normalized and used as the MMR relevance term; embeddings are only used for the diversity (document-to-document) term. This fixes the critical issue where MMR ignored expensive reranker scores and recomputed relevance from weaker embedding similarity.
- **MMR lambda default changed from 0.5 to 0.7** — relevance-weighted for web search (was 50/50 relevance/diversity, now 70/30). Research consensus: λ=0.7-0.8 for precision search.
- **Cross-encoder document construction enriched** — now includes Domain, Providers, ProviderCount in addition to Title, Snippet, URL. Snippet moved to second position (after Title) for better semantic importance with Cohere rerank v4.
- **Bi-encoder stage target is now configurable** via `RERANK_BI_ENCODER_STAGE_MULTIPLIER` (default `3.0`), preserving a wider shortlist for the cross-encoder without hardcoding a second candidate limit.
- **LLM reranker prompt uses the Qwen template's query and research_goal fields** — candidate payload remains limited to escaped title, URL, and snippet.
- **LLM reranker scores use normalized linear ordinal scoring** — the first ranked candidate scores `1.0`, the last scores `0.0` (or `1.0` for a one-candidate list), and scores are remapped after display-ID shuffling.
- **Terminal diversity ordering** — MMR consumes min-max-normalized reranker relevance scores for relevance and embeddings for the diversity signal, then returns only the diversified top-k slice without reattaching a tail.
- **Reranker fallback chain simplified to ONE chain**: `cohere_fast -> cohere_fast_openrouter -> voyage`. Always tries in this order regardless of configured engine; the existing Jina and GCP Cloud Run adapter modules remain available for direct integrations.
- **Modularized `server.py` tool handlers** — split `@mcp.tool` handlers, resources, and prompts into focused modules under `src/kindly_web_search_mcp_server/tools/`. `server.py` is now a thin registry that imports and registers handlers on the `mcp` instance. Fixed the missing `num_results` parameter in `web_search` and replaced logging f-strings with lazy formatting in touched code.

### Added
- Added `plans/grafana-observability-refresh-plan-2026-07-03.md` to reconcile the live Grafana setup with the current app telemetry, including the crawl4ai content stage, provider health visibility, branch/result lineage, and MotherDuck-backed quality panels.
- Added `plans/web_search-latency-report-2026-06-30.md` documenting the live MCP timeout analysis, provider bottlenecks, and code-path latency sources for `web_search`.
- Corrected `plans/web_search-latency-report-2026-06-30.md` to reflect the actual root causes: missing outer timeout, repeated provider bundle across branches, and the shared paid-provider semaphore bug.
- Added `plans/provider-root-cause-remediation-plan-2026-06-29.md` documenting the provider latency/root-cause findings and the no-new-tests remediation plan.
- Reworked `plans/IN-DESIGN/observability/full-pipeline-observability-implementation-plan-2026-06-30.md` into a live DuckDB-backed clean-break observability design for full `web_search` coverage, including branch/provider/rerank lineage and exact returned-response analytics.
- Added the DuckDB observability implementation for full `web_search` coverage: tool-call rows, returned-response rows, branch attempts, branch candidates, provider health transitions, and pipeline heartbeats, plus returned-object views and candidate-survival analytics.
- Added the repo-doc consolidation note and simplified `CLAUDE.md` to point at `AGENTS.md` as the single workspace guidance source.

### Fixed
- **MCP startup import regression** — deferred the experimental agent runner and RAKE-NLTK imports until their respective tools execute, removing LangChain/LangGraph and NLTK from the standard stdio startup path.
- **LiteLLM route model IDs separated from reported provider model IDs** — Cerebras/Groq worker calls now send provider-qualified route models such as `cerebras/gpt-oss-120b` and `groq/openai/gpt-oss-120b` to LiteLLM while preserving raw provider model IDs in telemetry.
- **Query rewrite model IDs now match the documented providers** — the rewrite/classifier LLM router now uses the documented Cerebras, Groq, and Vercel model IDs directly instead of inventing nested provider-prefixed strings. This restores the intended Cerebras → Groq → Vercel fallback ladder and avoids the malformed Vercel rewrite default.
- **Async DuckDB analytics no longer blocks the event loop** — the hot observability and pipeline write paths now dispatch DuckDB inserts through a shared background-write helper, covering search events, provider calls/candidates, rerank stages, final results, search runs, and pipeline observability inserts.
- **HF Inference API connection reuse** — singleton `AsyncInferenceClient` eliminates per-call TCP/TLS handshake overhead. Embedding latency dropped from 5-6s to ~1s (~5x improvement).
- **Search import path cleaned up** — removed stale `task_scope` references from live provider code and tests, kept the branch executor on direct `asyncio` primitives, and made BrightData Bing cancellation re-raise instead of returning an empty result list.
- **DuckDB now logs which reranker was actually used** — `search_runs` table has new `reranker_provider` and `reranker_model` columns. `RerankOutput` carries `provider`/`model` through the pipeline so the final chosen reranker (including fallback winners like `voyage` or `groq`) is recorded per run.
- **Search provider connect timeouts fixed** — `base_provider.py` now uses `httpx.Timeout(connect=5.0, read=25.0)` instead of a single 30s total timeout. Dead/unreachable providers (like `search_router`) fail fast at ~5s instead of hanging for 54s on TCP SYN retransmissions.
- **DuckDuckGo provider timeout now configurable** — `ddg_timeout_seconds` setting (env: `DDG_TIMEOUT_SECONDS`, default `10`) controls `DDGS(timeout=...)`. Previously hardcoded to 10s.
- Disabled the Google CSE provider registration so the search stack no longer routes live traffic through a Google Custom Search path that is blocked for this project.
- Redacted Google CSE 403s so `API_KEY_SERVICE_BLOCKED` now reports a clear Google Cloud authorization message instead of leaking the raw request URL.
- Switched both the Composio `web_search` provider path and `quick_web_search` to execute `COMPOSIO_SEARCH_TAVILY`, the live-working Composio Search action, and updated quick-search parsing for Tavily's `answer` plus `results` response shape.
- Updated the default Composio Search toolkit version to `20260618_00`; live probes showed the prior `20260424_00` pin returned a misleading `COMPOSIO_EXA_API_KEY` backend error for Composio Search actions.
- Bounded and cached the Qdrant query-embedding path so decomposed search branch fanout no longer stampedes Hugging Face inference with one raw embedding call per Qdrant branch.
- Hardcoded the Google CSE engine ID to the live configured value `771d303cf528e4b7c` so the Google CSE provider no longer depends on an unset `GOOGLE_CSE_ENGINE_ID`.
- Switched the Composio search provider parser to accept both citation-shaped and raw-result-shaped Composio payloads.
- Made the Composio client read `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` from the live environment at call time so quick search follows the same credentials loaded by the server bootstrap.
- Added branch-executor headroom so decomposed search branches can return provider partials before the wrapper cancels the task.
- **Fixed provider duplication in branch planner** — `_shard_providers` no longer pads provider lists with `cycle()`, so each provider appears in exactly one branch instead of being invoked multiple times per branch.
- **Fixed redundant branch creation when rewrite is disabled** — `pipeline.py` no longer injects an extra `QueryVariant` when `rewrite=False`; the canonical original branch from `build_search_branch_specs` is the only branch.
- **BrightData Google search now respects a configurable timeout** — new `BRIGHTDATA_GOOGLE_TIMEOUT_SECONDS` setting (default `20.0`) used as both the per-request and `run_provider` timeout. Google and Bing requests are now concurrent instead of sequential.
- **BrightData per-attempt logging** — logs each Google/Bing attempt URL and timing for easier observability.

### Added
- Unit tests for branch planner: provider sharding without duplication and correct branch counts for rewrite enabled/disabled (`tests/test_branch_planner.py`).

## [0.4.0] — 2026-06-28

### Added
- **TinyBERT-4L ONNX INT8 intent classifier** — replaces LLM-backed query understanding as primary path. 83% accuracy, 84% F1 macro across 6 search intents. ~5ms latency vs ~60s LLM.
- **6-class SearchIntent system** — expanded from 4 intents (`general`, `ai_coding`, `digital_humanities`, `comparison`) to 6 (`general`, `ai_coding_and_infrastructure`, `digital_humanities`, `comparison`, `social_media`, `news`). Updated all intents.py, intent_policy.py, schema, prompts, analytics judges.
- **Dockerized classifier service** on VPS at port 8686. FastAPI with /health and /classify endpoints. CPU-only torch image, 300MB RAM, auto-restart.
- **Persistent SSH tunnel** via systemd user service + autossh (port 18686 → VPS:8686). Auto-restarts on connection drop.
- **Training pipeline** — distilabel + Gemini API for synthetic data generation, custom GeminiLLM class, class-weighted WeightedTrainer, ONNX export + INT8 quantization.
- Classification report: general=0.86, social_media=0.87, digital_humanities=1.00, comparison=0.76, ai_coding=0.79, news=0.73.

### Changed
- `resolve_query_understanding` in resolver.py — ONNX classifier is primary path, LLM query understanding is fallback (only when classifier service is down).
- Added `intent_classifier_url`, `intent_classifier_timeout_seconds`, `intent_classifier_confidence_threshold`, `intent_classifier_enabled` settings.
- Classification report: general=0.86, social_media=0.87, digital_humanities=1.00, comparison=0.76, ai_coding=0.79, news=0.73.
- Created `docs/ARCHITECTURE.md` and `docs/ARCHITECTURE-DIAGRAMS.md` documenting the system architecture and data flows.
- Added `docs/CONFIGURATION.md` with environment variables and setup guide.
- Added `docs/GETTING-STARTED.md` quick start guide.
- Added `docs/DEVELOPMENT.md` and `docs/TESTING.md` development patterns and workflows.
- Added `docs/CONTRIBUTING.md` contribution guidelines.
- Added `plans/` directory with initial roadmap and design documents.
- Added `tests/test_branch_planner.py` for branch planner tests.

### Fixed
- Fixed classifier service URL to use the live VPS endpoint.
- Resolved query understanding fallback to LLM when classifier service is down.
- Fixed intent classification confidence threshold handling.

## [0.3.0] — 2026-06-15

### Added
- Initial web search MCP server with multi-provider search (SearXNG, Tavily, Brave, Jina).
- RRF merge and reranking pipeline.
- Content extraction pipeline with 7-stage resolution.
- YouTube transcript and search tools.
- Academic search across 6 scholarly sources.
- Gemini grounded search and Grok search.
- Semantic sitemap generation.
- Query cache and page cache layers.
- OpenTelemetry and DuckDB observability.

### Changed
- N/A

### Fixed
- N/A

## [0.2.0] — 2026-06-01

### Added
- Prototype MCP server with basic web search.

### Changed
- N/A

### Fixed
- N/A

## [0.1.0] — 2026-05-15

### Added
- Initial project scaffolding.
