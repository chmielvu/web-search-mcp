# AGENTS.md - Public Code Search Prototype

Agent-oriented public GitHub code search prototype (`pcs.py`). Single-file Python prototype (stdlib + `httpx`, optional `sentence-transformers`) combining GitHub GraphQL, GitHub REST code search, grep.app, and Sourcegraph GraphQL V2 into a unified agent tool.

## Key Files

| File | Role |
|---|---|
| `pcs.py` | Single-file hybrid public code search prototype (CLI & stdlib/httpx implementation) |
| `DESIGN.md` | v6 design document, provider comparison, query handling, and verified test scenarios |
| `test_pcs.py` | Unit and integration regression suite |

## Architecture & Search Engines

1. **GitHub GraphQL** (`api.github.com/graphql`): Repository discovery (`repos`), repo tree (`tree`), file blob (`file`), 1-request field precision.
2. **GitHub REST Code Search** (`api.github.com/search/code`): Code search with `text-match+json` fragments, total count, repository scoping.
3. **grep.app** (`grep.app/api/search`): Quota-free regex search across ~1M repos with line numbers (bot-walled 429 handled as status).
4. **Sourcegraph GraphQL V2** (`sourcegraph.com/.api/graphql`): `lineMatches`, symbols (`type:symbol`), and LSIF definitions/references (`intel`).

## Rules

- No quota/rate-limit bookkeeping preflight; requests dispatch directly and surface status for agent decision.
- Query handling strips conversational stopwords, preserves quoted phrases, converts `/pattern/flags` to regex filters, and builds deterministic variants.
- Line hydration fetches raw files for top-K candidates only (budget-capped).
- Hybrid ranking: `0.45 * lexical + 0.25 * provider + 0.2 * log-stars + 0.1 * recency`; optional `--cross-encoder` reranking degrades gracefully if `sentence-transformers` is unavailable.

## Testing

```bash
uv run python -m unittest discover -s prototypes/public_code_search -p "test_*.py"
```
