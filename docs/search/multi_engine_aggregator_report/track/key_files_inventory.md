# Raw source files inventory (everything I downloaded)

## Ketch (Go, multi-engine RRF, ~5MB total)
- `ketch__multi.go` (10.7K) — RRF fan-out with parallel goroutines, k=60, canonical URL dedup
- `ketch__parallel.go` (5.9K) — alternate parallel implementation
- `ketch__registry.go` (2.7K) — backend registry + resolver
- `ketch__keys.go` (1.8K) — multi-key rotation per provider
- `ketch__search.go` (727B) — interface
- `ketch__canonical.go` (3.3K) — URL canonicalization for dedup
- `ketch__brave.go` (5.2K) — Brave adapter
- `ketch__tavily.go` (6.9K) — Tavily adapter
- `ketch__exa.go` (9.4K) — Exa adapter
- `ketch__searxng.go` (3.8K) — SearXNG adapter
- `AGENTS.md` (16.8K), `README.md` (15.8K) — design docs

## Hermes-web-multi-provider (Python, cascade with cooldowns)
- `provider.py` (26K) — MultiWebProvider with SEARCH_CHAIN/EXTRACT_CHAIN, cooldowns, state.json
- `AGENTS.md` (5.8K), `README.md` (5.7K) — design rationale
- `plugin.yaml` (348B) — Hermes plugin manifest

## mcp-searxng (TypeScript, multi-instance SearXNG fanout)
- `mcp-searxng__search.ts` (35.4K) — core search logic
- `mcp-searxng__types.ts` (14.9K) — type definitions
- `mcp-searxng__cache.ts` (3.4K) — TTL cache with LFU eviction
- `mcp-searxng__searxng-instances.ts` (4.4K) — multi-instance health + cooldown
- `mcp-searxng__searxng-response.ts` (7.3K) — response parser
- `CONFIGURATION.md` (44K) — operator reference
- `README.md` (13.8K)

## agentic-search-arena (Python, LLM judge + BT aggregation)
- `arena__aggregate.py` (13.6K) — Bradley-Terry MLE aggregation + bootstrap CI
- `arena__anchors.py` (11.9K) — anchor scoring
- `arena__arbitrate.py` (9.2K) — Tier-2 human adjudication for ties
- `arena__judge.py` (7.9K) — blind order-swapped pairwise judge
- `arena__rerank.py` (11.4K) — reranking
- `arena__base_handler.py` (2.3K) — abstract ProviderHandler
- `arena__tavily_handler.py` (5.3K), `arena__exa_handler.py` (5.7K), `arena__brave_handler.py` (5.2K)
- `GOVERNANCE.md` (4.3K) — design rationale

## DDGS (Python metasearch library)
- `ddgs__ddgs.py` (10.4K) — DDGS facade with engine selection + ThreadPoolExecutor
- `ddgs__base.py` (4.4K) — BaseSearchEngine ABC
- `ddgs__results.py` (4.1K) — dataclass results with normalizers + ResultsAggregator
- `ddgs____init__.py` (1.6K) — exports

## AnySearch (multi-platform union)
- `anysearch_cli.py` (27.6K), `anysearch_cli.js` (21.1K) — CLI clients
- `generate.py` (9.6K) — API spec generator
- `constants.json` (278B), `doc_spec.md` (10.3K)

## DSH web search free (TypeScript, configurable provider order)
- `dsh__index.ts` (9.8K) — dsh plugin entrypoint
- `dsh__types.ts` (1.3K) — WebSearchProvider interface
- `README.en.md` (19.1K)

## FreeAskInternet (Python, search aggregator wrapper)
- `free_ask_internet.py` (9.7K), `server.py` (8.4K)

## Web Search Plus
- `SKILL.md` (15.5K), `FAQ.md` (9.1K), `config.example.json` (2.8K)

## Yandex-style o3-search-mcp
- `index.ts` (2.5K) — minimal MCP wrapper around OpenAI o3

## Misc
- `union_search.md` (3.1K) — runningZ1/union-search-skill union_search module docs
