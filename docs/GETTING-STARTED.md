<!-- generated-by: gsd-doc-writer -->
# Getting Started

This guide walks you through installing and running Kindly Web Search MCP Server for the first time.

## Prerequisites

Before installing, ensure you have the following:

| Requirement | Minimum Version | Notes |
|-------------|-----------------|-------|
| Python | 3.13+ | Required for `uvx` package execution |
| uv/uvx | Latest | Astral's Python package runner |
| Chromium-based browser | Any recent version | Chrome, Chromium, Edge, or Brave (optional but recommended for JS-heavy sites) |

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

A Chromium-based browser is required for extracting content from JavaScript-heavy websites. Specialized sources (StackOverflow, GitHub Issues, Wikipedia, arXiv) work without a browser via direct API integration.

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

You need at least one search provider. SearXNG is the primary (self-hosted, unlimited queries), while Gemini, Tavily, Brave, Jina, and Composio provide additional options.

**macOS / Linux:**
```bash
# Primary (self-hosted SearXNG):
export SEARXNG_BASE_URL="http://localhost:8080"

# Or paid/conditional providers:
export TAVILY_API_KEY="your-tavily-key"
export BRAVE_API_KEY="your-brave-key"
export JINA_API_KEY="your-jina-key"
export KINDLY_GEMINI_API_KEY="your-gemini-key"

# Or Composio LLM Search:
export COMPOSIO_API_KEY="your-composio-key"
export KINDLY_COMPOSIO_USER_ID="default"
```

**Windows (PowerShell):**
```powershell
$env:SEARXNG_BASE_URL="http://localhost:8080"

# Or paid/conditional providers:
$env:TAVILY_API_KEY="your-tavily-key"
$env:BRAVE_API_KEY="your-brave-key"
$env:JINA_API_KEY="your-jina-key"
$env:KINDLY_GEMINI_API_KEY="your-gemini-key"

# Or Composio LLM Search:
$env:COMPOSIO_API_KEY="your-composio-key"
$env:KINDLY_COMPOSIO_USER_ID="default"
```

### 2. Set GitHub Token (Recommended)

A GitHub token improves Issue/Discussion extraction quality and reduces rate limits. A read-only token for public repos is sufficient.

**macOS / Linux:**
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

Once your MCP client is configured, you can use the `web_search` tool. Note that `research_goal` is a **required** parameter describing what you're looking for.

**Tool call:**
```
web_search(
  query="how to fix Python asyncio timeout error",
  research_goal="Debug exact error and find reproducible fix for production deployment",
  num_results=3
)
```

**Expected output:**
```json
{
  "query": "how to fix Python asyncio timeout error",
  "results": [
    {
      "title": "asyncio.timeout() raises TimeoutError",
      "link": "https://stackoverflow.com/questions/...",
      "snippet": "Use asyncio.wait_for() with a timeout parameter...",
      "provider_count": 2
    },
    ...
  ]
}
```

The `web_search` tool returns lightweight results (title, link, snippet, provider_count) for discovery. Use `get_content` to extract full page content from a specific URL.

## First Content Extraction Example

Use `get_content` to fetch LLM-ready Markdown from a specific URL:

**Tool call:**
```
get_content(url="https://stackoverflow.com/questions/76546453/gcp-cloud-batch-gpu-error")
```

**Expected output:**
```json
{
  "input_url": "https://stackoverflow.com/questions/76546453/gcp-cloud-batch-gpu-error",
  "normalized_url": "https://stackoverflow.com/questions/76546453/gcp-cloud-batch-gpu-error",
  "fetched_url": "https://stackoverflow.com/questions/76546453/gcp-cloud-batch-gpu-error",
  "status": "success",
  "source_type": "stackexchange",
  "fetch_backend": "stackexchange_api",
  "page_content": "# GCP Cloud Batch fails with GPU instance template\n\n**Question:** I am trying to run a GCP Cloud Batch job...\n\n**Answer 1 (Accepted):** The issue is with the instance template...\n\n**Answer 2:** I had the same problem, here's what worked...\n\n**Comments:** This fixed it for me...",
  "window": {
    "offset": 0,
    "length": 20000,
    "returned_chars": 20000,
    "total_chars": 52000,
    "has_more": true,
    "next_offset": 20000
  }
}
```

For StackOverflow/StackExchange, GitHub Issues, Wikipedia, and arXiv, the server uses direct API integration to return full conversations with answers and comments.

## Batch Content Extraction Example

For 3+ URLs, use `batch_get_content` for efficient parallel fetching with budget control:

**Tool call:**
```
batch_get_content(
  urls=[
    "https://stackoverflow.com/questions/12345",
    "https://github.com/owner/repo/issues/100",
    "https://docs.python.org/3/library/asyncio.html"
  ],
  total_char_budget=60000
)
```

**Expected output:**
```json
{
  "results": [...],
  "total_requested": 3,
  "total_returned": 3,
  "total_chars_returned": 45000,
  "has_more": false,
  "cursor": null
}
```

If `has_more` is true, call again with the returned `cursor` to continue fetching.

## Using AI Search Tools

Kindly provides two AI-powered search tools that synthesize answers with citations.

### Gemini Search

Returns grounded answers with Google Search citations. Use for quick factual questions.

**Tool call:**
```
gemini_search(
  query="What is the latest stable version of FastMCP?",
  research_goal="Find current version for upgrade planning"
)
```

**Requirements:** Set `KINDLY_GEMINI_API_KEY` environment variable.

### Perplexity Search

Returns AI-synthesized answers with source citations using Perplexity Sonar. **This tool is rate-limited due to resource costs.** First call returns a steering message with query-writing guidance.

**Tool call:**
```
perplexity_search(
  query="Compare React Server Components vs traditional React SSR",
  depth="normal",
  research_goal="Understand architectural differences for migration decision"
)
```

**Requirements:** Set `POLLINATIONS_API_KEY` environment variable.

## YouTube Tools Usage

### YouTube Search

Find YouTube videos on a topic (search first, then extract transcripts):

**Tool call:**
```
youtube_search(query="Python asyncio tutorial", num_results=5)
```

**Requirements:** Requires `SEARXNG_BASE_URL` to be configured.

### YouTube Transcript

Extract captions from a YouTube video:

**Tool call:**
```
youtube_transcript(
  video_id_or_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
  format="text"
)
```

**Supported formats:**
- `text` — Plain transcript text
- `timestamped` — Transcript with timestamps [MM:SS]
- `json` — Raw transcript segments

**Recommended workflow:** Use `youtube_search` to find videos, then `youtube_transcript` to extract content.

## Composio Tools Usage

### Composio Similarlinks

Find related URLs from a known good starting point:

**Tool call:**
```
composio_similarlinks(url="https://docs.python.org/3/library/asyncio.html")
```

**Requirements:** Set `COMPOSIO_API_KEY` and `KINDLY_COMPOSIO_USER_ID` environment variables.

## Client Configuration

### Claude Code

**CLI method:**
```bash
claude mcp add --transport stdio kindly-web-search \
  -e SEARXNG_BASE_URL="$SEARXNG_BASE_URL" \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -- uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server
```

**Windows (PowerShell):**
```powershell
claude mcp add --transport stdio kindly-web-search `
  -e SEARXNG_BASE_URL="$env:SEARXNG_BASE_URL" `
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
        "SEARXNG_BASE_URL": "${SEARXNG_BASE_URL}",
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

If Claude Code times out on first run, prewarm the environment:
```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

### Codex

**CLI method:**
```bash
codex mcp add kindly-web-search \
  --env SEARXNG_BASE_URL="$SEARXNG_BASE_URL" \
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
        "SEARXNG_BASE_URL": "${env:SEARXNG_BASE_URL}",
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
        "SEARXNG_BASE_URL": "http://localhost:8080",
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
- `KINDLY_GEMINI_API_KEY`
- `TAVILY_API_KEY`
- `BRAVE_API_KEY`
- `JINA_API_KEY`
- `COMPOSIO_API_KEY` + `KINDLY_COMPOSIO_USER_ID`

### Browser Not Found

**Error:** `No Chromium-based browser executable found`

**Fix:**
1. Install Chrome/Chromium/Edge
2. Set the path if auto-detection fails:

**macOS / Linux:**
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

**macOS / Linux:**
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

### YouTube Transcript Blocked

**Error:** `Transcripts may be disabled or blocked`

**Fix:** Cloud IPs (AWS/GCP/Azure) may be blocked by YouTube. Use a proxy:
```bash
export KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL="http://your-proxy:8080"
```

### First Run Slow Startup

**Issue:** MCP client times out on first server start

**Fix:** Run the `uvx` command once in a terminal to prebuild the environment:
```bash
uvx --from git+https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server \
  kindly-web-search-mcp-server start-mcp-server --http --port 8000
```

After the environment builds (30-60s), stop with Ctrl+C and retry in your MCP client.

## Tool Routing Summary

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `web_search` | Discover URLs | Start here for web discovery. `rewrite=true` is standard. |
| `get_content` | Read one URL | When you have a specific URL to extract. |
| `batch_get_content` | Read 3+ URLs | Efficient parallel fetch with budget control. |
| `gemini_search` | Quick grounded answers | Factual questions with Google Search grounding. |
| `perplexity_search` | Deep synthesis | After refining query to one focused topic. |
| `youtube_search` | Find videos | Before extracting transcripts. |
| `youtube_transcript` | Extract video captions | After `youtube_search` returns video IDs. |
| `composio_similarlinks` | Find related URLs | Expand from a known good starting URL. |

## Next Steps

After getting the server running:

- **See ARCHITECTURE.md** for a deep dive into the search pipeline, content resolution, and caching layers
- **See CONFIGURATION.md** for complete environment variable documentation and tuning options
- **See DEVELOPMENT.md** for contributing guidelines and local development setup
- **See TESTING.md** for testing patterns and mock conventions