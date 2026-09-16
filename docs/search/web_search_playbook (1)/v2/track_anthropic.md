# Track: Anthropic web_search_20250305

Source: `anthropic-sdk-python` `src/anthropic/types/web_search_tool_20250305_param.py`, leeroopedia docs, agentnotebook.dev 2026 guide

What this track yielded:
1. The full `WebSearchTool20250305Param` TypedDict (used verbatim in PLAYBOOK_v2.md §2.5).
2. The exact mutex rule: `allowed_domains` and `blocked_domains` cannot co-exist.
3. The subdomain matching note (exact match — `docs.anthropic.com` does not match `anthropic.com`).
4. The `$10/1k searches` pricing rule and `max_uses` cost-governor pattern.
5. The wrapper example with `blocked_domains`, `user_location`, `max_uses: 3`.
