# Testing Guide

## Overview

The test suite uses `pytest` with `unittest.IsolatedAsyncioTestCase` for async tests. Tests mock external APIs to keep them deterministic while allowing opt-in live integration tests.

## Running Tests

### All tests

```bash
pytest
```

### Single test file

```bash
python -m pytest tests/test_server.py -v
```

### Focused test slice (core search contract)

```bash
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_profile_resolution.py tests/test_prompt_registry.py tests/test_query_understanding.py tests/test_provider_plan.py tests/test_training_jsonl.py tests/test_entity_response_fields.py tests/test_search_orchestrator.py tests/test_search_router.py
```

### With coverage

```bash
pytest --cov=kindly_web_search_mcp_server --cov-report=term-missing
```

## Test Organization

### Unit Tests

- `test_searxng_unit.py` - SearXNG provider
- `test_tavily_unit.py` - Tavily provider
- `test_ddg_unit.py` - DuckDuckGo provider
- `test_merge.py` - RRF merge logic
- `test_rerank_*.py` - Rerank engines and policy
- `test_entity_*.py` - Entity extraction

### Integration Tests

- `test_live_fetch_urls.py` - Live URL fetching (requires network)
- `test_composio_tools.py` - Composio integration
- `test_agentic_web_research.py` - Agentic research agent

### Component Tests

- `test_content_*.py` - Content extraction pipeline
- `test_search_*.py` - Search pipeline components
- `test_cache_*.py` - Caching layers
- `test_analytics_*.py` - Analytics DuckDB

## Mocking Patterns

### Provider Mocking

Tests patch under the `kindly_web_search_mcp_server.*` namespace:

```python
from unittest.mock import patch, AsyncMock

async def test_search_with_mock_provider():
    with patch("kindly_web_search_mcp_server.search.searxng.search_searxng") as mock:
        mock.return_value = [
            {"title": "Test", "url": "https://example.com", "content": "Snippet"}
        ]
        result = await search_searxng("test query", num_results=5)
        assert len(result) == 1
```

### Async Mocking

Use `AsyncMock` with `unittest.IsolatedAsyncioTestCase`:

```python
import unittest
from unittest.mock import AsyncMock, patch

class TestAsyncSearch(unittest.IsolatedAsyncioTestCase):
    @patch("kindly_web_search_mcp_server.search.tavily.search_tavily")
    async def test_tavily_search(self, mock_search):
        mock_search.return_value = AsyncMock(return_value=[])
        result = await search_tavily("test")
        mock_search.assert_called_once()
```

### Settings Mocking

```python
@pytest.fixture
def mock_settings():
    with patch("kindly_web_search_mcp_server.settings.settings") as s:
        s.reranking_enabled = True
        s.rrf_k = 60
        yield s
```

## Test Data

### Fixtures

Test data is in `tests/fixtures/`:

- `rerank_candidates.json` - Candidate results for rerank tests
- Other fixtures are generated inline

### Training Data

- `.kindly/training/query_understanding.jsonl` - Query understanding training data
- `evals/rerank_cases.jsonl` - Rerank evaluation cases

## Live Integration Tests

Some tests require live API access. These are skipped by default:

```bash
# Run with live tests
pytest -m live

# Or set env vars
export SEARXNG_BASE_URL="http://localhost:8080"
export TAVILY_API_KEY="tvly-..."
pytest
```

## Adding New Tests

### 1. Unit test pattern

```python
# tests/test_my_feature.py
from __future__ import annotations
import pytest
from unittest.mock import patch, AsyncMock

class TestMyFeature:
    @pytest.mark.asyncio
    async def test_basic_behavior(self):
        result = await my_function()
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_mock(self):
        with patch("kindly_web_search_mcp_server.module.dependency") as mock:
            mock.return_value = "test"
            result = await my_function()
            assert result == "test"
```

### 2. Integration test pattern

```python
import pytest

@pytest.mark.live
class TestLiveIntegration:
    @pytest.mark.asyncio
    async def test_real_api_call(self):
        # Requires real API key in env
        result = await search_real_provider("test query")
        assert len(result) > 0
```

### 3. Component test pattern

```python
class TestContentResolver:
    def test_parse_github_issue_url(self):
        from kindly_web_search_mcp_server.content.github_issues import parse_github_issue_url
        result = parse_github_issue_url("https://github.com/owner/repo/issues/123")
        assert result is not None
        assert result.owner == "owner"
```

## Test Configuration

### conftest.py

Sets up test environment with default provider keys:

```python
@pytest.fixture(scope="session", autouse=True)
def patch_settings():
    os.environ.setdefault("SEARXNG_BASE_URL", "https://searx.example.org")
    os.environ.setdefault("TAVILY_API_KEY", "test_api_key")
```

### pytest.ini / pyproject.toml

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

## CI/CD Integration

```yaml
# GitHub Actions example
- name: Run tests
  run: |
    pip install -e ".[dev]"
    pytest tests/ -v --tb=short
```

## Debugging Tests

### Verbose output

```bash
pytest -vvs tests/test_server.py::TestWebSearch::test_basic_search
```

### Stop on first failure

```bash
pytest -x tests/
```

### Run specific test

```bash
pytest tests/test_server.py::TestWebSearch -v
```

### Show local variables on failure

```bash
pytest -l tests/
```
