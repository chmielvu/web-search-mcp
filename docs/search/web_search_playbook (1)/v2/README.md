# Web Search Query Expansion Playbook v2

A web-search-only prompt and operator playbook. **No RAG, no vector DB, no Elasticsearch-as-corpus.** Live internet search engines only.

## What's inside

```
PLAYBOOK_v2.md                 # Main playbook (the deliverable)
raw/                           # Verbatim source files pulled from production repos
  tavily-ai__tavily-mcp__src__index.ts            # Tavily MCP server tool schema
  tavily-ai__langchain-tavily__.../tavily_search.py  # LangChain Tavily integration with full field docs
  exa-labs__exa-mcp-server__src__tools__webSearch.ts   # Exa MCP server web_search tool
  exa-labs__exa-py__exa_py__api.py               # Full Exa Python SDK
  perplexityai__modelcontextprotocol__src__server.ts   # Perplexity MCP server
  browser-use__browser-use__browser_use__agent__prompts.py  # Browser-use system-prompt templates
  tavily-ai__tavily-python__examples__*.py       # Tavily Python examples
  README.md files for each major repo
track_*.md                     # One-pager per evidence-track
```

## Quick start

Open `PLAYBOOK_v2.md` and read it in order:

1. **§0** — what changed from the rejected v1
2. **§1** — engine cheat-sheet matrix
3. **§2** — per-engine deep dive (Tavily, Exa, Sonar, OpenAI, Anthropic, Gemini, CSE, Bing, Brave)
4. **§3** — universal rewrite patterns
5. **§6** — hidden-gem templates
6. **§7** — anti-patterns (RAG traps to avoid)
7. **§9** — copy-paste appendix (8 verbatim prompts you can paste)

## Top 5 prompts to copy right now

1. **§9.1** — `gpt-researcher` `generate_search_queries_prompt` — the production-banned operator prompt
2. **§9.2** — `langchain` `research_agent_prompt` — the canonical "2-3 → 5 → stop" hard limits
3. **§9.5** — `exa` "diverse search queries" — the simplest working prompt in the ecosystem
4. **§9.9** — Elastic's 4 prompt set — keyword extraction, pseudo-answer, method-selector
5. **§2.3** — Sonar rewrite prompt (with the Sonar-specific "system prompt doesn't drive search" caveat)

## Source provenance

Every prompt in this document has a GitHub URL or vendor-docs URL next to it.
All raw source files are included verbatim in `raw/`. Total raw payload: ~290 KB.

## License

Synthesis © 2026 Mavis. Underlying prompts reproduced from MIT-licensed repos
under fair use; original authors credited inline.
