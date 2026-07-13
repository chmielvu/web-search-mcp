# AGENTS.md - Tests

This directory holds the runtime regression suite for the current tree.

## Main Test Slices

- Server and tool contracts: `test_server.py`, `test_tool_descriptions.py`,
  `test_public_output_serialization.py`, `test_observability_flow.py`
- Search pipeline: `test_search_orchestrator.py`, `test_branch_executor.py`,
  `test_branch_planner.py`, `test_search_router.py`, `test_provider_plan.py`,
  `test_provider_config.py`, `test_provider_health_and_content_quality.py`
- Content extraction: `test_content_*.py`, `test_page_content_resolver.py`,
  `test_sitemap.py`, `test_youtube*.py`, `test_whisper_client.py`
- Rerank and ranking: `test_rerank_*.py`, `test_diversity_ranking.py`,
  `test_voyage_rerank.py`, `test_jina_rerank.py`
- Analytics and observability: `test_analytics_*.py`,
  `test_observability_*.py`, `test_pipeline_tables.py`,
  `test_ai_search_provider_tracing.py`, `test_grafana_dashboard_json.py`
- Cache and index: `test_cache_*.py`, `test_page_cache_duckdb.py`,
  `test_exact_lru_cache.py`, `test_qdrant_search.py`
- CLI, AB testing, training, and client-steering middleware:
  `tests/cli/test_*.py`, `test_ab_*.py`, `test_training_jsonl.py`,
  `test_agent_steering_middleware.py`, `test_query_understanding*.py`,
  `test_prompt_registry.py`
- Provider-specific smoke/unit coverage: `test_ddg_unit.py`,
  `test_searxng_unit.py`, `test_tavily_unit.py`, `test_brightdata_provider.py`,
  `test_composio_*.py`, `test_github_*.py`, `test_hackernews_provider.py`

## Running Tests

- `pytest`
- `python -m pytest tests/test_server.py tests/test_search_orchestrator.py`
- `python -m pytest tests/test_cli*.py`
- `python -m pytest tests/test_rerank_core.py tests/test_rerank_stack.py`

## Conventions

- Patch under `kindly_web_search_mcp_server.*` unless the test is for the
  standalone classifier service
- Use `AsyncMock` and `unittest.IsolatedAsyncioTestCase` for async paths
- Keep reusable data in `tests/fixtures/`
- Prefer focused slices when validating a subsystem change
