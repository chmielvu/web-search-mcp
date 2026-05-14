<!-- generated-by: gsd-doc-writer -->
# Deployment

This document covers deployment options for the Kindly Web Search MCP Server.

## Deployment Overview

| Method | Use Case | Transport |
|--------|----------|-----------|
| `uvx` (local) | AI coding assistants (Claude Code, Codex, Cursor) | stdio (default) |
| HTTP mode | Direct API testing, web hooks, remote clients | HTTP/SSE |
| Docker | Isolated environment, production deployments | stdio or HTTP |
| Docker Compose | Full stack with SearXNG | HTTP (via nginx) |

For AI coding assistant integration, **stdio transport via `uvx`** is the primary deployment method.

---

## Local Development

### uvx Installation (Recommended for MCP Clients)

The simplest deployment for AI coding assistants:

```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

This runs the server with stdio transport, which MCP hosts (Claude Code, etc.) communicate with via subprocess.

### MCP Client Configuration

Add to your MCP client config (e.g., Claude Code `mcp.json`):

```json
{
  "mcpServers": {
    "web-search": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server",
        "kindly-web-search-mcp-server",
        "start-mcp-server"
      ],
      "env": {
        "SEARXNG_BASE_URL": "http://localhost:8080",
        "GITHUB_TOKEN": "ghp_xxxx"
      }
    }
  }
}
```

---

## HTTP Transport Mode

For testing, debugging, or remote access:

```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

### Use Cases

- Direct API testing via HTTP endpoints
- Web hook integration
- Remote MCP client connections
- Browser-based debugging

### HTTP Endpoints

When running in HTTP mode, the server exposes:
- MCP JSON-RPC endpoint at `http://127.0.0.1:8000/mcp`
- SSE endpoint for streaming at `http://127.0.0.1:8000/sse`

### Bind Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTMCP_HOST` | `127.0.0.1` | Bind host (use `0.0.0.0` for public) |
| `FASTMCP_PORT` | `8000` | Bind port |

---

## Docker Deployment

### Dockerfile

The project includes a production-ready Dockerfile:

```dockerfile
FROM python:3.13-slim
# Installs chromium for nodriver-based universal HTML extraction
# Runs as non-root user (app:10001)
# Default: stdio transport
ENTRYPOINT ["mcp-web-search"]
CMD ["--stdio"]
```

### Building the Image

```bash
docker build -t kindly-web-search-mcp .
```

### Running with stdio (MCP Client Integration)

```bash
docker run -i --rm \
  -e SEARXNG_BASE_URL=http://host.docker.internal:8080 \
  -e GITHUB_TOKEN=ghp_xxxx \
  kindly-web-search-mcp --stdio
```

For MCP client configuration with Docker:

```json
{
  "mcpServers": {
    "web-search": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "SEARXNG_BASE_URL=http://host.docker.internal:8080", "kindly-web-search-mcp", "--stdio"],
      "env": {}
    }
  }
}
```

### Running with HTTP Transport

```bash
docker run -d --name web-search-mcp \
  -p 8000:8000 \
  -e SEARXNG_BASE_URL=http://host.docker.internal:8080 \
  -e GITHUB_TOKEN=ghp_xxxx \
  -e FASTMCP_HOST=0.0.0.0 \
  kindly-web-search-mcp --http --port 8000
```

### Container Environment Variables

Pass environment variables via `-e` flags or an env file:

```bash
docker run -i --rm \
  --env-file .env \
  -v ./lancedb_data:/app/lancedb_data \
  kindly-web-search-mcp --stdio
```

### Volume Mounts

| Path | Purpose |
|------|---------|
| `/app/lancedb_data` | Semantic cache persistence (set `KINDLY_LANCEDB_DIR`) |
| `/app/.env` | Environment file (alternative to `-e` flags) |

### Container Security Notes

- Runs as non-root user (`app:10001`)
- Chrome sandbox disabled (`KINDLY_NODRIVER_SANDBOX=0`) for container compatibility
- Minimal apt packages (chromium, ca-certificates, fonts-liberation)

---

## Full Stack: Docker Compose with SearXNG

The `searxng-settings/` directory contains a Docker Compose setup for the complete stack:

```bash
cd searxng-settings
docker compose up -d
```

This launches:
- **SearXNG** (port 8080 via nginx)
- **Valkey** (Redis-compatible cache for SearXNG)
- **nginx** reverse proxy

See [searxng-settings/docker-compose.yml](../searxng-settings/docker-compose.yml) for configuration details.

---

## Cloud Deployment Options

### Serverless Considerations

| Platform | Suitability | Notes |
|----------|-------------|-------|
| AWS Lambda | Limited | Browser pool requires persistent state; cold starts problematic for nodriver |
| Google Cloud Run | Possible | Container-based; needs browser pool tuning |
| Azure Container Instances | Possible | Similar to Cloud Run |

**Warning:** The nodriver browser pool requires persistent browser instances. Serverless platforms with cold starts may cause browser initialization delays (12-30 seconds). Consider:

- Disabling universal HTML extraction (`KINDLY_BROWSER_EXECUTABLE_PATH=""`)
- Using HTTP extraction fallback only
- Pre-warming strategies

### Container Services

| Platform | Configuration |
|----------|---------------|
| AWS ECS/Fargate | Use Docker image; configure health checks |
| Google GKE | Standard Kubernetes deployment |
| Azure AKS | Standard Kubernetes deployment |
| Fly.io | `fly.toml` with HTTP transport |

<!-- VERIFY: Cloud platform deployment URLs and dashboard links vary per organization -->

### Kubernetes Deployment Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-search-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web-search-mcp
  template:
    metadata:
      labels:
        app: web-search-mcp
    spec:
      containers:
      - name: mcp-server
        image: kindly-web-search-mcp:latest
        ports:
        - containerPort: 8000
        env:
        - name: SEARXNG_BASE_URL
          value: "http://searxng-service:8080"
        - name: FASTMCP_HOST
          value: "0.0.0.0"
        command: ["mcp-web-search", "--http", "--port", "8000"]
        resources:
          limits:
            memory: "1Gi"
            cpu: "500m"
```

---

## Environment Configuration

### Required Variables

At minimum, one search provider must be configured:

| Variable | Example |
|----------|---------|
| `SEARXNG_BASE_URL` | `http://localhost:8080` (self-hosted, unlimited) |
| `KINDLY_GEMINI_API_KEY` | Gemini API key (also enables `gemini_search` tool) |
| `TAVILY_API_KEY` | Tavily API key (paid provider) |
| `BRAVE_API_KEY` | Brave Search API key (paid provider) |
| `JINA_API_KEY` | Jina API key (also used for reranking) |

### Recommended Variables

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | GitHub personal access token for Issues/Discussions extraction |
| `STACKEXCHANGE_KEY` | Higher quota for StackExchange API |
| `MISTRAL_API_KEY` | Query rewrite/variant generation |
| `POLLINATIONS_API_KEY` | Perplexity Sonar search |

### Optional Feature Flags

| Variable | Default | Effect |
|----------|---------|--------|
| `KINDLY_SEMANTIC_CACHE_ENABLED` | `true` | Enable LanceDB semantic cache |
| `KINDLY_QUERY_REWRITE_ENABLED` | `true` | Enable query expansion |
| `KINDLY_RERANKING_ENABLED` | `true` | Enable Jina cross-encoder reranking |

See [CONFIGURATION.md](CONFIGURATION.md) for the complete environment variable reference.

---

## Scaling Considerations

### Rate Limit Handling

Internal rate limiting protects against provider API limits:

| Variable | Default | Scope |
|----------|---------|-------|
| `KINDLY_RATE_LIMIT_WEB_SEARCH_RPS` | `4.0` | Cheap tools (web_search, get_content, gemini_search) |
| `KINDLY_RATE_LIMIT_WEB_SEARCH_BURST` | `12` | Burst capacity for cheap tools |
| `KINDLY_RATE_LIMIT_EXPENSIVE_RPS` | `0.5` | perplexity_search only |
| `KINDLY_RATE_LIMIT_EXPENSIVE_BURST` | `1` | Burst for perplexity |

Provider-specific limits (external):
- **SearXNG**: Self-hosted, no limits
- **Tavily/Brave/Jina**: API-specific (check provider documentation)
- **Gemini**: Free tier limits (check Google AI Studio)

### Cache Persistence

For multi-instance deployments, consider:

| Component | Default | Scaling Strategy |
|-----------|---------|------------------|
| LanceDB semantic cache | `./lancedb_data` | Shared volume or network storage |
| SQLite query cache | In-memory | Per-instance (acceptable for stateless) |
| Page cache | In-memory | Per-instance |

For horizontal scaling, use a shared volume for `KINDLY_LANCEDB_DIR` or consider disabling semantic cache per-instance.

### Browser Pool Sizing

| Variable | Default | Scaling Notes |
|----------|---------|---------------|
| `KINDLY_NODRIVER_BROWSER_POOL_SIZE` | `1` | Increase for high concurrency |
| `KINDLY_NODRIVER_REUSE_BROWSER` | `1` | Keep enabled for efficiency |
| `KINDLY_NODRIVER_ACQUIRE_TIMEOUT_SECONDS` | `30` | Increase if pool contention |

For high-throughput content extraction, increase pool size to 2-3 instances per container.

### Concurrency Control

| Variable | Default | Range |
|----------|---------|-------|
| `KINDLY_WEB_SEARCH_MAX_CONCURRENCY` | `3` | Clamped 1-5 |

Controls simultaneous provider requests during `web_search`.

---

## Monitoring & Observability

### Logging Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `WARNING` | Python log level (stderr only) |
| `KINDLY_DIAGNOSTICS` | `0` | Enable verbose diagnostics (set to `1`) |
| `KINDLY_STRUCTURED_LOGGING` | `false` | JSON structured logs for Loki |

### OpenTelemetry Integration

For Grafana Cloud or other OTLP backends:

```powershell
$env:OTEL_SERVICE_NAME="kindly-web-search-mcp"
$env:OTEL_SERVICE_VERSION="0.1.8"
$env:OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
$env:OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20<YOUR_TOKEN>"
$env:DEPLOYMENT_ENV="production"
```

### Prometheus Metrics Endpoint

Optional Prometheus scrape endpoint:

```powershell
$env:KINDLY_PROMETHEUS_PORT="9090"
```

When set, metrics are exposed at `http://localhost:9090/metrics`.

### Key Metrics

| Metric | Description |
|--------|-------------|
| `web_search_requests_total` | Total search invocations |
| `web_search_provider_calls_total` | Per-provider call count |
| `web_search_provider_duration_seconds` | Provider latency histogram |
| `web_search_cache_requests_total` | Cache hit/miss count |
| `mcp_tool_invocations_total` | MCP tool call count |

### Health Checks

No explicit health check endpoint. Monitor via:
- OpenTelemetry span success rates
- Provider call status codes
- Cache hit rates

---

## Security Considerations

### API Key Management

- **Never commit secrets** to `.env` files or source code
- Use platform secret managers (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault)
- For Docker, pass via `-e` flags or mounted secret files

### Secrets in Containers

```bash
# AWS ECS: Use Task Definition secrets
# Kubernetes: Use Secrets and mount as env vars
# Docker Swarm: Use docker secrets
```

### Network Isolation

- Bind to `127.0.0.1` by default (local only)
- For production, use reverse proxy with authentication
- SearXNG should be behind nginx (as in docker-compose.yml)

### Non-Root User

Docker container runs as `app:10001` (non-root) by default. Do not modify unless required.

### Chrome Sandbox

Disabled in container environments (`KINDLY_NODRIVER_SANDBOX=0`). This is standard practice for:
- Docker containers
- WSL environments
- Headless Linux servers

For high-security deployments, re-enable sandbox only if Chrome runs with proper namespaces.

---

## Troubleshooting Deployment Issues

### Common Issues

| Problem | Solution |
|---------|----------|
| Server refuses to start | Check at least one provider env var is set |
| Browser not detected | Set `KINDLY_BROWSER_EXECUTABLE_PATH` explicitly |
| LanceDB write errors | Ensure `KINDLY_LANCEDB_DIR` is writable |
| SearXNG connection refused | Verify SearXNG is running; check URL in env |
| HTTP mode not accessible | Check `FASTMCP_HOST` (use `0.0.0.0` for public) |
| Timeout errors | Increase `KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS` |

### Container-Specific Issues

| Problem | Solution |
|---------|----------|
| Chromium fails to start | Ensure fonts-liberation installed; check sandbox disabled |
| DNS resolution fails | Use `host.docker.internal` for local services |
| Volume permission denied | Check container user (10001) has write access |

### WSL/Windows Specific

| Problem | Solution |
|---------|----------|
| Browser auto-detection fails | Set explicit Chrome/Edge path |
| LanceDB path issues | Use relative path or Windows-compatible mount |

### Diagnostic Mode

Enable verbose diagnostics:

```bash
KINDLY_DIAGNOSTICS=1 LOG_LEVEL=DEBUG kindly-web-search-mcp-server start-mcp-server
```

---

## Related Documentation

- [CONFIGURATION.md](CONFIGURATION.md) - Complete environment variable reference
- [GETTING-STARTED.md](GETTING-STARTED.md) - Quick start guide
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture overview
- [searxng-settings/docker-compose.yml](../searxng-settings/docker-compose.yml) - SearXNG stack configuration