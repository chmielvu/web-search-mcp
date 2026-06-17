# AGENTS.md - Tests

This directory contains the test suite for the Kindly Web Search MCP Server.

## Structure

tests/
|-- __init__.py
|-- test_server.py                    # Core server tests
|-- test_page_content_resolver.py     # Content resolution tests
|-- test_tool_descriptions.py         # Tool contract tests
|-- test_prompt_registry.py           # Prompt registry tests
|-- test_query_understanding.py       # Query understanding tests
|-- test_provider_plan.py             # Provider plan tests
|-- test_training_jsonl.py            # Training JSONL tests
|-- test_entity_response_fields.py    # Entity response tests
|-- test_search_orchestrator.py       # Search orchestrator tests
|-- test_search_router.py             # Search router tests
|-- test_provider_config.py           # Provider config tests
|-- test_cache_observability.py       # Cache observability tests
|-- test_grafana_dashboard_json.py    # Grafana dashboard tests
|-- test_searxng_unit.py              # SearXNG unit tests
|-- test_ab_integration.py            # A/B testing integration tests
|-- test_ab_wiring_provider.py        # A/B wiring provider tests
|-- test_paid_serp_round_robin.py     # Paid SERP round-robin tests
|-- cli/                              # CLI tests
|   |-- __init__.py
|   -- test_cli*.py
-- fixtures/                         # Test fixtures and mock data

## Running Tests

### Full test suite
pytest

### Focused test slice (core search contract)
pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_prompt_registry.py tests/test_query_understanding.py tests/test_provider_plan.py tests/test_training_jsonl.py tests/test_entity_response_fields.py tests/test_search_orchestrator.py tests/test_search_router.py

### Single test file
pytest tests/test_searxng_unit.py -v

## Conventions
- Tests patch under kindly_web_search_mcp_server.* namespace
- Use AsyncMock with unittest.IsolatedAsyncioTestCase for async tests
- Fixtures in tests/fixtures/ for reusable test data
