<!-- generated-by: gsd-doc-writer -->
# Testing

This document describes the testing philosophy, structure, patterns, and commands for the Kindly Web Search MCP Server.

## Test Philosophy

Tests in this project are designed to be:

- **Deterministic**: Unit tests use mocks and fake HTTP clients to avoid external dependencies
- **Fast**: Async tests use anyio/pytest-asyncio without real network calls
- **Isolated**: Each test file targets a specific component with clear boundaries
- **Lightweight-first**: The `web_search` tool contract ensures results contain only title, link, snippet — no `page_content` field

Live integration tests exist but are gated by the `KINDLY_RUN_LIVE_TESTS=1` environment variable, ensuring CI runs unit tests only.

## Running Tests

### Full test suite

```bash
pytest
```

### Focused test slice (core search contract)

```bash
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py
```

### Single test file

```bash
python -m pytest tests/test_searxng_unit.py -v
```

### With verbose output

```bash
python -m pytest tests/test_server.py -v --tb=short
```

### Live integration tests

Live tests require environment setup:

```bash
# Set environment variables for live testing
export KINDLY_RUN_LIVE_TESTS=1
export KINDLY_BROWSER_EXECUTABLE_PATH="/path/to/chrome"
export KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS=180
export KINDLY_HTML_TOTAL_TIMEOUT_SECONDS=90

python -m pytest tests/test_live_fetch_urls.py -v
```

## Test Structure

| Test File | Coverage Scope |
|-----------|----------------|
| `test_server.py` | MCP tool contracts (`web_search`, `get_content`), timeout budget, concurrency limits |
| `test_search_router.py` | Multi-provider routing, RRF merge, circuit breaker behavior |
| `test_search_orchestrator.py` | Query rewrite → search → merge → rerank pipeline |
| `test_searxng_unit.py` | SearXNG provider: parsing, error handling, config validation |
| `test_tavily_unit.py` | Tavily provider: API response parsing |
| `test_page_content_resolver.py` | Lightweight result contract verification |
| `test_content_resolver_universal_fallback.py` | Staged fallback pipeline: StackExchange → GitHub → Wikipedia → HTTP → Universal HTML |
| `test_stackexchange_api_client.py` | StackExchange API: paginated fetch, URL parsing |
| `test_github_issues.py` | GitHub Issues: URL parsing, GraphQL render structure |
| `test_github_discussions.py` | GitHub Discussions: URL parsing, markdown rendering |
| `test_wikipedia.py` | Wikipedia: URL parsing, mobile host normalization, truncation markers |
| `test_arxiv.py` | arXiv: URL parsing (new/legacy IDs), Atom XML parsing, PDF → Markdown |
| `test_youtube.py` | YouTube: URL parsing (watch/shorts/embed/live), transcript formatting, search via SearXNG |
| `test_query_rewrite.py` | Query rewrite: canonicalization, policy classification, HF backend fallback |
| `test_tool_descriptions.py` | Docstring validation: agent-facing guidance, cross-references, env var mentions |
| `test_live_fetch_urls.py` | Integration tests for timeout-sensitive URLs (gated by `KINDLY_RUN_LIVE_TESTS`) |

## Mock Patterns

### AsyncMock for async functions

Tests patch under the `kindly_web_search_mcp_server.*` namespace:

```python
from unittest.mock import AsyncMock, patch

with patch(
    "kindly_web_search_mcp_server.server.run_web_search",
    new_callable=AsyncMock
) as mock_search:
    mock_search.return_value = WebSearchResponse(query="hello", results=[...])
    out = await web_search("hello", num_results=1)
```

### unittest.IsolatedAsyncioTestCase

For async test classes using unittest:

```python
import unittest

class TestWebSearchTool(unittest.IsolatedAsyncioTestCase):
    async def test_web_search_returns_results(self) -> None:
        # async test body
```

### pytest.mark.asyncio

For pytest-based async tests:

```python
import pytest

class TestYouTubeSearch:
    @pytest.mark.asyncio
    async def test_successful_search(self) -> None:
        # async test body
```

### httpx.MockTransport for HTTP mocking

Provider tests use `httpx.MockTransport` to simulate API responses:

```python
import httpx

def handler(request: httpx.Request) -> httpx.Response:
    # Assert request properties
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == "https://searx.example.org/search"
    # Return mock response
    return httpx.Response(200, json={"results": [...]})

transport = httpx.MockTransport(handler)
async with httpx.AsyncClient(transport=transport) as client:
    results = await search_searxng("query", num_results=1, http_client=client)
```

### Environment variable patching

Tests use `patch.dict(os.environ, ...)` to set/clear environment variables:

```python
import os
from unittest.mock import patch

with patch.dict(os.environ, {"SEARXNG_BASE_URL": "https://test.example.org"}, clear=True):
    # Test with controlled environment
```

### Clearing provider config

To test provider routing, clear competing provider env vars:

```python
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("BRAVE_API_KEY", None)
os.environ.pop("JINA_API_KEY", None)
os.environ["SEARXNG_BASE_URL"] = "https://searx.example.org"
```

## Testing Search Providers

Each provider has a dedicated unit test file:

### SearXNG (`test_searxng_unit.py`)

- Result parsing from JSON response
- Optional parameters (language, categories, engines, time_range, safesearch)
- Custom headers via `SEARXNG_HEADERS_JSON`
- User-Agent handling (env vs headers JSON precedence)
- Malformed result filtering
- Error handling: 403 Forbidden, 429 Rate Limited, invalid JSON
- Config validation: invalid `SEARXNG_BASE_URL`, malformed `SEARXNG_HEADERS_JSON`

### Tavily (`test_tavily_unit.py`)

- API response parsing
- Authorization header validation

### Search Router (`test_search_router.py`)

- Provider selection based on available config
- Concurrent multi-provider execution
- RRF deduplication (same URL from multiple providers)
- Circuit breaker opening on consecutive failures
- Error when no provider configured

## Testing Content Resolvers

### Content Resolver Universal Fallback (`test_content_resolver_universal_fallback.py`)

Tests the staged fallback pipeline:

1. StackExchange API → if URL matches, fetch thread markdown
2. GitHub Issues API → if URL matches, fetch issue thread
3. GitHub Discussions API → if URL matches, fetch discussion thread
4. Wikipedia API → if URL matches, fetch article markdown
5. HTTP extraction (trafilatura) → fallback for generic URLs
6. Universal HTML (nodriver) → last resort for JS-heavy sites

Pattern: Each stage is tested with mocks that either return data or raise errors to trigger fallback.

### StackExchange (`test_stackexchange_api_client.py`)

- Paginated fetch of question + answers
- API parameter validation (site, filter, pagesize)
- Question and answer data extraction

### GitHub Issues (`test_github_issues.py`)

- URL parsing for `/issues/{number}` paths
- Rejection of non-issue URLs (pulls, external sites)
- Markdown rendering structure (Question/Answers headers, reaction counts, permalinks)

### Wikipedia (`test_wikipedia.py`)

- URL parsing for `/wiki/{title}` and `/w/index.php?title=...`
- Mobile host normalization (`en.m.wikipedia.org` → `en.wikipedia.org`)
- Truncation marker in output markdown

### arXiv (`test_arxiv.py`)

- URL parsing for new-style IDs (`2205.01491`) and legacy IDs (`hep-th/9901001`)
- Version suffix handling (`v1`, `v2`)
- Atom XML metadata extraction (title, authors, categories, PDF URL)
- PDF → Markdown conversion (requires PyMuPDF)

## Testing YouTube Tools

### `test_youtube.py`

**URL parsing (`TestParseYouTubeUrl`):**
- Bare 11-character video IDs
- Watch URLs (`/watch?v=...`)
- Short URLs (`youtu.be/...`)
- Embed URLs (`/embed/...`)
- Shorts URLs (`/shorts/...`)
- Live URLs (`/live/...`)
- Mobile URLs (`m.youtube.com/...`)
- URLs with additional parameters (`&t=123s`)
- Invalid URL handling (non-YouTube, missing video ID)

**Transcript formatting (`TestFormatTranscript`):**
- Plain text concatenation
- Timestamped formatting (`[00:00]`, `[01:05]`)
- Duration calculation from segments
- Empty segment handling

**Transcript fetching (`TestFetchTranscriptData`):**
- ImportError handling when `youtube-transcript-api` not installed
- TranscriptsDisabled error handling

**YouTube search (`TestYouTubeSearch`):**
- Empty query handling
- Zero results request
- Missing SearXNG config error
- Successful search via mocked SearXNG response
- Results capped at requested `num_results`

## Testing Middleware

### Query Rewrite (`test_query_rewrite.py`)

- Fallback to original query when disabled
- Whitespace canonicalization via `normalize_query`
- Bypass mode for exact tokens (`site:`, quoted phrases)
- HF Space backend preference when available

### Query Policy (`test_query_policy.py` implicit via rewrite tests)

- Intent classification (troubleshooting, factual)
- Policy mode selection (bypass, light_rewrite, decompose)
- Must-keep terms preservation

### Tool Descriptions (`test_tool_descriptions.py`)

Docstring validation ensures agent-facing guidance:

- Multiple "When to use" bullet examples
- "When not to use" section present
- Cross-references between tools (`web_search` ↔ `get_content`)
- Environment variable mentions in config context
- `num_results` default and recommended range
- Output shape documentation (lightweight vs full content)

## Coverage Expectations

No coverage threshold is configured in `pyproject.toml`. The project relies on:

- Unit tests for all public APIs
- Integration tests gated by environment variable
- Contract tests for MCP tool behavior

If you want to check coverage:

```bash
pip install pytest-cov
python -m pytest --cov=kindly_web_search_mcp_server --cov-report=term-missing
```

## Debugging Test Failures

### Check environment setup

Tests require mock environment variables set in `conftest.py`:

```python
os.environ.setdefault("SEARXNG_BASE_URL", "https://searx.example.org")
os.environ.setdefault("TAVILY_API_KEY", "test_api_key")
```

If tests fail with "no provider configured", verify `conftest.py` is loaded.

### Verify patch namespace

Mocks must target the actual import location:

```python
# Correct: patch where the function is used
patch("kindly_web_search_mcp_server.server.run_web_search", ...)

# Wrong: patch where the function is defined
patch("kindly_web_search_mcp_server.search.orchestrator.run_web_search", ...)
```

### Check async mock setup

Use `new_callable=AsyncMock` for async functions:

```python
# Correct
patch("module.async_func", new_callable=AsyncMock)

# Wrong (returns non-async MagicMock)
patch("module.async_func")
```

### HTTP MockTransport debugging

If HTTP tests fail, inspect the handler assertions:

```python
def handler(request: httpx.Request) -> httpx.Response:
    print(f"Request: {request.method} {request.url}")  # Debug output
    print(f"Headers: {dict(request.headers)}")
    return httpx.Response(200, json={"results": [...]})
```

### Live test prerequisites

Live tests (`test_live_fetch_urls.py`) require:

- `KINDLY_RUN_LIVE_TESTS=1`
- `KINDLY_BROWSER_EXECUTABLE_PATH` pointing to Chrome/Chromium/Edge
- Extended timeouts (`KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS=180`)

Without these, tests are skipped automatically.