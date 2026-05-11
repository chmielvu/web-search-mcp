# FastMCP Improvements - Refined Implementation

## STATUS: ✅ IMPLEMENTED

All changes have been applied to `server.py`.

### 1. Tool Annotations (CRITICAL)
- **Draft used**: `annotations={"readOnlyHint": True, ...}` (raw dict)
- **Correct pattern**: Use `ToolAnnotations` class from `mcp.types`

### 2. Context Import (CRITICAL)
- **Draft used**: `from fastmcp import Context, CurrentContext`
- **Correct pattern**: 
  - `from fastmcp.dependencies import CurrentContext`
  - `from fastmcp.server.context import Context`

### 3. Resources (Minor)
- Pattern correct, but return types could be simpler

### 4. Prompts (Minor)
- Content verbose, could be more actionable

---

## Refined Implementation

### Imports to Add (at top of server.py)

```python
# Add after existing imports (around line 21)
from mcp.types import ToolAnnotations  # For tool annotations
from fastmcp.dependencies import CurrentContext  # For context injection
from fastmcp.server.context import Context  # Context type
```

---

### 1. Tool Annotations (Corrected)

All 6 tools should use `ToolAnnotations` class:

```python
# web_search - read-only, safe to call, idempotent
@mcp.tool(
    annotations=ToolAnnotations(
        title="Web Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,  # Interacts with external search APIs
    )
)
async def web_search(query: str, num_results: int = 3, rewrite: bool = True) -> dict:
    ...

# get_content - read-only, safe to call, idempotent
@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Content",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,  # Fetches from external URLs
    )
)
async def get_content(url: str) -> dict:
    ...

# gemini_search - read-only, idempotent, uses external API
@mcp.tool(
    annotations=ToolAnnotations(
        title="Gemini Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def gemini_search(query: str, structured_output: bool = False) -> dict:
    ...

# perplexity_search - read-only, idempotent, external API (rate-limited)
@mcp.tool(
    annotations=ToolAnnotations(
        title="Perplexity Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def perplexity_search(query: str, depth: str = "normal") -> dict:
    ...

# youtube_transcript - read-only, idempotent
@mcp.tool(
    annotations=ToolAnnotations(
        title="YouTube Transcript",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def youtube_transcript(
    video_id_or_url: str,
    language: str | None = None,
    translate_to: str | None = None,
    format: str = "text",
) -> dict:
    ...

# youtube_search - read-only, idempotent
@mcp.tool(
    annotations=ToolAnnotations(
        title="YouTube Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def youtube_search(query: str, num_results: int = 5) -> dict:
    ...
```

**Annotations rationale:**
- `readOnlyHint=True`: All tools only read data, no mutations
- `idempotentHint=True`: Repeated calls with same params return same results (searches, fetches)
- `openWorldHint=True`: All interact with external systems (web, APIs, YouTube)
- No `destructiveHint`: None of these tools perform destructive operations

---

### 2. Context Injection (Corrected)

Add Context to the two most-used tools: `web_search` and `get_content`

```python
# web_search - Add context injection
@mcp.tool(
    annotations=ToolAnnotations(
        title="Web Search",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def web_search(
    query: str,
    num_results: int = 3,
    rewrite: bool = True,
    ctx: Context = CurrentContext(),  # Injected context
) -> dict:
    """Search the web and return lightweight results only.
    
    Key instruction:
    Consider this as your default web search tool...
    
    Args:
        query: Search query string...
        num_results: Number of results...
        rewrite: If True, use Mistral to generate additional queries...
        ctx: FastMCP context (auto-injected, not shown to client)
    
    Returns:
        {"query": str, "results": [{"title": str, "link": str, "snippet": str}, ...]}
    """
    # Early progress reporting
    await ctx.info(f"Searching: {query[:80]}...")
    
    # ... existing implementation ...
    
    # After semantic cache check
    if cached_result:
        await ctx.debug(f"Semantic cache hit for query")
        return cached_result
    
    # Before actual search
    await ctx.info(f"Running multi-provider search for {num_results} results")
    
    # ... search orchestration ...
    
    # After getting results
    await ctx.info(f"Found {len(response_model.results)} results")
    
    return response


# get_content - Add context injection
@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Content",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def get_content(
    url: str,
    ctx: Context = CurrentContext(),  # Injected context
) -> dict:
    """Fetch a single URL and return best-effort, LLM-ready Markdown.
    
    Args:
        url: URL to fetch...
        ctx: FastMCP context (auto-injected)
    
    Returns:
        {"url": str, "page_content": str}
    """
    await ctx.info(f"Fetching: {url[:80]}...")
    
    # ... cache check ...
    
    # Before content resolution
    await ctx.debug(f"Starting content resolution pipeline")
    
    # ... resolution ...
    
    # After resolution
    if page_md:
        await ctx.info(f"Extracted {len(page_md)} chars")
    else:
        await ctx.warning(f"Content extraction returned empty")
    
    return GetContentResponse(
        url=url,
        page_content=page_md,
        diagnostics=diag.entries if diag_enabled else None,
    ).model_dump(exclude_none=True)
```

**Context methods to use:**
- `ctx.info()` - Key progress updates (shown to users)
- `ctx.debug()` - Detailed technical info (debugging)
- `ctx.warning()` - Something unusual but recoverable
- `ctx.error()` - Failures (use sparingly, prefer raising exceptions)

---

### 3. Resources (Refined)

```python
# ============ RESOURCES ============

@mcp.resource("status://providers")
def get_providers_status() -> str:
    """Which search providers are configured."""
    from .utils.diagnostics import mask_env_values
    
    lines = [
        "# Search Provider Status",
        "",
        f"**SearXNG** (Primary): {'✓ Configured' if settings.searxng_base_url else '✗ Not configured'}",
        f"**Tavily**: {'✓ Configured' if settings.tavily_api_key else '✗ Not configured'}",
        f"**Brave**: {'✓ Configured' if settings.brave_api_key else '✗ Not configured'}",
        f"**Jina**: {'✓ Configured' if settings.jina_api_key else '✗ Not configured'}",
        "",
        "## AI Search",
        f"**Gemini**: {'✓ Configured' if settings.gemini_api_key else '✗ Not configured'}",
        f"**Perplexity (Pollinations)**: {'✓ Configured' if settings.pollinations_api_key else '✗ Not configured'}",
        "",
        "## Other",
        f"**GitHub Token**: {'✓ Configured' if settings.github_token else '✗ Not configured'}",
    ]
    return "\n".join(lines)


@mcp.resource("status://features")
def get_features_status() -> str:
    """Server feature flags status."""
    lines = [
        "# Feature Status",
        "",
        f"**Semantic Cache**: {'✓ Enabled' if settings.semantic_cache_enabled else '✗ Disabled'}",
        f"**Query Rewrite**: {'✓ Enabled' if settings.query_rewrite_enabled else '✗ Disabled'}",
        f"**Reranking**: {'✓ Enabled' if settings.reranking_enabled else '✗ Disabled'}",
        "",
        "## Cache Settings",
        f"LanceDB Path: {settings.lancedb_dir}",
        f"Cache Dir: {settings.cache_dir}",
        "",
        "## Timeouts",
        f"Tool Timeout: {settings.tool_total_timeout_seconds}s (max {settings.tool_total_timeout_max_seconds}s)",
    ]
    return "\n".join(lines)


@mcp.resource("docs://workflow")
def get_workflow_doc() -> str:
    """Recommended workflow for using web search tools."""
    return """# Web Search Workflow

## Discovery → Extraction → Synthesis

### Step 1: Search
```python
web_search(query="your specific question", num_results=3)
```
Returns lightweight results (title, link, snippet).

### Step 2: Extract
```python
get_content(url="https://selected-url")
```
Returns LLM-ready Markdown.

### Step 3: Synthesize (optional)
```python
gemini_search(query="your question with context")
```
Returns AI-synthesized answer with citations.

## When to Use Each Tool

| Tool | Purpose |
|------|---------|
| web_search | Discover URLs |
| get_content | Read specific URL |
| gemini_search | Quick grounded answers |
| perplexity_search | Deep reasoning synthesis |

## Tips
- Search exact error messages in quotes
- Prefer official docs over blogs
- Use num_results=1-5 to limit context
"""
```

---

### 4. Prompts (Refined)

```python
# ============ PROMPTS ============

@mcp.prompt("debug-error")
def debug_error_prompt(error_message: str) -> str:
    """Prompt for debugging an error using web search."""
    return f"""Debug this error: {error_message}

Approach:
1. Search the exact error message in quotes
2. Check GitHub issues for similar reports
3. Verify library versions match solution
4. Apply fix and test

Start: web_search(query="{error_message}", rewrite=False)"""


@mcp.prompt("research-topic")
def research_topic_prompt(topic: str, depth: str = "comprehensive") -> str:
    """Prompt for researching a topic."""
    return f"""Research: {topic} (depth: {depth})

Workflow:
1. web_search(query="{topic}", num_results=5) → discover sources
2. get_content(url=...) on 2-3 promising results
3. gemini_search(query="{topic} summary") for synthesis

Focus on: official docs, GitHub repos, recent updates"""


@mcp.prompt("find-library-docs")
def find_library_docs_prompt(library: str, feature: str) -> str:
    """Prompt for finding library documentation."""
    return f"""Find docs for: {library} - {feature}

1. web_search(query="{library} {feature} site:gofastmcp.com OR site:docs.*")
2. get_content on official docs URL
3. gemini_search for quick syntax reference

Prefer official docs over blog posts"""
```

**Prompt best practices:**
- Keep prompts actionable (not just documentation)
- Include specific tool calls as examples
- Make parameters part of the generated prompt
- Return short, focused instructions

---

## Summary of Changes

| Component | Draft Issue | Correction |
|-----------|-------------|------------|
| Annotations | Used dict | Use `ToolAnnotations` class |
| Context Import | Wrong module | `fastmcp.dependencies.CurrentContext` |
| Resources | Verbose | Simplified to essential info |
| Prompts | Documentation-heavy | Actionable workflows |

## Files to Modify

1. **server.py** - Add imports, replace tool decorators, add resources and prompts
2. **models.py** - No changes needed
3. **settings.py** - No changes needed

## Implementation Order

1. Add imports (ToolAnnotations, CurrentContext, Context)
2. Replace all `@mcp.tool` decorators with annotated versions
3. Add `ctx: Context = CurrentContext()` to web_search and get_content
4. Add logging calls at key progress points
5. Add resources at end of file
6. Add prompts at end of file