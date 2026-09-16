# Track: Tavily

Source files:
- `raw/tavily-ai__tavily-mcp__src__index.ts` (40.1 KB) — MCP server tool schema, line 170-275
- `raw/tavily-ai__langchain-tavily__langchain_tavily__tavily_search.py` (21.4 KB) — field-level Pydantic descriptions the model reads
- `raw/tavily-ai__tavily-python__README.md` — official best-practices summary
- Vendor docs at `tavily.com/blog/research-en`, `deepwiki.com/tavily-ai/skills`, `github.com/tavily-ai/skills`

What this track yielded:
1. The full Pydantic Field descriptions the LLM sees — included verbatim in PLAYBOOK_v2.md §2.1.
2. The `_generate_suggestions()` heuristic for failed Tavily searches.
3. The `auto_parameters` opt-in note (Tavily rewrites your params based on the query content).
4. Tavily's official `skills/tavily-best-practices/SKILL.md` corroborates: keep queries under 400 chars, no operators.
