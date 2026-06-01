# FastMCP Client Steering Plan

Date: 2026-05-11T13:10:17+02:00
Status: research-backed implementation plan
Scope: Improve how Kindly Web Search MCP uses FastMCP to steer clients toward effective tool choice and explicit tool chaining.

## Sources Reviewed

Primary FastMCP documentation:

- https://gofastmcp.com/llms.txt
- https://gofastmcp.com/servers/server
- https://gofastmcp.com/servers/pagination
- https://gofastmcp.com/servers/prompts
- https://gofastmcp.com/servers/context
- https://gofastmcp.com/servers/resources
- https://gofastmcp.com/servers/transforms/code-mode
- https://gofastmcp.com/servers/transforms/resources-as-tools
- https://gofastmcp.com/servers/transforms/prompts-as-tools

Tooling note: crawl4ai/Kuzu MCP was attempted first, per request, but could not ingest the FastMCP pages in this environment. `crawl_single_page` failed for `https://gofastmcp.com/llms.txt` and `https://gofastmcp.com/servers/server`; `smart_crawl_url` failed because the crawler's Playwright Chromium executable was missing. Official FastMCP docs were used directly as fallback.

## Current Fit

The server already uses several FastMCP capabilities well:

- Uses standalone `fastmcp.FastMCP`, not the MCP SDK shim.
- Registers tools, resources, prompts, middleware, and `ToolAnnotations`.
- Exposes useful resources such as `status://providers`, `status://features`, and `docs://workflow`.
- Has agent-oriented docstrings for key tools.
- Uses `Context` for client-visible info messages.
- Uses middleware for rate limiting, expensive-tool protection, Gemini advisory, and query/result guidance.

The main gap is not tool count. The main gap is that the intended usage grammar is still too implicit. Clients should be steered toward:

```text
web_search -> get_content / batch_get_content -> optional synthesis
```

Rather than seeing every search-like tool as a flat interchangeable option.

## Recommendation 1: Update Server Instructions

FastMCP server instructions are explicitly client/LLM-facing steering text. The current instructions still imply search includes extraction, but the current architecture intentionally separates discovery from extraction.

Replace the server instructions with a concise chaining contract:

```text
This MCP server is a web research toolbox for agents. Use web_search for lightweight URL discovery only. Use get_content or batch_get_content to read selected URLs. Use gemini_search or perplexity_search only when synthesized cited answers are desired. Prefer explicit search -> fetch -> synthesize chains over one-shot broad calls.
```

This is the cheapest high-leverage change.

## Recommendation 2: Add PromptsAsTools

FastMCP `PromptsAsTools` creates `list_prompts` and `get_prompt` tools for clients that do not support MCP prompts directly. This fits coding-agent clients well because tools are often surfaced more reliably than prompts.

Proposed wiring:

```python
from fastmcp.server.transforms import PromptsAsTools

mcp.add_transform(PromptsAsTools(mcp))
```

Improve prompts into workflow guides:

- `debug_error_prompt`
- `current_library_docs_prompt`
- `compare_sources_prompt`
- `search_then_extract_prompt`
- `youtube_research_prompt`
- `recent_topic_verification_prompt`

This lets tool-only clients discover how to chain the MCP instead of guessing from individual tool descriptions.

## Recommendation 3: Add ResourcesAsTools

FastMCP `ResourcesAsTools` creates `list_resources` and `read_resource` tools for clients that cannot use the MCP resource protocol directly.

Proposed wiring:

```python
from fastmcp.server.transforms import ResourcesAsTools

mcp.add_transform(ResourcesAsTools(mcp))
```

This makes existing resources, especially `docs://workflow`, visible to tool-only clients. That directly improves client steering.

## Recommendation 4: Add Tool/Resource/Prompt Metadata

FastMCP supports tags and custom `meta` fields on prompts/resources and, by the broader component model, useful structured metadata should be exposed wherever supported.

Recommended tool tags:

```python
tags={"search", "discovery", "read-only", "open-world"}
tags={"content", "extraction", "follow-up"}
tags={"synthesis", "answer", "grounded"}
tags={"video", "youtube"}
tags={"image", "composio"}
```

Recommended `meta.kindly` shape:

```python
meta={
    "kindly": {
        "role": "discovery",
        "chain_next": ["get_content", "batch_get_content"],
        "returns": "lightweight_search_results",
        "cost": "low",
        "latency": "medium",
        "content_policy": "snippets_only"
    }
}
```

Use similar metadata for resources and prompts so clients can group and prioritize components without parsing prose.

## Recommendation 5: Add First-Class Chaining Hints to Results

Do not hide orchestration inside the server. Keep the external agent in control, but return a compact map of likely next steps.

For `web_search`, include something like:

```json
{
  "recommended_next_tools": [
    {
      "tool": "batch_get_content",
      "reason": "Fetch 2-3 selected result links for comparison",
      "input_from": "results[].link"
    },
    {
      "tool": "get_content",
      "reason": "Fetch one specific result",
      "input_from": "results[0].link"
    }
  ]
}
```

For `youtube_search`, recommend `youtube_transcript`.

For `composio_similarlinks`, recommend `get_content`.

This preserves transparent agent-controlled chaining while making the intended choreography obvious.

## Recommendation 6: Use Context Progress for Long-Running Tools

FastMCP context supports logging and progress reporting. The server already uses `ctx.info()` in places, but `ctx.report_progress()` would make slow operations feel much less opaque.

Recommended stages:

```text
web_search:
10/100 normalize/cache lookup
25/100 query rewrite
50/100 providers queried
75/100 merge/rerank
100/100 results ready

get_content:
10/100 cache lookup
35/100 specialized resolver
60/100 HTTP extraction
85/100 browser fallback if needed
100/100 markdown ready

batch_get_content:
N/M URLs complete
```

Use `ctx.info()` for concise human-readable updates and `ctx.report_progress()` for progress bars where clients support them.

## Recommendation 7: Add More Resources and Resource Templates

Existing resources are useful but should become a stronger steering layer.

Add static resources:

```text
docs://tool-chains
docs://provider-routing
docs://cost-latency-guide
docs://error-recovery
docs://search-contracts
```

Consider resource templates:

```text
provider://status/{provider}
workflow://{task_type}
cache://page/{url*}
```

`cache://page/{url*}` requires care because URLs contain slashes, but FastMCP supports wildcard URI template parameters.

## Recommendation 8: Use Elicitation Sparingly

FastMCP context supports client elicitation. This should not be used for normal `web_search`, because it interrupts agentic workflows.

Good elicitation candidates:

- `perplexity_search` query is too vague or too broad.
- `gemini_search` asks for current information without timeframe/domain.
- `batch_get_content` receives too many URLs without a selection strategy.

For most calls, returned `usage_hint` and `recommended_next_tools` fields are less disruptive.

## Recommendation 9: Consider Strict Input Validation

FastMCP can use `strict_input_validation=True` at server construction time. This is attractive for agent-facing tools because malformed tool calls should fail early instead of being silently coerced.

Potential constructor direction:

```python
mcp = FastMCP(
    "kindly-web-search",
    version=__version__,
    website_url="https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server",
    instructions=AGENT_STEERING_INSTRUCTIONS,
    strict_input_validation=True,
    mask_error_details=True,
    list_page_size=25,
)
```

Validate this against current tests before enabling, because some existing clients/tests may rely on flexible coercion.

## Recommendation 10: Treat Pagination as Future-Proofing

FastMCP pagination is useful when a server exposes many tools/resources/prompts. The current surface is not yet large enough to make pagination urgent.

Use `list_page_size` if:

- Composio expands the exposed tool count substantially.
- Provider-specific tools become numerous.
- Prompt/resource catalogs grow.

A reasonable future setting:

```python
list_page_size=25
```

## Recommendation 11: Keep CodeMode Opt-In

FastMCP `CodeMode` lets LLMs discover tools and write sandboxed Python to orchestrate them. It can reduce context bloat and round trips for large tool catalogs or multi-step workflows.

For this project, do not enable CodeMode as the default server surface yet. The catalog is still small enough that direct tool use is clearer, and the project philosophy favors external agent-controlled orchestration.

Consider a separate opt-in entrypoint:

```text
kindly-web-search-code-mode
```

Good use cases:

- Search several queries.
- Dedupe URLs.
- Fetch top pages.
- Return a compact source table.

Keep it explicitly separate from the default MCP registration.

## Recommended Implementation Sequence

1. Update server `instructions` to match the search/fetch/synthesize architecture.
2. Add `PromptsAsTools(mcp)`.
3. Add `ResourcesAsTools(mcp)`.
4. Add `docs://tool-chains` and `docs://provider-routing` resources.
5. Add tags/meta to prompts, resources, and tools.
6. Add `recommended_next_tools` / `usage_hint` fields to relevant tool results.
7. Add `ctx.report_progress()` to long-running tools.
8. Evaluate `strict_input_validation=True` against tests.
9. Add pagination only if the catalog grows.
10. Add CodeMode only as a separate optional entrypoint.

## Bottom Line

The best steering mechanism is not another search feature. It is making the usage grammar visible: server instructions, prompts and resources exposed as tools, metadata-rich components, and result-level next-step hints that teach `search -> extract -> synthesize` as the natural choreography.
