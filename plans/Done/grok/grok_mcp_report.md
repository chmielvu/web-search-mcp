# Grok API + MCP Architecture: Comprehensive Technical Report

*Research conducted June 2026 — covering xAI API, top GitHub MCP projects, OpenRouter integration, community insights, and cost analysis.*

---

## Executive Summary

xAI's Grok API offers two unique search capabilities unavailable in other LLM providers: **native web search** (`web_search` tool) and **X (Twitter) search** (`x_search` tool), both accessible via the Responses API. These can be exposed to agents like Claude Code, Cursor, and Windsurf via MCP servers. The community has built several notable open-source MCP wrappers — the best being **xbridge-mcp** (the most feature-complete) and **merterbak/Grok-MCP** (lightest weight). OpenRouter can route to Grok models but **cannot expose native xAI search tools** — a critical architectural limitation. Direct xAI API is required for real X/web search.

---

## 1. xAI Grok API: Core Search Capabilities

The core differentiator for Grok in an MCP context is the Responses API tool system. When you pass `"tools": [{"type": "web_search"}]` or `"tools": [{"type": "x_search"}]` in your request payload, Grok performs the retrieval internally and returns a synthesized answer — not raw search results. This is fundamentally different from a tool-calling pattern where the model asks the client to perform a search.

### Web Search Tool (`web_search`)

```json
{
  "type": "web_search",
  "allowed_domains": ["github.com", "arxiv.org"],
  "excluded_domains": ["pinterest.com"],
  "enable_image_understanding": false
}
```

- Domain allowlist/blocklist filtering
- Optional image understanding from web pages
- Returns synthesized answer with implicit source attribution
- Works on all current Grok text models

### X Search Tool (`x_search`)

```json
{
  "type": "x_search",
  "allowed_x_handles": ["elonmusk", "xai"],
  "excluded_x_handles": ["spambot123"],
  "from_date": "2026-01-01",
  "to_date": "2026-06-01",
  "enable_image_understanding": true,
  "enable_video_understanding": false
}
```

- Handle-level allowlist/blocklist
- Date range filtering (ISO 8601)
- Image and video understanding in posts
- Unique capability — no other LLM provider has native X post search

### API Endpoint

All tool-enabled requests use the **Responses API**, not the Chat Completions API:

```
POST https://api.x.ai/v1/responses
Authorization: Bearer $XAI_API_KEY
```

Regional endpoint override: `XAI_REGION=us-east-1` → `https://us-east-1.api.x.ai/v1/responses`

---

## 2. Top 5 GitHub MCP Projects — Code Evaluation

### Project 1: hrco/xbridge-mcp ⭐ **Best Overall**

**GitHub**: https://github.com/hrco/xbridge-mcp  
**Architecture**: Full async Python MCP server using `mcp` SDK (stdio transport)  
**Tools**: 19 tools across 5 categories

**Code quality highlights (from direct source inspection):**
- Uses a **shared persistent `httpx.AsyncClient`** (`_get_grok_client()`) with 300s timeout — avoids creating a new HTTP client per tool call, which is the correct pattern for high-frequency MCP usage
- Tool payload construction is clean: `web_search_tool: dict = {"type": "web_search"}` then conditionally appends filters — no unnecessary keys sent to API
- `extract_response_text()` handles the nested Responses API output structure correctly: `output[].type=="message" → content[].type=="output_text" → text`
- **Session management** (`SessionManager` class) maintains in-memory conversation history — enables multi-turn agentic loops via `grok-session-chat` tool
- **Tool chaining** (`ChainBuilder`) implements: `search_and_summarize`, `multi_source_research` (web + X), `debug_workflow` (X search → fix generation)
- Regional endpoint support via `XAI_REGION` env var

**Critical limitation**: Requires `XBRIDGE_KEY` (a proprietary paid key from xbridgemcp.com, 50 calls/day free). This is a SaaS layer on top of xAI — you still need `XAI_API_KEY` but also pay xBridge. Inspect `_validate_key()` before production use.

**Available models (as of source):**
```python
AVAILABLE_MODELS = [
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning", 
    "grok-4.20-multi-agent-0309",
    "grok-4", "grok-4-1-fast",
    "grok-4-1-fast-reasoning",
    "grok-4-0709",
    "grok-code-fast-1",
    "grok-3", "grok-3-fast", "grok-3-mini",
    "grok-2", "grok-2-latest", "grok-2-vision-1212",
]
DEFAULT_MODEL = "grok-4-1-fast"
```

**Tool categories:**

| Category | Tools |
|---|---|
| Core | `grok-chat`, `grok-web-search`, `grok-x-search`, `grok-models` |
| Sessions | `grok-session-create/list/get/delete/chat` |
| Chains | `grok-chain-search-summarize`, `grok-chain-research`, `grok-chain-debug` |
| Media | `grok-image-generate`, `grok-image-edit`, `grok-image-models`, `grok-video-generate` |
| Docs | `grok-docs-list`, `grok-docs-search`, `grok-docs-get` |

**Claude Code config:**
```json
{
  "mcpServers": {
    "xbridge": {
      "command": "python",
      "args": ["-m", "xbridge_mcp"],
      "env": {
        "XAI_API_KEY": "your-xai-key",
        "XBRIDGE_KEY": "your-xbridge-key"
      }
    }
  }
}
```

---

### Project 2: merterbak/Grok-MCP ⭐ **Best Lightweight**

**GitHub**: https://github.com/merterbak/Grok-MCP  
**Architecture**: Minimal Python MCP server, no proprietary key required  
**Tools**: `grok_chat`, `grok_web_search`, `grok_image_understand`, `grok_image_generate`

**Code highlights:**
- Direct `XAI_API_KEY` only — no third-party dependency
- Uses Chat Completions endpoint (`/v1/chat/completions`) rather than Responses API — this means web_search is passed as an OpenAI-compatible tool parameter
- Lighter codebase, easier to audit and modify
- No session management or tool chaining

**Best for**: Quick integration, self-hosted, no usage cap concerns.

**Install:**
```bash
pip install grok-mcp
# or: uvx grok-mcp
```

**Claude Code config:**
```json
{
  "mcpServers": {
    "grok": {
      "command": "uvx",
      "args": ["grok-mcp"],
      "env": {"XAI_API_KEY": "xai-..."}
    }
  }
}
```

---

### Project 3: RaiAnsar/claude_code-multi-AI-MCP ⭐ **Best Multi-LLM Orchestration**

**GitHub**: https://github.com/RaiAnsar/claude_code-multi-AI-MCP  
**Architecture**: Routes Claude Code requests to Grok, Gemini, and DeepSeek simultaneously for code review  
**Use case**: "Vibe-check" Grok's X-aware perspective against Gemini's reasoning on the same task

**Highlights:**
- Designed specifically for the Claude Code workflow
- Exposes Grok as a second-opinion peer — Claude writes code, Grok reviews via X/web search for community issues
- Useful pattern: Claude Code → MCP tool call → Grok X search for "known issues with [library version]" → synthesized back to Claude

**Limitation**: More complex setup; all three API keys required.

---

### Project 4: grok-cli-mcp (PyPI: grok-cli-mcp) ⭐ **Best CLI-Wrapped**

**Source**: https://pypi.org/project/grok-cli-mcp/  
**Architecture**: Wraps Grok CLI (the official xAI terminal client) as an MCP server  
**Attribution**: Open-sourced by Chang Xu (LinkedIn post, Jan 2026)

**Design pattern:**
```
Claude Code → MCP stdio → grok-cli-mcp → Grok CLI → xAI API
```

**Highlights:**
- Zero custom API logic — relies entirely on the Grok CLI for auth and transport
- Works out of the box after `grok` CLI authentication
- Community-recommended for "X trend radar" use inside Claude Code
- Lower maintenance burden as CLI handles API changes

**Prompt pattern from community:**
> *"Ask Grok for the latest chatter on X about [topic]"*

**Install:**
```bash
pip install grok-cli-mcp
claude mcp add --transport stdio --env GROK_API_KEY="your-key" grok -- python -m grok_cli_mcp
```

---

### Project 5: Official xAI Remote MCP Server ⭐ **Best Zero-Setup**

**URL**: `https://docs.x.ai/api/mcp` (public, no auth for docs tools)  
**Architecture**: Remote MCP server hosted by xAI — connects via HTTP/SSE transport  
**Confirmed in xbridge-mcp source**: `_call_docs_mcp()` proxies to this URL via JSON-RPC

**Tools**: `list_doc_pages`, `get_doc_page`, `search_docs`

**Claude Code config (remote MCP):**
```json
{
  "mcpServers": {
    "xai-docs": {
      "type": "http",
      "url": "https://docs.x.ai/api/mcp"
    }
  }
}
```

**Note**: This is documentation-only. xAI has not yet released an official full-capability (web_search + x_search) remote MCP. That gap is what the community projects above fill.

---

## 3. Architecture Patterns: Agent-to-Tool Flow

### Pattern A: Direct Grok Search MCP (Recommended)

```
Claude Code
    │
    ▼ tool call: grok-web-search(query="...")
MCP Server (local stdio)
    │
    ▼ POST /v1/responses  {tools: [{type: "web_search"}]}
xAI API (grok-4-1-fast)
    │
    ▼ Synthesized answer with web sources
MCP Server
    │
    ▼ TextContent response
Claude Code
```

This is the cleanest pattern. Grok handles retrieval internally — Claude Code gets a synthesized answer, not raw URLs.

---

### Pattern B: Session-Aware Research Chain

```
Claude Code
    │
    ▼ grok-chain-research(topic="X")
MCP Server
    │
    ├─ Step 1: grok-web-search(topic) → web_results
    ├─ Step 2: grok-x-search(topic)   → x_results
    └─ Step 3: grok-chat("Synthesize: {web_results} + {x_results}")
    │
    ▼ Unified research report
Claude Code
```

Used by xbridge-mcp's `ChainBuilder.multi_source_research()`. Critical: all three requests consume tokens; cost adds up quickly with large contexts.

---

### Pattern C: Debug Workflow (X-First)

```
Claude Code encounters an error
    │
    ▼ grok-chain-debug(error_message="...", tech_stack="React")
MCP Server
    │
    ├─ Step 1: grok-x-search("React error: {msg}")  → community posts
    └─ Step 2: grok-chat("Given these X posts, generate a fix for: {error}")
    │
    ▼ Fix with community context
Claude Code
```

Uniquely leverages X search for real-time community debugging — StackOverflow has a lag, X doesn't.

---

### Pattern D: Multi-Agent Peer Review

```
Claude Code (primary coder)
    │
    ├─ Writes implementation
    ▼ tool call: grok-chat(message="Review this code: {code}", model="grok-4.20-multi-agent-0309")
Grok MCP
    │
    ▼ Code review with X-aware context (can internally search X for library issues)
Claude Code receives review
```

`grok-4.20-multi-agent-0309` is optimized for this pattern — it has explicit multi-agent orchestration capabilities at the same $2/$6 per 1M price as its siblings.

---

## 4. OpenRouter Grok Integration — Critical Limitations

### What Works via OpenRouter

| Feature | Direct xAI API | OpenRouter |
|---|---|---|
| grok-4 / grok-4-1-fast text generation | ✅ | ✅ |
| grok-4.20 / grok-4.1-fast models | ✅ | ✅ |
| Function calling (tool_use) | ✅ | ✅ |
| Structured output (JSON mode) | ✅ | ✅ |
| **`web_search` native tool** | ✅ | ❌ |
| **`x_search` native tool** | ✅ | ❌ |
| OpenRouter web_search plugin (Exa) | N/A | ✅ (different provider) |
| 2M token context | ✅ | ✅ |

### OpenRouter Web Search — What It Actually Is

OpenRouter offers a `:online` model suffix (e.g., `x-ai/grok-4.1-fast:online`) and a `:nitro` throughput variant. The web search is powered by **Exa**, not xAI's native search. There is no native `x_search` equivalent.

OpenRouter pricing for Grok (as of June 2026):
- `x-ai/grok-4.20`: $1.25 input / $2.50 output per 1M tokens (vs xAI direct: $2.00/$6.00)
- `x-ai/grok-4.1-fast`: pricing TBD on OpenRouter (model listed)
- `x-ai/grok-4` (256K): $3.00/$15.00 per 1M (same as xAI direct)

### OpenRouter MCP Pattern (for non-X search use cases)

```python
# OpenRouter-compatible Grok MCP pattern
import openai

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="sk-or-...",
)

response = client.chat.completions.create(
    model="x-ai/grok-4.1-fast:online",  # :online enables Exa web search
    messages=[{"role": "user", "content": query}],
)
```

**Bottom line**: Use OpenRouter for cost-optimized Grok text generation and general web search. Use direct xAI API (via a Grok MCP server) when X/Twitter search is the goal.

---

## 5. Model Selection & Cost Analysis

### Text Models

| Model | Context | Input $/1M | Output $/1M | Best For |
|---|---|---|---|---|
| grok-4-1-fast | 2M | $0.20 | $0.50 | **MCP default — best cost/performance** |
| grok-4-1-fast-reasoning | 2M | $0.20 | $0.50 | Search + reasoning chains |
| grok-code-fast-1 | 256K | $0.20 | $1.50 | Code-specific tasks |
| grok-3-mini | 131K | $0.30 | $0.50 | Budget option, reasoning capable |
| grok-4.20-multi-agent | 2M | $2.00 | $6.00 | Multi-agent orchestration |
| grok-4 | 256K | $3.00 | $15.00 | Highest quality single tasks |
| grok-4.20-reasoning | 2M | $2.00 | $6.00 | Complex reasoning with 2M ctx |

### Cost Estimate Per MCP Operation (grok-4-1-fast)

| Operation | Approx tokens | Approx cost |
|---|---|---|
| Single web search + answer | ~800 in / ~400 out | $0.000360 |
| X search + answer | ~800 in / ~500 out | $0.000410 |
| chain-research (web + X + synthesis) | ~3000 in / ~1200 out | $0.00120 |
| chain-debug | ~2000 in / ~800 out | $0.000800 |

For Claude Code usage (dozens of MCP calls per session), `grok-4-1-fast` at $0.20/$0.50 is the clear default. Only escalate to `grok-4` or `grok-4.20-reasoning` for final synthesis steps in research chains.

---

## 6. Optimized System Prompts for Grok in MCP Context

### Web Search — Research Mode
```
You are a research assistant with real-time web access. When searching:
1. Prioritize primary sources (official docs, GitHub, academic papers)
2. Explicitly flag information older than 6 months
3. Return structured findings: [Summary], [Key Facts], [Source quality assessment]
4. If results conflict, state the disagreement explicitly
```

### X Search — Community Signals Mode
```
You are a community intelligence analyst monitoring X (Twitter).
Focus on:
- Developer and technical discussions (not hype)
- Bug reports, workarounds, and version-specific issues
- Sentiment from verified technical accounts
- Date-stamp all claims
Ignore: promotional posts, obvious bots, retweets without commentary
```

### X Search — Trend Detection
```
Analyze X posts about [TOPIC] from [DATE_RANGE].
Extract: (1) dominant sentiment, (2) top 3 recurring technical issues,
(3) notable expert opinions, (4) emerging patterns not yet in official docs.
Format as structured report, not narrative.
```

### Chain Research — Synthesis Step
```
You have received two search results: [WEB_RESULTS] and [X_RESULTS].
Synthesize into a unified technical brief:
- Prioritize official sources for facts, X for real-world usage signals
- Mark any contradiction between official docs and community experience
- Confidence level: HIGH (multiple sources agree) / MEDIUM / LOW (single source)
```

### Debug Workflow
```
You are debugging [TECH_STACK]. X search results show community discussions
about this error. Extract: (1) confirmed root cause if consensus exists,
(2) most-upvoted workaround, (3) whether this is a known bug with a fix in
a newer version. Then provide a concrete code fix.
```

---

## 7. Best Practices & Community Insights

### From Reddit/Community Analysis

- **Tool count matters**: Community consensus (r/ClaudeCode, May 2026) is to keep MCP servers to 4-6 max. Each registered tool adds to Claude's context window overhead on every request.
- **Avoid redundancy**: If Claude Code is your primary agent, don't add filesystem or git MCP tools — Claude Code has those natively. Use Grok MCP exclusively for its unique search capabilities.
- **X search is the real differentiator**: Community consistently reports that X search via Grok gives access to information not yet indexed on web (breaking bugs, same-day announcements, beta feature discussions).
- **Combine X + web for completeness**: Web search gives authoritative/documented info; X gives real-world/real-time signals. The chain pattern combining both is more valuable than either alone.

### Architectural Best Practices

1. **Prefer `grok-4-1-fast` as default model** in MCP config — 2M context at $0.20/$0.50 is exceptional value. Only override to `grok-4` for final-step high-stakes synthesis.

2. **Use `allowed_domains` filter aggressively** for web search:
   ```python
   allowed_domains=["github.com", "docs.python.org", "arxiv.org", "stackoverflow.com"]
   ```
   This dramatically improves result quality and reduces hallucination risk.

3. **Session management is underused** — xbridge-mcp's session tools let you maintain a research thread across multiple Claude Code tool calls. Useful for long debugging sessions.

4. **Do not use OpenRouter if X search is required.** The native `x_search` tool only works via direct xAI API (`api.x.ai/v1/responses`). OpenRouter's `:online` suffix uses Exa, which has no X data.

5. **Handle the Responses API output structure explicitly** — the xAI Responses API returns `output[].content[].type=="output_text"`, not the standard `choices[0].message.content` of Chat Completions. The `extract_response_text()` function in xbridge-mcp handles this correctly; copy it for any custom implementation.

6. **For agentic multi-step tasks**, use `grok-4.20-multi-agent-0309` — it is explicitly optimized for orchestration and tool-chaining workflows, with 2M context for long research chains.

7. **Image/video understanding in search** (`enable_image_understanding=True`) significantly increases latency and cost. Only enable when visual content is the actual subject of the query.

---

## 8. Recommended Setup for Claude Code (Production)

### Minimal Setup (X + Web Search Only)
```json
{
  "mcpServers": {
    "grok": {
      "command": "uvx",
      "args": ["grok-mcp"],
      "env": {
        "XAI_API_KEY": "xai-..."
      }
    }
  }
}
```
Use merterbak/Grok-MCP — no proprietary key, clean codebase, direct xAI API.

### Full-Featured Setup (Chains + Sessions + Media)
```json
{
  "mcpServers": {
    "xbridge": {
      "command": "python",
      "args": ["-m", "xbridge_mcp"],
      "env": {
        "XAI_API_KEY": "xai-...",
        "XBRIDGE_KEY": "your-xbridge-key",
        "XAI_REGION": "us-east-1"
      }
    }
  }
}
```
Use xbridge-mcp — caveat: 50 calls/day free tier, paid plan for more.

### Budget-Conscious Self-Hosted Setup
Build a minimal MCP server (~100 lines) targeting only `web_search` and `x_search`:
```python
from mcp.server import Server
import httpx, os

server = Server("grok-search")
XAI_API = "https://api.x.ai/v1/responses"

async def grok_search(query: str, search_type: str = "web") -> str:
    payload = {
        "model": "grok-4-1-fast",  # $0.20/$0.50 per 1M
        "input": [{"role": "user", "content": query}],
        "tools": [{"type": search_type}]
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(XAI_API,
            headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"},
            json=payload)
        return extract_text(r.json())
```
This avoids the XBRIDGE_KEY dependency entirely and costs ~$0.0004 per search call.

---

## 9. Limitations & Open Questions

- **xAI has not released an official full-capability MCP server** (only the docs MCP at `docs.x.ai/api/mcp`). All community projects are wrappers.
- **X search freshness**: Results depend on xAI's X indexing pipeline, not raw API access. Lag is typically minutes to hours for trending content.
- **OpenRouter x_search gap**: Confirmed as of June 2026 — no OpenRouter route to native X search. Community thread on r/openrouter confirms this.
- **grok-4.1-fast pricing**: Listed as $0.00 on pricepertoken.com — likely a data gap; actual pricing from xbridge-mcp source shows $0.20/$0.50 (same as grok-4-1-fast).
- **xbridge-mcp XBRIDGE_KEY**: The proprietary key requirement makes this a SaaS dependency. For production, consider forking the repo and removing the key validation, using your direct XAI_API_KEY only.
