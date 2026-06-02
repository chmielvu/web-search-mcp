# Agentic Web Research — LangGraph ReAct Agent with MCP Tool Integration (V2 Plan)

## Architecture Decision

**ReAct agent, not multi-agent, not programmatic pipeline.** The ReAct pattern fits the "AI agent consumer, no HITL, structured output" constraints because:

- **Single `create_react_agent`** gives the LLM agency over tool selection — it decides when to search, when to fetch full content, when to cross-reference, and when to synthesize
- **`langchain-mcp-adapters`** bridges external MCP tools into the agent's tool set (filesystem, databases, code execution, other MCP servers)
- **Internal tool wrappers** around `run_web_search`, `fetch_content_artifact`, `batch_get_content` avoid MCP protocol overhead for the server's own primitives
- **The LLM decides the research strategy** — breadth vs depth, how many pages to fetch, when to stop — via native tool-calling

### Key Research Findings That Shaped This Decision

- LangGraph `create_react_agent` v2 uses `Send` API for parallel tool execution within a single LLM turn
- `langchain-mcp-adapters` provides zero-config MCP→LangChain tool bridging (official LangChain repo, 3.6K stars)
- `langgraph-mcp-agents` (706 stars) is a Streamlit demo — not a framework, but confirms the ReAct+MCP integration pattern works
- Multi-agent supervisor architectures (`open_deep_research`) are designed for human-consumed research reports with 30-50 rounds and HITL — overkill for AI-agent consumers that want fast, structured results
- Research shows 30-50 tool calls is optimal for factual accuracy; more degrades quality by ~35% while costing 5x more
- Citation accuracy in LLM-generated reports is 39-77% — verification is critical (handled by structured output + source list)

## Graph Topology

```
                    ┌──────────────────────────────────────────────┐
                    │       LangGraph ReAct Agent                  │
                    │                                              │
    START ──► agent (LLM) ──► should_continue ──► END             │
                  │   ▲              │                             │
                  │   │              │ (if tool_calls)             │
                  │   │              ▼                             │
                  │   └─────── tools (ToolNode)                    │
                  │                                              │
                  │   Tool Set:                                   │
                  │   ┌──────────────────────────────────┐       │
                  │   │ • web_search(query, research_goal)│       │
                  │   │ • get_content(url, char_length)  │       │
                  │   │ • batch_get_content(urls, budget)│       │
                  │   │ • gemini_search(query)           │       │
                  │   │ • academic_search(query)         │       │
                  │   │ • discover_links(url)            │       │
                  │   │ • final_answer(answer, sources)  │       │
                  │   │                                  │       │
                  │   │ + External MCP tools (optional): │       │
                  │   │   • filesystem read/write        │       │
                  │   │   • github issues/search         │       │
                  │   │   • database query               │       │
                  │   │   • code execution               │       │
                  │   └──────────────────────────────────┘       │
                    └──────────────────────────────────────────────┘
```

The `final_answer` tool signals the agent to stop and return structured output. The LLM calls it when it determines research is complete. This gives us structured output without HITL — the LLM decides when it's done, calls `final_answer`, and the graph terminates.

## Tool Definitions (Internal Wrappers)

Rather than calling MCP tools via the MCP protocol (circular), we wrap existing internal functions as LangChain `StructuredTool` objects:

```python
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# --- web_search tool ---
class WebSearchInput(BaseModel):
    query: str = Field(description="Search query string")
    research_goal: str = Field(description="What you're trying to find and why")
    num_results: int = Field(default=5, ge=1, le=10)

async def _web_search_tool(query: str, research_goal: str, num_results: int = 5) -> str:
    """Search the web. Returns titles, URLs, and snippets. Not full page content."""
    response = await run_web_search(query, num_results=num_results,
        rewrite=True, research_goal=research_goal)
    return json.dumps([{
        "title": r.title, "url": r.link, "snippet": r.snippet,
        "domain": r.domain, "score": r.score
    } for r in response.results], indent=2)

web_search_tool = StructuredTool.from_function(
    coroutine=_web_search_tool,
    name="web_search",
    description="Search the web. Returns title, URL, snippet for each result. Use this first to discover sources. For full page content, use get_content or batch_get_content.",
    args_schema=WebSearchInput,
)

# --- get_content tool ---
class GetContentInput(BaseModel):
    url: str = Field(description="URL to fetch full content from")
    char_length: int = Field(default=8000, ge=500, le=20000)

async def _get_content_tool(url: str, char_length: int = 8000) -> str:
    """Fetch full markdown content from a URL."""
    options = build_fetch_options()
    artifact = await fetch_content_artifact(url, options)
    content = slice_content(artifact.page_content, 0, char_length)
    return json.dumps({
        "url": url, "status": artifact.status,
        "source_type": artifact.source_type,
        "content": content[:char_length],
        "has_more": len(artifact.page_content) > char_length
    }, indent=2)

get_content_tool = StructuredTool.from_function(
    coroutine=_get_content_tool,
    name="get_content",
    description="Fetch full markdown content from a URL. Use after web_search to read promising pages. Supports GitHub issues, StackExchange, Wikipedia, arXiv, and general HTML.",
    args_schema=GetContentInput,
)

# --- batch_get_content tool ---
class BatchGetContentInput(BaseModel):
    urls: list[str] = Field(description="List of URLs to fetch in parallel")
    char_length: int = Field(default=5000, ge=500, le=10000)

async def _batch_get_content_tool(urls: list[str], char_length: int = 5000) -> str:
    """Fetch multiple URLs in parallel with a budget."""
    params = BatchParams(max_concurrency=4, per_item_char_length=char_length,
                         total_char_budget=char_length * len(urls))
    result = await run_batch_fetch(urls=urls, params=params, cursor=None)
    return json.dumps([{
        "url": r.get("input_url"), "status": r.get("status"),
        "source_type": r.get("source_type"),
        "content": r.get("page_content", "")[:char_length]
    } for r in result["results"]], indent=2)

batch_get_content_tool = StructuredTool.from_function(
    coroutine=_batch_get_content_tool,
    name="batch_get_content",
    description="Fetch multiple URLs in parallel. Use when you have 3+ promising URLs from search results. More efficient than calling get_content repeatedly.",
    args_schema=BatchGetContentInput,
)

# --- final_answer tool ---
class FinalAnswerInput(BaseModel):
    answer: str = Field(description="Final synthesized answer with inline [N] citations")
    sources: list[dict] = Field(description="List of sources: [{url, title, key_finding}]")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="How confident are you?")
    gaps: str = Field(default="", description="Any remaining knowledge gaps or uncertainties")

async def _final_answer_tool(answer: str, sources: list[dict], confidence: float, gaps: str) -> str:
    """Signal research complete. Returns structured output."""
    return json.dumps({"answer": answer, "sources": sources, "confidence": confidence, "gaps": gaps})

final_answer_tool = StructuredTool.from_function(
    coroutine=_final_answer_tool,
    name="final_answer",
    description="Call this when research is complete. Synthesize all findings into a comprehensive answer with [N] citations. Include ALL sources you used. Be honest about confidence and gaps.",
    args_schema=FinalAnswerInput,
)

# Tools for gemini_search, academic_search, discover_links follow same pattern...
```

## External MCP Tool Loading (langchain-mcp-adapters)

The graph optionally loads tools from external MCP servers:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

mcp_client = MultiServerMCPClient({
    "filesystem": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
        "transport": "stdio"
    },
    # More servers configured via env vars
})

external_tools = await mcp_client.get_tools() if mcp_config_provided else []
```

All tools (internal + external) are merged and passed to `create_react_agent`.

## State Schema

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Annotated, Any
from langgraph.graph.message import add_messages

class AgenticResearchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list = Field(default_factory=list)
    step_count: int = Field(default=0)
    max_steps: int = Field(default=12)
    goal: str
    research_goal: str = ""

class AgenticResearchResponse(BaseModel):
    goal: str
    answer: str
    sources: list[dict]
    confidence: float
    gaps: str
    tool_calls_made: int
    providers_used: list[str]
```

## Graph Construction

```python
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

def build_research_agent(model, tools, research_goal: str):
    system_prompt = f"""You are a thorough web research agent. Your research goal: {research_goal}

Workflow:
1. Start with web_search to discover relevant sources
2. Use get_content or batch_get_content to read promising pages
3. Cross-reference findings across multiple sources
4. Use academic_search for scholarly sources when relevant
5. Use gemini_search for quick grounded verification
6. When you have sufficient evidence, call final_answer to synthesize

Rules:
- Cite EVERY claim with [N] where N is the source number
- Never fabricate — if unsure, say so in the gaps field
- Prioritize authoritative sources (official docs, academic papers, reputable publications)
- You have {max_steps} tool call maximum — be efficient
- Always include source URLs in the final_answer sources list"""

    agent = create_react_agent(
        model,
        tools,
        prompt=system_prompt,
        state_schema=AgenticResearchState,
        checkpointer=MemorySaver(),
        version="v2",  # Parallel tool execution
    )
    return agent
```

## MCP Tool Registration (server.py)

```python
from fastmcp.dependencies import CurrentContext

@mcp.tool(
    annotations=ToolAnnotations(
        title="Agentic Web Research",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def agentic_web_research(
    goal: str,
    research_goal: str | None = None,
    max_steps: int = 12,
    include_academic: bool = True,
    external_mcp_config: str | None = None,  # JSON string of MCP server configs
    ctx: Context = CurrentContext(),
) -> dict:
    """AI agent that autonomously researches a topic using web search tools.

    Unlike web_search (returns URL lists), this tool:
    1. Searches the web to discover relevant sources
    2. Reads full content from promising pages
    3. Cross-references and verifies findings
    4. Synthesizes a comprehensive answer with source citations

    Returns structured output: answer, sources list, confidence score, gaps.

    Args:
        goal: The research objective. Be specific.
              Example: "Compare React 19 vs Vue 4 SSR performance and developer experience"
        research_goal: Optional context about how results will be used.
                      Example: "I need this for a tech stack decision document"
        max_steps: Maximum tool calls (3-30). Higher = more thorough but slower. Default 12.
        include_academic: Whether to search academic sources (Semantic Scholar, arXiv)
        external_mcp_config: Optional JSON with additional MCP server configs for extended tool access
    """

    model = build_llm_model()  # Uses configured LLM (GROQ_API_KEY, CEREBRAS_API_KEY, or HF)
    tools = build_internal_tools(include_academic=include_academic)
    if external_mcp_config:
        external_tools = await load_external_mcp_tools(external_mcp_config)
        tools.extend(external_tools)

    agent = build_research_agent(model, tools, research_goal or goal)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": max_steps + 5}

    result = await agent.ainvoke({
        "messages": [HumanMessage(content=goal)],
        "goal": goal,
        "research_goal": research_goal or goal,
        "max_steps": max_steps,
    }, config)

    return parse_final_output(result)
```

## LLM Model Selection

The agent reuses existing LLM infrastructure. Priority:
1. `CEREBRAS_API_KEY` → Cerebras (fast inference, good for tool-calling)
2. `GROQ_API_KEY` → Groq (fast inference)
3. `MISTRAL_API_KEY` → Mistral (existing, used for query rewrite)
4. `OPENROUTER_API_KEY` → OpenRouter (any model)

All already configured in the codebase. No new API key requirements.

## Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `pyproject.toml` | Modify | Add `langgraph>=0.6.0`, `langchain-core>=0.3.0`, `langchain-mcp-adapters>=0.2.0`, `langchain-openai`/`langchain-anthropic` |
| `src/.../research/__init__.py` | Create | Module exports |
| `src/.../research/tools.py` | Create | Internal tool wrappers: web_search_tool, get_content_tool, batch_get_content_tool, gemini_search_tool, academic_search_tool, final_answer_tool |
| `src/.../research/agent.py` | Create | `build_research_agent()`, `build_llm_model()`, `parse_final_output()` |
| `src/.../models.py` | Modify | Add `AgenticResearchResponse` Pydantic model |
| `src/.../settings.py` | Modify | Add `KINDLY_RESEARCH_MODEL`, `KINDLY_RESEARCH_DEFAULT_MAX_STEPS`, `KINDLY_RESEARCH_MCP_CONFIG` |
| `src/.../server.py` | Modify | Add `agentic_web_research` MCP tool registration |
| `tests/test_agentic_research.py` | Create | Unit tests with mocked cores |
| `CHANGELOG.md` | Modify | Entry under `[Unreleased]` |
| `docs/AGENTIC_RESEARCH.md` | Create | Usage docs |

## Reuse Strategy (Detailed)

| Existing Capability | How It's Used in the Graph |
|---|---|
| `run_web_search()` (search/orchestrator.py) | `web_search_tool` — LLM calls with query + research_goal, rewrite=True |
| `fetch_content_artifact()` (content/fetch_pipeline.py) | `get_content_tool` — per-URL fetch with windowing |
| `run_batch_fetch()` (content/batch_orchestrator.py) | `batch_get_content_tool` — parallel multi-URL fetch |
| `gemini_search` (existing MCP tool) | `gemini_search_tool` — grounded answer verification |
| `academic_search` (existing orchestrator) | `academic_search_tool` — optional scholarly search |
| `discover_links()` (content/link_discovery.py) | `discover_links_tool` — deep exploration from high-signal pages |
| `query_cache` + `semantic_cache` + `page_cache` | Prevent duplicate searches/fetches across tool calls |
| `content/windowing.py` | Token budget enforcement at fetch boundaries |
| `middleware/rate_limits.py` | Research tool marked as expensive; respects existing rate limiting |
| `telemetry.py` + OTel | New spans: research.agent, research.tool_call |
| Existing LLM keys (CEREBRAS/GROQ/MISTRAL/OPENROUTER) | Model for agent reasoning — no new keys needed |

## Why This Beats the Original Plan

| Original Plan | V2 Plan |
|---|---|
| 3-level hierarchy (main→supervisor→researcher) | Single ReAct agent |
| Manual tool orchestration (custom nodes) | Standard `create_react_agent` + ToolNode |
| HITL plan review + draft review | No HITL — agent decides when done via `final_answer` |
| Custom compression pipeline | LLM manages its own context via bounded tool responses |
| ~500 lines of custom graph code | ~200 lines of tool wrappers + standard ReAct |
| No external MCP tool consumption | `langchain-mcp-adapters` for external tools |
| Requires new LLM for supervision | Uses existing LLM keys (Cerebras/Groq/Mistral) |
| `langgraph>=0.2` (outdated, missing Send API) | `langgraph>=0.6.0` (v2 parallel execution, Pydantic state) |

## Implementation Phases

### Phase 1 — Foundation
- Dependencies added to pyproject.toml
- Internal tool wrappers (tools.py)
- Agent factory + graph construction (agent.py)
- MCP tool registration in server.py
- Pydantic models for response
- Basic tests with mocked cores
- CHANGELOG + docs

### Phase 2 — External MCP Tools
- `langchain-mcp-adapters` integration
- External MCP config loading from env/KINDLY_RESEARCH_MCP_CONFIG
- Tool deduplication (internal + external)
- Connection lifecycle management

### Phase 3 — Production Hardening
- SqliteSaver/MemorySaver selection per env
- Step counting + recursion limit enforcement
- Error resilience (retry policies, graceful degradation)
- Streaming via `graph.astream()` + `ctx.info()` for progress
- Performance benchmarking

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM infinite loops (never calls final_answer) | `recursion_limit` in config, `max_steps` ceiling in state |
| Token explosion from fetching too many pages | Bounded `char_length` per tool call, per-tool page budget |
| Slow response time for multi-step research | `version="v2"` parallel tool execution, `batch_get_content` |
| LLM fabricates citations | Structured `final_answer` forces explicit source list; prompt instructs honesty |
| External MCP server failures | Isolated per-tool exception handling, graceful degradation |
| Circular dependency (MCP tool calling itself) | Internal wrappers bypass MCP protocol entirely |
