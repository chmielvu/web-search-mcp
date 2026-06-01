# Observability Enhancement & Grafana Dashboard Design Plan
**Project:** kindly-web-search-mcp-server (FastMCP web search aggregator)
**Date:** 2026 (plan mode session)
**Role:** Grafana OTEL Engineer
**Canonical location (for harness):** The long session-specific path under ~/.grok/sessions/<uuid>/plan.md (this file is the working copy; copy if needed for exit).

## Context
The web-search-mcp project is a production-grade Python MCP server (FastMCP) offering multi-provider search (SearXNG/Tavily/Brave/Jina + academic/YouTube), RRF merge, query rewrite (Mistral/LiteLLM), policy-driven decomposition (FunctionGemma), staged content resolution (GitHub GraphQL, Wikipedia, arXiv, trafilatura + nodriver browser fallback), semantic + exact caching (LanceDB/SQLite), reranking (Voyage/Jina bi+cross), and embeddings.

**Current Observability State (from codebase exploration via terminal + code reads):**
- **Strong foundation already present** (no greenfield):
  - OTel deps (opentelemetry-api/sdk, otlp-proto-http, exporter-prometheus, httpx + logging instrumentations) + structlog + prometheus-client in pyproject.toml (v0.1.8).
  - `src/kindly_web_search_mcp_server/telemetry.py`: detailed docstring outlining **3-layer MCP model** (Transport/JSON-RPC, Tool Execution/pipeline, Agentic Performance), custom semantic attributes (`search.query`, `provider.name`, `cache.search_type`, `content.stage`, `mcp.method.name`, `gen_ai.tool.name`), `init_telemetry()` skeleton with Resource, Batch processors, OTLP HTTP exporters (traces+metrics), optional Prometheus reader, some Counter/Histogram metrics registered (provider_call, search_total, cache_request, mcp_tool).
  - Early wiring in `server.py` (package): `load_dotenv` + `init_telemetry(service_name="web-search-mcp")` **before other imports** (critical for auto-instr + stdio MCP handshake), imports `record_mcp_tool_call`, `record_*` helpers, constants like `SEARCH_QUERY`; calls `emit_tool_observability_event`.
  - `utils/observability.py`: payload-safe helpers (`preview_text`, `_normalize_for_body/extra`, `current_trace_context`, `serialize_search_results`) gated by `KINDLY_OBSERVABILITY_MAX_*` envs (prevents huge attrs from search results — excellent for search use case).
  - Domain-specific emitters: `search/flow_observability.py`, `search/merge_observability.py`, `rerank/observability.py`, `search_instrumented.py`.
  - Supporting: DuckDB analytics store (`analytics/duckdb_store.py`), diagnostics, structured logging (`utils/logging.py`, `structured_logging.py`), middleware (rate_limits, expensive_tool_tracking, session_tracking, query_guidance).
  - `grafana_reports/2026-05-14-mcp-health-report.md`: log-derived 24h analysis (~166 tool calls across web_search/get_content/batch, high cache hits ~415 across exact/semantic/page, ~50+ trafilatura parsing errors, provider/rerank warnings) showing real usage patterns and gaps in miss/error/stage visibility.
- **Gaps (why this enhancement is high-value)**:
  - No `GRAFANA_CLOUD_*` or `KINDLY_OTEL_*` helpers in `settings.py` (only standard `OTEL_*` assumed in telemetry docstring example using `otlp-gateway-prod-eu-west-2.grafana.net`).
  - `telemetry.py` implementation incomplete (setup code visible but full meter init, log provider, sampling config, robust Windows/pwsh Basic auth handling, idempotency, graceful no-op when disabled, shutdown not fully hardened).
  - Coverage incomplete: not uniform spans/metrics across **all** providers (searxng, tavily, brave, jina, pollinations, ddg, academic_*), content resolver stages (fetch_pipeline, github_issues, wikipedia, arxiv, stackexchange, safe_fetch, jina_reader), cache paths (hit/miss + semantic score), nodriver/chromium_pool browser tasks, query rewrite/classifier/ decomposition, embeddings/hf_inference, full orchestrator (rewrite → parallel search → merge/rerank).
  - No exposed `/metrics` HTTP endpoint for direct Prometheus scrape (FastMCP is primarily stdio/HTTP transport; needs custom route or sidecar).
  - No sampling (head-based `parentbased_traceidratio` or tail via processor) — important for cost control on MCP usage.
  - No production-ready Grafana dashboards or JSON artifacts (only ad-hoc md report from logs).
  - Docs (CONFIGURATION.md, DEVELOPMENT.md, README) completely silent on OTel/Grafana setup, env vars, troubleshooting, dashboard import.
  - The health report explicitly highlights under-instrumentation of misses, extraction errors by stage, provider variance — exactly what OTel + Grafana dashboards solve (RED metrics, error events, duration histograms by `content.stage`/`provider.name`).

This aligns with project history (`.agent/CONTINUITY.md` phases on query policy/merge/rerank, `.hermes/plans`, existing `.serena` and coderag observability artifacts). The enhancement completes the vision into **first-class Grafana Cloud Application Observability** (auto RED + custom, Tempo traces with full pipeline, correlated signals, importable dashboards) while preserving MCP stdio contract, Windows/pwsh ergonomics, and "hobby/experimental but shipping real work" ethos.

**Business/Dev Value**: Dramatically lower MTTR for the exact issues in the report (content extraction flakiness, provider health, cache effectiveness, policy decisions); cost visibility for rerank/LLM/embedding calls; production readiness for any hosted or team usage of the MCP; aligns with Grafana skills and best practices.

## Recommended Approach (Selected; Why Not Alternatives)
**Phased, incremental, heavy reuse of existing foundation** (no big rewrite or new frameworks):
1. **Foundation Hardening (1-2 focused changes)**: Extend `settings.py` + complete `telemetry.py`. Make init robust, add sampling + Grafana Cloud convenience (while documenting standard OTEL_* preference per skill).
2. **Deep, Consistent Instrumentation**: Add spans + metrics at remaining boundaries using the excellent existing helpers (`record_*`, `emit_*`, `observability.py` preview/normalize, per-module observability files). Follow OTel semconv (HTTP + emerging MCP/GenAI) + project custom (`search.*`, `provider.*`, `content.stage`).
3. **Grafana Dashboards (the visible deliverable)**: 5 focused, self-contained, importable JSON dashboards designed per the dashboarding skill (schemaVersion 41, variables, time series for rates/latency, stat for golden signals, heatmap for distributions, table/bar for breakdowns, transformations for derived %). Place in `grafana/dashboards/`.
4. **Docs + DX + Tests**: Update CONFIG/DEVELOPMENT + new OBSERVABILITY.md with copy-paste env snippets, local + Cloud steps, troubleshooting. Add telemetry smoke tests. Update CHANGELOG.
5. **Verification loop**: Local (prometheus/console), Cloud (if creds), dashboard import + data validation against real tool calls.

**Why this over alternatives**:
- Direct OTLP (vs always Alloy) for simplicity in dev/MCP use; Alloy recommended for prod enrichment/sampling.
- Convenience `GRAFANA_CLOUD_*` vars (optional) for pwsh users who struggle with Base64 headers (per global Claude.md Windows notes).
- Sampling default 10-20% head-based (tunable) — balances visibility vs cost (Grafana skill + research).
- No new heavy deps; leverage what's already in pyproject.
- Dashboards are JSON (import via UI or API) — not scenes (this is not a Grafana app plugin).

**Tradeoffs Accepted** (transparent):
- Slightly more code in hot paths (mitigated by helpers + feature flags via env).
- Attribute cardinality controlled by existing preview logic (good).
- Direct export simpler but less enrichment than Alloy (document both).

## Critical Files to Modify / Create
**Implementation (core for working OTel + data flow)**:
- `src/kindly_web_search_mcp_server/settings.py` — Add section for OTel (KINDLY_OTEL_ENABLED default true, KINDLY_OTEL_SAMPLING_RATIO=0.1, service namespace/version overrides, optional GRAFANA_CLOUD_* convenience for easy pwsh setup). Keep lightweight (already imported everywhere).
- `src/kindly_web_search_mcp_server/telemetry.py` — Finish `init_telemetry`: complete Resource (SERVICE_NAME etc. + env), all Meter instruments (expand for content stages, browser, rewrite), sampling via env/processor, robust OTLP setup (handle GRAFANA_CLOUD_INSTANCE_ID + API_KEY + ENDPOINT or standard OTEL_), log bridge, shutdown, no-op guards. Improve docstring examples for Windows.
- `src/kindly_web_search_mcp_server/server.py` (the package one) — Extend tool wrappers to ensure 100% coverage of the 6 tools + batch; use lifespan for context propagation if FastMCP supports; optional Prometheus ASGI app mount for /metrics scrape.
- `src/kindly_web_search_mcp_server/search/orchestrator.py`, `search_instrumented.py`, `search/flow_observability.py`, `search/merge.py`, `search/merge_observability.py` — Ensure parent span for full search, child spans + metrics for rewrite mode, provider calls, merge, rerank, diversity.
- `src/kindly_web_search_mcp_server/content/fetch_pipeline.py` + all resolvers (github_issues.py, wikipedia.py, arxiv.py, stackexchange.py, safe_fetch.py, etc.) + `content/batch_orchestrator.py` — Stage-aware spans (`content.stage= "github_graphql" | "wikipedia" | "trafilatura" | "browser_nodriver"`), duration, bytes, fallback decisions, errors.
- `src/kindly_web_search_mcp_server/scrape/` (universal_html.py, chromium_pool.py, nodriver_worker.py, fetch.py) — Browser pool usage, task latency, JS-heavy site handling metrics/events.
- `src/kindly_web_search_mcp_server/cache/` (all .py) + `analytics/duckdb_store.py` — Hit/miss counters (by type), semantic match score distribution, duration; optional export of analytics to OTel.
- `src/kindly_web_search_mcp_server/rerank/core.py` + `bi_encoder.py` + `embeddings/hf_inference.py` + query rewrite/classifier modules — Provider-specific latency, model, token-ish sizes (where applicable).

**Docs & Deliverables**:
- `CHANGELOG.md` — `[Unreleased]` Added: "Full OpenTelemetry instrumentation (traces + metrics) for MCP tools, search pipeline, content resolution, caches, scraping; Grafana Cloud setup docs; 5 importable dashboards."
- `docs/CONFIGURATION.md` — New subsection "Observability & Grafana Cloud" (all env vars with examples for direct + Alloy, Windows notes).
- `docs/OBSERVABILITY.md` (new) — Complete guide: why, quickstart (5 min to first trace), env var reference, local verification (Jaeger or Prometheus), Cloud setup, dashboard import, sampling/cost, troubleshooting, semantic conventions used.
- `grafana/dashboards/` (new dir) — 5x `kindly-mcp-*-dashboard.json` (overview, pipeline, providers, content, cache) + `README.md` with import curl/UI steps + variable usage + screenshots placeholders.
- `.env.example` (create if missing or append) — OTEL_ and GRAFANA_CLOUD_ examples.

**Tests**:
- Extend or add `tests/test_telemetry.py` (or in existing server/orchestrator tests) — Assert init, span emission, metric values on simulated calls (using InMemory exporters or mocks).

**No / Minimal Changes**:
- Individual provider modules (they already feed into instrumented paths).
- Core business logic (only add telemetry calls at boundaries).
- MCP tool I/O contracts (keep lightweight).

## Existing Functions / Utilities to Reuse (with Paths)
- `init_telemetry`, `record_mcp_tool_call`, `record_tool_details`, `record_gemini_search` etc., constants (`SEARCH_QUERY`, `CACHE_SEARCH_TYPE`, `MCP_RESOURCE_URI`), metric vars in `src/kindly_web_search_mcp_server/telemetry.py`.
- `preview_text`, `_normalize_for_body`, `_normalize_for_extra`, `current_trace_context`, `serialize_search_results`, `_stable_hash` in `src/kindly_web_search_mcp_server/utils/observability.py` (and the KINDLY_OBSERVABILITY_MAX_* env handling).
- `emit_tool_observability_event`, `emit_observability_event` in utils + domain files.
- `flow_observability.py`, `merge_observability.py`, `rerank/observability.py` emitters and serializers.
- `settings.Settings` dataclass + all `os.environ.get("KINDLY_*")` pattern.
- `configure_logging`, `Diagnostics`, `new_request_id` in utils.
- `search_single_query` / instrumented paths.
- DuckDB store for any hybrid local analytics + OTel correlation.
- Existing test patterns (patch under `kindly_web_search_mcp_server.*`, IsolatedAsyncioTestCase for async).

## Verification (Concrete, Reproducible Steps — Minimum Gold Standard)
1. **No-regression baseline**: `python -m pytest tests/test_server.py tests/test_search_orchestrator.py tests/test_page_content_resolver.py tests/test_search_router.py -q --tb=no`.
2. **Local OTel smoke (Prometheus path)**: In pwsh: `$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"; $env:OTEL_TRACES_EXPORTER="otlp"; $env:OTEL_METRICS_EXPORTER="otlp"; $env:OTEL_RESOURCE_ATTRIBUTES="service.name=web-search-mcp,service.namespace=kindly,deployment.environment=local"`. Run the MCP (or `test_smoke.py` + manual tool calls via fastmcp or client). If using prometheus reader, hit the metrics endpoint or check logs. Confirm spans contain `search.query`, `provider.name`, `content.stage`, etc.
3. **Grafana Cloud path** (requires free stack + token with MetricsPublisher/TracesPublisher): Set `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` (Basic with instance:token or use new convenience vars), run realistic usage (web_search on technical queries, get_content on github issues/wiki/arxiv pages, youtube, batch). In Grafana Cloud UI:
   - Application Observability → Services → web-search-mcp (see RED metrics, error rate, latency, traces).
   - Explore → Traces (Tempo) — click a search trace, expand children for rewrite/provider/content stages.
   - Metrics Explorer or dashboards — query `kindly_*` or `http_*` + custom.
4. **Dashboard validation** (after import):
   - Import the 5 JSONs (stable UIDs).
   - Time range 1h or 6h, variables: service=web-search-mcp, environment=local or production, provider=~".+".
   - Verify: request rate by tool (time series), p95 latency (stat + heatmap), provider success % table, content extraction stage breakdown (http vs browser vs api), cache hit ratio over time with transformations, top error messages (table).
   - Test drilldown/links if present; refresh; export.
5. **Docs + Windows path**: Follow OBSERVABILITY.md exactly in a fresh pwsh session. Test both direct OTEL_ (with Base64) and the new GRAFANA_CLOUD_* convenience. Confirm data appears.
6. **Sampling & cost**: Set sampling 0.05, run 200 tool calls, verify in Grafana that volume is reduced but key errors still captured.
7. **Full sign-off**: All tests green, CHANGELOG updated, new grafana_reports/ entry or note, no MCP client breakage (stdio or HTTP transport).

**Evidence Artifacts**: Updated health report in grafana_reports/ showing OTel-powered metrics vs old log-only; screenshots or terminal output of traces/dashboards (in PR or follow-up).

## Risks / Edge Cases / Future Work
- **MCP stdio handshake**: init_telemetry must stay before any heavy imports/HTTP (already positioned correctly — preserve).
- **Windows Base64 / headers**: Document Alloy as preferred for teams; convenience vars mitigate.
- **Cardinality**: Relies on existing preview logic + sampling + good label hygiene (e.g. no raw URLs in high-cardinality labels).
- **FastMCP specifics**: If lifespan/context changes in future FastMCP, re-verify propagation.
- **Future**: Tail sampling for "keep all errors", Pyroscope profiles (nodriver CPU?), integration with existing DuckDB for long-term cost attribution, Grafana Scenes version for plugin embedding.

**Dependencies on User**: Grafana Cloud stack access for full Cloud verification (local/Tempo works without). Any existing Alloy deployment.

This plan is complete, evidence-based (specific file:line patterns from reads), actionable, and scoped to deliver visible value (dashboards + working traces/metrics) quickly while respecting the project's excellent existing observability skeleton.

---
*Research sources: Direct reads of pyproject.toml, settings.py (120+ lines), telemetry.py (full structure), server.py, utils/observability.py, orchestrator.py, grafana_reports/..., multiple Select-String for OTel usage across src/. Web research on Grafana OTel Python + Cloud OTLP + dashboards. Full review of grafana-core:opentelemetry and dashboarding skills (via terminal reads of their SKILL.md).*
*All recommendations follow "research first, plan then execute", "reuse existing", "Windows/pwsh first", and "verify before claim" principles.*
