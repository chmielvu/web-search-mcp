# Track: Brave Search API

Source: `brave.com/learn/brave-search-api-news-api/`, `brave.com/search/api/glossary/web-search-api/`

What this track yielded:
1. The endpoint split: `/web/search`, `/news/search`, `/images/search`, `/videos/search`, `/local/search`.
2. The `freshness` short codes: `pd`/`pw`/`pm`/`py` (and the long YYYY-MM-DD..YYYY-MM-DD form).
3. The `country` and `search_lang` query params.
4. Brave's recommendation: structured JSON results include multiple `snippets` per article — ideal for RAG... wait, ideal for LLM grounding (Brave's own phrasing). Confirm again: Brave means LLM grounding, i.e., feeding search results into a downstream LLM as context. That's web search — same as Tavily/Exa's intended use.
5. Pricing: $5 free credit on signup, plans starting $3/1k queries.
