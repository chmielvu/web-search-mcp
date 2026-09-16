# Track: Exa

Source files:
- `raw/exa-labs__exa-mcp-server__src__tools__webSearch.ts` (5.7 KB) — `web_search_exa` tool
- `raw/exa-labs__exa-mcp-server__src__tools__webFetch.ts` (5.3 KB) — `web_fetch_exa` companion
- `raw/exa-labs__exa-py__exa_py__api.py` (143.5 KB) — full Python SDK
- `raw/exa-labs__exa-py__examples__basic_search.py` — official sample
- Vendor docs at `exa.ai/docs/reference/search-api-guide`, `exa.ai/docs/reference/search-best-practices`, `exa.ai/docs/examples/exa-researcher`

What this track yielded:
1. The `category:<type>` inline prefix regex (used by MCP to lift category out of the query).
2. Exa's own canonical "diverse search queries" prompt (used in PLAYBOOK_v2.md §9.5).
3. The "describe the ideal page, not keywords" framing — the model-facing `description` field text.
4. The MCP fallback behavior: highlights by default, text only when highlights missing.
