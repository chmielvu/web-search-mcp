# Web MCP Server

A privacy-focused web search MCP (Model Context Protocol) server that provides web search and content extraction capabilities. Uses **SearxNG** as the primary search engine with **Google scraping** as a fallback.

## Features

- **Web Search** - Search the web with category filters (general, news, images, videos, science, files)
- **Content Extraction** - Extract readable content from URLs as markdown
- **Search Suggestions** - Get query suggestions for better searches
- **Privacy-Focused** - Uses SearxNG metasearch engine
- **Fallback Support** - Automatically falls back to Google scraping if SearxNG is unavailable
- **Relevance Ranking** - Query-aware reranking, deduplication, and low-signal filtering
- **Security-Aware Search** - CVE/security queries prioritize trusted advisory sources
- **Rate Limiting** - Built-in rate limiting to prevent abuse
- **Docker Ready** - Single-container deployment with SearxNG included

## Tools Provided

| Tool | Description |
|------|-------------|
| `web_search` | Search the web with query, category, and limit options |
| `fetch_content` | Extract and convert webpage content to markdown |
| `get_suggestions` | Get search query suggestions |

## Installation

### Option 1: Docker (Recommended)

```bash
# Build the image
docker build -t web-mcp:latest .

# Run the container
docker run --rm -i web-mcp:latest
```

### Option 2: Python Package

```bash
# Clone the repository
git clone https://github.com/your-org/web-mcp.git
cd web-mcp

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Optional: tests, lint, type checks

# Or install as package
pip install -e .

# Run the server
python -m web_mcp.server
```

### Option 3: With External SearxNG

If you have an existing SearxNG instance:

```bash
# Set the SearxNG URL
export SEARXNG_URL=http://your-searxng-instance:8080

# Run the MCP server
python -m web_mcp.server
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARXNG_URL` | `http://localhost:8080` | SearxNG server URL |
| `SEARXNG_TIMEOUT` | `10` | Request timeout in seconds |
| `SEARCH_ENGINE_PROFILE_MODE` | `auto` | Query-aware engine profile mode (`auto` or `off`) |
| `SEARCH_SECURITY_ENGINES` | `brave,bing,duckduckgo,wikipedia,github,stackoverflow` | Engines used for security/CVE queries |
| `SEARCH_GENERAL_ENGINES` | `` | Engines for general queries (empty = SearxNG defaults) |
| `SEARCH_CANDIDATE_MULTIPLIER` | `5` | Candidate expansion before reranking |
| `SEARCH_MAX_CANDIDATES` | `30` | Maximum candidates before reranking |
| `SEARCH_MIN_QUALITY_SCORE` | `2.5` | Security-query quality threshold for fallback merge |
| `FALLBACK_ENABLED` | `true` | Enable Google scraping fallback |
| `RATE_LIMIT_REQUESTS` | `30` | Max requests per period |
| `RATE_LIMIT_PERIOD` | `60` | Rate limit period in seconds |
| `MAX_CONTENT_LENGTH` | `10000` | Max characters in fetched content |
| `FETCH_ALLOW_PRIVATE_NETWORK` | `false` | Allow fetching localhost/private network URLs |
| `DEFAULT_SEARCH_LIMIT` | `5` | Default number of search results |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `JSON_LOGS` | `false` | Output logs in JSON format |

### Configuration File

Create a `.env` file in the project root:

```env
SEARXNG_URL=http://localhost:8080
SEARXNG_TIMEOUT=10
SEARCH_ENGINE_PROFILE_MODE=auto
SEARCH_SECURITY_ENGINES=brave,bing,duckduckgo,wikipedia,github,stackoverflow
SEARCH_GENERAL_ENGINES=
SEARCH_CANDIDATE_MULTIPLIER=5
SEARCH_MAX_CANDIDATES=30
SEARCH_MIN_QUALITY_SCORE=2.5
FALLBACK_ENABLED=true
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_PERIOD=60
MAX_CONTENT_LENGTH=10000
FETCH_ALLOW_PRIVATE_NETWORK=false
DEFAULT_SEARCH_LIMIT=5
LOG_LEVEL=INFO
JSON_LOGS=false
```

## Usage with MCP Clients

### Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "web-mcp": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "web-mcp:latest"]
    }
  }
}
```

Or with Python:

```json
{
  "mcpServers": {
    "web-mcp": {
      "command": "python",
      "args": ["-m", "web_mcp.server"],
      "env": {
        "SEARXNG_URL": "http://localhost:8080"
      }
    }
  }
}
```

### Other MCP Clients

The server uses stdio transport, making it compatible with any MCP-compatible client.

## Tool Reference

### web_search

Search the web for information.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | The search query |
| `category` | string | No | Search category: `general`, `images`, `videos`, `news`, `science`, `files` |
| `limit` | integer | No | Maximum results (default: 5, min: 1, max: 10) |

**Example:**

```json
{
  "name": "web_search",
  "arguments": {
    "query": "Python async programming",
    "category": "general",
    "limit": 5
  }
}
```

**Response:**

```markdown
# Search Results for: Python async programming

*Provider: searxng | 5 results*

---

## 1. Async IO in Python: A Complete Guide
**URL:** https://realpython.com/async-io-python/

Complete guide to async programming in Python...

## 2. Python asyncio Documentation
**URL:** https://docs.python.org/3/library/asyncio.html

Official Python asyncio documentation...
```

### fetch_content

Extract readable content from a URL.
By default, only public `http`/`https` targets are allowed (`FETCH_ALLOW_PRIVATE_NETWORK=false`).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | Yes | The URL to fetch content from |
| `max_length` | integer | No | Maximum content length (default: 10000, min: 500, max: 20000) |

**Example:**

```json
{
  "name": "fetch_content",
  "arguments": {
    "url": "https://example.com/article",
    "max_length": 5000
  }
}
```

**Response:**

```markdown
# Article Title

> Brief description of the article

**Author:** John Doe
**Source:** example.com
**URL:** https://example.com/article

---

[Article content in markdown format...]
```

### get_suggestions

Get search query suggestions.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | The partial search query |

**Example:**

```json
{
  "name": "get_suggestions",
  "arguments": {
    "query": "python asyn"
  }
}
```

**Response:**

```markdown
# Suggestions for: python asyn

1. python async await
2. python asyncio tutorial
3. python async http requests
4. python async context manager
5. python asyncio vs threading
```

## Development

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check src tests

# Run type checking
mypy src
```

### Manual MCP Smoke Test (Container + stdio)

Use this to verify the real MCP integration path used by CLI agents.

`test.py` starts the containerized MCP server as a child process with:
`docker run --rm -i web-mcp:latest`
and validates `initialize`, `list_tools`, and `call_tool` flows.

The container contract is stdio-only. Detached mode (`docker run -d ...`) is intentionally not supported for MCP clients.

```bash
# 1) Build image
docker build -t web-mcp:latest .

# 2) Run smoke script from repo root (with your virtualenv active)
.venv/bin/python test.py

# Optional: custom inputs
.venv/bin/python test.py \
  --image web-mcp:latest \
  --query "python asyncio" \
  --suggest-query "python asyn" \
  --content-url "https://example.com" \
  --limit 3 \
  --max-length 800

# Optional: full response blocks
.venv/bin/python test.py --verbose
```

What `test.py` verifies:

- MCP session initialization against containerized server
- Expected tools are registered: `web_search`, `fetch_content`, `get_suggestions`
- Tool calls succeed over MCP stdio transport

Script behavior notes:

- If you pass only one of `--query` or `--suggest-query`, that value is reused for both
- `test.py` prints compact pass/fail summaries by default; use `--verbose` to show full tool outputs
- Use `--docker-command` if your environment uses a different container runtime command

### Project Structure

```
web-mcp/
├── src/web_mcp/
│   ├── __init__.py
│   ├── config.py           # Configuration management
│   ├── server.py           # MCP server entry point
│   ├── search/
│   │   ├── base.py         # SearchResult, SearchResponse, SearchProvider ABC
│   │   ├── searxng.py      # SearxNG provider
│   │   ├── google.py       # Google scraping fallback
│   │   ├── fallback.py     # Fallback orchestration + quality gate
│   │   ├── relevance.py    # Scoring, ranking, dedup, snippet cleaning
│   │   └── provider_registry.py # Shared provider singleton
│   ├── tools/
│   │   ├── web_search.py   # web_search tool
│   │   ├── fetch_content.py # fetch_content tool
│   │   └── suggestions.py  # get_suggestions tool
│   └── utils/
│       ├── logger.py       # Structured logging
│       ├── rate_limiter.py  # Rate limiting
│       ├── content_extractor.py # HTML-to-markdown extraction
│       └── validation.py   # Shared input validation
├── tests/                  # Test suite
├── docker/                 # Docker configuration
│   ├── searxng/           # SearxNG settings
│   └── entrypoint.sh      # Container entrypoint
├── Dockerfile             # Single-container Docker build
├── pyproject.toml         # Python project config
├── requirements.txt       # Runtime dependencies
└── requirements-dev.txt   # Test/lint/type dependencies
```

## Troubleshooting

### Common Issues

**1. SearxNG Connection Refused**

```
Error: Failed to connect to SearxNG
```

- Ensure SearxNG is running: `curl http://localhost:8080/config`
- Check `SEARXNG_URL` environment variable
- If using Docker via MCP stdio, ensure the image is current (`docker build -t web-mcp:latest .`)

**2. Google Rate Limiting**

```
Error: Google rate limit hit (429)
```

- Reduce request frequency
- SearxNG should be used as primary; Google is fallback only
- Wait a few minutes before retrying

**3. Content Extraction Failed**

```
Error: Failed to extract content from page
```

- The page may use JavaScript rendering (not supported)
- The page may block automated requests
- Try with a different URL

**4. Import Errors**

```
ModuleNotFoundError: No module named 'web_mcp'
```

- Ensure you're in the virtual environment
- Install the package: `pip install -e .`
- Check `PYTHONPATH` includes `src/`

### Debug Mode

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
python -m web_mcp.server
```

### Docker Debugging

```bash
# Run container interactively
docker run -it --entrypoint /bin/sh web-mcp:latest

# View logs
docker logs <container>
```

## Security Considerations

- **SearxNG Secret**: Change `SEARXNG_SECRET` in production
- **Rate Limiting**: Configure `RATE_LIMIT_REQUESTS` to prevent abuse
- **Network**: Container exposes port 8080 (for debugging only)
- **User Permissions**: Container defaults to root-managed processes; harden users/permissions for production

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest`
5. Submit a pull request

## Acknowledgments

- [SearxNG](https://github.com/searxng/searxng) - Privacy-respecting metasearch engine
- [MCP](https://modelcontextprotocol.io/) - Model Context Protocol
- [Trafilatura](https://github.com/adbar/trafilatura) - Web content extraction
