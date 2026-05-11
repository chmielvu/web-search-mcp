<!-- generated-by: gsd-doc-writer -->
# Development Guide

This guide covers local development setup, project structure, code patterns, and contribution workflows for the Kindly Web Search MCP Server.

## Development Setup

### Prerequisites

- **Python 3.13+** (required; 3.14 supported but optional "advanced PDF layout" extras are disabled)
- **uv** or **uvx** for dependency management (recommended)
- A Chromium-based browser (Chrome/Chromium/Edge/Brave) for universal HTML extraction testing
- At least one search provider configured for testing:
  - `SEARXNG_BASE_URL` (self-hosted, primary)
  - `TAVILY_API_KEY`, `BRAVE_API_KEY`, or `JINA_API_KEY` (paid providers)

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server.git
cd kindly-web-search-mcp-server

# Create virtual environment and install dev dependencies
uv venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows PowerShell

uv pip install -e ".[dev]"
```

### Environment Variables

Create a `.env` file in the project root (or set environment variables):

```bash
# Required: at least one search provider
SEARXNG_BASE_URL=http://localhost:8080
# Or paid providers:
TAVILY_API_KEY=tvly-...
BRAVE_API_KEY=...
JINA_API_KEY=...

# Recommended: GitHub token for better Issue extraction
GITHUB_TOKEN=ghp_...

# Optional: advanced features
MISTRAL_API_KEY=...          # Query rewrite
KINDLY_GEMINI_API_KEY=...    # Gemini grounding
POLLINATIONS_API_KEY=...     # Perplexity Sonar

# Optional: browser path (auto-detected if unset)
KINDLY_BROWSER_EXECUTABLE_PATH=/path/to/chrome

# Debugging
KINDLY_DIAGNOSTICS=1         # Enable JSON-line diagnostics to stderr
```

### First Run

```bash
# Test the MCP server locally (stdio mode)
uv run kindly-web-search-mcp-server start-mcp-server

# Or HTTP mode for manual testing
uv run kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

---

## Project Structure

```
src/kindly_web_search_mcp_server/
├── server.py              # FastMCP server entry point, tool definitions
├── cli.py                 # CLI wrapper with start-mcp-server subcommand
├── models.py              # Pydantic response models
├── settings.py            # Environment-based configuration (dataclass)
│
├── search/                # Multi-provider web search pipeline
│   ├── __init__.py        # Provider registry, RRF merge, circuit breaker
│   ├── orchestrator.py    # Query rewrite → search → merge → rerank
│   ├── searxng.py         # SearXNG provider implementation
│   ├── tavily.py          # Tavily provider implementation
│   ├── brave.py           # Brave Search provider implementation
│   ├── jina.py            # Jina AI provider implementation
│   ├── pollinations.py    # Perplexity Sonar via Pollinations API
│   ├── gemini_search_tool.py # Gemini search MCP tool with Google Search grounding
│   ├── youtube.py         # YouTube search via SearXNG engine
│   ├── merge.py           # RRF merge implementation
│   ├── normalize.py       # Query/URL normalization utilities
│   ├── query_rewrite.py   # Mistral-backed query expansion
│   ├── query_policy.py    # Intent classification (bypass/light_rewrite/decompose)
│   └── query_policy_resolver.py # Policy resolution backend selection
│   └── query_policy_hf.py # HF Space query policy backend
│
├── content/               # URL → Markdown resolution pipeline
│   ├── resolver.py        # Staged fallback dispatcher
│   ├── stackexchange.py   # StackOverflow/API Q&A extraction
│   ├── github_issues.py   # GitHub Issues (GraphQL)
│   ├── github_discussions.py # GitHub Discussions (GraphQL)
│   ├── wikipedia.py       # Wikipedia MediaWiki API
│   ├── arxiv.py           # arXiv papers (PDF → Markdown)
│   ├── youtube.py         # YouTube transcript extraction
│
├── scrape/                # Universal HTML extraction
│   ├── universal_html.py  # Nodriver headless browser extraction
│   ├── http_extract.py    # Trafilatura HTTP extraction (no browser)
│   ├── chromium_pool.py   # Browser instance pooling
│   ├── nodriver_worker.py # Nodriver worker utilities
│   ├── extract.py         # Content extraction helpers
│   ├── fetch.py           # HTTP fetch utilities
│   ├── sanitize.py        # HTML sanitization
│
├── cache/                 # Caching layers
│   ├── query_cache.py     # Exact query cache (SQLite-backed)
│   ├── semantic_cache.py  # LanceDB semantic similarity cache
│   ├── page_cache.py      # URL → page_content cache
│   ├── store.py           # Cache store implementation
│   ├── schema.py          # LanceDB schema definitions
│   └── content_type.py    # Content type classification
│
├── embeddings/            # Embedding services
│   ├── hf_space.py        # HuggingFace Space embedding client
│   └── rate_limiter.py    # Rate limiting for embedding calls
│
├── rerank/                # Result reranking
│   ├── core.py            # Rerank orchestration
│   ├── bi_encoder.py      # Bi-encoder similarity
│   ├── hf_space_rerank.py # HF Space cross-encoder reranking
│   └── diversity.py       # Diversity filtering
│
├── middleware/            # FastMCP middleware
│   ├── expensive_tool_protection.py # Rate-limit expensive tools
│   ├── gemini_advisory.py # Gemini usage tips
│   ├── query_guidance.py  # Query quality tips
│
└── utils/
    ├── diagnostics.py     # JSON-line diagnostics system
    └── logging.py         # Logging configuration
```

---

## Key Files and Responsibilities

| File | Purpose |
|------|---------|
| `server.py` | FastMCP server definition, tool handlers (`web_search`, `get_content`, `gemini_search`, `perplexity_search`, `youtube_transcript`, `youtube_search`) |
| `settings.py` | All `KINDLY_*` environment variables, defaults in `Settings` dataclass |
| `search/__init__.py` | Provider detection, RRF merge, circuit breaker, budget tracking |
| `search/orchestrator.py` | Coordinates rewrite → multi-provider search → merge → rerank |
| `content/resolver.py` | 7-stage fallback: StackExchange → GitHub Issues → GitHub Discussions → Wikipedia → arXiv → HTTP extract → Universal HTML |
| `scrape/universal_html.py` | Nodriver-based browser extraction for JS-heavy sites |
| `middleware/expensive_tool_protection.py` | "Think first, then call" pattern for rate-limited tools |

---

## Code Patterns to Follow

### Async-First Design

All I/O operations are async. Use `httpx.AsyncClient` for HTTP calls:

```python
async with httpx.AsyncClient(timeout=30) as client:
    resp = await client.get(url, params=params)
```

### Settings Access

Import the singleton `settings` from `settings.py`:

```python
from .settings import settings

if settings.semantic_cache_enabled:
    # ...
```

### Diagnostics

Pass `Diagnostics` objects through the pipeline for debugging:

```python
from ..utils.diagnostics import Diagnostics

async def my_function(diagnostics: Diagnostics | None = None) -> None:
    if diagnostics:
        diagnostics.emit("my_function.start", "Starting work", {"input": value})
```

Enable diagnostics via environment:

```bash
KINDLY_DIAGNOSTICS=1  # JSON-line output to stderr
```

### Error Handling

Use specific exception classes per module:

```python
class SearxngError(RuntimeError):
    pass

class SearxngConfigError(SearxngError):
    pass
```

Return graceful error messages in Markdown format for content resolution:

```python
return f"_Failed to retrieve content: {type(e).__name__}_\n\nSource: {url}\n"
```

### Pydantic Models

All tool responses use Pydantic models from `models.py`:

```python
from ..models import WebSearchResponse, WebSearchResult

return WebSearchResponse(query=query, results=results)
```

### Mocking in Tests

Patch under the `kindly_web_search_mcp_server.*` namespace:

```python
with patch("kindly_web_search_mcp_server.content.resolver.parse_stackexchange_url", ...):
    # test code
```

For async: use `AsyncMock` with `unittest.IsolatedAsyncioTestCase`.

---

## Adding a New Search Provider

1. **Create the provider module** in `src/kindly_web_search_mcp_server/search/`:

   ```python
   # search/my_provider.py
   from __future__ import annotations
   import os
   import httpx
   from ..models import WebSearchResult

   class MyProviderError(RuntimeError):
       pass

   class MyProviderConfigError(MyProviderError):
       pass

   async def search_my_provider(
       query: str,
       *,
       num_results: int,
       http_client: httpx.AsyncClient | None = None,
   ) -> list[WebSearchResult]:
       """Search using MyProvider API."""
       api_key = os.environ.get("MY_PROVIDER_API_KEY", "").strip()
       if not api_key:
           raise MyProviderConfigError("MY_PROVIDER_API_KEY not set")

       # Implement API call here
       # Return list[WebSearchResult] with title, link, snippet
       return results
   ```

2. **Register the provider** in `search/__init__.py`:

   - Add import: `from .my_provider import search_my_provider`
   - Add detection function: `_has_my_provider_key()`
   - Add to `_search_single_provider()` calls in `search_single_query()`

3. **Add environment variable** to `settings.py` if needed:

   ```python
   my_provider_api_key: str = os.environ.get("MY_PROVIDER_API_KEY", "")
   ```

4. **Write unit tests** in `tests/test_my_provider.py`:

   ```python
   import pytest
   from unittest.mock import patch, AsyncMock
   from kindly_web_search_mcp_server.search.my_provider import search_my_provider

   @pytest.mark.asyncio
   async def test_search_my_provider_success():
       with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
           mock_get.return_value.json.return_value = {"results": [...]}
           results = await search_my_provider("test query", num_results=5)
           assert len(results) > 0
   ```

---

## Adding a New Content Resolver

1. **Create the resolver module** in `src/kindly_web_search_mcp_server/content/`:

   ```python
   # content/my_source.py
   from __future__ import annotations
   import httpx
   from urllib.parse import urlparse

   class MySourceError(RuntimeError):
       pass

   def parse_my_source_url(url: str) -> dict:
       """Parse and validate MySource URL. Returns extraction params."""
       parsed = urlparse(url)
       if parsed.netloc != "mysource.com":
           raise MySourceError("Not a MySource URL")
       # Extract ID or other params from URL
       return {"source_id": ...}

   async def fetch_my_source_markdown(url: str) -> str:
       """Fetch MySource content and return LLM-ready Markdown."""
       params = parse_my_source_url(url)
       # Call API, format as Markdown
       return markdown_content
   ```

2. **Add to the resolver pipeline** in `content/resolver.py`:

   ```python
   from .my_source import (
       MySourceError,
       fetch_my_source_markdown,
       parse_my_source_url,
   )

   async def resolve_page_content_markdown(url: str, ...) -> str | None:
       # Add stage before HTTP extraction:
       try:
           parse_my_source_url(url)
       except MySourceError:
           pass
       else:
           try:
               return await fetch_my_source_markdown(url)
           except Exception as e:
               # Fallback to universal HTML or return error note
               ...
   ```

3. **Write unit tests** in `tests/test_my_source.py`:

   ```python
   import pytest
   from unittest.mock import patch, AsyncMock
   from kindly_web_search_mcp_server.content.my_source import parse_my_source_url, fetch_my_source_markdown

   def test_parse_my_source_url_valid():
       params = parse_my_source_url("https://mysource.com/item/123")
       assert params["source_id"] == "123"

   def test_parse_my_source_url_invalid():
       with pytest.raises(MySourceError):
           parse_my_source_url("https://other.com/item/123")
   ```

---

## Adding Middleware

1. **Create the middleware module** in `src/kindly_web_search_mcp_server/middleware/`:

   ```python
   # middleware/my_middleware.py
   from __future__ import annotations
   from fastmcp.server.middleware import Middleware, MiddlewareContext
   from fastmcp.exceptions import ToolError
   from typing import Any

   class MyMiddleware(Middleware):
       async def on_call_tool(
           self,
           context: MiddlewareContext,
           call_next: Any,
       ) -> Any:
           tool_name = context.message.name
           # Intercept or modify tool calls
           if tool_name == "some_tool":
               # Check conditions, raise ToolError to block
               # Or allow through: return await call_next(context)
           return await call_next(context)

   def create_my_middleware() -> MyMiddleware:
       return MyMiddleware()
   ```

2. **Register in `middleware/__init__.py`**:

   ```python
   from .my_middleware import MyMiddleware, create_my_middleware

   __all__ = [..., "MyMiddleware", "create_my_middleware"]
   ```

3. **Add to server in `server.py`**:

   ```python
   from .middleware import create_my_middleware
   mcp.add_middleware(create_my_middleware())
   ```

---

## Testing Locally

### Run All Tests

```bash
pytest
```

### Run Focused Test Slice

Core search contract tests:

```bash
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py
```

### Run Single Test File

```bash
python -m pytest tests/test_searxng_unit.py -v
```

### Test Configuration

Tests patch environment variables in `conftest.py`:

```python
os.environ.setdefault("SEARXNG_BASE_URL", "https://searx.example.org")
os.environ.setdefault("TAVILY_API_KEY", "test_api_key")
```

---

## Linting and Formatting

### Ruff (Linter + Formatter)

```bash
# Check linting issues
ruff check src/

# Auto-fix linting issues
ruff check src/ --fix

# Format code
ruff format src/

# Check formatting without changes
ruff format src/ --check
```

### Configuration

Ruff is configured via `pyproject.toml` (if present) or defaults to PEP 8 style.

---

## Debugging with Diagnostics

### Enable Diagnostics

```bash
KINDLY_DIAGNOSTICS=1
```

This emits JSON-line diagnostics to stderr for each request:

```
KINDLY_DIAG {"request_id":"uuid","stage":"resolver.start","msg":"Resolving URL","elapsed_ms":12,"data":{"url":"..."}}
```

### Diagnostics in Tool Responses

When enabled, tool responses include `diagnostics` field:

```json
{
  "url": "...",
  "page_content": "...",
  "diagnostics": [{"stage": "resolver.route", "msg": "Matched StackExchange URL", ...}]
}
```

### Key Diagnostic Stages

| Stage | Description |
|-------|-------------|
| `resolver.start` | URL resolution begins |
| `resolver.route` | Handler matched (stackexchange, github_issue, etc.) |
| `resolver.http_success` | HTTP extraction succeeded |
| `resolver.fallback` | Handler failed, falling back to HTML |
| `search.provider_select` | Available providers for query |
| `search.rrf_merge` | RRF merge completed |
| `web_search.rewrite_plan` | Query rewrite policy resolved |
| `content.timeout` | Content fetch timed out |

---

## Git Workflow

### Branch Naming

No documented convention. Follow common patterns:

- `feat/feature-name` for new features
- `fix/bug-name` for bug fixes
- `refactor/component-name` for refactoring

### Commit Guidelines

Follow conventional commit format:

```
feat(search): add DuckDuckGo provider backup
fix(content): handle arXiv PDF timeout gracefully
docs(readme): clarify browser path setup
```

### Pull Request Process

(No documented PR template exists.)

Recommended checklist:

1. Run tests: `pytest`
2. Run lint/format: `ruff check src/ && ruff format src/`
3. Update documentation if changing API or behavior
4. Add tests for new functionality
5. Ensure environment variables documented in `settings.py`

---

## Common Development Issues

### Browser Not Found

If `get_content` returns "No Chromium-based browser executable found":

1. Install Chrome/Chromium/Edge
2. Set explicit path:

   ```powershell
   $env:KINDLY_BROWSER_EXECUTABLE_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
   ```

### Browser Connection Timeout

Increase timeouts:

```bash
KINDLY_NODRIVER_RETRY_ATTEMPTS=5
KINDLY_NODRIVER_DEVTOOLS_READY_TIMEOUT_SECONDS=20
KINDLY_HTML_TOTAL_TIMEOUT_SECONDS=45
```

### Tool Timeout on Windows

Windows headless browser cold starts can be slow. Increase:

```powershell
$env:KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS="180"
$env:KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS="600"
$env:KINDLY_WEB_SEARCH_MAX_CONCURRENCY="1"
```

### No Provider Configured

Set at least one:

```bash
SEARXNG_BASE_URL=http://localhost:8080
# Or: TAVILY_API_KEY, BRAVE_API_KEY, JINA_API_KEY
```

### Semantic Cache Errors

LanceDB issues can occur on first run. Ensure `lancedb_data/` directory is writable:

```bash
KINDLY_LANCEDB_DIR=./lancedb_data
```

---

## Next Steps

- See README.md for installation and usage
- See ARCHITECTURE.md for system design details
- See CONFIGURATION.md for environment variable reference