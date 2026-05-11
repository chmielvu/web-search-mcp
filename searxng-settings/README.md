# SearXNG for AI Coding Agents

Optimized SearXNG deployment for AI coding agents (smolagents, LangChain, MCP servers) focusing on Python and AI/ML queries.

## Quick Start

```bash
cd C:\Users\Jan\CLI\searxng-agent
docker-compose up -d
```

Wait ~15 seconds for health check, then verify:
```bash
curl "http://localhost:8080/search?q=python async retry&format=json&engines=github_code,stackoverflow"
```

## Configuration Highlights

### Engine Curation (12 engines, optimized for coding)

| Category | Engines | Weight |
|----------|---------|--------|
| **General** | duckduckgo, brave, wikipedia | 1.0-1.5 |
| **Code** | github, github_code, stackoverflow, pypi, npm | 2.0-2.5 |
| **AI/ML** | huggingface, arxiv | 1.5-1.8 |
| **Q&A** | askubuntu, superuser | 1.5 |

### Key Settings

- `keep_only:` pattern for clean whitelist
- `limiter: false` for agent workloads
- `request_timeout: 2.5s` for responsiveness
- Valkey 9-alpine with LRU cache (256MB)
- JSON format enabled

### github_code Authentication

Default uses `ghc_auth.type: "none"` (works without token, ~30 req/min).

For higher limits, add a GitHub PAT:
```yaml
- name: github_code
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

# Code-specific search
curl "http://localhost:8080/search?q=error+ModuleNotFoundError&format=json&engines=stackoverflow,github_code&categories=it"

# AI/ML search
curl "http://localhost:8080/search?q=transformer+attention+mechanism&format=json&engines=arxiv,huggingface&categories=science"

# Time-filtered (recent)
curl "http://localhost:8080/search?q=python news&format=json&time_range=week"
```

## Docker Management

```bash
# Start
docker-compose up -d

# Logs
docker-compose logs -f searxng

# Health check
docker-compose ps

# Stop
docker-compose down

# Update
docker-compose pull && docker-compose up -d
```

## Validation Checklist

After deployment, verify:
1. `curl "http://localhost:8080/search?q=test&format=json"` → returns JSON
2. Cache hit: repeat same query → latency <100ms
3. Code engines: query with `engines=github_code` → returns code snippets
4. No rate limits: rapid queries work without 429 errors

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