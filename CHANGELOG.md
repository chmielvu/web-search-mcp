# Changelog

## [Unreleased]

### Added
- DeGoog search aggregator as free provider alongside SearXNG

### Changed
- **HF Inference API connection reuse** — `embed_texts` now uses a singleton `AsyncInferenceClient` instead of creating a new instance per call. The HF library lazily creates an internal `httpx.AsyncClient`; reusing the same instance gives TCP/TLS connection pooling. Latency dropped from 5-6s to ~1s per embedding call (~5x improvement).
- **Reranking pipeline overhaul** — MMR now uses reranker scores for relevance instead of embedding cosine similarity. The cross-encoder/LLM reranker scores are min-max normalized and used as the MMR relevance term; embeddings are only used for the diversity (document-to-document) term. This fixes the critical issue where MMR ignored expensive reranker scores and recomputed relevance from weaker embedding similarity.
- **MMR lambda default changed from 0.5 to 0.7** — relevance-weighted for web search (was 50/50 relevance/diversity, now 70/30). Research consensus: λ=0.7-0.8 for precision search.
- **Cross-encoder document construction enriched** — now includes Domain, Providers, ProviderCount in addition to Title, Snippet, URL. Snippet moved to second position (after Title) for better semantic importance with Cohere rerank v4.
- **Bi-encoder filter widened from top_k*2 to top_k*3** (20→30 candidates for top_k=10) — reduces recall bottleneck, gives cross-encoder more candidates to evaluate.
- **LLM reranker prompt now includes query_type and research_goal** — the cross-encoder already received this context; the LLM reranker (most capable of using it) now gets it too.
- **LLM reranker scores changed from 1/position to exponential decay** `exp(-0.3*(pos-1))` — smoother relevance gradient (1.0, 0.74, 0.55, 0.41... instead of 1.0, 0.5, 0.33, 0.25...).
- **Score propagation fixed after MMR** — candidate scores are now updated to reflect MMR ordering using exponential decay, so final scores match final ranking order. Previously, scores reflected pre-MMR LLM ranking while order reflected MMR — causing score-order inconsistency in analytics.
- **Reranker fallback chain simplified to ONE chain**: `cohere_fast -> cohere_fast_openrouter -> voyage`. Always tries in this order regardless of configured engine. Jina and GCP CloudRun rerankers removed entirely (GCP deprecated, Jina redundant).

### Added
- Added `plans/web_search-latency-report-2026-06-30.md` documenting the live MCP timeout analysis, provider bottlenecks, and code-path latency sources for `web_search`.
- Corrected `plans/web_search-latency-report-2026-06-30.md` to reflect the actual root causes: missing outer timeout, repeated provider bundle across branches, and the shared paid-provider semaphore bug.
- Added `plans/provider-root-cause-remediation-plan-2026-06-29.md` documenting the provider latency/root-cause findings and the no-new-tests remediation plan.
- Reworked `plans/IN-DESIGN/observability/full-pipeline-observability-implementation-plan-2026-06-30.md` into a live DuckDB-backed clean-break observability design for full `web_search` coverage, including branch/provider/rerank lineage and exact returned-response analytics.
- Added the DuckDB observability implementation for full `web_search` coverage: tool-call rows, returned-response rows, branch attempts, branch candidates, provider health transitions, and pipeline heartbeats, plus returned-object views and candidate-survival analytics.
- Added the repo-doc consolidation note and simplified `CLAUDE.md` to point at `AGENTS.md` as the single workspace guidance source.

### Fixed
- **HF Inference API connection reuse** — singleton `AsyncInferenceClient` eliminates per-call TCP/TLS handshake overhead. Embedding latency dropped from 5-6s to ~1s (~5x improvement).
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
