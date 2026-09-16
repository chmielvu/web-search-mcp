# Track: OpenAI web_search (Responses API)

Source: `platform.openai.com/docs/api-reference/responses/compact` (WebSearchToolCall + WebSearch schemas), `platform.openai.com/docs/guides/tools-web-search`, `learn.microsoft.com/en-us/azure/foundry/openai/how-to/web-search`

What this track yielded:
1. The full `WebSearch` object schema: type, filters.allowed_domains, search_context_size, user_location.
2. The `WebSearchCall.action` shape: { type:"search", queries[], sources[] } — `queries` is plural, returned for reasoning models.
3. The combined filters + user_location + context_size JSON body (used verbatim in PLAYBOOK_v2.md §2.4).
4. The `include=["web_search_call.action.sources"]` flag for citation logging.
5. The braintrustdata cookbook's `WEB_SEARCH_INSTRUCTIONS` (production system prompt that orchestrates the agent without rewriting the query).
