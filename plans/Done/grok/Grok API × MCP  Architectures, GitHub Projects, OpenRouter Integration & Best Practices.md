# Grok API × MCP: Architectures, GitHub Projects, OpenRouter Integration & Best Practices
## Executive Summary
xAI's Grok API provides two native server-side search tools — `web_search` and `x_search` — that are exposed exclusively through the **Responses API** (`/v1/responses`), not the Chat Completions endpoint. Wrapping these tools in an MCP server gives agents like Claude Code, Cursor, and any other MCP client access to real-time web and X/Twitter search backed by Grok's live data infrastructure. As of mid-2026, the ecosystem has matured significantly: Grok 4.3 is the recommended production model for agentic MCP workflows, Grok 4.1 Fast is the most cost-effective option, and OpenRouter offers a parallel but structurally different integration path.[^1][^2][^3][^4]

***
## 1. The Core Architecture: How Grok Search Tools Work
### 1.1 Responses API vs Chat Completions
The critical architectural decision when building a Grok MCP server is which API endpoint to use. xAI's official stance is unambiguous:[^1]

| Feature | Responses API (`/v1/responses`) | Chat Completions (`/v1/chat/completions`) |
|---|---|---|
| `web_search` tool | ✅ Native support | ❌ Not available |
| `x_search` tool | ✅ Native support | ❌ Not available |
| MCP remote tools | ✅ Supported | ❌ Not supported |
| Stateful conversations | Built-in via `previous_response_id` | Manual history management |
| Reasoning models | Full support | No reasoning content |
| Billing optimization | Automatic caching | Full history billed each turn |
| Future features | All new capabilities here | Legacy, limited updates |

**Bottom line**: Every Grok MCP server that wraps real native search MUST use `/v1/responses` as the backend. Projects that use `/v1/chat/completions` with a `search_parameters` hack (an older pattern) will return stale training data without the live infrastructure.
### 1.2 The Two Native Search Tools
**`web_search`** — general web with real-time browsing:[^5]
- `allowed_domains` / `excluded_domains` (max 5 each)
- `enable_image_understanding` — Grok can analyze images found on pages
- `enable_image_search` — returns Markdown image embeds in responses

**`x_search`** — live X/Twitter indexing:[^6]
- `allowed_x_handles` / `excluded_x_handles` (max 20 each, mutually exclusive)
- `from_date` / `to_date` in ISO8601
- `enable_image_understanding` — analyze images in posts
- `enable_video_understanding` — analyze videos in posts (X Search only)

Both tools are invoked the same way in the Responses API:

```python
from openai import OpenAI

client = OpenAI(api_key=os.getenv("XAI_API_KEY"), base_url="https://api.x.ai/v1")

response = client.responses.create(
    model="grok-4.3",
    input=[{"role": "user", "content": "What are people saying about LLMs on X today?"}],
    tools=[
        {"type": "web_search"},
        {"type": "x_search"}
    ],
)
```

The model decides when to invoke each tool. Citations are returned in `response.citations`.
### 1.3 Live Search Billing
Search is billed per source used, not per request:[^7]
- **$25 per 1,000 sources** ($0.025/source)
- Each web page, X post, news item, or RSS item = 1 source
- A typical search call resolves 3–10 sources

For a high-volume agent, search billing can dominate token costs. Cap result counts where possible.
### 1.4 xAI's Own MCP Infrastructure
xAI operates a public docs MCP server at `https://docs.x.ai/api/mcp` and supports **Remote MCP Tools** directly from the Responses API:[^8][^9]

```python
from xai_sdk.tools import mcp

chat = client.chat.create(
    model="grok-4.3",
    tools=[
        mcp(server_url="https://your-mcp.example.com/mcp", server_label="mytools"),
        mcp(server_url="https://mcp.deepwiki.com/mcp", server_label="deepwiki"),
    ],
)
```

This means Grok itself can *call out to* any Streamable HTTP or SSE MCP server — a reversal of the typical pattern where Claude calls an MCP server that proxies Grok. Both directions are now valid architecture choices.

***
## 2. Agent-to-Tool Flow: MCP Architecture Patterns
### Pattern A: Local Stdio MCP (Standard, Claude Code / Claude Desktop)
```
Claude Code / Claude Desktop
        │  JSON-RPC over stdio
        ▼
Local MCP Server Process (Node.js / Python)
        │  HTTPS REST
        ▼
xAI Responses API (api.x.ai/v1/responses)
  ├── web_search tool
  └── x_search tool
```

**When to use**: Personal dev workflows, Claude Desktop, Claude Code on your machine.
**Pros**: Zero latency to MCP layer, no hosting cost, trivial auth (env var).
**Cons**: Doesn't work for Claude Routines, mobile clients, or team-shared setups.
### Pattern B: Remote HTTP MCP (Cloudflare Workers)
```
Claude (any surface — Routines, Desktop, mobile)
        │  Streamable HTTP MCP
        ▼
Cloudflare Worker (your account, ~free tier)
        │  Bearer token via Worker secret
        ▼
xAI Responses API
  ├── web_search tool
  └── x_search tool
```

**When to use**: Claude Routines (autonomous agents), shared team access, mobile use.
**Pros**: Portable, always-on, no laptop dependency, Cloudflare free tier = 100k req/day.
**Cons**: Requires Cloudflare account, slightly more setup, no stateful context.
### Pattern C: Grok as Orchestrator with MCP Clients
```
User / Automation
        │
Grok Responses API (grok-4.3 as orchestrator)
        │  Remote MCP Tool calls
        ▼
One or more Remote MCP Servers
  ├── Your business logic MCP
  ├── Database MCP
  └── External API MCP
```

**When to use**: Grok-native pipelines, Grok Build CLI workflows, or when you want Grok reasoning to drive tool selection across many MCP endpoints.[^8]
### Pattern D: Multi-Model via OpenRouter (Grok as Capability Layer)
```
Claude Code (orchestrator, MCP client)
        │
openrouter:web_search server tool
        │  engine: "native" → routes to xAI
        ▼
xAI native web_search + x_search
        │
Synthesized answer with citations → back to Claude
```

This pattern is important for multi-model architectures where Claude is the agent but you want Grok's native search quality.[^10][^11]

***
## 3. Top 5 GitHub MCP Projects: Evaluation
### 3.1 `stat-guy/grok-search-mcp` ⭐ Recommended for Claude Desktop
**Pattern**: Local Stdio (Pattern A), Node.js, NPX-compatible  
**Tools exposed**: `grok_search`, `grok_web_search`, `grok_news_search`, `grok_twitter`, `health_check`

**Architecture analysis**: Uses `@modelcontextprotocol/sdk` with `StdioServerTransport`. The internal API call correctly targets `/v1/chat/completions` with `search_parameters: { mode: "on" }` — this is the legacy live search API, not the newer Responses API. Still works, but misses Responses API features (stateful turns, reasoning integration).

**Strengths**:
- Highly detailed `learnings.md` documenting every failure mode encountered
- Comprehensive mode returns structured timelines, direct quotes, multi-perspective analysis, verification status
- Configurable retry logic with exponential backoff
- Production-ready error handling patterns

**Key lessons documented in repo**:
- Always test `npx <package-name>` before Claude Desktop config
- Model name changes break everything silently (`grok-beta` → `grok-3-latest` → now `grok-4.3`)
- Must explicitly set `search_parameters.mode: "on"` or you get training-data-only responses
- Always request JSON format in system prompt — regex parsing of natural language is fragile

**Verdict**: Best learning resource and battle-tested Node.js reference for Claude Desktop. Upgrade the backend to `/v1/responses` for full feature access.

***
### 3.2 `auny-ai/grok-mcp-server` ⭐⭐ Recommended for Remote / Routines
**Pattern**: Remote Streamable HTTP (Pattern B), TypeScript, Cloudflare Workers  
**Tools exposed**: `x_search`, `grok_web_search`, `grok_chat`, `grok_image_generate`, `grok_image_understand`, `grok_image_edit`, `grok_video_generate`, `grok_structured_output`, `grok_reasoning` (9 tools)

**Architecture analysis**: Stateless Cloudflare Worker with a shared `xaiFetch(env, path, body)` helper. Each tool maps to the appropriate xAI API endpoint. Correct use of Worker secrets for `XAI_API_KEY`. Client→server auth is URL-secrecy only (add bearer token in `fetch` handler for teams).

```
MCP client (Claude / Cursor)
        │  Streamable HTTP MCP at /mcp
        ▼
Cloudflare Worker
        │  Bearer auth via Worker secret
        ▼
xAI API (api.x.ai/v1)
  ├── /responses       — chat, search, vision, structured, reasoning
  ├── /images/generations
  ├── /images/edits
  └── /videos/generations
```

**Strengths**:
- Best architecture for portable, always-on access
- Comprehensive tool surface: media generation + search + reasoning in one server
- 4-command deploy: `git clone` → `npm install` → `wrangler secret put` → `wrangler deploy`
- Well-documented `server.tool()` pattern for adding custom tools
- Part of the larger `auny-ai/claude-os` ecosystem

**Weaknesses**:
- Uses `grok-4.3` hardcoded default (no model override UX for non-dev users)
- Client→server auth is URL-only, inadequate for shared deployments
- No streaming responses from Worker to client (returns full response)

**Verdict**: Best production-ready remote MCP server for Grok. Strong reference architecture for Cloudflare Workers pattern.

***
### 3.3 `wynandw87/claude-code-grok-mcp` ⭐⭐ Most Complete Feature Set
**Pattern**: Local Stdio (Pattern A), Python, `claude mcp add` compatible  
**Tools exposed**: 18 tools including `ask`, `code_review`, `brainstorm`, `search_web`, `search_x`, `run_code`, `upload_file`, `generate_image`, `edit_image`, `generate_video`, `edit_video`, `analyze_image`, `text_to_speech`, `speech_to_text`, `chat` (with multi-turn sessions), `list_sessions`, `end_session`, `server_info`

**Architecture analysis**: Uses the xAI REST API directly (`requests` library, no SDK), mixing `/v1/responses` for search/web tools, `/v1/chat/completions` for multi-turn sessions, and dedicated endpoints for media and voice. The Python server registers via `claude mcp add -s user -t stdio Grok python3 /path/to/server.py -e XAI_API_KEY=...`.

**Standout features**:
- Server-side session management for multi-turn Grok conversations (30-minute TTL, session IDs)
- Full TTS API integration: 5 built-in voices (ara/eve/leo/rex/sal), custom voice clone support, 20+ languages, $15/1M chars
- STT: diarization, multichannel, 24 languages, keyterm boosting
- Video generation and editing via `/v1/videos/generations` and `/v1/videos/edits`
- CLI config tool: `python3 server.py config --list-models` / `--model grok-4.20-multi-agent-0309`
- `x_search` with `allowed_x_handles`, date ranges

**Supported model IDs** (from repo):
- `grok-4.3` — 1M context, $1.25/$2.50 per 1M
- `grok-4.20-0309-reasoning` — 1M context, $1.25/$2.50
- `grok-4.20-0309-non-reasoning` — 1M context, $1.25/$2.50
- `grok-4.20-multi-agent-0309` — **2M context**, $1.25/$2.50
- `grok-imagine-image` — $0.02/image
- `grok-imagine-image-quality` — $0.05/image
- `grok-imagine-video` — $0.05/sec

**Verdict**: Most feature-complete single-server implementation. If you need TTS, STT, video, image editing, AND search in one MCP, this is it.

***
### 3.4 `zeromask1337/grok-search-mcp` ⭐ Clean TypeScript Reference
**Pattern**: Dual-mode (Stdio + HTTP), TypeScript, Bun runtime, Hono framework  
**Tools exposed**: `x_search` only (focused scope)

**Architecture analysis**: Clean separation of concerns — `XAIClient` handles all xAI API calls, `XSearchTool` wraps the business logic, `MCPHandler` handles JSON-RPC dispatch, with separate entry points for stdio and HTTP modes. Correctly uses `/v1/responses` with `tools: [{ type: "x_search" }]`.

```typescript
// Correct Responses API call
const request = {
  model: "grok-4-1-fast",
  input: [{ role: "user", content: query }],
  tools: [{ type: "x_search" }],
  stream: true, // SSE streaming supported
};
```

**Standout feature**: Full SSE streaming implementation — the `searchStream()` method yields tokens progressively from the Responses API, which no other reviewed project implements.

**Weaknesses**:
- Single tool focus (x_search only, no web_search)
- Bun-only runtime (most teams use Node.js)

**Verdict**: Best code architecture and streaming reference. Fork it to add `web_search` and you have a clean production-grade TypeScript MCP server.

***
### 3.5 Community Pattern: OpenRouter + xAI native via Kilo Code / OpenCode
**Pattern**: Inline config (no custom MCP server), via existing tool-calling clients  
**Source**: Kilo Code discussion #5253, OpenRouter docs[^12][^10]

This isn't a GitHub project per se, but it's the most widely-discussed community pattern for getting Grok native search into agents without writing a custom MCP server:

```json
// OpenCode / OpenRouter config — forces native xAI web + x search
{
  "provider": {
    "openrouter": {
      "options": {
        "extraBody": {
          "plugins": [{ "id": "web" }]
        }
      }
    }
  }
}
```

For the newer `openrouter:web_search` server tool approach:
```json
{
  "tools": [
    {
      "type": "openrouter:web_search",
      "parameters": {
        "engine": "native",
        "max_results": 10
      }
    }
  ]
}
```

When `engine: "native"` is set and the model is an xAI model, OpenRouter passes the request to xAI's own `web_search` + `x_search` infrastructure. The `x_search_filter` top-level parameter can further filter X results by date range and handles.[^13][^10][^12]

**Verdict**: Zero-code path to Grok native search in any OpenRouter-compatible agent. Tradeoff: less control over tool parameters, search quality depends on OpenRouter's passthrough fidelity.

***
## 4. Grok API Direct vs OpenRouter: Integration Comparison
| Dimension | xAI Direct (`api.x.ai/v1/responses`) | OpenRouter (`openrouter.ai/api/v1`) |
|---|---|---|
| **x_search native** | ✅ Full parameter control | ✅ With `engine: native` or `plugins: web` |
| **web_search native** | ✅ Full parameter control | ✅ With `engine: native` |
| **x_search advanced params** | `allowed_x_handles`, `from_date`, `to_date`, video/image understanding | Limited passthrough via `x_search_filter` (date range only)[^13] |
| **web_search domain filters** | `allowed_domains`, `excluded_domains` | `include_domains`, `exclude_domains` (Exa engine only) |
| **Stateful turns** | `previous_response_id` | Not exposed |
| **Reasoning models** | Full support with streaming reasoning tokens | Supported but no reasoning token visibility |
| **Fallback on search failure** | None (errors) | Falls back to Exa if native fails |
| **Pricing (search)** | $25/1k sources direct | Exa: $4/1k results; Native: provider rate |
| **Model switching** | xAI models only | Any of 400+ models |
| **Setup complexity** | Low (one API key) | Low (one API key) |
| **MCP server pattern** | Required for agent access | Required for agent access, OR use server tool inline |
| **Streaming** | Full SSE from `/v1/responses` | Supported |
| **Remote MCP to Grok** | ✅ Grok calls out to your MCP server | ❌ Not available |

**Key insight from community discussions**: OpenRouter's `engine: "native"` with xAI models does relay the request to xAI's native search infrastructure. The `x_search_filter` parameter works as a top-level field. However, several users reported uncertainty about whether the full `x_search` parameter set (handle filtering, image/video understanding) is fully respected through the proxy — the safe choice for production is direct xAI API access.[^12][^14]

For **OpenRouter's own search engine** (Exa-based), it's a different product from xAI's native search and offers different quality characteristics. The community consensus in mid-2026 is: use OpenRouter for multi-model orchestration and routing, use xAI direct for highest-fidelity Grok native search.[^14][^12]

***
## 5. Model Selection for MCP + Search Workloads
### Current xAI Model Lineup (May–June 2026)
| Model | Context | Input $/1M | Output $/1M | Cached Input | Best For |
|---|---|---|---|---|---|
| `grok-4.3` | 1M | $1.25 | $2.50 | ~$0.13 | Agentic MCP, production[^15][^16] |
| `grok-4.20-multi-agent-0309` | **2M** | $1.25 | $2.50 | ~$0.20 | Large context multi-agent |
| `grok-4.20-0309-reasoning` | 1M | $1.25 | $2.50 | ~$0.20 | Complex reasoning + search |
| `grok-4.1-fast` (reasoning) | 2M | **$0.20** | **$0.50** | ~$0.02 | High-volume agentic, cost-sensitive[^2][^17] |
| `grok-4` | 256K | $3.00 | $15.00 | — | Frontier research (expensive)[^18] |
| `grok-build-0.1` | 256K | $1.00 | $2.00 | — | Agentic coding pipelines[^15] |
### Recommendation Matrix
**For a Grok MCP server used by Claude Code:**
- **Daily driver**: `grok-4.1-fast` (reasoning off) — 138 t/s, $0.20/$0.50, 2M context. Best for simple web lookups and X search queries where you want near-instant responses.[^19]
- **Quality searches**: `grok-4.3` — verified #1 jump in agentic task completion, 98% instruction-following, optimized for tool-calling workflows. Use for research-grade queries.[^3]
- **Deep reasoning + search**: `grok-4.20-0309-reasoning` — enables chain-of-thought over search results, useful for complex multi-source analysis.
- **Cost ceiling**: `grok-4` — only justified for frontier reasoning tasks; 6× more expensive than `grok-4.3` on output.

**Search billing math for a typical session** (10 searches, 5 sources each):
- 50 sources × $0.025 = **$1.25 in search costs** regardless of model
- Plus `grok-4.1-fast` tokens: ~10K input + 2K output ≈ **$0.003**
- Plus `grok-4.3` tokens: ~10K input + 2K output ≈ **$0.018**

At this usage level, search source billing dominates — model choice is relatively secondary to search result count.

***
## 6. System Prompts & Grok-Specific Prompt Engineering
### 6.1 Grok Tool-Use Prompts
Community finding from the ECC/Grok discussion thread: Grok responds significantly better to XML tags than raw Markdown for structured context:[^20]

```xml
<instructions>
You have access to web_search and x_search tools. Use them as follows:
- For current events, news, or time-sensitive data: always invoke web_search first
- For social sentiment, community reactions, or public figure statements: use x_search
- Cite every factual claim with the URL from citations
- For technical topics, prefer official documentation domains
</instructions>

mmand-registry>
/research <topic>: Invoke web_search + x_search, synthesize into structured report
/xmonitor <handle>: x_search filtered to allowed_x_handles=[handle], last 7 days
/factcheck laim>: web_search for corroboration, return verification_status
</command-registry>
```
### 6.2 Structured Output Pattern for Reliable Parsing
The most-cited community lesson: always request JSON from the system prompt when building MCP tools that need to parse Grok's output:

```python
SEARCH_SYSTEM_PROMPT = """
You are a search assistant. For every query, perform the search and return JSON only:
{
  "results": [
    {
      "title": "string",
      "snippet": "string (max 200 chars)",
      "url": "string",
      "source": "string",
      "published_date": "YYYY-MM-DD",
      "relevance": "high|medium|low"
    }
  ],
  "summary": "string (3-5 sentence synthesis)",
  "confidence": "high|medium|low",
  "search_type_used": "web|x|both"
}
Do not include any text outside the JSON object.
"""
```
### 6.3 X Search Operator Patterns
Grok's `x_search` supports advanced X search operators within the query string itself:

```
# High-engagement technical posts
query = "Claude Code MCP min_faves:100 filter:blue_verified lang:en"

# Posts from specific technical communities  
query = "LangGraph agents site:x.com since:2026-01-01"

# Sentiment monitoring for a product
query = "\"your_product\" (problem OR bug OR broken OR love OR amazing)"
```

These operators work because xAI's x_search backend passes them to X's own search infrastructure.
### 6.4 Rule Placement for Long Sessions
Community insight: Grok's instruction-following degrades when rules are deep in the context. In system prompts or CLAUDE.md files that feed into Grok via MCP, put invariant rules at the **bottom** of the system prompt so they remain "top of mind." This matters for long agentic sessions with large retrieved contexts.[^20]

***
## 7. Implementation Best Practices
### 7.1 MCP Server Robustness
From `stat-guy/grok-search-mcp` learnings and `auny-ai/grok-mcp-server` architecture:

```javascript
// Always: retry with exponential backoff
const retry = async (fn, maxRetries = 3) => {
  for (let i = 0; i < maxRetries; i++) {
    try { return await fn(); }
    catch (e) {
      if (i === maxRetries - 1) throw e;
      await new Promise(r => setTimeout(r, 1000 * Math.pow(2, i)));
    }
  }
};

// Always: validate model name against current API
// Model names change (grok-beta → grok-3-latest → grok-4.3)
// Never hardcode; fetch from /v1/models or use env var

// Always: JSON fallback parsing
const parseGrokResponse = (content) => {
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    try { return JSON.parse(jsonMatch); }
    catch {}
  }
  return { summary: content, results: [] }; // graceful fallback
};
```
### 7.2 Tool Description Quality
Grok 4.3's 98% instruction-following means tool descriptions must be precise. Vague descriptions cause the model to either over-invoke tools or skip them:[^3]

```python
# Weak (causes over-invocation)
"Search the web for information"

# Strong (correct call frequency)
"""Search the live web for factual, time-sensitive, or technical information 
published after October 2023. Use for: current events, product documentation,
API changes, recent research papers. Do NOT use for: general knowledge questions
answerable from training data, creative tasks, code generation."""
```
### 7.3 Search Result Count vs Cost
Each source costs $0.025. A `max_results: 20` default on every call costs $0.50 per query. Use tiered defaults:[^7]

| Query type | Recommended `max_results` | Estimated cost |
|---|---|---|
| Quick fact lookup | 3 | $0.075 |
| Standard research | 7–10 | $0.18–$0.25 |
| Comprehensive analysis | 15–20 | $0.38–$0.50 |
| X sentiment scan | 10–15 (posts) | $0.25–$0.38 |
### 7.4 Claude Desktop vs Claude Code Config Patterns
```json
// Claude Desktop — local NPX package
{
  "mcpServers": {
    "grok-search": {
      "command": "npx",
      "args": ["grok-search-mcp"],
      "env": { "XAI_API_KEY": "xai-..." }
    }
  }
}

// Claude Desktop — remote Cloudflare Worker (auny-ai pattern)
{
  "mcpServers": {
    "grok": {
      "url": "https://grok-mcp-server.<your-account>.workers.dev/mcp"
    }
  }
}

// Claude Code CLI — Python server (wynandw87 pattern)
// claude mcp add -s user -t stdio Grok python3 /path/to/server.py -e XAI_API_KEY=xai-...
```
### 7.5 Security Checklist
- Store `XAI_API_KEY` only in env vars or Cloudflare Worker secrets — never in code[^21]
- For remote MCP servers, add bearer token auth in the `fetch` handler (5-line change)
- Validate and sanitize search queries before forwarding to xAI (prevent prompt injection through search results)
- Set `allowed_domains` to trusted sources for high-stakes agents

***
## 8. Community Insights & Known Issues
### What the Community Gets Right
**Claude Code + Grok MCP = cheap research layer**: The dominant pattern in r/ClaudeCode and r/ClaudeAI is using Claude as the reasoning orchestrator and Grok's search tools as the real-time data layer. Claude handles code generation, architecture decisions, and multi-step planning; Grok handles "what's current in the world."[^22][^23]

**Avoid the `search_parameters` legacy API**: Several community discussions warn that projects still using the old Chat Completions API with `search_parameters: { mode: "on" }` are on a deprecated path. Migrate to the Responses API.[^1]

**OpenRouter web plugin deprecated**: As of May 2026, OpenRouter deprecated `plugins: [{ id: "web" }]` in favor of `tools: [{ type: "openrouter:web_search" }]`. Projects using the old plugin syntax should migrate.[^13]
### Known Pain Points
1. **Model name churn**: xAI has renamed/versioned models multiple times. Production MCP servers should parameterize the model name via env var rather than hardcoding.

2. **Context bleeding in long agentic loops**: When combining `previous_response_id` (stateful Responses API) with search tool calls over many turns, context can grow large quickly. Monitor `response.usage.total_tokens` and implement periodic context compression.

3. **X search rate limits**: The `x_search` tool can hit X API rate limits during high-frequency searches. Grok 4.3 handles this gracefully with partial results, but implement result caching (30-minute TTL minimum) for repeated queries.

4. **OpenRouter x_search fidelity**: Community discussion on r/openrouter flags that OpenRouter's native passthrough for xAI x_search may not honor all parameters (especially `enable_image_understanding` and `enable_video_understanding`). Use xAI direct for full parameter control.[^14]

5. **Cloudflare Workers 50ms CPU limit**: For complex Grok reasoning calls with many tool invocations, the default Cloudflare Workers CPU time limit can be hit. Use `waitUntil()` or upgrade to Workers Paid plan if Grok reasoning runs long.

***
## 9. Production Architecture Recommendation
For a multi-agent system (e.g., an agentic visual novel engine or coding assistant) that needs Grok search as a tool layer, the recommended architecture as of June 2026:

```
Claude Code (orchestrator, user-facing agent)
        │  MCP stdio / remote MCP
        ▼
Grok MCP Server (auny-ai Cloudflare pattern, extended)
  ├── x_search(query, allowed_x_handles, date_range)    — Grok 4.1-fast (speed)
  ├── web_search(query, allowed_domains)                 — Grok 4.3 (quality)
  ├── grok_reasoning(prompt, effort="high")              — Grok 4.3 (analysis)
  └── grok_structured_output(prompt, schema)             — Grok 4.3 (extraction)
        │  HTTPS to api.x.ai/v1/responses
        ▼
xAI Responses API
  ├── Live web index (updated continuously)
  ├── X post index (real-time)
  └── Grok reasoning layer (thinking tokens)
```

**Model routing in the MCP server** (cost optimization):
```python
def select_model(tool_name, query_length):
    if tool_name in ("x_search", "web_search") and query_length < 500:
        return "grok-4-1-fast"  # $0.20/$0.50 — fast lookups
    elif tool_name == "grok_reasoning":
        return "grok-4.3"       # $1.25/$2.50 — quality reasoning
    else:
        return "grok-4.3"       # safe default
```

This routing can reduce token costs by 6× on high-frequency search queries while using the full reasoning capacity of `grok-4.3` only where it matters.

---

## References

1. [Comparison with Chat Completions API](https://docs.x.ai/developers/model-capabilities/text/comparison) - Compare the Responses API with the legacy Chat Completions API.

2. [Grok 4.1 Fast: Independent Reviews And Benchmarks - Medium](https://medium.com/@leucopsis/grok-4-1-fast-independent-reviews-and-benchmarks-3aa61849858a) - Grok 4.1 Fast is the newest large language model from Elon Musk’s xAI, released in mid-November 2025...

3. [Grok 4.3 model improvements and features - Facebook](https://www.facebook.com/groups/aisaas/posts/4475157572803623/) - It's in agentic performance. Independent testing shows Grok 4.3 made one of the largest single jumps...

4. [Web Search | Add Real-time Web Data to AI ... - openrouter.ai](https://openrouter.ai/docs/guides/features/plugins/web-search) - Enable real-time web search capabilities in your AI model responses. Add factual, up-to-date informa...

5. [Web Search - xAI Docs](https://docs.x.ai/developers/tools/web-search) - The Web Search tool enables Grok to search the web in real-time and browse web pages to find informa...

6. [X Search - xAI Docs](https://docs.x.ai/developers/tools/x-search) - The X Search tool enables Grok to perform keyword search, semantic search, user search, and thread f...

7. [Grok API Pricing 2026: Complete xAI Models & Live Search Cost ...](https://the-rogue-marketing.github.io/grok-api-latest-llms-pricing-october-2025/) - See how much xAI's Grok API costs in 2026. A detailed guide to Grok 4, Grok 3 Mini, Live Search quer...

8. [Remote MCP Tools](https://docs.x.ai/docs/guides/tools/remote-mcp-tools) - Learn how to connect and use remote MCP (Model Context Protocol) servers to extend AI capabilities w...

9. [Docs MCP | xAI Docs](https://docs.x.ai/developers/docs-mcp) - xAI hosts a Model Context Protocol (MCP) server that gives AI assistants and agents direct access to...

10. [Web Search Server Tool | Real-Time Web Search for Any Model](https://openrouter.ai/docs/guides/features/server-tools/web-search) - The openrouter:web_search server tool gives any model on OpenRouter access to real-time web informat...

11. [Consistent Web Search and Fetch Across Every Model - OpenRouter](https://openrouter.ai/announcements/agentic-web-tools) - Give any tool-calling model the ability to search the web and fetch page content on its own, with mu...

12. [Support for Grok native server side tool calling #5253 - GitHub](https://github.com/Kilo-Org/kilocode/discussions/5253) - Following up from the Q&A with Brendan and Brian during the Kilo Code video call today: https://docs...

13. [Responses API Beta Web Search | Real-time Information ...](https://openrouter.ai/docs/api/reference/responses/web-search) - Enable web search capabilities with real-time information retrieval and citation annotations using O...

14. [Question about "search" models](https://www.reddit.com/r/openrouter/comments/1oamns7/question_about_search_models/) - Question about "search" models

15. [Models - xAI Docs](https://docs.x.ai/developers/models) - We have dedicated models and APIs for audio, image, and video capabilities. For everything else, use...

16. [xAI Grok API Pricing May 2026: Grok 4.3, 4.20 & Fast Models ...](https://the-rogue-marketing.github.io/grok-xai-api-pricing-may-2026/) - Stop overpaying for Grok. Compare pricing for Grok 4.3 Reasoning, 4.20, and 4.1 Fast. Learn how xAI’...

17. [xAI Grok API Pricing (May 2026) — Grok 4 Per-Token Costs](https://www.aipricing.guru/xai-pricing/) - Complete xAI Grok API pricing for May 2026. Grok 4, Grok 4.20, Grok 4.1 Fast, Grok 3, Grok 3 Mini, G...

18. [Grok 4 API Pricing 2026 - Costs, Performance & Providers](https://pricepertoken.com/pricing-page/model/xai-grok-4) - Pricing starts at $3.00 per million input tokens and $15.00 per million output tokens. The model sup...

19. [Grok 4.1 Fast vs Grok 4.3: AI Benchmark Comparison 2026](https://benchlm.ai/compare/grok-4-1-fast-vs-grok-4-3) - Grok 4.3 is priced at $1.25 input / $2.50 output per 1M tokens, versus $0.20 input / $0.50 output pe...

20. [How to use everything-claude-code with Grok (xAI)? Best practices ...](https://github.com/affaan-m/ECC/discussions/1077) - Ask Grok to role-play as specific agents (e.g. planner , refactor-cleaner , code-reviewer ) + load s...

21. [The Complete Guide to Claude Code: Global CLAUDE.md, MCP Servers, Commands, and Why Single-Purpose Chats Matter](https://www.reddit.com/r/ClaudeAI/comments/1qbkk1n/the_complete_guide_to_claude_code_global_claudemd/) - The Complete Guide to Claude Code: Global CLAUDE.md, MCP Servers, Commands, and Why Single-Purpose C...

22. [I built an MCP server & Plugin using Claude code that queries GPT-5, Claude, Gemini, and Grok simultaneously from your IDE — uses your existing $20/mo subscriptions (no API keys needed)](https://www.reddit.com/r/ClaudeCode/comments/1rusqgd/i_built_an_mcp_server_plugin_using_claude_code/) - I built an MCP server & Plugin using Claude code that queries GPT-5, Claude, Gemini, and Grok simult...

23. [What are your best practices for Claude Code in early 2026?](https://www.reddit.com/r/Anthropic/comments/1qmu07f/what_are_your_best_practices_for_claude_code_in/) - What are your best practices for Claude Code in early 2026?

