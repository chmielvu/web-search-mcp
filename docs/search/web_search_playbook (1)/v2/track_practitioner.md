# Track: Practitioner guides

Source:
- `dev.to/yaruyng/query-rewrite-in-rag-systems-why-it-matters-and-how-it-works-3mmd` — note: title says "RAG" but the prompt inside is web-search generic. The body uses web search to retrieve; the "RAG" label is on the technique, not the corpus. Includes a clean production prompt:
```
You are a search query optimizer.
Rewrite the user's question to improve retrieval quality.
Rules:
1. Preserve the original meaning.
2. Remove conversational language.
3. Add missing keywords if necessary.
4. Generate 3 different search queries.
User Question:
{query}
Return JSON format:
{ "intent": "...", "queries": ["...", "...", "..."] }
```
- `elastic.co/search-labs/blog/query-rewriting-llm-search-improve` — the 4 verbatim prompts in PLAYBOOK_v2.md §9.9.
- `jimmyresearch.com/modules/tavily-search-integration/` — practical Tavily decision rules (when to use `basic` vs `advanced`).
- `zenn.dev/aiforall/articles/e2ac6ea8ea3285` — Japanese practitioner guide to Tavily parameter tuning.

What this track yielded:
1. The Elastic 4-prompt set is the most-quoted rewrite prompt library in the dev community — included verbatim.
2. The dev.to prompt above — minimal, JSON output, 3 queries per call.
3. The Tavily production heuristic "if a wrong-or-shallow result costs more than one extra LLM turn, use advanced."
