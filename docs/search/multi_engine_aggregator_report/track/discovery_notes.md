# Discovery phase notes

## Method

1. Authenticated as chmielvu via `GITHUB_TOKEN` (PAT) — rate limit at 5000/hr core, 30/hr search
2. `gh search repos "mcp web search"` — 15 top results, manually filtered
3. `gh search code "fallback" "provider" --language python` — code-level intersections
4. Spot-checked via PyGithub `search_repositories` for compound queries
5. Cross-read vendor-commentary blog posts to surface production-quality candidates

## Keywords used (effective ones)

- `"mcp web search"` → broad, 15 results
- `"search aggregator"` → 10 results (mostly MetaSearch, FreeAskInternet cluster)
- `"multi search engine"` (language: python) → 15 results (mostly tutorials, not useful)
- `tavily exa brave` (composite in code) → direct hits on Hermes, DSH, Ketch adapter files
- `Reciprocal Rank Fusion` → 4 hits (Argus, Gigaxity, Ketch, pi-search-hub)
- `topic:mcp-server "multi"` → 12 results (broad)

## Effective candidate-discovery queries

1. Exact-repo keyword: `gh search repos "tavily exa brave"` — direct hits on DSH, Gigaxity, Hermes
2. Engine-count intersection: `gh search code "tavily" "exa"` — joined approach
3. Author-bot patterns: `gh search repos "topic:ai-agents" "search aggregator"` — niche but precise
4. Description-based: `gh search code "Fan-out"` `gh search code "Reciprocal Rank Fusion"`

## False positives (excluded)

- `tavily-ai/tavily-python`, `exa-labs/exa-py`, etc. — vendor SDKs (per user's brief)
- LangChain / LlamaIndex — RAG-flavored, not multi-engine
- Single-engine wrappers without RRF / cascade / fan-out (e.g. yoshiko-pg/o3-search-mcp)
- Awesome-list repos (most are noise)

## Final shortlist (top 13)

After filtering, 13 projects met ALL criteria:
- Not a vendor repo
- Combines ≥2 distinct search APIs OR multi-instance SearXNG
- Has source code (not release-only)
- Active in 2026
- Licensed (MIT or compatible)

Plus 6 additional discovered during deep read (DSH plugin, anysearch union, runnerz, etc.) for a total of 18 graded projects.
