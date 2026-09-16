# Track: Perplexity Sonar

Source files:
- `raw/perplexityai__modelcontextprotocol__src__server.ts` (29.4 KB) — MCP server implementation
- Vendor docs at `perplexity.mintlify.app/guides/search-control-guide`, perplexityaimagazine.com Sonar guide, neura.market Sonar config

What this track yielded:
1. The exact `search_recency_filter`, `search_domain_filter`, `search_context_size` zod schemas.
2. The CRITICAL rule that "the system prompt is not visible to the search step" — included as the lead insight in PLAYBOOK_v2.md §2.3.
3. The agent presets: ASK_PRESET="fast", REASON_PRESET="medium", RESEARCH_PRESET="high".
4. The "fail-fast" instruction ("If you cannot find relevant search results, state that clearly") that should always be passed through.
