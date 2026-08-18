# pcs — public code search for AI agents (v6)

Single-file Python prototype (`pcs.py`, stdlib + httpx, no hard ML deps) that
combines four engines into one agent tool:

| Engine | Interface | Auth | Gives |
|---|---|---|---|
| GitHub GraphQL | `api.github.com/graphql` | token | repo discovery, repo tree, file blob — field-precise, 1 call |
| GitHub REST `/search/code` | `api.github.com/search/code` | token | code search with text-match fragments, total_count |
| grep.app | `grep.app/api/search` | none (bot-walled) | regex search ~1M repos, line numbers |
| Sourcegraph GraphQL V2 | `sourcegraph.com/.api/graphql` | anonymous | **code search through GraphQL**: lineMatches, symbols, LSIF defs/refs |

## Search capabilities

| Command | Engine | What the agent gets |
|---|---|---|
| `search QUERY` | GitHub REST code + grep.app + Sourcegraph, hybrid | code hits with fragments, line numbers, ranked, budgeted |
| `repos QUERY --sort stars` | GitHub GraphQL | repo discovery: stars, language, pushed, description |
| `issues QUERY --semantic\|--hybrid` | GitHub REST `/search/issues` | issues incl. GitHub's semantic/hybrid modes + labels |
| `commits QUERY` | GitHub REST `/search/commits` | commit messages + text-match snippets |
| `symbols QUERY --lang go` | Sourcegraph `type:symbol` | symbol definitions: kind, container, language, exact line |
| `tree OWNER/REPO` | GitHub GraphQL | repo tree in one call |
| `file OWNER/REPO PATH` | GitHub GraphQL | blob content, size/truncation flags |
| `intel OWNER/REPO PATH LINE` | Sourcegraph LSIF | definitions/references at a position |

## Why hybrid (best of both worlds)

GitHub's GraphQL API **has no `type: CODE` search** (canonical: Stack Overflow
#45382069). So one API can never do everything:

- **GraphQL wins for structure**: repo discovery with `repositoryCount`, tree
  walks, blob reads — one request, only the fields you ask for, no pagination
  overhead.
- **REST wins for code**: `/search/code` is the only GitHub code-search surface
  (1000-result cap, `text-match+json` accept header for fragments).
- **Sourcegraph is the "GraphQL code search" workaround**: its public GraphQL V2
  does what GitHub's GraphQL can't — `lineMatches` (line numbers + previews),
  `symbols`, optional `content` preload, and LSIF `definitions`/`references`.
- **grep.app is quota-free regex offload**: regex over ~1M repos with line
  numbers, no auth (note: raw API is Vercel-bot-walled as of 2026-08; the
  official route is `mcp.grep.app`).

The pipeline: GraphQL discover (parallel) → REST code search + grep.app +
Sourcegraph (parallel) → dedupe → rank → hydrate line numbers for top-K (raw
fetch, concurrency 8, 5s timeout) → token-budgeted output.

Deliberately no quota/rate-limit bookkeeping: no preflight, no pacing, no
retry-on-429 logic. Requests go out as-is; provider failures and blockages are
surfaced as statuses so the agent can decide.

## Query handling

- Stopword stripping incl. conversational prose ("how do people implement X"
  → "X"); short stopwords removed too.
- Quoted phrases (`"rate limit" python`) pass through to the API verbatim;
  scoring uses unquoted terms.
- `/pattern/flags` tokens become a local post-filter regex AND are passed as
  the raw regex to grep.app / Sourcegraph regexp mode (cross-provider regex).
- Qualifiers pass through untouched; validation mirrors octocode + fulll
  (repo requires owner; qualifier-only queries rejected).
- Deterministic refinement (no LLM): primary variant is the faithful compiled
  query; secondary is the top-2-terms tighter variant.

## Mechanisms and their proven sources

| Mechanism | Source (read on GitHub, 2026-08) |
|---|---|
| 1000-result / page-11 guard, raw-URL line resolution, concurrency cap | `fulll/github-code-search` `api.ts` |
| Filter architecture (`uses_core_api` accounting per step) | `janeklb/gh-search` `filters.py` |
| Repo requires owner; ≥1 non-qualifier term; scoped-zero → one-cheap-probe (archived/renamed/not_found) | `bgauryy/octocode` `execution.ts` |
| Sourcegraph GraphQL V2 queries incl. LSIF code intel, content-preload toggle | `twn39/sourcegraph-search` `queries.py` |
| Cross-encoder rerank: 2000-char chunks, `0.5*max + 0.5*mean`, negative-score shift; weighted fusion w/ log-stars + recency | `zamalali/DeepGit` |
| `fields` subsetting / minimal output to cut tokens | `github/github-mcp-server` `search.go` |
| Schema-validated provider responses; unified result shape | `spences10/mcp-omnisearch` |
| Enrichment API-call caps | `jasperan/discover-github` |
| Per-hit content expansion with outcome enum (OK/NO_HIT/PARTIAL/ERROR) | `JetXu-LLM/llama-github` |

## Ranking

Lexical: term presence in path (2.0) / match text (1.0) + fragment-coverage
bonus (fraction of query terms co-occurring × 1.5). Fusion (no cross-encoder):
0.45·lexical + 0.25·provider + 0.2·log-stars + 0.1·recency, normalized.
With `--cross-encoder`: 0.4·CE + 0.2·lexical + 0.2·log-stars + 0.2·recency.

## Usage

```
python pcs.py search "rate limit retry" --top 10          # hybrid, all engines
python pcs.py search "x-ratelimit-reset repo:fulll/github-code-search"
python pcs.py search "retry after /retry-after/i" --regexp # local regex post-filter
python pcs.py search "..." --deep                          # paginate REST to 1000
python pcs.py search "..." --engine sourcegraph            # GraphQL code search
python pcs.py search "..." --budget 800|16000              # token budget scales fetch
python pcs.py search "..." --cross-encoder                 # optional CE rerank
python pcs.py search "..." --json --top 3
python pcs.py tree octocode/octocode                       # GraphQL tree
python pcs.py file janeklb/gh-search ghsearch/gh_search.py # GraphQL blob
python pcs.py intel github.com/psf/requests requests/sessions.py 130  # LSIF
python pcs.py repos "topic:code-search" --sort stars      # repo discovery
python pcs.py issues "exponential backoff" --semantic     # issues, incl. semantic
python pcs.py commits "add retry logic"                   # commit history search
python pcs.py symbols "fetchWithRetry" --lang go          # symbol search
```

Token resolution: `GITHUB_TOKEN`/`GH_TOKEN` env, else `gh auth token`.

## Verified live (2026-08-14)

- Hybrid global search: gh + sourcegraph hits merged, ranked, tokens counted.
- Repo-scoped: 5 hits in `fulll/github-code-search`, real line numbers (L27/L51/L43).
- `tree`/`file`: GraphQL round-trips in ~2s.
- `intel`: graceful empty when LSIF index missing.
- Natural language: "how do people implement retry with exponential backoff"
  → compiled `retry exponential backoff`, relevant top hits.
- Quoted phrase: `"rate limit" python` preserved in the API query.
- Regex token `/retry-after/i` → Sourcegraph regexp mode returns exact code
  matches (e.g. guile `response-retry-after`), post-filter applied.
- Deep pagination: 110 hits / 10 pages / 58s.
- Budget: `--budget 800` → per_page 5; `--budget 16000` → per_page 45.
- Cross-encoder: clean degradation to lexical rerank when ML stack absent.
- grep.app: 429 Vercel checkpoint surfaced as status, not silent zero.
- 403 rate-limit: clean error + exit 2, no leaked task exceptions
  (await section wrapped in try/finally task cancellation).
- Unicode (Spanish) queries work.
- `symbols "fetchWithRetry" --lang go` -> 12 symbol defs with containers + lines.
- `issues "exponential backoff retry" --semantic` -> 259952 total, labeled issues.
- `commits "add retry backoff"` -> 374792 total, messages + match snippets.
- `repos "github code search mcp" --sort stars` -> ranked discovery with descriptions.

## Known limits

- grep.app raw API is bot-walled (429) — engine reports it; MCP endpoint untested.
- Sourcegraph anonymous access occasionally returns empty responses (surfaced).
- LSIF code intel exists only for SCIP-indexed repos.
- Cross-encoder needs `sentence-transformers` (heavy); off by default.
- Line hydration fetches whole raw files for top-K only (budget-capped).
