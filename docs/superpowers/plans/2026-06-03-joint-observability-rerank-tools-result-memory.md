# Joint Observability, Rerank, Tool Surface, and Result Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four input plans as one measured refactor: coherent observability/eval, smaller FastMCP tool exposure, benchmarked reranking, and entity-aware result memory.

**Architecture:** Build the observability/eval substrate first so every later behavioral change is measured. Then add FastMCP profile/tag visibility and rerank engine abstraction. Finally replace LanceDB binary caches with explicit exact LRU, DuckDB page cache, and Qdrant result memory enriched by GLiNER2 entities.

**Tech Stack:** Python 3.13, FastMCP 3.2.4, DuckDB, Grafana/MotherDuck dashboards, Langfuse judge traces, mcpevals, Voyage/Jina/current rerank providers, FlashRank or FastEmbed local rerank baseline, Qdrant local mode, GLiNER2 optional extra.

---

## Evidence And Cross-Reference Audit

Current timestamp for recency-sensitive checks: `2026-06-03T14:41:08.3655188+02:00`.

Input plans reviewed:

- `plans/TODO/coherent-observability-eval-plan-2026-06-03.md`
- `plans/TODO/coherent-fastmcp-tools-plan-2026-06-03.md`
- `plans/TODO/coherent-reranking-plan-2026-06-03.md`
- `plans/TODO/entity-aware-result-memory-plan.md`

Local code facts used:

- `server.py` currently performs exact cache lookup, semantic cache lookup, provider search, exact cache write, and semantic cache write in the `web_search` path.
- `server.py` also uses semantic cache in `academic_search`; deleting semantic cache must update both tool paths.
- `search/merge.py` already supports `list_weights`, so result-memory candidates can enter as a lower-weight virtual result list.
- `rerank/core.py` already preserves merged candidate order when all providers fail and emits rerank telemetry through existing helpers.
- `analytics/evals.py` already defines eval tables. The eval plan should extend or reshape that module, not add a disconnected schema.
- `utils/observability.py` persists only selected event prefixes. New `rerank.*`, `entity.*`, `result_memory.*`, and `eval.*` events require an explicit whitelist update or they will be visible in logs but absent from DuckDB analytics.
- `pyproject.toml` still depends on `lancedb>=0.25.0`; `uv.lock` resolves FastMCP 3.2.4 and LanceDB 0.30.2.

Docs-backed facts used:

- FastMCP 3.2.4 supports tool tags, annotations, allowlist visibility such as `mcp.enable(tags={"safe_default"}, only=True)`, tag hiding such as `mcp.disable(tags={"experimental"})`, `ResourcesAsTools`, `PromptsAsTools`, `RegexSearchTransform`, and experimental `CodeMode`.
- Hugging Face lists `fastino/gliner2-base-v1` with library `gliner2`; PyPI shows `gliner2` with `GLiNER2.from_pretrained("fastino/gliner2-base-v1")`.
- Qdrant Python client supports local in-memory and local persistent modes through `QdrantClient(":memory:")` and `QdrantClient(path=".kindly/result_memory")`.
- PyPI confirms `mcpevals` is the current package name for the `mcp-eval` runner.

## Compatibility And Failure Policy

The user explicitly requested no backward compatibility. This plan therefore removes legacy compatibility layers instead of preserving old behavior.

Decisions:

- Delete LanceDB-backed semantic cache files and settings when result memory lands.
- Do not keep aliases for `KINDLY_SEMANTIC_CACHE_ENABLED`, `KINDLY_SEMANTIC_CACHE_MIN_SCORE`, or `KINDLY_LANCEDB_DIR`.
- Do not keep `cache_hit="semantic"` as a live response path.
- Do not add PromptsAsTools or ResourcesAsTools wrappers as a compatibility feature in the first implementation. Native prompts/resources stay available. Wrappers require a future eval-proven reason.
- Do not add empty `try/except` blocks or `except Exception: pass`.
- New optional integrations must either be disabled by settings before use or fail loudly in focused tests. If production degradation is desired later, it must emit an error event with `failure_mode`, `retryable`, and `component`, and have an explicit setting.

## Contradictions Resolved

- **Cache observability vs result memory:** The observability plan asks for exact/semantic/page cache panels. The entity-memory plan removes semantic cache. Joint resolution: P0 dashboards show exact LRU, page cache, and result-memory lookup/injection/survival metrics. Semantic cache panels are not added.
- **Rerank plan excludes GLiNER hot path while entity plan adds entity rerank features:** Joint resolution: rerank engine abstraction and eval harness land before entity-overlap blending. Entity-overlap is measured as a candidate feature, not mixed into rerank before baseline metrics exist.
- **FastMCP compatibility wrappers vs no backward compatibility:** Joint resolution: tags, profiles, and search transform are in scope. Prompts/resources-as-tools compatibility wrappers are removed from the base plan.
- **Eval tables already exist vs plan says add eval tables:** Joint resolution: reshape `analytics/evals.py` into the canonical schema and add missing candidate/tool-call/judge/failure tables there.
- **Entity plan uses fail-open examples:** Joint resolution: replace fail-open examples with explicit disabled state, explicit error events, and failing tests for enabled-but-broken integrations.
- **Storage migration says page cache can share analytics DuckDB:** Joint resolution: page cache gets its own DuckDB file and lock module to avoid competing with append-only analytics writes.
- **Result memory default-on vs unmeasured new dependency:** Joint resolution: result memory is enabled only after storage tests, Qdrant local tests, and candidate-injection evals pass. The final config may set it on by default after the phase is complete; implementation does not assume legacy fallback.

## File Structure

Create:

- `src/kindly_web_search_mcp_server/observability/events.py` - event-name constants and allowed DuckDB prefixes.
- `src/kindly_web_search_mcp_server/observability/stages.py` - run-stage names and timeline helpers.
- `src/kindly_web_search_mcp_server/evals/__init__.py` - eval package export.
- `src/kindly_web_search_mcp_server/evals/cases.py` - eval case models.
- `src/kindly_web_search_mcp_server/evals/metrics.py` - deterministic metrics.
- `src/kindly_web_search_mcp_server/evals/judges.py` - DeepEval-style judge prompts and JSON parsing.
- `src/kindly_web_search_mcp_server/evals/runner.py` - mcpevals runner adapter.
- `src/kindly_web_search_mcp_server/tools/catalog.py` - canonical tool profile and tag catalog.
- `src/kindly_web_search_mcp_server/tools/profiles.py` - FastMCP enable/disable application.
- `src/kindly_web_search_mcp_server/rerank/models.py` - rerank candidate/result/engine models.
- `src/kindly_web_search_mcp_server/rerank/engines.py` - provider engine implementations and registry.
- `src/kindly_web_search_mcp_server/rerank/policy.py` - bypass and eligibility policy.
- `src/kindly_web_search_mcp_server/cache/exact_lru.py` - in-memory exact query LRU.
- `src/kindly_web_search_mcp_server/cache/page_duckdb.py` - DuckDB-backed page cache.
- `src/kindly_web_search_mcp_server/cache/result_memory.py` - Qdrant-backed result memory.
- `src/kindly_web_search_mcp_server/entity/__init__.py` - entity package export.
- `src/kindly_web_search_mcp_server/entity/models.py` - `EntitySpan` and entity payload models.
- `src/kindly_web_search_mcp_server/entity/default_schema.py` - coding/web extraction labels.
- `src/kindly_web_search_mcp_server/entity/chunk.py` - chunking with offset preservation.
- `src/kindly_web_search_mcp_server/entity/postprocess.py` - validation, dedup, overlap merge, normalization.
- `src/kindly_web_search_mcp_server/entity/gliner_client.py` - lazy GLiNER2 extraction client.
- `src/kindly_web_search_mcp_server/entity/overlap.py` - entity overlap scoring.

Modify:

- `src/kindly_web_search_mcp_server/server.py`
- `src/kindly_web_search_mcp_server/settings.py`
- `src/kindly_web_search_mcp_server/models.py`
- `src/kindly_web_search_mcp_server/utils/observability.py`
- `src/kindly_web_search_mcp_server/analytics/duckdb_store.py`
- `src/kindly_web_search_mcp_server/analytics/evals.py`
- `src/kindly_web_search_mcp_server/search/orchestrator.py`
- `src/kindly_web_search_mcp_server/search/merge.py`
- `src/kindly_web_search_mcp_server/search/query_policy.py`
- `src/kindly_web_search_mcp_server/search/query_rewrite.py`
- `src/kindly_web_search_mcp_server/cache/__init__.py`
- `src/kindly_web_search_mcp_server/cache/query_cache.py`
- `src/kindly_web_search_mcp_server/cache/page_cache.py`
- `src/kindly_web_search_mcp_server/cache/content_type.py`
- `src/kindly_web_search_mcp_server/rerank/core.py`
- `src/kindly_web_search_mcp_server/rerank/observability.py`
- `src/kindly_web_search_mcp_server/telemetry.py`
- `grafana/dashboards/kindly-mcp-quality-dashboard.json`
- `docs/ARCHITECTURE.md`
- `docs/CONFIGURATION.md`
- `docs/TESTING.md`
- `CHANGELOG.md`
- `.agent/CONTINUITY.md`
- `pyproject.toml`

Delete:

- `src/kindly_web_search_mcp_server/cache/store.py`
- `src/kindly_web_search_mcp_server/cache/semantic_cache.py`
- `src/kindly_web_search_mcp_server/cache/schema.py`

## Phase 0: Baseline And Audit Guardrails

### Task 0.1: Capture Current Behavior

**Files:**
- Modify: `.agent/CONTINUITY.md`
- Read: `plans/TODO/*.md`
- Read: `src/kindly_web_search_mcp_server/server.py`
- Read: `src/kindly_web_search_mcp_server/search/orchestrator.py`
- Read: `src/kindly_web_search_mcp_server/rerank/core.py`

- [ ] **Step 1: Run baseline tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_search_orchestrator.py tests/test_rerank_core.py tests/test_duckdb_analytics.py -q
```

Expected: existing suite passes or failures are captured with exact test names before edits.

- [ ] **Step 2: Run baseline lint on targeted files**

Run:

```powershell
& .\.venv\Scripts\python.exe -m ruff check src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/search/orchestrator.py src/kindly_web_search_mcp_server/rerank/core.py src/kindly_web_search_mcp_server/analytics/duckdb_store.py
```

Expected: clean or unrelated existing findings recorded before edits.

- [ ] **Step 3: Verify MCP startup before refactor**

Run:

```powershell
& .\.venv\Scripts\kindly-web-search.exe --help
```

Expected: command exits `0` and prints CLI usage.

- [ ] **Step 4: Commit baseline note**

Run:

```powershell
git add .agent/CONTINUITY.md
git commit -m "docs: record joint refactor baseline"
```

Expected: commit records only baseline notes.

## Phase 1: Observability And Eval Substrate

### Task 1.1: Canonical Event Names And Persistence Prefixes

**Files:**
- Create: `src/kindly_web_search_mcp_server/observability/events.py`
- Modify: `src/kindly_web_search_mcp_server/utils/observability.py`
- Test: `tests/test_observability_events.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `rerank.`, `entity.`, `result_memory.`, and `eval.` events persist through `_persist_analytics_event`, while unrelated events do not.

```python
import logging

from kindly_web_search_mcp_server.utils import observability


def test_new_quality_event_prefixes_are_persisted(monkeypatch):
    captured = []

    def fake_append_event(event, payload):
        captured.append((event, payload))

    monkeypatch.setattr(
        "kindly_web_search_mcp_server.analytics.duckdb_store.append_event",
        fake_append_event,
    )

    logger = logging.getLogger("test")
    observability.emit_observability_event(logger, "rerank.eligibility", query="q")
    observability.emit_observability_event(logger, "entity.query_extracted", query="q")
    observability.emit_observability_event(logger, "result_memory.lookup", query="q")
    observability.emit_observability_event(logger, "eval.case_completed", query="q")

    assert [event for event, _ in captured] == [
        "rerank.eligibility",
        "entity.query_extracted",
        "result_memory.lookup",
        "eval.case_completed",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_observability_events.py -q
```

Expected: fails because new prefixes are not persisted.

- [ ] **Step 3: Implement event constants and whitelist**

Create `observability/events.py` with:

```python
PERSISTED_EVENT_PREFIXES = (
    "query.rewrite.",
    "search.",
    "provider.",
    "tool.",
    "agentic.",
    "content.",
    "middleware.",
    "session.",
    "rerank.",
    "entity.",
    "result_memory.",
    "eval.",
)
```

Update `utils/observability.py` to import `PERSISTED_EVENT_PREFIXES` and use it inside `_persist_analytics_event`.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_observability_events.py tests/test_duckdb_analytics.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/observability/events.py src/kindly_web_search_mcp_server/utils/observability.py tests/test_observability_events.py
git commit -m "feat: persist quality event prefixes"
```

### Task 1.2: Eval Schema Reconciliation

**Files:**
- Modify: `src/kindly_web_search_mcp_server/analytics/evals.py`
- Create: `src/kindly_web_search_mcp_server/evals/cases.py`
- Create: `src/kindly_web_search_mcp_server/evals/metrics.py`
- Test: `tests/test_eval_schema.py`

- [ ] **Step 1: Write schema tests**

Assert that existing eval tables remain named under `analytics/evals.py`, and missing tables are added there: `eval_tool_calls`, `eval_candidate_sets`, `eval_scores`, `eval_judge_calls`, and `eval_failures`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_eval_schema.py -q
```

Expected: fails because the additional eval tables are absent.

- [ ] **Step 3: Extend `build_eval_table_sql`**

Add the five missing tables with `eval_run_id`, `eval_case_id`, `recorded_at`, `run_key`, `payload_json`, and specific fields for tool name, candidates, metric name, judge model, and failure code.

- [ ] **Step 4: Add deterministic metric models**

Create `evals/cases.py` with Pydantic models for `EvalCase`, `ExpectedToolCall`, and `CandidateSet`.

Create `evals/metrics.py` with deterministic functions for:

- `expected_tool_called`
- `forbidden_tool_not_called`
- `latency_within_budget`
- `mrr_at_k`
- `ndcg_at_k`
- `top_k_domain_hit`

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_eval_schema.py tests/test_duckdb_analytics.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/analytics/evals.py src/kindly_web_search_mcp_server/evals tests/test_eval_schema.py
git commit -m "feat: reconcile eval analytics schema"
```

## Phase 2: FastMCP Tool Profiles And Search

### Task 2.1: Tool Catalog, Tags, And Profiles

**Files:**
- Create: `src/kindly_web_search_mcp_server/tools/catalog.py`
- Create: `src/kindly_web_search_mcp_server/tools/profiles.py`
- Modify: `src/kindly_web_search_mcp_server/server.py`
- Modify: `src/kindly_web_search_mcp_server/agent/mcp.py`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Test: `tests/test_tool_profiles.py`

- [ ] **Step 1: Write profile tests**

Test expected visible tools for `default`, `research`, `media`, `diagnostic`, `experimental`, and `full`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_tool_profiles.py -q
```

Expected: fails because no profile catalog exists.

- [ ] **Step 3: Add catalog**

Create a catalog keyed by tool name. Each entry has `profile`, `tags`, `expensive`, and `experimental`.

Required default profile:

- `web_search`
- `get_content`
- `batch_get_content`
- `discover_links`

Research profile adds:

- `gemini_search`
- `perplexity_search`
- `academic_search`
- `grok_search`
- `agentic_web_research`

Media profile:

- `youtube_search`
- `youtube_transcript`

- [ ] **Step 4: Add settings**

Add `tool_profile` with allowed values `default|research|media|diagnostic|experimental|full`. Do not add legacy aliases.

- [ ] **Step 5: Apply FastMCP visibility**

Use FastMCP 3.2.4 allowlist and hide APIs after tool registration. For example, use `mcp.enable(tags={"safe_default"}, only=True)` for the default profile and `mcp.disable(tags={"experimental"})` outside the experimental profile. Register tool decorators with concrete tag sets and `ToolAnnotations` objects instead of maintaining a parallel private router.

- [ ] **Step 6: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_tool_profiles.py tests/test_tool_descriptions.py tests/test_server.py -q
```

Expected: profile tests pass and existing tool schemas remain valid for visible tools.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/tools src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/agent/mcp.py src/kindly_web_search_mcp_server/settings.py tests/test_tool_profiles.py
git commit -m "feat: add FastMCP tool profiles"
```

### Task 2.2: Tool Search Transform

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Test: `tests/test_tool_search_transform.py`

- [ ] **Step 1: Write tests**

Assert that enabling tool search exposes FastMCP search/call meta-tools and that queries for docs, URL fetch, and YouTube transcript surface the right underlying tools.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_tool_search_transform.py -q
```

Expected: fails because `RegexSearchTransform` is not wired.

- [ ] **Step 3: Add transform behind explicit setting**

Add `KINDLY_TOOL_SEARCH_ENABLED`. When true, add `fastmcp.server.transforms.search.RegexSearchTransform()` after profile selection. Emit `tool_surface.search_enabled` and `tool_surface.profile_applied` events.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_tool_profiles.py tests/test_tool_search_transform.py -q
```

Expected: profile and search-transform tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/settings.py tests/test_tool_search_transform.py
git commit -m "feat: add opt-in FastMCP tool search"
```

## Phase 3: Rerank Engine Abstraction And Bypass Policy

### Task 3.1: Rerank Models And Engines

**Files:**
- Create: `src/kindly_web_search_mcp_server/rerank/models.py`
- Create: `src/kindly_web_search_mcp_server/rerank/engines.py`
- Modify: `src/kindly_web_search_mcp_server/rerank/core.py`
- Test: `tests/test_rerank_engines.py`

- [ ] **Step 1: Write failing tests**

Test that `voyage`, `jina`, `gcp_cloudrun`, and `none` engines return `RerankResult` with ordered indexes, scores, engine id, model id, duration, and failure fields.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_rerank_engines.py -q
```

Expected: fails because engine models do not exist.

- [ ] **Step 3: Create models**

Define `RerankCandidate`, `RerankResult`, and `RerankEngine` protocol. Include `fallback_reason`, `warnings`, and `score_distribution`.

- [ ] **Step 4: Move provider calls into engines**

Wrap current `voyage_rerank`, `jina_rerank`, and `gcp_cloudrun_rerank` calls behind engine classes. Preserve current merged-order behavior only when selected engine is `none` or explicit bypass says skip.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_rerank_engines.py tests/test_rerank_core.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/rerank/models.py src/kindly_web_search_mcp_server/rerank/engines.py src/kindly_web_search_mcp_server/rerank/core.py tests/test_rerank_engines.py
git commit -m "feat: introduce rerank engine abstraction"
```

### Task 3.2: Bypass Policy And Observability

**Files:**
- Create: `src/kindly_web_search_mcp_server/rerank/policy.py`
- Modify: `src/kindly_web_search_mcp_server/rerank/core.py`
- Modify: `src/kindly_web_search_mcp_server/rerank/observability.py`
- Test: `tests/test_rerank_policy.py`

- [ ] **Step 1: Write policy tests**

Test bypass reasons for low candidate count, exact literal, navigational exact-domain match, degraded engine health, and eval-proven harmful query class.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_rerank_policy.py -q
```

Expected: fails because policy module does not exist.

- [ ] **Step 3: Implement policy**

Return a typed decision: `should_rerank`, `reason`, `query_type`, `candidate_count`, and `engine_health`.

- [ ] **Step 4: Emit events**

Emit `rerank.eligibility`, `rerank.engine_selected`, `rerank.completed`, and `rerank.bypassed` through `emit_observability_event`.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_rerank_policy.py tests/test_rerank_core.py tests/test_observability_events.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/rerank/policy.py src/kindly_web_search_mcp_server/rerank/core.py src/kindly_web_search_mcp_server/rerank/observability.py tests/test_rerank_policy.py
git commit -m "feat: add observable rerank bypass policy"
```

## Phase 4: Rerank Eval Harness And Local Baseline

### Task 4.1: Deterministic Rerank Dataset

**Files:**
- Create: `evals/rerank_cases.jsonl`
- Create: `tests/fixtures/rerank_candidates.json`
- Modify: `src/kindly_web_search_mcp_server/evals/metrics.py`
- Test: `tests/test_rerank_eval_metrics.py`

- [ ] **Step 1: Add fixture and tests**

Add at least 10 seed cases in this phase. Grow to 50 before accepting P0 completion.

- [ ] **Step 2: Implement metric calculations**

Metrics: MRR@5, nDCG@10, top-3 domain hit, duplicate URL/domain rate, provider survival, candidate count before/after, latency, timeout, and fallback.

- [ ] **Step 3: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_rerank_eval_metrics.py tests/test_eval_schema.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit**

Run:

```powershell
git add evals/rerank_cases.jsonl tests/fixtures/rerank_candidates.json src/kindly_web_search_mcp_server/evals/metrics.py tests/test_rerank_eval_metrics.py
git commit -m "test: add rerank eval metrics"
```

### Task 4.2: Local Rerank Baseline

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/kindly_web_search_mcp_server/rerank/engines.py`
- Test: `tests/test_local_rerank_engine.py`

- [ ] **Step 1: Verify selected library**

Choose FlashRank or FastEmbed after a local install/import probe. Add only the selected dependency.

- [ ] **Step 2: Write mocked tests**

Mock the selected library and assert it reorders candidates without network access.

- [ ] **Step 3: Add local engine**

Add engine id `local_minilm` with default model `cross-encoder/ms-marco-MiniLM-L6-v2`.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_local_rerank_engine.py tests/test_rerank_engines.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add pyproject.toml uv.lock src/kindly_web_search_mcp_server/rerank/engines.py tests/test_local_rerank_engine.py
git commit -m "feat: add local rerank baseline"
```

## Phase 5: Storage Replacement

### Task 5.1: Exact Query LRU

**Files:**
- Create: `src/kindly_web_search_mcp_server/cache/exact_lru.py`
- Modify: `src/kindly_web_search_mcp_server/cache/query_cache.py`
- Modify: `src/kindly_web_search_mcp_server/cache/__init__.py`
- Test: `tests/test_exact_lru_cache.py`

- [ ] **Step 1: Write LRU tests**

Test deterministic keying, TTL expiry, max-size eviction, and no LanceDB import.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_exact_lru_cache.py -q
```

Expected: fails because LRU implementation is absent.

- [ ] **Step 3: Implement LRU**

Use `collections.OrderedDict`, monotonic timestamps, and a lock. Keep the public `lookup` and `store` method names only where `server.py` still calls them during this phase.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_exact_lru_cache.py tests/test_server.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/cache/exact_lru.py src/kindly_web_search_mcp_server/cache/query_cache.py src/kindly_web_search_mcp_server/cache/__init__.py tests/test_exact_lru_cache.py
git commit -m "feat: replace exact query cache with LRU"
```

### Task 5.2: DuckDB Page Cache

**Files:**
- Create: `src/kindly_web_search_mcp_server/cache/page_duckdb.py`
- Modify: `src/kindly_web_search_mcp_server/cache/page_cache.py`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Test: `tests/test_page_cache_duckdb.py`

- [ ] **Step 1: Write page cache tests**

Test URL-hash lookup, TTL expiry, metadata JSON round trip, and locked writes.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_page_cache_duckdb.py -q
```

Expected: fails because page DuckDB backend is absent.

- [ ] **Step 3: Implement separate DuckDB file**

Add `KINDLY_PAGE_CACHE_DUCKDB_PATH` with default `.kindly/cache/page_cache.duckdb`. Do not write page cache rows into `.kindly/analytics/search_events.duckdb`.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_page_cache_duckdb.py tests/test_page_content_resolver.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/cache/page_duckdb.py src/kindly_web_search_mcp_server/cache/page_cache.py src/kindly_web_search_mcp_server/settings.py tests/test_page_cache_duckdb.py
git commit -m "feat: move page cache to DuckDB"
```

### Task 5.3: Remove Semantic Cache And LanceDB

**Files:**
- Delete: `src/kindly_web_search_mcp_server/cache/store.py`
- Delete: `src/kindly_web_search_mcp_server/cache/semantic_cache.py`
- Delete: `src/kindly_web_search_mcp_server/cache/schema.py`
- Modify: `src/kindly_web_search_mcp_server/server.py`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Modify: `src/kindly_web_search_mcp_server/cache/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_semantic_cache_removed.py`

- [ ] **Step 1: Write removal tests**

Assert no runtime code imports `lancedb`, `SemanticCacheStore`, `get_semantic_cache`, `set_semantic_cache`, `semantic_cache_enabled`, or `lancedb_dir`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_semantic_cache_removed.py -q
```

Expected: fails because semantic cache still exists.

- [ ] **Step 3: Remove semantic cache from `web_search`**

Delete semantic cache lookup/write sections. Exact cache can remain as LRU until result memory lands.

- [ ] **Step 4: Remove semantic cache from `academic_search`**

Delete the second semantic cache lookup/write path around the academic search tool.

- [ ] **Step 5: Remove settings and deps**

Delete `lancedb_dir`, `semantic_cache_enabled`, and `semantic_cache_min_score`. Remove `lancedb` from `pyproject.toml`; regenerate `uv.lock`.

- [ ] **Step 6: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_semantic_cache_removed.py tests/test_server.py tests/test_duckdb_analytics.py -q
```

Expected: all selected tests pass and `rg "lancedb|semantic_cache" src pyproject.toml` returns no runtime references except changelog/docs migration text.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/cache src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/settings.py pyproject.toml uv.lock tests/test_semantic_cache_removed.py
git rm src/kindly_web_search_mcp_server/cache/store.py src/kindly_web_search_mcp_server/cache/semantic_cache.py src/kindly_web_search_mcp_server/cache/schema.py
git commit -m "refactor: remove LanceDB semantic cache"
```

## Phase 6: Entity Extraction Core

### Task 6.1: Entity Models, Schema, Chunking, And Postprocess

**Files:**
- Create: `src/kindly_web_search_mcp_server/entity/models.py`
- Create: `src/kindly_web_search_mcp_server/entity/default_schema.py`
- Create: `src/kindly_web_search_mcp_server/entity/chunk.py`
- Create: `src/kindly_web_search_mcp_server/entity/postprocess.py`
- Test: `tests/test_entity_core.py`

- [ ] **Step 1: Write tests**

Test offset correction, overlap deduplication, version normalization, repo-ref validation, and label schema presence.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_core.py -q
```

Expected: fails because entity package is absent.

- [ ] **Step 3: Implement pure-Python core**

No GLiNER import in these files. Reuse `content/windowing.py` boundary behavior where practical.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_core.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/entity tests/test_entity_core.py
git commit -m "feat: add entity extraction core models"
```

### Task 6.2: Lazy GLiNER2 Client

**Files:**
- Create: `src/kindly_web_search_mcp_server/entity/gliner_client.py`
- Modify: `pyproject.toml`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Test: `tests/test_gliner_client.py`

- [ ] **Step 1: Add optional extra**

Add `gliner2` under an `entity-extraction` optional dependency group. Do not import it at package import time.

- [ ] **Step 2: Write mocked tests**

Mock `GLiNER2.from_pretrained` and assert lazy loading, async `to_thread`, threshold propagation, and normalized `EntitySpan` output.

- [ ] **Step 3: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_gliner_client.py -q
```

Expected: fails because client is absent.

- [ ] **Step 4: Implement client**

Use `fastino/gliner2-base-v1` as default model and expose `KINDLY_ENTITY_EXTRACTION_ENABLED`, `KINDLY_GLINER_MODEL`, and `KINDLY_GLINER_THRESHOLD`.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_gliner_client.py tests/test_entity_core.py -q
```

Expected: all selected tests pass without installing the optional extra.

- [ ] **Step 6: Commit**

Run:

```powershell
git add pyproject.toml uv.lock src/kindly_web_search_mcp_server/entity/gliner_client.py src/kindly_web_search_mcp_server/settings.py tests/test_gliner_client.py
git commit -m "feat: add optional GLiNER2 client"
```

## Phase 7: Qdrant Result Memory

### Task 7.1: Result Memory Store

**Files:**
- Create: `src/kindly_web_search_mcp_server/cache/result_memory.py`
- Modify: `pyproject.toml`
- Modify: `src/kindly_web_search_mcp_server/settings.py`
- Test: `tests/test_result_memory.py`

- [ ] **Step 1: Add dependency**

Add `qdrant-client` to core dependencies. Use local Qdrant mode only in this phase.

- [ ] **Step 2: Write tests**

Test deterministic point IDs, collection-per-embedding-dimension naming, payload round trip, age decay, entity-overlap boost, and no duplicate query-result stores.

- [ ] **Step 3: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_result_memory.py -q
```

Expected: fails because result memory is absent.

- [ ] **Step 4: Implement Qdrant local store**

Use `QdrantClient(":memory:")` when `KINDLY_RESULT_MEMORY_PATH` is empty and `QdrantClient(path=settings.result_memory_path)` when set. Name collections with the embedding model and dimension to avoid vector-size conflicts.

- [ ] **Step 5: Emit events**

Emit `result_memory.lookup`, `result_memory.store`, `result_memory.candidate_injected`, and `result_memory.candidate_survived`.

- [ ] **Step 6: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_result_memory.py tests/test_observability_events.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

Run:

```powershell
git add pyproject.toml uv.lock src/kindly_web_search_mcp_server/cache/result_memory.py src/kindly_web_search_mcp_server/settings.py tests/test_result_memory.py
git commit -m "feat: add Qdrant result memory store"
```

### Task 7.2: Candidate Injection Into Merge

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py`
- Modify: `src/kindly_web_search_mcp_server/search/merge.py`
- Modify: `src/kindly_web_search_mcp_server/models.py`
- Test: `tests/test_result_memory_injection.py`

- [ ] **Step 1: Write injection tests**

Assert memory candidates become a virtual provider list with provider `result_memory`, list weight `settings.result_memory_candidate_weight`, and dedup by canonical URL through existing merge behavior.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_result_memory_injection.py -q
```

Expected: fails because orchestrator does not inject memory candidates.

- [ ] **Step 3: Add candidate conversion**

Convert `CandidateResult` to `WebSearchResult` with `resource_type="cached"` and `providers=["result_memory"]`.

- [ ] **Step 4: Insert before RRF merge**

Append the historical list and matching `list_weights` entry before `merge_search_results`.

- [ ] **Step 5: Store survivors after rerank**

Compare final result URLs against injected memory candidate URLs and emit `candidate_survived` metrics.

- [ ] **Step 6: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_result_memory_injection.py tests/test_search_orchestrator.py tests/test_rerank_core.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/search/orchestrator.py src/kindly_web_search_mcp_server/search/merge.py src/kindly_web_search_mcp_server/models.py tests/test_result_memory_injection.py
git commit -m "feat: inject result memory candidates into merge"
```

## Phase 8: Entity Integration

### Task 8.1: Query Entities And Must-Keep Terms

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/query_policy.py`
- Modify: `src/kindly_web_search_mcp_server/server.py`
- Test: `tests/test_entity_query_policy.py`

- [ ] **Step 1: Write tests**

Assert entities are extracted from the original query before rewrite and augment must-keep terms without deleting regex-preserved literals.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_query_policy.py -q
```

Expected: fails because query entities are not passed through.

- [ ] **Step 3: Implement pass-through**

Extract once in `web_search`, attach to an internal request context, and pass into query policy and orchestrator. Do not extract from rewritten variants.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_query_policy.py tests/test_query_rewrite.py tests/test_search_orchestrator.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/search/query_policy.py src/kindly_web_search_mcp_server/server.py tests/test_entity_query_policy.py
git commit -m "feat: use query entities for must-keep terms"
```

### Task 8.2: Entity Fields In Search And Content Results

**Files:**
- Modify: `src/kindly_web_search_mcp_server/models.py`
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py`
- Modify: `src/kindly_web_search_mcp_server/content/resolver.py`
- Test: `tests/test_entity_response_fields.py`

- [ ] **Step 1: Write tests**

Assert search results and content responses include `entities` only when extraction is enabled, and omit the field when disabled.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_response_fields.py -q
```

Expected: fails because response models do not contain entity fields.

- [ ] **Step 3: Add models**

Add `entities: list[EntitySpan] | None` to search and content response models. Because there is no backward compatibility requirement, update docs and tests to the new schema directly.

- [ ] **Step 4: Add extraction hooks**

Extract entities from final search title/snippet payloads and fetched markdown content when enabled. Emit `entity.search_result_extracted` and `entity.content_extracted`.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_response_fields.py tests/test_server.py tests/test_page_content_resolver.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/models.py src/kindly_web_search_mcp_server/search/orchestrator.py src/kindly_web_search_mcp_server/content/resolver.py tests/test_entity_response_fields.py
git commit -m "feat: expose extracted entities in tool outputs"
```

### Task 8.3: Entity Overlap As Measured Rerank Feature

**Files:**
- Create: `src/kindly_web_search_mcp_server/entity/overlap.py`
- Modify: `src/kindly_web_search_mcp_server/rerank/core.py`
- Modify: `src/kindly_web_search_mcp_server/rerank/policy.py`
- Test: `tests/test_entity_rerank_overlap.py`

- [ ] **Step 1: Write overlap tests**

Test exact match boosts, version mismatch penalties, repo mismatch penalties, and neutral labels.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_rerank_overlap.py -q
```

Expected: fails because overlap scorer does not exist.

- [ ] **Step 3: Implement scorer**

Return a bounded score in `[-1.0, 1.0]`. Keep weights in settings as `KINDLY_RERANK_ENTITY_OVERLAP_WEIGHT`.

- [ ] **Step 4: Blend only when eval flag is enabled**

Use `KINDLY_RERANK_ENTITY_OVERLAP_ENABLED`. Emit entity overlap distribution in rerank summary events.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_entity_rerank_overlap.py tests/test_rerank_core.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add src/kindly_web_search_mcp_server/entity/overlap.py src/kindly_web_search_mcp_server/rerank/core.py src/kindly_web_search_mcp_server/rerank/policy.py tests/test_entity_rerank_overlap.py
git commit -m "feat: add measured entity overlap rerank signal"
```

## Phase 9: Dashboards, Alerts, And Judge Metrics

### Task 9.1: Grafana Panels

**Files:**
- Modify: `grafana/dashboards/kindly-mcp-quality-dashboard.json`
- Modify: `src/kindly_web_search_mcp_server/analytics/evals.py`
- Test: `tests/test_grafana_dashboard_json.py`

- [ ] **Step 1: Write dashboard JSON tests**

Assert dashboard JSON parses and contains panels for tool profile usage, result-memory injection/survival, rerank latency/quality, eval pass rate, and entity extraction latency.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_grafana_dashboard_json.py -q
```

Expected: fails because panels are absent.

- [ ] **Step 3: Add panels against real event names**

Use event names emitted in Phases 1-8. Do not add panels for removed semantic cache events.

- [ ] **Step 4: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_grafana_dashboard_json.py tests/test_duckdb_analytics.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add grafana/dashboards/kindly-mcp-quality-dashboard.json src/kindly_web_search_mcp_server/analytics/evals.py tests/test_grafana_dashboard_json.py
git commit -m "feat: add joint quality dashboard panels"
```

### Task 9.2: Judge Metrics And Langfuse Trace Metadata

**Files:**
- Create: `src/kindly_web_search_mcp_server/evals/judges.py`
- Create: `src/kindly_web_search_mcp_server/evals/runner.py`
- Modify: `pyproject.toml`
- Test: `tests/test_eval_judges.py`

- [ ] **Step 1: Add mcpevals dependency**

Add `mcpevals` as a dev/eval dependency, not a hot-path runtime dependency.

- [ ] **Step 2: Write judge tests**

Test JSON-only parsing for `tool_choice_correct`, `argument_correctness`, `source_usefulness`, and `ranking_quality`.

- [ ] **Step 3: Run test to verify it fails**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_eval_judges.py -q
```

Expected: fails because judges are absent.

- [ ] **Step 4: Implement judge adapter**

Persist deterministic and judge scores to DuckDB eval tables. Send judge call metadata to Langfuse when configured. Never call an LLM judge from a user-facing `web_search` request.

- [ ] **Step 5: Run tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_eval_judges.py tests/test_eval_schema.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add pyproject.toml uv.lock src/kindly_web_search_mcp_server/evals tests/test_eval_judges.py
git commit -m "feat: add MCP eval judge metrics"
```

## Phase 10: Full Verification And Documentation

### Task 10.1: Documentation And Changelog

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/TESTING.md`
- Modify: `CHANGELOG.md`
- Modify: `.agent/CONTINUITY.md`

- [ ] **Step 1: Update architecture docs**

Document the new pipeline:

```text
request -> exact LRU -> entity extraction -> result memory lookup -> rewrite -> provider search -> RRF merge -> rerank policy -> rerank engine -> result memory store -> response
```

- [ ] **Step 2: Update configuration docs**

Remove LanceDB and semantic cache env vars. Add tool profile, tool search, page DuckDB cache, result memory, Qdrant, entity extraction, and rerank entity-overlap settings.

- [ ] **Step 3: Update testing docs**

Add focused verification commands for each phase and the final combined suite.

- [ ] **Step 4: Update changelog**

Add `[Unreleased]` entries under Added, Changed, Fixed, and Removed.

- [ ] **Step 5: Update continuity**

Add concise facts with ISO timestamp and provenance tags.

- [ ] **Step 6: Commit**

Run:

```powershell
git add docs/ARCHITECTURE.md docs/CONFIGURATION.md docs/TESTING.md CHANGELOG.md .agent/CONTINUITY.md
git commit -m "docs: document joint quality refactor"
```

### Task 10.2: Final Verification

**Files:**
- Read: all modified runtime files
- Read: all modified docs

- [ ] **Step 1: Run focused tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_search_orchestrator.py tests/test_rerank_core.py tests/test_duckdb_analytics.py tests/test_tool_profiles.py tests/test_result_memory.py tests/test_entity_core.py tests/test_eval_schema.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run lint**

Run:

```powershell
& .\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: clean or only unrelated pre-existing findings documented.

- [ ] **Step 3: Run import compile**

Run:

```powershell
& .\.venv\Scripts\python.exe -m compileall -q src
```

Expected: exits `0`.

- [ ] **Step 4: Verify MCP startup**

Run:

```powershell
& .\.venv\Scripts\kindly-web-search.exe --help
```

Expected: exits `0`.

- [ ] **Step 5: Verify no removed symbols remain**

Run:

```powershell
rg -n "SemanticCacheStore|get_semantic_cache|set_semantic_cache|semantic_cache_enabled|semantic_cache_min_score|lancedb_dir|import lancedb" src pyproject.toml
```

Expected: no runtime matches.

- [ ] **Step 6: Commit verification fixes**

Run:

```powershell
git status --short
git add -A
git commit -m "test: verify joint quality refactor"
```

Expected: no uncommitted implementation or documentation changes remain after the commit.

## Acceptance Criteria

The joint plan is complete when:

- FastMCP tools have tags, profiles, and opt-in tool search verified by client-level tests.
- Prompts/resources compatibility wrappers are not added in the base implementation.
- Rerank engine abstraction supports `none`, current public providers, and a measured local baseline.
- Rerank bypass decisions are observable.
- Eval tables and deterministic metrics exist in the existing analytics module.
- Judge metrics persist scores to DuckDB and trace judge calls in Langfuse outside user-facing requests.
- LanceDB semantic cache is removed from runtime code, dependencies, settings, and status output.
- Exact query cache is an in-memory LRU.
- Page cache uses a separate DuckDB file.
- Result memory uses Qdrant local mode and injects lower-weight historical candidates into RRF.
- Entity extraction is optional, lazy-loaded, observable, and has no silent failure path.
- Result-memory utilization, candidate survival, entity extraction, rerank quality, tool-choice, and eval pass-rate dashboards use real emitted event names.
- Docs, changelog, and continuity reflect the new architecture and removed settings.

## Execution Recommendation

Use subagent-driven development for this plan. The phase boundaries are independent enough for fresh workers, but each phase must end with review and focused verification before the next phase starts.
