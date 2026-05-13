# SearXNG for AI Coding Agents

Optimized SearXNG deployment for AI coding agents (smolagents, LangChain, MCP servers) focusing on reliable general discovery, exact coding-error lookup, package lookup, and science/AI searches.

## Quick Start

```bash
docker compose -f searxng-settings/docker-compose.yml up -d
```

Wait ~15 seconds for health check, then verify:
```bash
curl "http://localhost:8080/search?q=python async retry&format=json"
```

## Configuration Highlights

### Engine Curation (15 engines, live-tested for this MCP)

| Category | Engines | Weight |
|----------|---------|--------|
| **General default** | duckduckgo, startpage, wikipedia | 1.0-1.4 |
| **Code/Q&A available** | github, github code, stackoverflow, askubuntu, superuser | 1.4-2.5 |
| **Packages/AI** | pypi, npm, huggingface | 1.4-1.8 |
| **Science** | arxiv, semantic scholar, openalex, pubmed | 1.1-1.5 |

Excluded after live tests:

- `bing`: returned high-rank unrelated pages for exact technical queries.
- `brave`: immediately hit upstream too-many-requests suspension locally.
- `mojeek`: loaded, then failed its startup probe with HTTP 403 and suspended for 24h.
- `crossref`: produced useful science results but timed out during live probes and triggered a SearXNG unresponsive-engine error.
- Broad `it` engines such as Docker Hub, Sourcehut, MDN, and Microsoft Learn: useful in principle, but noisy without per-query routing.

### Key Settings

- `keep_only:` pattern for clean whitelist
- `limiter: true` with reverse proxy + forwarded client IP headers
- `request_timeout: 3.0s` for responsiveness
- Valkey 9-alpine with LRU cache (256MB)
- `SEARXNG_VALKEY_URL` / `valkey.url`, not deprecated `redis.url`
- JSON format enabled

### `github code` Authentication

Default uses `ghc_auth.type: "none"` (works without token, ~30 req/min).

For higher limits, add a GitHub PAT:
```yaml
- name: github code
  ghc_auth:
    type: "personal_access_token"
    token: "ghp_your_token_here"
```

## Usage with AI Agents

### LangChain Integration

```python
from langchain_community.tools import SearxngSearch

search = SearxngSearch(
    searx_host="http://localhost:8080",
    params={"format": "json"}
)

# Per-query engine override
search.run("python async retry pattern", 
    engines="github_code,stackoverflow,pypi")
```

### MCP Integration (ihor-sokoliuk/mcp-searxng)

Add to Claude Code settings:
```json
{
  "mcpServers": {
    "searxng": {
      "command": "npx",
      "args": ["-y", "@ihor-sokoliuk/mcp-searxng"],
      "env": {
        "SEARXNG_URL": "http://localhost:8080"
      }
    }
  }
}
```

### Direct JSON API

```bash
# Basic search
curl "http://localhost:8080/search?q=query&format=json"

# Exact coding-error search; default general engines performed best in live tests
curl "http://localhost:8080/search?q=%22RuntimeError%3A%20Event%20loop%20is%20closed%22%20%22pytest-asyncio%22%20github%20issue&format=json"

# Science search
curl "http://localhost:8080/search?q=transformer+attention+mechanism&format=json&categories=science"

# Time-filtered (recent)
curl "http://localhost:8080/search?q=python news&format=json&time_range=week"
```

## Docker Management

```bash
# Start
docker compose -f searxng-settings/docker-compose.yml up -d

# Logs
docker compose -f searxng-settings/docker-compose.yml logs -f searxng

# Health check
docker compose -f searxng-settings/docker-compose.yml ps

# Stop
docker compose -f searxng-settings/docker-compose.yml down

# Update
docker compose -f searxng-settings/docker-compose.yml pull
docker compose -f searxng-settings/docker-compose.yml up -d
```

## Validation Checklist

After deployment, verify:
1. `curl "http://localhost:8080/search?q=test&format=json"` → returns JSON
2. FastMCP docs query returns `gofastmcp.com` in the top results in ~3s.
3. Exact pytest-asyncio error query returns GitHub issues / StackOverflow in the top results in ~3s.
4. Science query with `categories=science` returns arXiv/Semantic Scholar/OpenAlex/PubMed in ~5-6s.
5. Avoid relying on `categories=it` until query-specific routing is implemented and re-tested; live tests showed either noisy results or zero results depending on the engine set.

## Resource Limits

Default config:
- 2 CPU cores max
- 1GB memory max
- Valkey cache: 256MB with LRU eviction

Adjust in docker-compose.yml for your hardware.

## Files

- `docker-compose.yml` - Container orchestration
- `searxng-config/settings.yml` - SearXNG configuration
- `README.md` - This file
