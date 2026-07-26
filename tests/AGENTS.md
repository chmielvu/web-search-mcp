# AGENTS.md - Tests

Runtime regression suite (130+ test files).

## Test Organization

| Slice | Test Files |
|---|---|
| Server & tools | `test_server.py`, `test_tool_descriptions.py`, `test_tool_profiles.py` |
| Search pipeline | `test_search_service.py`, `test_search_contracts.py`, `test_search_ranking.py`, `test_search_planning_why.py`, `test_search_merge_cache.py` |
| Content | `test_content_*.py`, `test_page_content_resolver.py`, `test_sitemap.py` |
| Rerank | `test_rerank_core.py`, `test_rerank_bi_encoder.py`, `test_rerank_llm.py`, `test_bm25_rerank.py` |
| Analytics | `test_analytics_*.py`, `test_pipeline_tables.py`, `test_search_quality_scores.py`, `test_judges_facets.py` |
| Cache | `test_exact_lru_cache.py`, `test_page_cache_duckdb.py` |
| CLI | `tests/cli/` subdirectory |
| YouTube | `test_youtube*.py`, `test_youtube_api.py` |
| Providers | `test_provider_registry.py`, `test_brave_providers.py`, `test_tavily_unit.py`, `test_ddg_unit.py`, `test_langsearch_provider.py` |
| A/B Testing | `test_ab_*.py` |
| LLM | `test_llm_router.py`, `test_prompt_registry.py` |
| Middleware | `test_middleware*.py`, `test_agent_steering_middleware.py` |
| Misc | `test_training_jsonl.py`, `test_entity_*.py`, `test_hf_inference_embeddings.py`, `test_qdrant_search.py` |

## Running Tests

```bash
# Full suite
uv run pytest

# Subsystem slices
uv run pytest tests/test_server.py tests/test_search_orchestrator.py
uv run pytest tests/test_cli*.py
uv run pytest tests/test_rerank_core.py
uv run pytest tests/test_analytics_*.py
uv run pytest tests/test_content_*.py
```

## Conventions

- Patch under `kindly_web_search_mcp_server.*` namespace unless testing the standalone classifier service.
- Use `AsyncMock` and `unittest.IsolatedAsyncioTestCase` for async paths.
- Keep reusable data in `tests/fixtures/`.
- Prefer focused slices when validating a subsystem change.
- Analytics lifecycle tests use temporary DuckDB files and `drain_duckdb_writes`; tool telemetry assertions patch the typed `insert_tool_call_event` seam rather than the removed generic event sink.
- `conftest.py` patches `SEARXNG_BASE_URL` and `TAVILY_API_KEY` for deterministic unit tests.
