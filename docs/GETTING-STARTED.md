<!-- generated-by: gsd-doc-writer -->
# Getting Started

This guide walks you through installing and running Kindly Web Search MCP Server for the first time.

## Prerequisites

Before installing, ensure you have the following:

| Requirement | Minimum Version | Notes |
|-------------|-----------------|-------|
| Python | 3.13+ | Required for `uvx` package execution |
| uv/uvx | Latest | Astral's Python package runner |
| Chromium-based browser | Any recent version | Chrome, Chromium, Edge, or Brave (optional but recommended) |

### Installing uvx

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

Re-open your terminal and verify:
```bash
uvx --version
```

### Installing a Browser (Optional)

A Chromium-based browser is required for extracting content from JavaScript-heavy websites. Specialized sources (StackOverflow, GitHub Issues, Wikipedia, arXiv) work without a browser.

**macOS:**
```bash
brew install --cask chromium
```

**Windows:** Install Chrome or Edge from their official websites.

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get update
sudo apt-get install -y chromium
```

## Installation Steps

### 1. Set Search Provider Credentials

You need at least one search provider. SearXNG is the primary (self-hosted, unlimited queries), while Tavily, Brave, and Jina run concurrently as paid alternatives.

**macOS / Linux:**
```bash
# Primary (self-hosted SearXNG):
export SEARXNG_BASE_URL="http://localhost:8080"
# Or paid providers:
export TAVILY_API_KEY="your-tavily-key"
export BRAVE_API_KEY="your-brave-key"
export JINA_API_KEY="your-jina-key"
```

**Windows (PowerShell):**
```powershell
$env:SEARXNG_BASE_URL="http://localhost:8080"
# Or paid providers:
$env:TAVILY_API_KEY="your-tavily-key"
$env:BRAVE_API_KEY="your-brave-key"
$env:JINA_API_KEY="your-jina-key"
```

### 2. Set GitHub Token (Recommended)

A GitHub token improves Issue/Discussion extraction quality and reduces rate limits. A read-only token for public repos is sufficient.

```bash
export GITHUB_TOKEN="your-github-token"
```

**Windows (PowerShell):**
```powershell
$env:GITHUB_TOKEN="your-github-token"
```

### 3. Run the MCP Server

The standard command used by all MCP clients:

```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

**First-run note:** The initial `uvx` invocation may take 30-60 seconds while it builds the tool environment. If your MCP client times out, run the command once in a terminal to prewarm it, then retry in your client.

### 4. Configure Your MCP Client

Add the server to your MCP client configuration. See the [Client Configuration](#client-configuration) section below for specific client setups.

## First Search Example

Once your MCP client is configured, you can use the `web_search` tool:

**Tool call:**
```
web_search(query="how to fix Python asyncio timeout error", num_results=3)
```

**Expected output:**
```json
{
  "results": [
    {
      "title": "asyncio.timeout() raises TimeoutError",
      "link": "https://stackoverflow.com/questions/...",
      "snippet": "Use asyncio.wait_for() with a timeout parameter..."
    },
    ...
  ]
}
```

The `web_search` tool returns lightweight results (title, link, snippet) for discovery. Use `get_content` to extract full page content.

## First Content Extraction Example

Use `get_content` to fetch LLM-ready Markdown from a specific URL:

**Tool call:**
```
get_content(url="https://stackoverflow.com/questions/76546453/gcp-cloud-batch-gpu-error")
```

**Expected output:**
```json
{
  "page_content": "# GCP Cloud Batch fails with GPU instance template\n\n**Question:** I am trying to run a GCP Cloud Batch job...\n\n**Answer 1 (Accepted):** The issue is with the instance template...\n\n**Answer 2:** I had the same problem, here's what worked...\n\n**Comments:** This fixed it for me...",
  "url": "https://stackoverflow.com/questions/76546453/..."
}
```

For StackOverflow/StackExchange, GitHub Issues, Wikipedia, and arXiv, the server uses direct API integration to return full conversations with answers and comments.

## Using AI Search Tools

Kindly provides two AI-powered search tools that synthesize answers with citations.

### Gemini Search

Returns grounded answers with Google Search citations:

**Tool call:**
```
gemini_search(query="What is the latest stable version of FastMCP?")
```

**Requirements:** Set `KINDLY_GEMINI_API_KEY` environment variable.

### Perplexity Search

Returns AI-synthesized answers with source citations using Perplexity Sonar:

**Tool call:**
```
perplexity_search(query="Compare React Server Components vs traditional React SSR", depth="normal")
```

**Note:** This tool is rate-limited due to resource costs. First call returns query-writing guidance.

## YouTube Tools Usage

### YouTube Transcript

Extract captions from a YouTube video:

**Tool call:**
```
youtube_transcript(video_id_or_url="https://youtube.com/watch?v=dQw4w9WgXcQ", format="text")
```

**Supported formats:**
- `text` - Plain transcript text
- `timestamped` - Transcript with timestamps

### YouTube Search

Find YouTube videos on a topic:

**Tool call:**
```
youtube_search(query="Python asyncio tutorial", num_results=5)
```

## Client Configuration

### Claude Code

**CLI method:**
```bash
claude mcp add --transport stdio kindly-web-search \
  -e TAVILY_API_KEY="$TAVILY_API_KEY" \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -- uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

**Windows (PowerShell):**
```powershell
claude mcp add --transport stdio kindly-web-search `
  -e TAVILY_API_KEY="$env:TAVILY_API_KEY" `
  -e GITHUB_TOKEN="$env:GITHUB_TOKEN" `
  -- uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server `
  kindly-web-search-mcp-server start-mcp-server
```

**File method:** Create `.mcp.json` in your project:
```json
{
  "mcpServers": {
    "kindly-web-search": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server",
        "kindly-web-search-mcp-server",
        "start-mcp-server"
      ],
      "env": {
        "TAVILY_API_KEY": "${TAVILY_API_KEY}",
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

If Claude Code times out, set a 120s startup timeout:
```bash
export MCP_TIMEOUT=120000
```

### Codex

**CLI method:**
```bash
codex mcp add kindly-web-search \
  --env TAVILY_API_KEY="$TAVILY_API_KEY" \
  --env GITHUB_TOKEN="$GITHUB_TOKEN" \
  -- uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

### Cursor

Create `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "kindly-web-search": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server",
        "kindly-web-search-mcp-server",
        "start-mcp-server"
      ],
      "env": {
        "TAVILY_API_KEY": "${env:TAVILY_API_KEY}",
        "GITHUB_TOKEN": "${env:GITHUB_TOKEN}"
      }
    }
  }
}
```

### Claude Desktop

Edit `claude_desktop_config.json`:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kindly-web-search": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server",
        "kindly-web-search-mcp-server",
        "start-mcp-server"
      ],
      "env": {
        "TAVILY_API_KEY": "paste-your-key",
        "GITHUB_TOKEN": "paste-your-token"
      }
    }
  }
}
```

## Troubleshooting Common Issues

### No Search Provider Key

**Error:** `web_search fails: no provider key`

**Fix:** Set at least one of:
- `SEARXNG_BASE_URL`
- `TAVILY_API_KEY`
- `BRAVE_API_KEY`
- `JINA_API_KEY`

### Browser Not Found

**Error:** `No Chromium-based browser executable found`

**Fix:**
1. Install Chrome/Chromium/Edge
2. Set the path if auto-detection fails:
   ```bash
   export KINDLY_BROWSER_EXECUTABLE_PATH="/usr/bin/chromium"
   ```
   **Windows:**
   ```powershell
   $env:KINDLY_BROWSER_EXECUTABLE_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
   ```

### Content Extraction Timeout

**Error:** `Failed to retrieve page content: TimeoutError`

**Fix:** Increase timeout settings:
```bash
export KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS=180
export KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS=600
```

**Windows:**
```powershell
$env:KINDLY_TOOL_TOTAL_TIMEOUT_SECONDS="180"
$env:KINDLY_TOOL_TOTAL_TIMEOUT_MAX_SECONDS="600"
```

### Browser Connection Failed

**Error:** `Failed to connect to browser`

**Fix:**
1. Increase retries: `KINDLY_NODRIVER_RETRY_ATTEMPTS=5`
2. Ensure proxy settings don't interfere: `NO_PROXY=localhost,127.0.0.1`
3. Increase ready timeout: `KINDLY_NODRIVER_DEVTOOLS_READY_TIMEOUT_SECONDS=20`

### First Run Slow Startup

**Issue:** MCP client times out on first server start

**Fix:** Run the `uvx` command once in a terminal to prebuild the environment, then retry in your MCP client.

## Next Steps

After getting the server running:

- **See ARCHITECTURE.md** for a deep dive into the search pipeline, content resolution, and caching layers
- **See CONFIGURATION.md** for complete environment variable documentation and tuning options
- **See DEVELOPMENT.md** for contributing guidelines and local development setup