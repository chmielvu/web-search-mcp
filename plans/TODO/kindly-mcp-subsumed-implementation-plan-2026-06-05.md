# Kindly Web Search MCP - Subsumed Implementation Plan

> Status: TODO execution plan.
> Created: 2026-06-05T07:25:31+02:00.
> Supersedes for execution sequencing:
> - `plans/TODO/Kindly Web-Search MCP Server - Deep Technical Analysis & Roadmap.md`
> - `plans/TODO/kindly_mcp_recommendations.md`
>
> This document preserves the substance of both plans, normalizes names to the current repo layout, and sequences the work so it can be executed without reinterpreting old naming/path variants.

## Goal

Make the MCP server easier for agents to inspect, select, and compose by tightening the FastMCP surface, then improve search quality and operational feedback with small high-leverage pipeline upgrades.

This is not a rewrite. It is a focused TODO plan over the existing architecture:

- Discovery tools stay lightweight: `web_search` discovers URLs, `get_content` and `batch_get_content` fetch content.
- Existing result memory, entity extraction, DuckDB analytics, dashboards, eval tables, profiles, resources, and prompts are extended rather than replaced.
- External agents remain in control of orchestration; hidden deep-research behavior is out of scope.

## Preconditions

1. Bump FastMCP to the latest stable floor verified on 2026-06-05: `fastmcp>=3.4.0`.
2. Verify the bump with:
   - `uv lock --upgrade-package fastmcp`
   - `uv run --no-sync python -c "import fastmcp; print(fastmcp.__version__)"`
   - `uv run --no-sync python -m pytest tests/test_tool_profiles.py tests/test_tool_descriptions.py tests/test_server.py -q`
   - `uv run --no-sync python -m ruff check src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/tools src/kindly_web_search_mcp_server/composio_tools.py src/kindly_web_search_mcp_server/analytics/tools.py`
3. If FastMCP 3.4.0 breaks analytics app mounting or transforms, keep the dependency bump but gate new 3.4-specific features behind explicit settings until the breakage is fixed.
4. Do not add new code into oversized modules where avoidable. `server.py` and `settings.py` are already too large under repo rules; new feature logic should land in dedicated modules and be registered from the entrypoint.

## Phase 0 - FastMCP Upgrade And Surface Baseline

### 0.1 Upgrade FastMCP

Change `pyproject.toml` from:

```toml
"fastmcp>=2.0.0",
```

to:

```toml
"fastmcp>=3.4.0",
```

Acceptance:

- Lock resolves to FastMCP 3.4.0 or newer stable.
- Existing resources, prompts, profiles, transforms, and tool registration still import.
- Focused tool/profile tests pass.

### 0.2 Inventory Public MCP Surface

Create a deterministic inventory test that asserts:

- Core default tools: `web_search`, `get_content`, `batch_get_content`, `discover_links`.
- Research tools: `gemini_search`, `perplexity_search`, `grok_search`, `academic_search`, `agentic_web_research`, Composio tools.
- Media tools: `youtube_search`, `youtube_transcript`, `composio_image_search`.
- Diagnostic tools: `analytics_query`, `analytics_report`.
- Existing resources: `status://providers`, `status://features`, `docs://workflow`.
- Existing prompts: `plan_web_research`, `evaluate_web_results`, `research_gap_analysis`, `suggest_tool`.

Acceptance:

- A single test fails if a public tool/resource/prompt is renamed or accidentally hidden by a profile.

## Phase 1 - Unified Tool Catalog

### Problem

Core tools use `tools/catalog.py` and `tool_kwargs(...)`, while Composio and analytics tools still register inline annotations. That splits profile filtering, tags, annotations, and future search/visibility policy across multiple places.

### Work

1. Extend `ToolCatalogEntry` with first-class fields:
   - `read_only`
   - `idempotent`
   - `open_world`
   - `expensive`
   - `experimental`
2. Add catalog entries for:
   - `quick_web_search`
   - `composio_similarlinks`
   - `composio_image_search`
   - `analytics_query`
   - `analytics_report`
   - optional internal/agent tools if exposed later, such as `rerank_candidates`
3. Replace inline `ToolAnnotations(...)` in:
   - `src/kindly_web_search_mcp_server/composio_tools.py`
   - `src/kindly_web_search_mcp_server/analytics/tools.py`
4. Keep current snake_case tool names. Do not introduce compatibility aliases.

Acceptance:

- `tests/test_tool_profiles.py` covers Composio and analytics profile behavior.
- Analytics tools keep `openWorldHint=False`.
- Expensive tools keep the rate-limit/middleware classification.
- Default profile remains small and safe.

## Phase 2 - Native Structured Output Schemas

### Problem

Tools return Pydantic model dumps, but clients cannot reliably inspect native MCP output schemas from `tools/list`.

### Work

1. Use FastMCP's current structured output path:
   - Prefer return type annotations where FastMCP can infer the schema.
   - Use `output_schema=` for explicit JSON schema overrides.
   - Do not use `result_type=` on `@mcp.tool`; reserve `result_type` for `ctx.sample()`.
2. Start with:
   - `web_search` -> `WebSearchResponse`
   - `get_content` -> `GetContentResponse`
   - `batch_get_content` -> `BatchGetContentResponse`
   - `youtube_search` -> `YouTubeSearchResponse`
   - `youtube_transcript` -> `YouTubeTranscriptResponse`
   - `academic_search` -> `AcademicSearchResponse`
3. Add schema assertions in tests against FastMCP's in-memory `Client`.

Acceptance:

- Clients can inspect structured output for core tools.
- Tool return payloads remain backward compatible as dict-like JSON.
- Error responses still use the existing structured error contract.

## Phase 3 - Expand MCP Resources And Prompts

### Existing State

These existing entries are baseline only for this phase.
They are not "done" for Phase 3; they remain TODO to refine and expand.

The server already exposes:

- `status://providers`
- `status://features`
- `docs://workflow`
- `plan_web_research`
- `evaluate_web_results`
- `research_gap_analysis`
- `suggest_tool`

### Work

Refine and expand, do not replace:

1. Add analytics resources:
   - `analytics://reports/{report_name}`
   - `analytics://schema`
   - `analytics://candidate-survival`
   - `analytics://cache-hit-rates`
2. Add cache/session resources:
   - `cache://stats`
   - `session://current`
   - `settings://public`
3. Add prompt catalog entries:
   - `research_workflow`
   - `academic_deep_dive`
   - `video_research`
   - `source_triage`
4. Keep prompt messages compatible with FastMCP `Message` roles: `user` and `assistant` only.

Acceptance:

- `list_resources` and `list_prompts` expose the new entries.
- Resources never return secrets.
- Analytics resources reuse deterministic report/query code where possible.

## Phase 4 - Voyage Rerank Instruction Steering

### Problem

`rerank/voyage.py` uses `rerank-2.5` but does not pass task instructions.

### Work

1. Add optional `instruction: str | None` to the Voyage engine path.
2. Derive a default instruction from:
   - `research_goal` when available.
   - query type / classifier output where available.
   - a conservative fallback: prioritize authoritative, specific, recent sources without hiding relevant older canonical docs.
3. Emit instruction presence and length in rerank observability, not full user text if it risks sensitive leakage.

Acceptance:

- Existing rerank behavior is preserved when no instruction is provided.
- Tests cover payload construction and fallback preservation.
- Eval harness can compare instruction vs no-instruction ranking.

## Phase 5 - Entity And Result-Memory Activation Policy

### Existing State

- Result memory defaults to enabled.
- Entity extraction and entity-overlap rerank scoring are opt-in.
- Qdrant result memory is a candidate source for merge/rerank, not a semantic cache that short-circuits `web_search`.

### Work

1. Keep GLiNER/entity extraction opt-in by default unless startup/runtime cost is measured and acceptable.
2. Put GLiNER2 behind a first-class Cloud Run inference service before depending on it in the hot path:
   - primary sources checked on 2026-06-05:
     - GLiNER2 repo/model docs: <https://github.com/fastino-ai/GLiNER2>
     - HF model card: <https://hf.co/fastino/gliner2-base-v1>
     - HF large model card: <https://hf.co/fastino/gliner2-large-v1>
     - GLiNER2 Docker pattern: <https://docs.vast.ai/examples/ner/gliner2>
     - Cloud Run GPU docs: <https://docs.cloud.google.com/run/docs/configuring/services/gpu>
     - Cloud Run GPU best practices: <https://docs.cloud.google.com/run/docs/configuring/services/gpu-best-practices>
   - live Cloud Run service identifier as of 2026-06-05: `functiongemma-classifier` in project `magdalenka-ecosystem`, region `us-central1`, latest ready revision `functiongemma-classifier-00005-cd7`, URL `https://functiongemma-classifier-373347358125.us-central1.run.app`
   - start with `fastino/gliner2-base-v1` for latency/cost measurement; promote to `fastino/gliner2-large-v1` only if evals show material quality lift
   - expose a small FastAPI surface: `/health`, `/extract`, `/classify`, `/extract_structured`, `/extract_combined`
   - container must lazy-load once per instance and reuse the model; no per-request downloads
   - package model weights into the image or mount/cache them explicitly; avoid cold-start download from Hugging Face in production
   - CPU Cloud Run is acceptable for low QPS because GLiNER2 is CPU-first; use Cloud Run GPU only if latency SLO or throughput needs it
   - for GPU, target one NVIDIA L4 instance first; Cloud Run L4 provides 24 GB VRAM, so GLiNER2 base/large should fit comfortably
   - tune concurrency empirically; begin conservative (`concurrency=1-4`) because model inference is CPU/GPU-bound
   - enable startup CPU boost and consider `min-instances=1` only if interactive latency matters more than idle cost
3. Add a GLiNER2 unified-schema pilot based on the `fastino/gliner2-official-demo` Space:
   - source pattern: <https://hf.co/spaces/fastino/gliner2-official-demo>
   - one schema text can combine `<entities>`, `<classification>`, and `<structures>`
   - target modules: `entity/default_schema.py`, `entity/gliner_client.py`, `entity/models.py`, and a new `entity/unified_schema.py` if the parser does not fit existing files
   - query/content classification output must be compared against the current FunctionGemma classifier before removing the FunctionGemma path
   - preserve lazy loading; do not preload GLiNER2 at import or server startup
4. Add a documented "personal enhanced" profile that enables:
   - `KINDLY_ENTITY_EXTRACTION_ENABLED=true`
   - `KINDLY_RERANK_ENTITY_OVERLAP_ENABLED=true`
   - current result-memory settings
5. Add a profile/status resource section explaining whether entity/result memory is active.
6. Do not silently enable heavy optional model loading for every user.
7. Document Qdrant semantics explicitly:
   - Qdrant lookup contributes historical candidates into the same RRF/rerank pool as live providers
   - Qdrant must never return a whole cached `web_search` response on similarity hit
   - exact query LRU may still return exact same-query responses; that is separate from Qdrant result memory

Acceptance:

- Clear env/profile toggle documented.
- No import-time model loading.
- Entity failures remain non-fatal when enabled.
- GLiNER2 Cloud Run smoke test passes before any plan item depends on remote GLiNER2 inference.
- Unified schema pilot returns entities + classification + structured fields from one model call, with latency/quality compared against current NER + FunctionGemma.
- Qdrant result-memory tests prove memory hits are injected as candidates before merge/rerank, not returned as final cached hits.

## Phase 6 - LLM Fan-Out And Parallel Query Decomposition

### Problem

Current query decomposition metadata exists, but multi-hop branches are not treated as independently searchable branches end-to-end. FunctionGemma is also the wrong primary tool for generative fan-out if GLiNER2 owns extraction/classification and LLMs own rewrite generation.

### Work

1. Replace the prior FunctionGemma fan-out proposal with an LLM-generated fan-out plan:
   - source pattern: <https://hf.co/spaces/ryanshelley/ai_query_faning>
   - one structured JSON call generates 8-10 branch queries plus a compact reasoning summary
   - branch categories: `related`, `implicit`, `comparative`, `reformulation`, `entity_expanded`
   - normalized target modules: `search/query_rewrite.py`, `search/query_rewrite_plan.py`, `search/query_decomposition.py`, and `search/orchestrator.py`
   - candidate LLM backends should reuse existing explicit LLM paths, not GLiNER2: Pollinations/Mistral-style rewrite backend, Gemini when configured, or host sampling when explicitly selected
2. Use GLiNER2 as a non-generative guide once the Cloud Run service is available:
   - extract entities, topics, intent labels, query constraints, and structured slots
   - pass those as bounded inputs to the LLM fan-out prompt
   - do not ask GLiNER2 to invent rewritten queries; it is not the generative component
3. Demote FunctionGemma to one of:
   - legacy classifier baseline during evaluation
   - fallback only when GLiNER2 service is unavailable and no LLM classification is configured
   - removal candidate once GLiNER2 + LLM rewrite evals pass
4. Extend the decomposition schema to include branch controls:
   - `query`
   - `branch_type`
   - `weight`
   - `must_keep_terms`
   - `max_results`
   - `reason`
5. Add a branch execution primitive outside `server.py`.
6. Run decomposed branch searches with bounded `asyncio.gather` and an internal semaphore:
   - generate up to 8-10 branches
   - dispatch at most `KINDLY_DECOMPOSITION_MAX_CONCURRENCY` at once
   - cap total provider calls so fan-out cannot multiply every provider indefinitely
7. Merge branch results with weighted RRF.
8. Persist branch metadata into existing DuckDB event/view shape:
   - `branch_index`
   - `branch_query`
   - `branch_type`
   - `branch_weight`
   - `branch_latency_ms`
   - `branch_result_count`

Implementation note:
- The executor scaffold is already in code: `search/branch_executor.py` now runs bounded concurrent branches, `search/result_memory_pipeline.py` keeps Qdrant candidate injection/persistence separate from merge logic, `search/finalize_results.py` holds post-processing, and the orchestrator is now under the 300-line module cap. The structured LLM fan-out generator now lives in `search/query_fanout.py`, `search/query_fanout_client.py`, and `search/query_rewrite_branching.py`.

Acceptance:

- Comparative/multi-hop fixture cases improve recall without excessive latency.
- Branch execution has a concurrency cap.
- Existing single-query path is unchanged when decomposition is off.
- LLM fan-out fails closed to the current single-query/rewrite path when structured JSON validation fails.
- FunctionGemma is not listed as the primary fan-out generator.

## Phase 7 - Search To Fetch To Memory Feedback Loop

### Problem

Result memory stores candidate-level evidence and reinjects candidates into merge/rerank, but high-quality `get_content` reads are not fed back as stronger known-good candidate evidence.

### Work

1. When `get_content` succeeds, optionally store:
   - URL
   - content hash
   - quality score
   - source type
   - fetch backend
   - associated query identity when available
2. Use this as a known-good boost in future result-memory candidates.
3. Keep the search path semantics explicit:
   - exact LRU cache may answer exact repeated queries
   - Qdrant similarity hits only add candidates to the provider mix
   - no semantic-cache response substitution is allowed
4. Avoid hidden automatic fetches from `web_search`.

Acceptance:

- Manual search -> fetch -> future search loop improves known-good URL recall.
- No new fetches occur without tool caller control.
- Result-memory store remains best-effort and non-fatal.
- Similar Qdrant hits are visible as `providers=["result_memory"]` candidates and pass through merge/rerank.

## Phase 8 - Batch Progress And Fetch Caching

### Work

1. Add optional progress callback or `Context` adapter to `content/batch_orchestrator.py`.
2. Emit per-URL progress from `batch_get_content`.
3. Cache `discover_links` results with a short TTL where cache key includes:
   - URL
   - include_external
   - include_sitemap
   - max_links
4. Add an academic-search exact LRU keyed by query + filters + sources.
5. Add CDX-backed Wayback fallback for terminal direct-fetch failures:
   - source pattern: <https://hf.co/spaces/radhey234/waybackdomainanalyser>
   - the Space demonstrates Wayback CDX lookup plus archive URL fetch; adapt the pattern, do not copy demo credentials or unrelated OpenRouter analysis code
   - target modules: `content/safe_fetch.py`, `content/fetch_pipeline.py`, and `content/artifact.py`
   - retry only for direct-fetch terminal `403`, `404`, and `410` by default
   - prefer latest valid CDX snapshot URL `https://web.archive.org/web/{timestamp}/{original}` over blind `https://web.archive.org/web/{url}` when CDX is available
   - record `fetch_backend="wayback"` and `fetched_url` in the returned artifact metadata
6. Add a lightweight content-quality scorer inspired by `WordLift/content-evaluation-ai`:
   - source pattern: <https://hf.co/spaces/WordLift/content-evaluation-ai>
   - new implementation dependency candidate: `textstat`
   - target modules: new `content/quality.py`, `content/fetch_pipeline.py`, `agent/content_tools.py`, and `rerank/core.py` only when quality metadata is present
   - score axes: purpose/query match, source/accuracy heuristics, depth, readability grade, SEO/keyword coverage, and extracted-entity coverage
   - run after crawl/fetch; do not make normal `web_search` secretly fetch pages just to compute quality
   - for explicit deep/agentic crawled flows, drop or downweight low-quality pages before reranking when `KINDLY_CONTENT_QUALITY_FILTER_ENABLED=true`
   - default to conservative downweighting rather than hard dropping official docs or canonical references with poor readability

Acceptance:

- Long batch fetches stream useful progress.
- Repeated link discovery avoids redundant page fetches.
- Academic cache does not mask provider errors indefinitely; TTL required.
- 403/404/410 content reads can recover from Wayback when enabled, with provenance visible in metadata.
- Quality filtering is opt-in, observable, and does not introduce hidden fetches behind `web_search`.

## Phase 9 - Elicitation And Sampling

### Elicitation

Use `ctx.elicit()` sparingly:

- Confirm expensive `grok_search` / `perplexity_search` when session budgets are exceeded.
- Ask for a missing `research_goal` only when it materially changes tool behavior.

Acceptance:

- Never blocks clients that do not support elicitation without a clear fallback error.
- Middleware still enforces hard rate/cost limits.

### Sampling

Add an optional host-LLM summary backend:

- Keep current Chutes summary path as an explicit backend.
- Add `summary_backend="sampling"` only when `ctx.sample()` is available.
- Use `result_type` only inside `ctx.sample()` calls if typed outputs are needed.

Acceptance:

- Summary backend is caller-visible.
- No hidden host-LLM calls when `summary_mode="none"`.

## Phase 10 - Remote/Long-Lived Runtime

### Work

1. Document and test `streamable-http` runtime with current CLI flags.
2. Add a Docker Compose profile for a long-lived local/remote MCP server.
3. Evaluate `fastmcp-remote` only if it improves client compatibility over native HTTP transport.

Acceptance:

- Stdio remains the default local launcher.
- HTTP mode is documented for cache/result-memory reuse across sessions.
- Secrets stay in env vars, not committed config.

## Phase 11 - Verification And Documentation

For every implemented phase:

1. Add focused tests first or alongside the change.
2. Run the smallest meaningful verification slice.
3. Update `CHANGELOG.md`.
4. Update `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, or `docs/TESTING.md` if behavior/config changes.
5. Update `.agent/CONTINUITY.md` when the phase materially changes project state.

## Priority Order

1. FastMCP 3.4.0 bump and focused compatibility checks.
2. Unified tool catalog coverage for Composio + analytics.
3. Structured output schemas for core tools.
4. Analytics/cache/session resources.
5. Voyage instruction steering.
6. GLiNER2 Cloud Run service readiness and smoke tests.
7. GLiNER2 unified-schema pilot for entity/classification/structure extraction.
8. LLM fan-out + bounded scatter-gather decomposition guided by GLiNER2 outputs.
9. Batch progress and `discover_links` cache.
10. Wayback fallback for terminal fetch failures.
11. Lightweight content-quality scorer and opt-in pre-rerank filtering for crawled flows.
12. Prompt catalog expansion.
13. Elicitation for expensive tools.
14. Sampling summary backend.
15. Search->fetch->memory feedback.
16. Remote/long-lived runtime profile.

## Non-Goals

- No broad security/auth/multi-tenant hardening in this plan.
- No hidden internal deep-research loop behind `web_search`.
- No default heavy model loading without measured startup/runtime evidence.
- No compatibility aliases for old non-snake-case tool names.
- No committed credentials or demo API keys from public HF Spaces.
- No automatic post-crawl quality filtering on normal lightweight `web_search`.
- No Qdrant/semantic similarity hit may bypass live providers and return a whole cached response.
- No FunctionGemma-generated query fan-out as the primary rewrite strategy; generative rewrite/fan-out belongs to LLM backends.
- No deleting the source TODO files until this plan is executed or explicitly archived.
