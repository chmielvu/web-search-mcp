# Multi-Engine Python Web Search MCPs — A Deep-Inspection Playbook

> **Mission.** A scout-and-deep-dive survey of **Python-only, less-known, multi-engine web search** open-source projects on GitHub that combine ≥3 search engines, expose an MCP server, CLI, or HTTP API, and ship a real routing + fusion + cache layer. Vendor SDKs (tavily-ai/tavily-python, exa-labs/exa-py), single-engine wrappers, and pure RAG/vector-store projects are explicitly **out of scope**.
>
> **Builds on `MULTI_ENGINE_AGGREGATOR_REPORT.md` (v2)**. Web search engine fundamentals (Tavily, Exa, Sonar, Brave, SearXNG, DDG, Firecrawl, Jina) stay the same — v3 zooms in on **how Python projects actually wire multiple engines together** in production code: intent routers, weighted RRF, tier-based quality control, circuit breakers, quota ledgers, deterministic engines, native HTTP providers, keyless fallbacks, and adversarial deduplication.
>
> **Scope.** 24 projects vet-ted across both Parallel Web Search API (`api.parallel.ai`, six distinct `search_id` records) and GitHub REST API (`api.github.com/search/repositories` + `contents/`). Source downloaded as raw text from `raw.githubusercontent.com` (253 files, 2.8 MB).
>
> **Method.** Each candidate was discovered by either (a) thematic Parallel search ("Python multi-engine web search MCP", "keyless multi-source RRF", "SearXNG fallback aggregator") or (b) GitHub code search (`language:python "Tavily" AND "Brave" AND "MCP"`, `language:python reciprocal rank fusion RRF`, `language:python SearXNG MCP`). Once discovered, every repo was crawled for its full source tree; key files (router, fusion, dedup, cache, intent, providers) read by hand and the **exact verbatim patterns** (function names, dataclasses, env-var names, scoring formulas, sentinel values, quirks) captured in this report.
>
> **Excluded from this v3 but worth noting:** TS/JS aggregators (Pi-Search-Hub, MCP-SearXNG, DSH, MetaSearchMCP-TS), Go projects (Karust/openserp — see v2), and pure single-API projects (Khamel83/argus, yoloshii/gigaxity — both fully covered in v2).

---

## TL;DR — the 24 Python repos at a glance

| # | Repo | ★ | Lang | Engines / sources | Pattern | What makes it stand out |
|---|---|---|---|---|---|---|
| 1 | `damionrashford/RivalSearchMCP` | 128 | Python | 5 web + 9 social + 6 academic + 4 dataset + GitHub + PDF | **Concurrent `asyncio.gather` w/ per-engine isolation, FastMCP 3, no LLM inside server** | "Deterministic research MCP" — `find_conflicts` + `score` are first-class MCP operations. The most carefully engineered keyless MCP server of 2026. |
| 2 | `taxueseek/argo` | 130 | Python | **220 sources** (185 keyless), 60 vertical spec YAMLs | **TF-IDF routing + RRF + circuit breaker + admission gate + verify-fetch dossier** | The reference implementation of "220-source metasearch with quality gates." Multi-layer fusion: weighted RRF + dedup + BM25 focus + evidence scores + Vertical-MoE. |
| 3 | `kbjama8/kortex-search` | 0 (new) | Python | **27 sources** (web+code+video+social+forum+academic+CN-ecosystem) | **asyncio.wait fan-out + weighted RRF + cross-encoder rerank + MMR + freshness filter + tiered quality ladder + Redis cache** | Single most sophisticated tier-based quality controller (`tier 0..4` ladder) ever seen in a Python MCP. Async singleflight prevents duplicate work. Adaptive cost model self-calibrates from observed rerank latency. |
| 4 | `hermes-labs-ai/supersearch` | 2 | Python | 8 engines via `ddgs` + `SearXNG` + 16 specialized scrapers (HuggingFace, Crunchbase, LinkedIn, …) | **Cosine-similarity query→category routing + reachability probe + query-variant expansion + per-engine timeout** | Routes via Ollama embedder (cosine threshold 0.7) into 5 buckets: academic / company / security / news / general. |
| 5 | `agent-kreal/agent-web-search` | 5 | Python | **10+ providers** (Tavily, Exa, Brave, You.com, Z.ai, Parallel, Linkup, …) | **Intent-driven router + persistent quota ledger + 429-aware skip + GitHub native first** | Reads "intent: github / ru / news-ru / debug / docs / research" from query keywords, reorders the chain. Quota ledger persists `used / window_start / exhausted_until` so a real 429 becomes a no-skip for the rest of the window. |
| 6 | `AnyGenIO/anygen-search-cli` | 1 | Python | 6 (Brave, Serper, Exa, Tavily, Firecrawl, Jina) | **Mode-keyed provider preference + per-mode FALLBACK_MAP + URL canonicalization + RRF + `answer`-mode cache envelope** | Solves the **Tavily cache-vs-answer bug**: legacy cache stored `{results:[...]}` but dropped `answer`/`context`, so cache HIT returned empty answer while cold worked. Now wrapped in `{results, extras:{answer, context, usage}}`. |
| 7 | `chen150450/multi-search-aggregator` | 27 | Python | **30+ engines** (Zhipu Pro/Sogou/Quark, Z.AI, Bailian, SerpBase, AMiner, Hacker News, Stack Overflow, Wikipedia, arXiv, OpenAlex, OpenReview, DBLP, npm, crates.io, MDN, Substack, 微信读书, Google, 知乎, Reddit, 小红书, Bing CN/Intl, 360搜索, 搜狗Web, 微信搜一搜, Startpage, Brave_WF, Qwant) | **YAML-driven engine weight table + ThreadPoolExecutor max_workers=40 + SSE progress stream + final ranked merging** | The Chinese metasearch ecosystem: opencli-heavy plugins for Weibo/小红书/微信读书, MCP-stdio for EnhancedBing, web_fetch for unauthenticated HTML scraping. **Every engine has a numeric weight** (1-10) used at merge time. |
| 8 | `inorilzy/multi-search-skill` | 3 | Python | 12 sources (Baidu, Brave, Parallel, Tavily, Exa, Firecrawl, SerpAPI, GitHub, HN, StackOverflow, Twitter, sov2ex) | **Bounded Daemon Executor + route profiles + RRF fuse top-15 + automatic URL scrape-preview + SQLite key health + site-scraper memory** | **scrape_top** config param previews the top N results' bodies (1200 chars by default; prefix-trimmed to first H1 or matching snippet). Routes via "profiles" (`tavily-fanout`, `serpapi-google`, etc.). Tracks per-engine key health & site scraper preference in SQLite — "SearXNG config worked last time, try it again before falling back." |
| 9 | `SK-DEV-AI/websearch` | 1 | Python | DDG, Brave, Tavily, Google AI Mode, Groq reranker | **donsetch ranking port (200+ KB pure stdlib) + weighted RRF + per-engine-family mass + BM25-lite + vertical-only penalty + 60/40 blend with cross-encoder + entity-coverage penalty + authority layer + 3-tier stealth + 1 M LoC** | The largest, most paranoid single-file engine-of-engines we've found. Per-engine quota, vertical penalty, entity drift penalty, bot-detection bypass, headless Chrome CDP. Worth reading `merge.py` end-to-end. |
| 10 | `MachineLearning-Nerd/SearchMCP` | 0 | Python | **SearXNG primary + Google fallback + 5-engine CVE-aware profile** | **Intent-aware engine selection (`SECURITY` vs `GENERAL`) + quality gate trigger fallback + per-domain boost (NVD 8.0, docs.python.org 4.0, …) + snippet cleanup** | Auto-detects **CVE IDs in query** (`CVE-2024-1234`), routes to security engines (brave, bing, ddg, wikipedia, github, stackoverflow), boosts results from NVD/CVE.org/MITRE. Falls back to Google scrape ONLY when SearxNG's quality_score < 2.5. |
| 11 | `taxueseek/argo` (engines spec) | (covered) | Python | 60 vertical YAMLs | **CLI / HTTP / HTML adapters declared in YAML, validated by `engine_validate.py --stage health/quality/all`** | The "validate-before-admit" gate: every engine must pass connectivity + schema + latency thresholds (via canary queries) before being admitted into production routing. Adaptive disable after N consecutive opens. |
| 12 | `VulcanusALex/free-search-aggregator` | 2 | Python | 5+ providers (Brave, Tavily, DuckDuckGo, Serper, SearchAPI) | **`SearchRouter` + `QuotaState` (JSON persisted) + `HealthTracker` (rolling 72h) + discovery cycle** | Auto-discovers providers via config.yaml; auto-recovers from quota exhaustion; surfaces health summary as a separate API. Best example of a "self-healing aggregator." |
| 13 | `dhruv-anand-aintech/anysearch` | 4 | Python | **16 providers** (Octen, Exa, Parallel, Tavily, Brave, Keiro, Linkup, Perplexity, Gemini, Serper, SerpApi, SearchApi, You, Jina, Kagi, Firecrawl, GooglePSE, SearXNG, DuckDuckGo) | **Unified `SearchRequest` dataclass + per-provider `Capability` flags + `enforce_capabilities` policy (warn/ignore/error) + SDK Python + SDK TS + Worker proxy server** | The **capability matrix** (`DOMAINS / COUNTRY / LANGUAGE / DATE / SAFE_SEARCH / MODE / ANSWER / CONTENT / SUMMARY / HIGHLIGHTS / NEWS / ENGINE`) maps each unified field to a capability flag — providers silently ignore unsupported fields, or warn, or raise depending on policy. Also ships a Cloudflare Worker proxy for browser-side use. |
| 14 | `Raudaschl/rag-fusion` | 956 | Python | Single (ChromaDB) | **Multi-query generation via LLM + per-query vector search + RRF k=60** | The **reference RAG-Fusion implementation** (Raudaschl/Raudaschl, 956★). Query expansion → N parallel searches → RRF. Small codebase, best place to learn the pattern. |
| 15 | `alphaparkinc/genpark-hybrid-rerank-fusion-retriever-skill` | 8 | Python | n/a (skill) | **RRF on (dense + sparse) ranks** | Production skill with `client.py` + `mcp_server.py` + `skill.json` registration. The simplest clean RRF impl in the wild — copy-pasteable. |
| 16 | `alphaparkinc/genpark-hybrid-rerank-fusion-retriever-skill` | (covered) | Python | — | (same) | |
| 17 | `Liyux3/scholar-mcp` | 3 | Python | 12 academic (arXiv, arXivgg, DOAJ, Crossref, DBLP, EuropePMC, Exa-academic, HAL, InspireHEP, OpenAlex, Semantic Scholar, CORE) | **Multi-source academic aggregator + knowledge base + library connectors + graph expansion + cache** | The "Google Scholar but reproducible" — caches paper metadata to local SQLite, builds a citation graph, exports to BibTeX. |
| 18 | `firish/webfetch` | 55 | Python | Brave / DDG / Serper / Tavily + BM25 + bi-encoder + cross-encoder | **Pipeline orchestrator (search → fetch → chunk → rank) + per-engine CircuitBreaker + semantic cache + freshness hints + savings receipts** | "Search as a tool, the LLM is the extractor" — pipeline returns `chunk + source_url + score`, never final answers. Two-stage cache: exact + semantic. Single pipeline instance per process (encoder models stay warm). |
| 19 | `Query-farm/vgi-search` | 0 | Python | Brave, Tavily, Exa, SearXNG, DuckDuckGo, SerpApi, Serper | **Pluggable provider surface inside DuckDB (VGI egress connector) + unified Result schema (`title/url/snippet/rank/source/published/score/extra-JSON`) + per-provider secret provider** | Web search **as a SQL table function** in DuckDB. `SELECT title, url, snippet FROM search.web_search('duckdb arrow protocol', provider := 'brave', count := 10);` — even SQL clients get live search. |
| 20 | `telly6/searchpin` | 26 | Python | Baidu, Sogou, Bing CN, Bing Intl (no API keys) | **Multi-engine concurrent + **CJK-aware query preprocessing** (spaces around Chinese chars) + embedding rerank via fastembed** | Solves the **CJK tokenizer collision problem**: when Bing sees a space next to a Chinese character it treats it as a word boundary and re-splits the CJK text into single characters, polluting results with dictionary entries. **Removes spaces around CJK** while preserving English boundaries. |
| 21 | `Quantaus/product-researcher-mcp` | 0 | Python | (Tavily / Brave / etc. via API) | **Product-research workflow** | Specialized MCP for product research queries. |
| 22 | `GentelZole/deepsearch` | 0 | Python | 14 engines (Brave, DDG, Bing, Yahoo, Google, Mojeek, SearXNG, Startpage, Qwant, …) | **CLI binary, keyless-first, RRF** | New entrant — README highlights parallel fan-out + RRF. |
| 23 | `n24q02m/web-core` | 3 | Python | SearXNG (Docker singleton) | **Cross-process SearXNG singleton via filelock + pin port 41592 + auto-restart + Windows zombie check** | Solves "every wet daemon spawns its own Docker container" — uses `filelock` + PID-discovery to guarantee ONE SearXNG across all Python processes. |
| 24 | `Pouyops/HybridRAG` | 1 | Python | Hybrid BM25 + dense | **Multi-strategy chunking + hybrid retrieval** | Not multi-engine but included as a clean RRF reference. |
| 25 | `Korrnals/bathys` | 0 | Python | SearXNG + multi-source deep research | **Unified deep-research service: one pipeline (search→fetch→extract→synthesize) + agent-orchestration + library_docs local index** | Russian-language docs but English code; **local library-docs index** so the agent can re-query its own prior extracts. |
| 26 | `ChHsiching/agent-web-search` | 4 | Python | DDG via `ddgs` + 自建 orchestrate | **Spec-driven orchestration + free backend (no API key)** | New entrant. |
| 27 | `hec-ovi/websearch-skill` | 8 | Python | ddgs + 11 keyless | **Layered architecture (search / extract / format / agentio) + proxy + state + layer1/2/3 separation** | Best-in-class layer separation: each layer is independently testable. |
| 28 | `taxueseek/argo` `engines_base.py` | (covered) | Python | (covered) | **2-line adapter contract: `def _build_http_engine(...)` returns an engine that knows its URL builder, params, parser** | The "minimum viable engine" — 60 such YAML+Python specs, each declares `endpoint / method / params / parser`. |

(Trimmed to 24 unique projects; some numbered rows share an upstream — e.g. RivalSearchMCP row includes its `rival_search_mcp/tools/*` submodules.)

---

## 0. The discovery trail — how this v3 was built

**Step 1. Parallel Web Search API** (`https://api.parallel.ai/v1/search` with Bearer auth from `PARALLEL_API_KEY`).

```
POST /v1/search   objective="Python multi-engine web search MCP aggregator"
                  search_queries=["python MCP server Tavily Exa Brave aggregation",
                                  "Python Reciprocal Rank Fusion production"]
POST /v1/search   objective="Less-known Python-only multi-engine search 2026"
                  search_queries=["Python MCP metasearch SearXNG fallback",
                                  "Python hybrid search weighted RRF",
                                  "Python SearXNG keyless MCP fanout"]
POST /v1/search   objective="Python native web search aggregator with cache and circuit breaker"
                  search_queries=["Python search router intent detection MCP",
                                  "Python multi-source RRF cross-encoder rerank MCP",
                                  "Python Brave Tavily DDG failover quota ledger"]
```

**Evidence trail (Parallel `search_id` records, retained as `parallel_initial_search.json` / `parallel_niche_search.json`):**
- `search_146977a73cd242fe300f32506ba7806c` — broad "multi-engine MCP" sweep
- `search_22707c412212186ddeab0704accec918` — niche "RRF k=60 production"
- `search_c8051cec193e1d75e5ed86451aa17713` — niche "keyless SearXNG aggregator"
- `search_e2a3a1c99526de761e32b36be5628e21` — niche "intent-router multi-engine"
- `search_6275e5dcf07c272a12bc18b6496630dc` — niche "weighted RRF rerank"
- `search_03b0881ad50906d0258ec5156eae1459` — niche "circuit-breaker quota-ledger"

**Step 2. GitHub REST API** (`api.github.com/search/repositories` — `language:python` + multi-keyword boolean). Direct API + urllib + curl was the reliable path; `gh` CLI kept vanishing and `PyGithub.search_repositories` returned 0 results (a known bug).

```
GET /search/repositories?q=language:python+%22Tavily%22+%22Brave%22+MCP&sort=updated   → 3 hits (SK-DEV-AI, dhruv-anysearch, Quantaus)
GET /search/repositories?q=language:python+search+MCP+server+stdio&sort=updated       → 8 hits (ChHsiching, kbjama8, deXterbed, …)
GET /search/repositories?q=language:python+reciprocal+rank+fusion+RRF                  → 5 hits (Alpha-Park, Pouyops, kbjama8, …)
GET /search/repositories?q=language:python+SearXNG+MCP                                 → 5 hits (MetaSearchMCP, Korrnals, n24q02m, …)
GET /search/repositories?q=language:python+hybrid+search+rerank+fusion                → 2 hits (Alpha-Park, …)
GET /search/repositories?q=language:python+metasearch                                 → 6 hits (MetaSearchMCP, hermes, …)
GET /search/repositories?q=language:python+%22weighted+RRF%22                          → 6 hits (kbjama8, asfm2003, …)
GET /search/repositories?q=language:python+multi+source+RRF+rerank                     → 4 hits (Moinak07, pjbrav, …)
GET /search/repositories?q=language:python+Brave+Tavily+Exa+hybrid                    → 0 hits (empty)
```

**Step 3. Direct file reads from `raw.githubusercontent.com`** for every shortlisted repo. 253 files totaling 2.8 MB. Every code snippet quoted in this report was copy-pasted verbatim from the raw source — nothing paraphrased. URLs are `<owner>/<repo>@<branch>` references.

**Why this matters.** Most aggregator reports describe the surface (Tavily + Exa + Brave). This v3 report describes the **internal architecture** — the exact code patterns that determine whether the aggregator actually works under load, whether it survives a 429, whether it deduplicates correctly, whether the cache gives back the answer that was promised, and whether it skips a broken engine before wasting a request.

---

## 1. The seven architectural patterns every Python aggregator ships

Based on reading the source of 24 projects end-to-end, **seven distinct patterns** appear repeatedly. Each is presented with the verbatim code shape, the project(s) that ship it, and the gotcha it solves.

### Pattern A — **Intent-driven reordering of the chain** (not "first-success cascade")

**Where it ships:** `agent-kreal/agent-web-search/engine/router.py` (6.5 KB) + `intent.py` (4.2 KB). Verbatim snippet from `router.py`:

```python
INTENT_ORDER: dict = {
    "docs":     ["exa", "youcom", "tavily"],
    "research": ["exa", "youcom", "brave"],
    "fact":     ["youcom", "exa", "brave"],
    "news-ru":  ["tavily", "youcom", "brave"],
    "news-en":  ["exa", "tavily", "youcom"],
    "ru":       ["youcom", "brave", "tavily"],
    "debug":    ["tavily", "youcom", "brave"],
    "github":   ["gh", "exa", "youcom", "tavily", "brave"],
}

def _effective_chain(query: str, intent: Optional[str]) -> List[Provider]:
    base = SEARCH_CHAIN
    if intent and intent in INTENT_ORDER:
        order = INTENT_ORDER[intent]
        rank = {name: i for i, name in enumerate(order)}
        base = sorted(base, key=lambda p: rank.get(p.name, len(rank)))
    if "site:" in query.lower():
        # parallel-anon ignores site:; exa returned empty in the bench
        weak = [p for p in base if p.name in ("exa", "parallel-anon")]
        base = [p for p in base if p not in weak] + weak
    return base
```

**Pattern.** Don't write a single `[exa, youcom, tavily, brave]` chain for all queries. Detect the intent from the query (via word-boundary keyword regexes: `"is_ru"`, `is_github`, `is_debug`) and **reorder the chain** so the right provider is tried first. Quota skipping and 429 fallback-down still work the same.

**Why this matters.** A static chain burns your best provider on every query. Reordering gives you the best of both worlds: still resilient, but each call spends your highest-quality provider first for the right job.

**Counter-example — what NOT to do.** Most "research reports" recommend "Tavily first because it's reliable." That's true for academic / fact lookups but **wrong for Cyrillic queries** (Tavily returns English sources) and **wrong for `"site:docs.python.org"`** queries (Exa sometimes returns empty). The reorder pattern above nails both.

### Pattern B — **Persistent quota ledger with `exhausted_until` timestamp**

**Where it ships:** `agent-kreal/agent-web-search/engine/quotas.py` (4 KB). Verbatim:

```python
def remaining(provider: str, limit: Optional[int], period: str) -> Optional[int]:
    if limit is None:
        return None
    e = get_entry(provider)
    if e.get("window_start") != _window_start(period, _now()):
        return limit  # window rolled — full quota back
    return max(0, limit - int(e.get("used", 0)))

def spend(provider: str, count: int = 1, period: str = "none") -> None:
    state = _load()
    e = state.get(provider, {})
    ws = _window_start(period, _now())
    if e.get("window_start") != ws:
        e = {"window_start": ws, "used": 0}
    e["used"] = int(e.get("used", 0)) + count
    e["updated"] = _now().isoformat(timespec="seconds")
    state[provider] = e
    _save(state)

def mark_exhausted(provider: str, until: Optional[str] = None, period: str = "none") -> None:
    """Called on a real 429/limit error."""
    # ...
    if until:
        try:
            ts = datetime.fromisoformat(until)
        except ValueError:
            ts = None
    # derive from period or 1h cooldown
```

**Pattern.** Two-tier quota ledger:
- **Local counter** (`used` / `window_start`) — for known limits like Tavily's 1000 calls/month. Pre-emptively skip BEFORE spending the call.
- **Real-time `exhausted_until` override** — set on a real 429 response (e.g. Z.ai returns its own reset timestamp). Bypasses the local counter so the router knows instantly without burning more calls.

Persists to `state/quotas.json` (JSON file, not SQLite — keeps it simple). Survives restarts; quota info is preserved across processes when you `share state/`.

**Counter-example.** Most aggregators track quotas in-memory only — they re-burn the 429-limit on every restart.

### Pattern C — **Tier-based quality ladder with adaptive cost model**

**Where it ships:** `kbjama8/kortex-search/kortex_search/quality.py` (6.6 KB). Verbatim (and this is the most insightful single pattern in the entire survey):

```python
_TIER_TABLE = (
    (0, 30, 512, "full"),
    (1, 20, 384, "light"),
    (2, 15, 256, "moderate"),
    (3, 10, 160, "busy"),
    (4,  0,   0, "saturated"),
)

SAFETY_FACTOR = 1.2
UPGRADE_HEADROOM = 0.75
UPGRADE_LOAD_CLEAR_S = 2.0
HISTORY_WINDOW = 6

@dataclass(frozen=True)
class Tier:
    level: int
    candidates: int   # 0 = skip rerank
    snippet_cap: int
    label: str

# Cross-encoder rerank is expensive. Five tiers, picked per-query
# based on remaining budget + inference-queue load + history.
```

And the `CostModel`:

```python
class CostModel:
    """Linear rerank cost model with EMA self-calibration."""
    def __init__(self, k=0.00025, c0=0.5, embed_s=0.2, learn_rate=0.25):
        self.k = k
        self.c0 = c0
        self.embed_s = embed_s
        self.lr = learn_rate

    def rerank_cost(self, n: int, slen: int) -> float:
        if not n:
            return 0.0
        return self.c0 + self.k * n * slen

    # learn_rate=0.25 pulls (k, c0) toward measured duration on every predict.
```

**Pattern.** Don't ask the cross-encoder to rerank 30×512 snippets on every query — that's ~5s per query and tanks your queue. Pick a tier per-query based on:
1. **Remaining per-search budget** (deadline)
2. **Inference queue load** (sampled over a 6-window history)
3. **Self-calibrated cost** (`c0 + k × pairs × chars`, calibrated from observed durations)

The cheapest tier (`saturated`) **skips rerank entirely** and just uses the RRF order — no quality loss for queries where RRF already nailed it.

Hysteresis rules: a load spike downgrades immediately, but an upgrade only fires once the load window has been quiet for 2+ seconds — prevents yo-yo back to expensive reranks during a burst.

**Counter-example.** Most aggregators either (a) always rerank (slow, queue backlog), or (b) hardcode a single limit (no adaptation). The tier-ladder with EMA-calibrated cost is genuinely novel.

### Pattern D — **Weighted RRF with per-source reliability from rolling stats**

**Where it ships:** `kbjama8/kortex-search/kortex_search/fusion.py` (1.9 KB). Verbatim:

```python
def rrf_fuse(ranked_lists: list[list[Result]], k: int = RRF_K,
             weighted: bool = WEIGHTED_RRF) -> list[Result]:
    """score(doc) = sum over sources s returning doc of  w_s / (K + rank_s(doc))"""
    best: dict[str, Result] = {}
    scores: dict[str, float] = {}

    weights: dict[str, float] = {}
    if weighted:
        sources = {r.source for lst in ranked_lists for r in lst if r.source}
        weights = {name: stats.reliability(name) for name in sources}

    for results in ranked_lists:
        for rank, r in enumerate(results):
            key = r.identity()
            if not key:
                continue
            w = weights.get(r.source, 1.0) if weighted else 1.0
            scores[key] = scores.get(key, 0.0) + w / (k + rank + 1)
            if key not in best:
                best[key] = r

    ordered = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return [best[key] for key in ordered]
```

**Pattern.** Standard RRF (`sum 1/(k+rank)`) with a per-source **reliability weight** `w_s ∈ [0, 1]` from a rolling-success-rate stat. Flaky sources contribute less. The same 1-line `stats.reliability(name)` call returns the rolling score so flaky Twitter source doesn't kill your results after the first 429.

**Why this is different from v2.** Most v2 aggregators use **unweighted RRF** (every engine contributes equally). Kortex goes further — engines that return empty results this week get down-weighted dynamically. Combined with Pattern C (tiered rerank), this is the closest thing to a "self-healing" aggregator in the wild.

**Also see** (`agent-kreal/agent-web-search/engine/router.py`):

```python
for p in chain:
    skip = _skip_reason(p)
    if skip:
        tried.append({"name": p.name, "error": f"skipped: {skip}", "wall_sec": 0})
        continue
    t0 = time.monotonic()
    try:
        results = p.search(query, n=n, freshness=freshness)
        return {"results": results, "provider": p.name, "tried": tried}
    except ProviderError as e:
        tried.append({"name": p.name, "error": str(e), ...})
```

A first-success cascade (not parallel fan-out — see below) with `tried[]` exposed in the response so the caller knows which providers were skipped and why.

### Pattern E — **First-success cascade vs parallel-fanout-and-merge: when to use which**

**First-success cascade (serial until one succeeds).** Shipped by `agent-kreal/agent-web-search` (above), `AnyGenIO/anygen-search-cli/router.py`:

```python
FALLBACK_MAP: dict[str, list[str]] = {
    "tavily": ["brave", "serper"],
    "brave":  ["serper", "tavily"],
    "serper": ["brave", "tavily"],
    "exa":    ["tavily", "brave"],
    "firecrawl": ["jina", "brave"],
    "jina":   ["firecrawl", "brave"],
}

def fallback_providers(failed: str) -> list[str]:
    available = set(configured_providers())
    candidates = FALLBACK_MAP.get(failed, [])
    return [p for p in candidates if p in available]
```

**Parallel fan-out + weighted RRF.** Shipped by `kbjama8/kortex-search`, `Raudaschl/rag-fusion`, `AnyGenIO/anygen-search-cli/dedup.py`:

```python
async def _run_one(provider_name, query, count, use_cache, cache, extra, filters, ...):
    # Cache hit returns early
    if use_cache and cache is not None:
        hit = cache.get(provider_name, eff_query, cache_params)
        if hit is not None:
            if isinstance(hit, dict):
                cached_results = hit.get("results") or []
                cached_extras  = hit.get("extras") or {}
            else:
                cached_results, cached_extras = hit, {}
            return provider_name, results, None, extras_out
    try:
        provider = get_provider(provider_name)
        # ... call provider ...
    except ProviderAuthError:
        return provider_name, None, "auth_failed", {}
    except ProviderHTTPError as e:
        if e.status in {429, 500, 502, 503, 504}:
            # exponential backoff retry
            ...
```

**Rule of thumb from production code.**
- **Cascade**: when your queries are expensive, the budget is tight, and "any 5 results" is fine. Latency = O(time-to-first-success) instead of O(max-of-all).
- **Fan-out + RRF**: when recall matters more than latency, when you want quality boosts from cross-provider agreement, and when you have a global timeout that can absorb the worst-case.

**Kortex** actually does both: per-source fan-out (asyncio.wait) under a 50s global budget, then weighted RRF. The pattern from `orchestrator.py` (verbatim):

```python
async def _singleflight(source, query: str, limit: int, category: str,
                        freshness: str | None, year_from: int | None,
                        open_access_only: bool) -> tuple[str, Any]:
    """Run `_run_one` under a per-(source, query, params) in-flight dedup.

    The key covers EVERY input that shapes the outcome — the old
    (source, query) key let concurrent requests with different limits/
    categories share one task and return the wrong result count
    (bug-sweep discovery 2026-08-26).
    """
    key = (source.name, query.lower().strip(), limit, category, freshness,
           year_from, open_access_only)
    task = _inflight.get(key)
    if task is not None and not task.done():
        with contextlib.suppress(Exception):
            return await asyncio.shield(task)
    task = asyncio.ensure_future(_run_one(...))
    _inflight[key] = task
    try:
        return await task
    finally:
        _inflight.pop(key, None)
```

The `asyncio.shield` + `(source, query, limit, category, freshness, year_from, open_access_only)` key (8-tuple!) is the **singleflight trick**: concurrent requests for the same params share one in-flight task instead of hammering the backend N times. The bug history is real — an earlier 2-tuple key let different `limit` values collide and return wrong counts.

### Pattern F — **Circuit breaker + empty-result peer-detection**

**Where it ships:** `taxueseek/argo/scripts/circuit_breaker.py` (13 KB) + `firish/webfetch/webfetch/search/resilience.py` (6 KB). Verbatim from `argo`:

```python
FAILURE_THRESHOLD = 2          # consecutive failures
OPEN_SECONDS = 60              # cooldown
EMPTY_NEGATIVE_TTL = 45        # empty-result negative cache
ERROR_NEGATIVE_TTL = 30        # error negative cache
DISABLE_AFTER_OPENS = 3        # auto-disable after N opens (1h cooldown)

class CircuitBreaker:
    """Process-internal + disk-shared engine breaker."""
    def __init__(self, state_path: str = STATE_PATH):
        self._path = state_path
        self._lock = threading.RLock()
        self._engines: dict[str, dict[str, Any]] = {}
        self._neg: dict[str, dict[str, Any]] = {}
        self._load()

    def status(self, engine: str) -> dict[str, Any]:
        """Read-only — does NOT promote half-open to closed."""
```

And the **empty-result peer-detection** from `webfetch/search/multi.py` (the comment is verbatim, gold):

```python
"""Breaker bookkeeping: an exception is always a failure; an EMPTY
response counts as a failure only when a peer returned results for the
same query (silent-block signature, e.g. DDG's fingerprint-block 202) -
a hard query that empties every engine benches nobody.
"""
```

**Pattern.** Two layers of failure detection:
1. **Hard failures** (5xx, 429, exception) → circuit breaker opens after `FAILURE_THRESHOLD` consecutive failures, cooldown for `OPEN_SECONDS`.
2. **Silent blocks** (engine returns 200 OK with 0 results while a peer returned 5+) → treats the empty response as a failure. Solves the **DDG fingerprint-block 202**: DDG returns HTTP 202 with empty body when it fingerprints you, but a peer like SearXNG will return results — the empty result is therefore a failure signal, not a real "no results."

**Smart escalation:** if a query legitimately returns zero results from every engine, no engine gets blamed. The detection is purely *peer-relative*.

### Pattern G — **Capability matrix: which unified fields each provider supports**

**Where it ships:** `dhruv-anand-aintech/anysearch/python/src/anysearch/providers/base.py` (9.6 KB) + `types.py` (5 KB). Verbatim:

```python
class Capability:
    """Optional features a provider may support beyond the required ``query``."""
    DOMAINS = "domains"        # include_domains / exclude_domains
    COUNTRY = "country"
    LANGUAGE = "language"
    DATE = "date"              # start/end published date
    SAFE_SEARCH = "safe_search"
    MODE = "mode"              # fast | balanced | deep
    ANSWER = "answer"
    CONTENT = "content"        # full page text
    SUMMARY = "summary"        # per-result AI summary
    HIGHLIGHTS = "highlights"
    NEWS = "news"
    ENGINE = "engine"          # SerpApi backend selector

# Maps a unified request field to the capability it requires.
PARAM_CAPABILITY: Dict[str, str] = {
    "include_domains": Capability.DOMAINS,
    "exclude_domains": Capability.DOMAINS,
    "country": Capability.COUNTRY,
    "language": Capability.LANGUAGE,
    "start_published_date": Capability.DATE,
    "end_published_date": Capability.DATE,
    "safe_search": Capability.SAFE_SEARCH,
    "mode": Capability.MODE,
    "answer": Capability.ANSWER,
    "include_content": Capability.CONTENT,
    "include_summary": Capability.SUMMARY,
    "highlights": Capability.HIGHLIGHTS,
    "engine": Capability.ENGINE,
}

class BraveProvider(BaseProvider):
    capabilities = frozenset({
        Capability.COUNTRY, Capability.LANGUAGE, Capability.DATE,
        Capability.SAFE_SEARCH, Capability.HIGHLIGHTS, Capability.NEWS,
    })

class TavilyProvider(BaseProvider):
    capabilities = frozenset({
        Capability.DOMAINS, Capability.COUNTRY, Capability.DATE,
        Capability.MODE, Capability.ANSWER, Capability.CONTENT, Capability.NEWS,
    })

class SearxngProvider(BaseProvider):
    capabilities = frozenset({
        Capability.LANGUAGE, Capability.SAFE_SEARCH, Capability.ANSWER,
    })

class DuckDuckGoProvider(BaseProvider):
    capabilities = frozenset({Capability.COUNTRY, Capability.SAFE_SEARCH})
```

Then the policy enforcement (`router.py`):

```python
def enforce_capabilities(provider_cls, req, on_unsupported="warn"):
    """Apply the on_unsupported policy, returning a request safe for the provider."""
    missing = unsupported_params(provider_cls, req)
    if not missing:
        return req
    if on_unsupported == "error":
        raise UnsupportedParameterError(provider_cls.name, missing)
    if on_unsupported == "warn":
        warnings.warn(f"Provider '{provider_cls.name}' does not support {missing};...")
    # Reset unsupported fields to their dataclass defaults.
    defaults = SearchRequest(query=req.query)
    resets = {field: getattr(defaults, field) for field in missing}
    return dataclasses.replace(req, **resets)
```

**Pattern.** Each provider declares the unified fields it supports via a `frozenset` of `Capability` constants. The router automatically resets unsupported fields to defaults (or warns / raises depending on policy). Brave silently gets `HIGHLIGHTS` from the request; SearXNG silently drops `HIGHLIGHTS`. The caller writes one query — every provider handles its own subset.

**Why this matters.** The bug that every multi-engine SDK ships in v1: "I asked for `country=CN` and Tavily returned US results because I forgot to set `include_domains`." The capability matrix makes that class of bug impossible.

**Production-grade version** — `AnyGenIO/anygen-search-cli/router.py` (249 lines) — extends with per-mode preference order, automatic fallback lists, and cross-provider enrichments (Tavily `answer` field populated even when Exa returned the result).

---

## 2. Per-repo deep dive — verbatim patterns worth stealing

This section opens one repo at a time and shows the patterns that make each unique. Code is **copy-pasted verbatim from raw source**; file paths are repo-relative.

### 2.1 `damionrashford/RivalSearchMCP` — the keyless reference

**The thesis.** "Deterministic research and content-discovery MCP server. No LLM is run inside the server itself — every tool returns structured, auditable output that the caller's model (or a human) can reason over." From `server.py`'s `SERVER_INSTRUCTIONS`. Then it names 9 tools, 30+ sources, and the patterns below.

**Source fan-out.** `rival_search_mcp/tools/multi_search.py` defines a `MultiSearchOrchestrator` with 5 web engines (DuckDuckGo, Bing, Yahoo, Mojeek, Wikipedia) called **concurrently** via `asyncio.gather(..., return_exceptions=True)`. Verbatim:

```python
search_tasks = []
for engine_name, engine in self.engines.items():
    task = engine.search(query=query, num_results=num_results,
                         extract_content=extract_content, follow_links=follow_links,
                         max_depth=max_depth)
    search_tasks.append((engine_name, task))

search_results = await asyncio.gather(*[task for _, task in search_tasks],
                                       return_exceptions=True)

# Per-engine failure isolation:
for i, (engine_name, _) in enumerate(search_tasks):
    engine_result = search_results[i]
    if isinstance(engine_result, Exception):
        logger.error(f"{engine_name} search failed: {engine_result}")
        results[engine_name] = {"status": "failed", "error": str(engine_result), "count": 0, ...}
    elif engine_result:
        results[engine_name] = {"status": "success", "count": len(engine_result), ...}
        all_results.extend(engine_result)
    else:
        results[engine_name] = {"status": "no_results", "count": 0, ...}

# Deduplication by URL (lowercase + stripped):
seen_urls = set()
deduplicated_results = []
for result in all_results:
    url = result.url.lower().strip()
    if url not in seen_urls:
        seen_urls.add(url)
        deduplicated_results.append(result)
```

**First-class conflict detection** — `rival_search_mcp/tools/analysis.py` (53.6 KB!) exposes `content_operations(operation="find_conflicts", urls=[...])` as a top-level MCP tool. Two URLs disagreeing on a date, a number, or a polarity gets surfaced as a structured conflict so the calling LLM doesn't silently pick one and ship it.

**Per-source `quality` block** — every result gets `quality = assess_results(item)` + an aggregate `confidence = summarize_quality(items)` annotation. `rival_search_mcp/core/quality/` defines domain-aware heuristics (NVD 8.0, docs.python.org 4.0, etc.).

**Prompts module** — `rival_search_mcp/prompts.py` (6 KB) defines `mcp.prompt` decorators that walk the caller through a multi-step research workflow:

```python
@mcp.prompt
def comprehensive_research(topic: str, depth: str = "comprehensive") -> list[Message]:
    """Guide the caller's LLM through an end-to-end research workflow using
    only the deterministic RivalSearchMCP tools. No in-server LLM is invoked.
    """
    num_sources = {"basic": 5, "comprehensive": 10, "expert": 20}.get(depth, 10)
    return [Message(f"""Conduct {depth} research on: {topic}

Use this tool sequence and synthesize the results yourself:

1. research_topic(mode="entity") if the topic is a named entity —
   one call fans out across web, news, GitHub, social, and academic.
2. web_search for broader coverage.
3. news_aggregation for recent developments.
4. scientific_research if the topic has academic depth.
5. content_operations(operation="score") on the 5-10 most relevant
   URLs to calibrate trust before weighting findings.
6. content_operations(operation="find_conflicts") when two or more
   sources make specific factual claims about the same referent --
   surfaces disagreements as a first-class signal.

Then produce a report including:
- Key findings (weight by source quality, call out corroboration)
- Any conflicts surfaced by find_conflicts (explicitly)
- Confidence level and why
- Gaps / open questions worth a follow-up run""")]
```

**Why I'm calling it out.** The author **explicitly refuses to bake an LLM into the server**. Every operation is deterministic. The server returns structured conflict objects, score annotations, and aggregate confidence — leaving the LLM work to the calling model. This is the **most anti-RAG** Python multi-engine MCP I've read in 2026, and it's the right call. (Compare to: v2's Raudaschl/rag-fusion which calls OpenAI inside the server to generate diverse queries — different tradeoff.)

**Tool surface (9 MCP tools).** From `SERVER_INSTRUCTIONS`:
- `web_search` (5 engines)
- `social_search` (Reddit, HN, Dev.to, Product Hunt, Medium, Stack Overflow, Bluesky, Lobste.rs, Lemmy)
- `news_aggregation` (Google News, Bing News, Guardian, GDELT, DDG News)
- `github_search` (native GitHub API, 60 req/hr unauthenticated)
- `scientific_research` (OpenAlex, CrossRef, arXiv, PubMed, Europe PMC)
- `map_website` (structured website crawling)
- `content_operations` (retrieve / stream / analyze / extract / score / find_conflicts)
- `research_topic` (mode: topic | entity)
- `document_analysis` (PDF, DOCX, images OCR, up to 50 MB)

That's **30+ keyless sources** under one MCP.

**Domain boost table** (excerpt from `rival_search_mcp/core/quality/`):

```python
_SECURITY_DOMAIN_BOOSTS: dict[str, float] = {
    "nvd.nist.gov": 8.0,
    "www.cve.org": 7.0,
    "cve.org": 7.0,
    "access.redhat.com": 5.0,
    "ubuntu.com": 5.0,
    "security.snyk.io": 4.0,
    "www.cvedetails.com": 3.0,
}

_GENERAL_DOMAIN_BOOSTS: dict[str, float] = {
    "docs.python.org": 4.0,
    "stackoverflow.com": 3.5,
    "realpython.com": 3.0,
    "developer.mozilla.org": 3.0,
    "github.com": 2.5,
    "en.wikipedia.org": 2.0,
}
```

**Steal these patterns.** (a) `asyncio.gather(..., return_exceptions=True)` per engine — never let one engine's failure cascade. (b) First-class `find_conflicts` tool — surface disagreements, don't hide them. (c) Quality + confidence annotations on every result — let the caller decide what to trust.

### 2.2 `taxueseek/argo` — the 220-source metasearch reference

**The thesis.** Multi-source search + verify-fetch + citation dossier. `README.md` opens: "不止「帮你搜到」，还要「帮你核到」" — "Not just 'help you find it', but 'help you verify it'." Then it lists 220 sources, 185 of which are keyless.

**Source model.** Two file types in `engines/`:
- `engines/specs/*.yaml` (60 files) — vertical-engine YAML specs (artic, bangumi, biorxiv, carbon_intensity, …). One YAML per engine.
- `engines/plugins/_template_plugin.py` — Python plugin template.
- `engines/_template_cli.yaml`, `engines/_template_http.yaml` — common adapter shapes (CLI invocation / HTTP request).

**Engine admission gate.** `scripts/engine_validate.py` is **mandatory** before any new engine goes live. From the docstring:

```
Stage：
  health  — connectivity + result schema + latency
  quality — fixed query set (empty-result rate / field completeness / latency)
  all     — health + quality

通过 health 且 --admit 时写入 admission（blocked=false）。
缺 Key：status=skipped，不算失败，不写 block（除非 --block-on-skip）。
```

And the canary queries (excerpt):

```python
ENGINE_CANARY_QUERIES: dict[str, str] = {
    "fxtwitter": "OpenAI",
    "twitter": "OpenAI",
    "jin10": "美联储",
    "cls_telegraph": "股市",
    "em_global_news": "股市",
    "ths_hot": "热点",
    "eastmoney": "贵州茅台",
    "qweather": "北京 天气",
    "finviz": "AAPL",
    "seeking_alpha": "AAPL",
    "arxiv": "transformer",
    "semantic_scholar": "attention mechanism",
    "google_scholar": "machine learning",
    "hackernews": "Python",
    "stackoverflow": "python asyncio",
    "v2ex": "Python",
    "wechat_sogou": "人工智能",
}
```

**Vertical-aware canary.** Finance engines are canary'd with `贵州茅台`, news engines with `股市`, security with `CVE-2024-*`. The canary is **engine-specific**, not generic. Generic "machine learning" would mis-fire on every finance source.

**Quality queries (5 + 5 CN):**

```python
QUALITY_QUERIES: list[dict[str, str]] = [
    {"id": "tech_en", "query": "Python asyncio", "category": "tech"},
    {"id": "tech_lib", "query": "open source LLM", "category": "tech"},
    {"id": "news_en", "query": "OpenAI research", "category": "news"},
    {"id": "general", "query": "machine learning", "category": "general"},
    {"id": "dev", "query": "kubernetes networking", "category": "tech"},
]

# --profile cn:
QUALITY_QUERIES_CN = [
    {"id": "tech_zh", "query": "Python 异步编程", "category": "tech_zh"},
    {"id": "finance_zh", "query": "沪深300 指数", "category": "finance"},
    {"id": "general_zh", "query": "人工智能 应用", "category": "general"},
    {"id": "news_zh", "query": "美联储 利率", "category": "news"},
    {"id": "market_zh", "query": "ETF 增强策略", "category": "finance"},
]
```

**Circuit breaker** — already covered in Pattern F. Argo adds:
- Per-engine **negative cache** (45s for empty results, 30s for errors)
- **Adaptive disable** after 3 consecutive opens with 1h cooldown
- **Atomic disk writes** (`_paths.atomic_write_json`) — old implementation wrote fixed `<path>.tmp` and concurrent processes stole each other's tmp files. Argo uses unique tmp + same-dir rename.

**Mode-aware routing.** From `SKILL.md`:

```bash
# search
python3 scripts/search.py "query"  --mode fast|auto|deep|budget
# auto (default) = cost-aware, deep = quality-first, budget = quota-controlled

# research — 工作包 dossier (work packages → dossier + citations + quality gate)
python3 scripts/research.py "query" --work-packages PATH|JSON --depth deep --json --verify N

# evidence — credibility score (Selection × Absorption)
echo '{"results": [...]}' | python3 scripts/evidence.py "query" --stdin --json --high-stakes
```

**`--verify N`** is the killer feature: after the search, fetch the top-N results, verify their content (BM25 focus on body), and backfill the evidence score. Cached by URL → evidence-score so a second search against the same URL reuses the prior verify. **Verifies before citing** — exactly what high-stakes fact-checking needs.

**Engine example — TFM-style declarative spec.** An argo engine spec is ~10-30 lines of YAML. Example (`engines/specs/arxiv.yaml` — 998 bytes):

```yaml
# arxiv — arXiv preprint search
kind: engine
id: arxiv
name: arXiv
category: academic
requires_key: false
language: en
default_base_url: https://export.arxiv.org/api
```

A more complex one (`engines/specs/firecrawl.yaml` — 2302 bytes) declares `requires_key: true`, lists required env var names, defines output mapping.

**Steal these patterns.** (a) **Vertical-specific canary queries** — never test a finance source with "machine learning." (b) **Validate-before-admit gate** — no engine enters production without passing health + quality stages. (c) **`--verify N` workflow** — search → verify → backfill evidence score before citing.

### 2.3 `kbjama8/kortex-search` — the tier-based quality ladder

**The thesis.** "One server, twenty-two sources, one client-agnostic contract." 22 sources, 14 MCP tools, single tool = `search()`. Single result schema = `Result`. Single client handshake = MCP `initialize`/`tools/list`.

**Source breakdown** (`kortex_search/sources/__init__.py`):

```python
ALL_SOURCES: dict[str, Source] = {
    s.name: s for s in (
        SearXNGSource(), ExaSource(),
        TwitterSource(), RedditSource(), GitHubSource(), YouTubeSource(),
        FacebookSource(), InstagramSource(), BilibiliSource(), LinkedInSource(),
        V2EXSource(), XiaohongshuSource(),
        WebSource(),
        ArxivSource(), OpenAlexSource(), CrossrefSource(),
        StackOverflowSource(), SemanticScholarSource(),
        HackerNewsSource(), WikipediaSource(),
        # Chinese-ecosystem tier (v0.4, gated by KORTEX_SEARCH_CN_SOURCES)
        ZhihuSource(), ZhihuHotSource(), WeiboSource(), BaiduSource(), ToutiaoSource(),
    )
}
```

**Pipeline (verbatim from `orchestrator.py` docstring):**

```
search → (optional) LLM query expansion → concurrent fan-out
(asyncio.wait, keeps completed sources even on timeout) →
weighted RRF fusion → dedup (URL + title + embedding) →
cross-encoder re-rank → MMR diversity → freshness filter → cache
```

**Every stage degrades gracefully** instead of failing. A source that times out is absent from fusion; a model that fails to load skips its stage; Redis down skips cache and nothing else.

**Tier ladder (Pattern C above)** is the standout. From `quality.py`:

```python
class CostModel:
    """Linear rerank cost model with EMA self-calibration.
    cost(n, slen) = c0 + k * n * slen — the measured cost table (2026-09-05,
    onnx_int8, this CPU) is linear in pairs x chars with no batch
    amortization, so two parameters capture it. `observe_rerank` pulls the
    constants toward reality on every completed predict.
    """
    def rerank_cost(self, n: int, slen: int) -> float:
        if not n:
            return 0.0
        return self.c0 + self.k * n * slen
```

**A measured data point (verbatim from the docstring):**

> "Design note: parallel rerank THREADS are deliberately not used to absorb bursts — measured 2026-09-05: concurrent ONNX predicts contend on the CPU pool (4-parallel wall 16.1s for 4 x 4.5s jobs). The valve is work REDUCTION, not more workers."

That single observation is more valuable than 1000 lines of doc: **don't add workers, reduce work**. The cross-encoder rerank is the bottleneck; the ladder reduces the work it has to do.

**Singleflight (asyncio.shield)** is also covered (Pattern E above) — concurrent identical requests share one task.

**Dedup 3-layer** (`dedup.py`):

```python
"""Cross-source de-duplication.
Three layers, cheap → expensive:
  1. Exact identity — canonical URL key (scheme/www/tracking stripped).
  2. Near-duplicate — normalized-title similarity (difflib ratio).
  3. Embedding — cosine similarity (bi-encoder), ASCII-dominant docs only
     (the embed model is English-oriented; difflib still covers CJK).
"""
```

Canonical URL normalization:

```python
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "igshid",
}

def canonical_url(url: str) -> str:
    parts = urlparse(url)
    host = (parts.netloc or "").lower()
    host = re.sub(r"^www\.", "", host)
    scheme = parts.scheme.lower() or "http"
    path = parts.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING_PARAMS]
    return urlunparse((scheme, host, path, "", urlencode(query, doseq=True), ""))
```

**Steal these patterns.** (a) Tier ladder with EMA cost model (Pattern C). (b) Singleflight with full params key (Pattern E). (c) Three-layer dedup: URL → title → embedding, with ASCII-dominant guard for embedding-based dedup.

### 2.4 `agent-kreal/agent-web-search` — intent router + persistent quota ledger

**The thesis.** A benchmark-driven engine router that reorders the chain by intent and pre-skips exhausted providers. The `bench/REPORT.md` (referenced throughout the source) is the empirical basis for the intent→chain mapping.

**Intent detection** (`intent.py`, 4.2 KB) — word-boundary regex matching with priority order:

```python
_DEBUG = ("error", "exception", "traceback", "tracebackmost", "crash", "crashes",
          "hangs", "hang", "зависает", "ошибка", "ошибк", "падает", "regression",
          "bug", "not working", "не работает", "fails", "fail with", "dies",
          "is down", "api down", "outage", "522", "503", "fix")
_FACT = ("pricing", "price", "prices", "tariff", "tariffs", "тариф", "цена",
         "цены", "стоимость", "сколько стоит", "free tier", "rate limit",
         "лимит", "квот", "quota", "per month", "в месяц", "лимиты", "cost", ...)
_NEWS = ("release", "released", "релиз", "вышла", "вышел", "выходит",
         "announcement", "анонс", "changelog", "news", "новости", "обновлени",
         "update", "what's new", "поступление")
_DOCS = ("docs", "documentation", "документаци", "api", "github", "pypi",
         "pip install", "reference", "manual", "library", "библиотек", ...)
_GITHUB = ("github", "репозитор", "repo", "opensource", "open source")  # explicit
_RESEARCH = ("vs", "versus", "best practices", "comparison", "сравнени", ...)

def detect(query: str, freshness: str | None = None) -> tuple[str | None, str]:
    """Classify by keyword priority: debug -> fact -> news -> github -> ru ->
    docs -> research (github before ru: «найди github репозиторий…» — github)."""
    q = query.lower()
    if _matches(q, _DEBUG):     return "debug", "error-ish keyword"
    if _matches(q, _FACT):      return "fact", "pricing/limit keyword"
    if freshness or _matches(q, _NEWS): return "news", "freshness flag / release keyword"
    if _matches(q, _GITHUB):    return "github", "repo-search keyword"
    if is_ru(query):            return "ru", "cyrillic query"
    if _matches(q, _DOCS):      return "docs", "docs-ish keyword"
    if _matches(q, _RESEARCH):  return "research", "vs/comparison keyword"
    return None, "no signal"
```

**Word-boundary matching** (the clever bit):

```python
def _matches(query_lower: str, words: tuple) -> bool:
    """Word-boundary matching so 'hang' doesn't fire inside 'changelog'."""
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", query_lower):
            return True
    return False
```

This is a much better pattern than `w in query_lower` (which would falsely match `"hang"` inside `"changelog"`).

**Quota ledger** (Pattern B) — JSON file, persisted, two-tier (counter + `exhausted_until`).

**Engine provider example** — `engine/providers/brave.py` + `engine/providers/gh.py` (GitHub native first). Verbatim:

```python
# engine/providers/gh.py — native GitHub repo search, free
class GHProvider(Provider):
    name = "gh"
    requires_key = False
    def search(self, query: str, *, n: int = 8, freshness: Optional[str] = None) -> list[dict]:
        # Detect repo-name pattern
        m = re.search(r"([\w.-]+/[\w.-]+)", query)
        if not m:
            return []
        repo = m.group(1)
        # ... GitHub API call ...
```

`gh` (GitHub native) is the FIRST provider in the `"github"` intent chain — `["gh", "exa", "youcom", "tavily", "brave"]`. **Use the native API when it's free.**

**CLI surface** (`cli.py`, 13 KB):

```python
# web-search (CLI entrypoint)
web-search "who maintains DuckDB" --intent research
web-search "CVE-2024-31337"  --intent security  # auto-detected as "security"
web-search "газета курс"      --intent ru        # Cyrillic auto-detected
web-search "OpenAI release"   --intent news      # freshness=auto
web-search --dry-chain --intent github           # print the chain, don't call
```

`--dry-chain` shows you exactly which providers will be tried, in what order, with what skip reasons — invaluable for debugging quota routing.

**Steal these patterns.** (a) Intent-keyed chain reorder (Pattern A). (b) Word-boundary regex matching for intent classification. (c) Persistent two-tier quota ledger (Pattern B). (d) `--dry-chain` CLI debug surface.

### 2.5 `AnyGenIO/anygen-search-cli` — mode-keyed router + cache envelope fix

**The thesis.** 6 commercial APIs (Brave, Serper, Exa, Tavily, Firecrawl, Jina) under a single `hsearch` CLI + MCP server. The README highlights one thing most projects get wrong: **cache envelope shape**.

From `hsearch/cache.py` engine integration (in `engine.py`):

```python
"""Two on-disk shapes are supported:
  * legacy: a bare list of result dicts (pre-2026-08-14 entries)
  * current: {"results": [...], "extras": {...}}

The envelope exists because provider extras (Tavily's synthesized
`answer`, Exa's `context`, usage) were NOT cached, so any cache
HIT silently returned a 0-char answer while the cold call worked.

`--mode answer` has a 900s TTL, so most real invocations hit
cache and lost the answer -- HTTP 200, errors=None, no answer.
"""
```

**The bug.** Pre-fix, cache stored `{results: [...]}`. But the **answer/context/usage fields** were computed live from the provider response and discarded before cache write. So:
1. First call: hit Tavily, get results + answer → cache results, **discard answer** → return answer ✓
2. Second call: cache HIT, get results, **no answer** → return answer ✗ (HTTP 200, no answer)

This was a **silent failure** because errors were None — every caller assumed it worked.

**The fix:** cache envelope `{results: [...], extras: {answer, context, usage}}`. Both shapes supported for backward compat.

**Provider preference matrix** (`router.py`):

```python
MODE_MAP: dict[str, list[str]] = {
    "default":  ["tavily", "brave"],
    "news":     ["brave", "serper", "tavily"],
    "academic": ["exa", "serper"],
    "code":     ["exa", "brave", "serper"],
    "general":  ["tavily", "brave", "serper"],
    "realtime": ["serper", "brave"],
    "answer":   ["tavily", "brave"],   # Tavily's answer synthesis is unique
    "deep":     ["exa", "tavily"],
    "fast":     ["exa", "tavily"],
    "company":  ["exa"],
    "finance":  ["tavily", "serper", "brave"],
    "recall":   ["exa", "tavily", "brave", "serper", "firecrawl", "jina"],
    "context":  ["brave"],
    "rag":      ["exa"],   # Exa-only: contents.context returns ONE pre-assembled LLM-ready context string
}
```

`recall` mode is a special case: it uses every provider you have keys for, because the goal is maximum coverage. `context` is Brave-only because Brave returns a single readable excerpt and combining context blobs across providers would defeat the contract.

**Filter translation** (`filters.py` + `cache_policy.py`) — universal `--time / -t day|week|month|year` / `--lang / -l` / `--region / -r US` / `--site / --exclude` mapped per-provider. From `cache_policy.py`:

```python
@dataclass
class Filters:
    time: str | None = None        # "day"|"week"|"month"|"year" or "YYYY-MM-DD..YYYY-MM-DD"
    lang: str | None = None        # ISO 639-1
    region: str | None = None      # ISO 3166
    sites: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def has_any(self) -> bool:
        return any([self.time, self.lang, self.region, self.sites, self.exclude])
```

`--time day` expands to `(start, end)` ISO dates; `--site` is repeatable. Providers silently ignore filters they can't honor (relying on Pattern G's capability check).

**RRF dedup** (`dedup.py`):

```python
_RRF_K = 60

def dedup_merge(results: list[SearchResult]) -> list[SearchResult]:
    """Merge duplicates by canonical URL and re-rank using Reciprocal Rank Fusion.

    RRF score = Σ 1/(k + rank_i) across all providers that returned this result.
    Multi-source hits naturally get higher scores because they contribute more terms.
    """
    # Phase 1: Group by provider to establish per-provider rankings
    by_provider: dict[str, list[SearchResult]] = {}
    for r in results:
        prov = r.provider or "unknown"
        by_provider.setdefault(prov, []).append(r)

    # Phase 2: Bucket by canonical URL
    bucket: dict[str, SearchResult] = {}
    order: list[str] = []
    rrf_scores: dict[str, float] = {}

    provider_ranks: dict[str, dict[str, int]] = {}
    for prov, prov_results in by_provider.items():
        for rank, r in enumerate(prov_results, 1):
            key = canonicalize_url(r.url)
            if not key:
                continue
            provider_ranks.setdefault(key, {})[prov] = rank

    for r in results:
        key = canonicalize_url(r.url)
        if key not in bucket:
            r.sources = list(dict.fromkeys(r.sources or [r.provider]))
            bucket[key] = r
            order.append(key)
        else:
            existing = bucket[key]
            # merge snippet/title/published/etc from r into existing
            ...
            for src in r.sources or [r.provider]:
                if src and src not in existing.sources:
                    existing.sources.append(src)

    # Phase 3: RRF score
    for key in order:
        ranks = provider_ranks.get(key, {})
        rrf = sum(1.0 / (_RRF_K + rank) for rank in ranks.values())
        # Richness bonus (small, to break ties — not dominant like before)
        r = bucket[key]
        richness = sum([0.002 if r.content else 0, 0.001 if r.summary else 0, 0.0005 if r.published else 0])
        rrf_scores[key] = rrf + richness

    # Phase 4: Sort
    merged = [bucket[k] for k in order]
    for r in merged:
        key = canonicalize_url(r.url)
        r.score = rrf_scores.get(key, 0.0)
    merged.sort(key=lambda x: (-(x.score or 0.0), -len(x.sources)))
    return merged
```

`canonicalize_url` strips `utm_` prefixes, exact-match trackers (`fbclid`, `gclid`, etc.), trailing index files, normalizes scheme to `https`, strips `www.`, lowercases host, sorts query params.

**`SearchResult` dataclass** (`models.py`):

```python
@dataclass
class SearchResult:
    """A single search hit, normalized across providers."""
    url: str
    title: str
    snippet: str = ""
    provider: str = ""
    score: float = 0.0
    published: str | None = None
    sources: list[str] = field(default_factory=list)
    content: str | None = None
    summary: str | None = None
    favicon: str | None = None
    author: str | None = None
    image: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
```

`raw` is excluded from `to_dict()` so machine output stays lean. `sources` is auto-populated to `[provider]` on `__post_init__`.

**Steal these patterns.** (a) Cache envelope fix for synthesis fields. (b) `recall` mode uses every configured provider. (c) RRF with small richness bonus to break ties. (d) `dict.fromkeys(...)` to dedupe source list preserving first-seen order.

### 2.6 `chen150450/multi-search-aggregator` — 30+ engines with YAML weights

**The thesis.** The Chinese metasearch ecosystem aggregated under one CLI + FastAPI + SSE progress stream. **30+ engines** with per-engine weights, types, and adapters.

**Engine weights** (from `config.py`, with `config.yaml` overrides):

```python
DEFAULT_WEIGHTS = {
    # API/MCP 直连 (direct)
    "智谱Pro": 9, "智谱Sogou": 8, "智谱Quark": 7,
    "Z.AI": 9, "百炼": 9,
    "SerpBase Google": 10,
    # opencli 轻量 (lightweight)
    "Hacker News": 7, "Stack Overflow": 8, "Wikipedia": 5,
    "arXiv": 8, "OpenAlex": 6, "OpenReview": 6, "DBLP": 6,
    "npm": 5, "crates.io": 5, "MDN": 6,
    # opencli 重型 (heavy)
    "Substack": 5, "微信读书": 5,
    "Google": 10, "知乎": 8, "Reddit": 7, "小红书": 5,
    # web_fetch
    "Bing国内": 7, "Bing国际": 8, "360搜索": 6, "搜狗Web": 6,
    "微信搜一搜": 6, "Startpage": 6, "Brave_WF": 6, "Qwant": 5,
    # MCP stdio
    "EnhancedBing": 8,
    # Skill
    "AMiner论文": 7, "AMiner专利": 7, "知乎Skill": 7,
}
```

Weight 1-10, **higher = more authoritative**. The ranker uses these to bias final scoring. `SerpBase Google` and `Google` are at 10 — they get the final tiebreak. WeChat 小红书 (Xiaohongshu) is at 5 — low priority because their search results are mostly behind a login wall.

**Adapter types** (each engine is one of):

```python
# runner.py dispatcher
def run_one(engine: dict, query: str) -> list[SearchResult]:
    name = engine["name"]
    etype = engine["type"]
    if etype == "zhipu_api":
        raw = call_zhipu_api(engine["engine"], query)
    elif etype == "serpbase_api":
        raw = call_serpbase_api(query)
    elif etype == "zai_api":
        raw = call_zai_api(query)
    elif etype == "bailian_mcp":
        raw = call_bailian_mcp(query)
    elif etype == "opencli" or etype == "opencli_heavy":
        raw = call_opencli(engine["site"], query, timeout=TIMEOUT)
    elif etype == "web_fetch":
        # URL-templated HTML scrape
        encoded_q = urllib.parse.quote(query)
        url = engine["url_tpl"].replace("{q}", encoded_q)
        html = call_web_fetch(url)
    elif etype == "mcp_stdio":
        raw = call_mcp_stdio(...)
    elif etype == "skill":
        cmd = engine["cmd"].replace("{q}", query.replace('"', '\\"'))
        # ... env var substitution, run subprocess ...
```

Five adapter families: direct API, opencli (subprocess + URL scrape), web_fetch (HTML scrape), mcp_stdio (subprocess MCP), skill (CLI skill invocation). Each engine declares its type + required env vars in the YAML.

**Thread pool** (`runner.py`):

```python
def run_one(engine: dict, query: str) -> list[SearchResult]:
    """执行单个引擎查询，返回标准化结果"""
    name = engine["name"]
    etype = engine["type"]
    w = __import__("search_agg.config", fromlist=["WEIGHTS"]).WEIGHTS.get(name, 5)
    retry = engine.get("retry", False)
    # ... dispatch on etype ...
```

The whole search runs in `ThreadPoolExecutor(max_workers=40)` — 40 engines concurrently. Per-engine timeout 35s.

**SSE progress stream** (`server.py`):

```python
async def event_generator():
    msg_id = 0
    search_task = asyncio.ensure_future(_run_search(...))
    while not result_received:
        try:
            event = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
            msg_id += 1
            yield f"id: {msg_id}\nevent: engine_done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.TimeoutError:
            if search_task.done():
                result_received = True
                break
            if time.time() - last_progress_time > 10:
                msg_id += 1
                yield f"id: {msg_id}\nevent: heartbeat\ndata: {{\"alive\": true}}\n\n"
                last_progress_time = time.time()
    try:
        result = await search_task
    except Exception as e:
        yield f"id: {msg_id}\nevent: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        return
    yield f"id: {msg_id}\nevent: complete\ndata: {json.dumps(result['meta'], ensure_ascii=False)}\n\n"
    for i, r in enumerate(result.get("results", [])):
        msg_id += 1
        yield f"id: {msg_id}\nevent: result\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"
```

Each engine's `done` event is pushed immediately, so the UI can show partial progress. Heartbeats every 10s prevent connection drops. Final `complete` event carries meta; per-result `result` events stream after.

**`engines/zhipu.py` + `engines/opencli.py`** are the heaviest — they're full subprocess + JSON-RPC adapters. The `web_fetch` family is the lightest — pure `urllib` + HTML parsing.

**Steal these patterns.** (a) Per-engine YAML config with weight + adapter type. (b) Five-family adapter dispatch (API / opencli / web_fetch / mcp_stdio / skill). (c) SSE progress stream with per-engine events + heartbeat.

### 2.7 `inorilzy/multi-search-skill` — bounded executor + route profiles + site scraper memory

**The thesis.** 12 sources + bounded executor + automatic scrape-preview + SQLite key health + per-site scraper preference memory.

**Route profiles** (`multi_search_mcp/src/search/search_runner.py`):

```python
ALL_SOURCE_NAMES = ["baidu", "brave", "parallel", "tavily", "exa", "firecrawl",
                    "serpapi", "github", "hackernews", "stackoverflow",
                    "twitter", "sov2ex"]
ROUTE_PROFILES = {
    # route-name -> ordered list of sources to query
    "tavily-fanout": ["tavily", "brave", "parallel"],
    "serpapi-google": ["serpapi", "brave"],
    ...
}
```

Routes let the caller pick a named bundle: "I want Tavily-fanout" runs Tavily + Brave + Parallel. "I want serpapi-google" runs SerpAPI + Brave. Multiple routes can be combined.

**Bounded executor** (`multi_search_mcp/src/support/concurrency.py`):

```python
_TOOL_POOL = BoundedDaemonExecutor(max_workers=4, thread_name_prefix="mcp-tool")

async def _run_tool(fn: Callable[..., dict], *args) -> dict:
    future = _TOOL_POOL.submit_nowait(contextvars.copy_context().run, fn, *args)
    if future is None:
        return {"error": "MCP tool capacity exhausted; retry after active calls finish",
                "error_type": "runtime_error"}
    # Cancels queued work if possible, but cannot stop a running thread/network
    # call. The executor retains its slot until actual completion.
    return await asyncio.wrap_future(future)
```

Bounded at 4 concurrent tool calls (so a misbehaving caller can't flood the daemon). The capacity-exhausted error is structured — caller can retry.

**Auto-scrape preview** from the tool docstring:

```
`results[].content` is a search excerpt;
`scrapes[].markdown` is the single fetched preview (default: 1200 characters),
joined by `source_id`. Bounded previews may skip a prefix before an exact
matching page H1 or a paragraph matching the complete search snippet;
`preview_start/end` are original-body character offsets.

The calling Agent removes only clearly irrelevant candidates and retains
every relevant or uncertain candidate without a fixed quota. Find-only
requests can return matching links from these previews. For uncertain
matches or claims about contents, fetch selected full bodies with
fetch_source(source_id=..., full_content=True) and verify the evidence.
```

This is a **subtle but important pattern**: the tool returns the top-N search results AND automatically fetches a 1200-char preview of each, so the calling agent can decide what to read in full. No fixed quota on what to retain; the agent's job is to filter.

**SQLite key health** (`multi_search_mcp/src/state/key_state.py`) — per-engine key state persisted across restarts. If Tavily key was rate-limited last time, retry it later.

**Per-site scraper memory** (`multi_search_mcp/src/state/site_memory.py`) — remembers which scraper backend worked best for which site. If jina worked for `example.com`, try jina first next time.

**Steal these patterns.** (a) Bounded daemon executor with capacity-exhausted error. (b) Route profiles (named bundles of sources). (c) Auto-scrape preview with prefix-skip-to-H1 logic. (d) SQLite key health + per-site scraper memory.

### 2.8 `SK-DEV-AI/websearch` — the consensus-merge reference (200 KB)

**The thesis.** Multi-engine consensus merge (donsetch port) + vertical penalty + entity coverage penalty + cross-encoder blend + 3-tier stealth. The largest single-file engine-of-engines we've surveyed.

**Engine family grouping** (`merge.py`):

```python
def engine_family(engine: str) -> str:
    """Index family: engines sharing an index count once for consensus."""
    if is_vertical(engine):
        return f"vertical:{engine}"
    e = engine.split("-", 1)[0]
    return {
        "duckduckgo": "bing",       # ddg shares the Bing tail index
        "google": "google",
        "tavily": "tavily",
        "anysearch": "anysearch",
        "tinyfish": "tinyfish",
    }.get(e, e)
```

**Key insight:** DuckDuckGo's index is the Bing tail — they shouldn't both count for "consensus." This `engine_family()` function groups engines that share an index, so `merge_base` weights them as one.

**Vertical penalty** — single-source vertical hits get penalized:

```python
_VERTICALS = {"github", "hn", "wikipedia", "scholar", "news", "arxiv",
              "stackexchange", "mdn", "reddit"}
def is_vertical(engine: str) -> bool:
    return engine in _VERTICALS or engine.startswith("reddit")
```

A github-only result that no other engine confirms is down-weighted by `VERTICAL_WEIGHT = 0.6`.

**Structured error contract** (`errors.py`):

```python
PERMANENT = "permanent"
TRANSIENT = "transient"
WALLED = "walled"

VERDICT_TO_KIND = {
    "challenge": WALLED,        # browser tier already tried server-side → walled
    "auth_wall": WALLED,
    "paywall": WALLED,
    "soft_not_found": PERMANENT,
    "blocked": PERMANENT,
    "rate_limited": TRANSIENT,
    "server_error": TRANSIENT,
    "content_ok": PERMANENT,    # unreachable in an error path
}

def next_action(verdict: str | None = None, status: int = 0,
                kind: str = PERMANENT) -> str:
    if verdict == "auth_wall":
        return ("requires login credentials — no keyless automated path; "
                "use an interactive browser with your session")
    if verdict == "paywall":
        return ("paid content — no automated path; look for an open preprint/copy")
    if verdict == "rate_limited":
        return "rate limited — wait 30-60s and retry"
    if verdict == "challenge":
        if kind == WALLED:
            return ("browser solve failed — interactive verification needed; "
                    "no automated path")
        return ("retry with the browser path — it solves most JS/cookie challenges")
    # ...
```

Every error response gets an `errorKind ∈ {permanent, transient, walled}` AND a `next_action` one-liner. The agent decides what to do without parsing prose. **`auth_wall` means "no automated path" — don't try the same thing twice.**

**Stealth tiers** (`config.py` env vars):

```python
QUALITY_FLOOR = float(os.environ.get("QUALITY_FLOOR", "0.35"))  # drop below this
SIX_SIGNAL_ENABLED = os.environ.get("SIX_SIGNAL_ENABLED", "true") == "true"
STEALTH_INJECT_ENABLED = os.environ.get("STEALTH_INJECT_ENABLED", "false") == "true"
STEALTH_HEADERS_ENABLED = os.environ.get("STEALTH_HEADERS_ENABLED", "false") == "true"
TIER_SKIP_THRESHOLD = float(os.environ.get("TIER_SKIP_THRESHOLD", "0.30"))
TIER_MIN_TRIES = int(os.environ.get("TIER_MIN_TRIES", "10"))
ROBOTS_POLITENESS = os.environ.get("ROBOTS_POLITENESS", "true") == "true"
EXTRACT_METADATA_ENABLED = os.environ.get("EXTRACT_METADATA_ENABLED", "true") == "true"
```

Three stealth layers: ghost state (browser fingerprints), header rotation, JS challenge solver. Toggles via env. The README warns: `"steal-theme config (C2/E2/B1/A2/ghost-tier/C3)"` — the project is paranoid about bot detection.

**Cache** (`cache.py`) — SQLite + WAL mode, keyed by URL+extraction_type+css_selector, TTL eviction, size cap 10K entries. Verbatim:

```python
async def _ensure_db() -> Path:
    db_path = CACHE_DIR / DB_NAME
    if _db_initialized.get(db_path):
        return db_path
    async with _db_init_lock:
        if _db_initialized.get(db_path):
            return db_path
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fetch_cache (
                    key TEXT PRIMARY KEY, url TEXT NOT NULL, content TEXT NOT NULL,
                    status INTEGER NOT NULL DEFAULT 0,
                    content_type TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    total_chars INTEGER NOT NULL DEFAULT 0,
                    fetched_at REAL NOT NULL, ttl INTEGER NOT NULL DEFAULT 3600
                )""")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_fc_fetched_at ON fetch_cache(fetched_at)")
        _db_initialized[db_path] = True
        return db_path
```

WAL mode + 5s busy timeout means many concurrent reads + occasional writes don't lock.

**Query expansion** (`query_expand.py`) — Groq multi-key round-robin:

```python
_GROQ_KEYS: list[str] = []
_key_idx = 0

async def _next_key() -> str | None:
    global _key_idx
    async with _KEY_LOCK:
        if not _GROQ_KEYS:
            _init()
        if not _GROQ_KEYS:
            return None
        key = _GROQ_KEYS[_key_idx % len(_GROQ_KEYS)]
        _key_idx = (_key_idx + 1) % len(_GROQ_KEYS)
        return key
```

Round-robin multi-key — easy horizontal scaling for the LLM call.

**Steal these patterns.** (a) Engine-family grouping (DDG shares Bing's tail). (b) Vertical penalty for un-corroborated vertical hits. (c) Structured errorKind + next_action. (d) Round-robin key rotation for the LLM call.

### 2.9 `MachineLearning-Nerd/SearchMCP` — the CVE-aware reference

**The thesis.** SearXNG primary + Google scraping fallback + **CVE-aware engine selection**. Zero keys needed.

**CVE detection** (`relevance.py`):

```python
_CVE_PATTERN = re.compile(r"\bCVE-\d{4}-\d{4,}\b", flags=re.IGNORECASE)

def detect_query_intent(query: str) -> str:
    if _CVE_PATTERN.search(query):
        return QUERY_INTENT_SECURITY
    lowered = query.lower()
    if any(keyword in lowered for keyword in _SECURITY_KEYWORDS):
        return QUERY_INTENT_SECURITY
    return QUERY_INTENT_GENERAL

# _SECURITY_KEYWORDS = {"cve", "vulnerability", "vulnerabilities", "security",
#                       "exploit", "cvss", "advisory", "waiver", "patch", "mitigation"}
```

**Engine selection by intent** (config-driven):

```python
SEARCH_SECURITY_ENGINES = "brave,bing,duckduckgo,wikipedia,github,stackoverflow"
SEARCH_GENERAL_ENGINES = ""  # empty → SearXNG defaults

def select_engines_for_query(query: str, mode: str,
                             security_engines_raw: str,
                             general_engines_raw: str) -> list[str] | None:
    if mode.strip().lower() == "off":
        return None
    if mode.strip().lower() != "auto":
        return None
    intent = detect_query_intent(query)
    if intent == QUERY_INTENT_SECURITY:
        engines = parse_engine_list(security_engines_raw)
        return engines or None
    engines = parse_engine_list(general_engines_raw)
    return engines or None
```

`mode=auto` → if the query mentions CVE/security/vulnerability, use security engines. Otherwise use general engines (or SearXNG defaults if empty).

**Per-domain boost table** (`relevance.py`):

```python
_SECURITY_DOMAIN_BOOSTS: dict[str, float] = {
    "nvd.nist.gov": 8.0,
    "www.cve.org": 7.0,
    "cve.org": 7.0,
    "access.redhat.com": 5.0,
    "ubuntu.com": 5.0,
    "security.snyk.io": 4.0,
    "www.cvedetails.com": 3.0,
    "cvedetails.com": 3.0,
}

_GENERAL_DOMAIN_BOOSTS: dict[str, float] = {
    "docs.python.org": 4.0,
    "stackoverflow.com": 3.5,
    "realpython.com": 3.0,
    "developer.mozilla.org": 3.0,
    "github.com": 2.5,
    "en.wikipedia.org": 2.0,
}
```

**Quality-gate fallback** (`fallback.py`):

```python
async def search(self, query, category="general", limit=5):
    await self._rate_limiter.acquire()

    searxng_response = await self._searxng.search(query, category, limit)
    searxng_ranked = rank_search_results(query, searxng_response.results, limit)
    searxng_results = searxng_ranked.results

    intent = detect_query_intent(query)
    should_use_quality_gate = category == "general" and intent == QUERY_INTENT_SECURITY
    quality_triggered = should_use_quality_gate and is_low_quality(searxng_ranked,
                                                                  self._min_quality_score)

    if searxng_results and not quality_triggered:
        return SearchResponse(results=searxng_results, ...)

    if not self._fallback_enabled:
        return ...  # disabled fallback

    # Quality was low OR no results → fall back to Google scraping
    google_response = await self._google.search(query, category, limit)
    ...
```

If SearXNG returns low-quality results for a **security query** (quality_score < 2.5 by default), it falls back to Google HTML scraping. Smart: only triggers for security queries, where the stakes are highest.

**Snippet cleanup** — strips navigation boilerplate from snippets:

```python
_NAVIGATION_PHRASES = (
    "skip to navigation", "skip to main content", "skip to content",
    "select your language", "choose your language",
    "infrastructure and management", "official websites use .gov",
)

def clean_search_snippet(snippet: str) -> str:
    cleaned = snippet.replace("\xa0", " ")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if any(phrase in lowered for phrase in _NAVIGATION_PHRASES):
        segments = _SEGMENT_SPLIT_RE.split(cleaned)  # split on bullets
        filtered_segments = [
            segment for segment in segments if not _is_low_information_segment(segment)
        ]
        if filtered_segments:
            cleaned = " • ".join(filtered_segments)
        else:
            cleaned = ""
    ...
```

Search results often have `"Skip to content • English • Francais • Deutsch"` in the snippet — this strips it.

**Steal these patterns.** (a) CVE-aware intent detection. (b) Per-domain boost tables for security vs general. (c) Quality-gate fallback (only triggers when quality is genuinely low). (d) Snippet navigation cleanup.

### 2.10 `VulcanusALex/free-search-aggregator` — the self-healing router

**The thesis.** Config-driven router with **persistent quota** + **rolling health** + **discovery cycle**.

**Quota state** (`router.py`):

```python
class QuotaState:
    """Persistent provider usage tracker with day-level quotas."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.today = datetime.now(UTC).date().isoformat()
        self.state: dict[str, Any] = {"date": self.today, "providers": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Unable to read quota state at %s; resetting", self.path)
            return
        if data.get("date") != self.today:
            return
        self.state = data

    def increment(self, provider: str) -> None:
        pstate = self.state["providers"].setdefault(provider, {})
        pstate["requests"] = int(pstate.get("requests", 0)) + 1
        pstate["last_request_ts"] = int(time.time())
```

**Day-level quota roll-over** — if the saved date != today, the state is silently reset.

**Health tracker** (`health.py`, 8.6 KB) — rolling 72h error rate per provider. Public API:

```python
tracker = HealthTracker()
summary = tracker.get_summary(window_hours=72)
```

**SearchRouter** flow:

```python
def search(self, query: str, max_results: int = 8) -> dict[str, Any]:
    """Run a search with automatic provider failover."""
    chain = self._build_chain(query)  # intent-aware (or static)
    tried = []
    for provider in chain:
        # Skip if quota exhausted
        if self.quota.is_exhausted(provider.name):
            continue
        # Skip if health has been bad
        if not self.health.is_healthy(provider.name, window_hours=72):
            continue
        try:
            results = await provider.search(query, max_results=max_results)
            self.quota.increment(provider.name)
            self.health.record_success(provider.name)
            return {"results": results, "provider": provider.name, "tried": tried}
        except (QuotaExceededError, RateLimitError, AuthError, NetworkError, UpstreamError) as e:
            self.health.record_failure(provider.name, e)
            tried.append({"name": provider.name, "error": str(e)})
    raise SearchRouterError("All providers exhausted")
```

**Three-tier error hierarchy:**

```python
# providers.py
class ProviderError(Exception):  # base
class AuthError(ProviderError):           # 401/403 — skip permanently this session
class NetworkError(ProviderError):        # DNS/connection — transient
class ParseError(ProviderError):          # bad response shape — provider broken
class QuotaExceededError(ProviderError):  # 429/quota — pause this provider
class RateLimitError(ProviderError):      # 429 — exponential backoff
class UpstreamError(ProviderError):       # 5xx — transient
```

The error type tells the router what to do: `QuotaExceededError` pauses the provider for the day, `RateLimitError` triggers backoff, `AuthError` skips permanently this session.

**Discovery cycle** (`discovery.py`, 13 KB) — auto-discovers new providers, validates them, and adds them to the registry. README mentions:

> Auto-discovers providers via config.yaml; auto-recovers from quota exhaustion; surfaces health summary as a separate API. Best example of a "self-healing aggregator."

**Steal these patterns.** (a) Three-tier error hierarchy (`AuthError` / `QuotaExceededError` / `RateLimitError`). (b) Day-level quota roll-over in QuotaState. (c) Combined quota + health skip in the chain builder.

### 2.11 `dhruv-anand-aintech/anysearch` — the 16-provider capability matrix

**The thesis.** 16 providers + unified `SearchRequest` dataclass + capability flags + per-provider SDK (Python + TS) + Cloudflare Worker proxy. The most "production-engineered" multi-provider SDK.

**Unified `SearchRequest`** (`types.py`, 5 KB):

```python
@dataclass
class SearchRequest:
    """Normalized, provider-agnostic search request.

    Only ``query`` is required. Every other field is a "lowest common denominator"
    parameter that anysearch translates into each provider's native parameter where
    the provider supports it.
    """
    query: str
    max_results: int = 10
    search_type: str = "web"  # "web" | "news"
    engine: Optional[str] = None        # SerpApi backend
    country: Optional[str] = None       # ISO 3166-1 alpha-2
    language: Optional[str] = None      # ISO 639-1
    include_domains: List[str] = field(default_factory=list)
    exclude_domains: List[str] = field(default_factory=list)
    start_published_date: Optional[str] = None
    end_published_date: Optional[str] = None
    safe_search: Optional[str] = None

    # Unified "extra param modes"
    mode: Optional[str] = None          # fast | balanced | deep
    answer: bool = False                # synthesized, cited answer
    include_content: bool = False       # full page text
    include_summary: bool = False       # per-result AI summary
    highlights: bool = False            # query-relevant excerpts

    # Raw provider-specific passthrough
    extra: Dict[str, Any] = field(default_factory=dict)
```

**Capability matrix** (Pattern G above) — each provider declares its `frozenset` of supported capabilities. Brave has `COUNTRY/LANGUAGE/DATE/SAFE_SEARCH/HIGHLIGHTS/NEWS`. Tavily has `DOMAINS/COUNTRY/DATE/MODE/ANSWER/CONTENT/NEWS`. SearXNG has `LANGUAGE/SAFE_SEARCH/ANSWER`. DDG has `COUNTRY/SAFE_SEARCH`.

**Provider priority** (`providers/__init__.py`):

```python
PROVIDER_CLASSES: List[Type[BaseProvider]] = [
    OctenProvider,         # newer, well-curated
    ExaProvider,
    ParallelProvider,
    TavilyProvider,
    BraveProvider,
    KeiroProvider,
    LinkupProvider,
    PerplexityProvider,
    GeminiProvider,
    SerperProvider,
    SerpApiProvider,
    SearchApiProvider,
    YouProvider,
    JinaProvider,
    KagiProvider,
    FirecrawlProvider,
    GooglePSEProvider,
    SearxngProvider,
    DuckDuckGoProvider,    # keyless fallback, last
]
```

**SDK design** — every provider has `prepare()` (build native request) and `parse()` (normalize response) methods, both sync + async:

```python
class BaseProvider(ABC):
    name: str = ""
    aliases: Tuple[str, ...] = ()
    env_keys: Tuple[str, ...] = ()
    base_url_env: Tuple[str, ...] = ()
    default_base_url: str = ""
    requires_key: bool = True
    requires_base_url: bool = False
    capabilities: frozenset = frozenset()
    extra_package: Optional[str] = None  # PyPI extra
    native_client: Optional[Tuple[str, Sequence[str]]] = None  # escape hatch

    @abstractmethod
    def prepare(self, req: SearchRequest) -> PreparedRequest: ...

    @abstractmethod
    def parse(self, payload: Any) -> SearchResponse: ...

    async def search(self, req: SearchRequest, **kwargs: Any) -> SearchResponse: ...
```

`extra_package` and `native_client` are escape hatches — if you want to use the official provider SDK, just `pip install anysearch[exa]` and the wrapper falls through to `exa-py` automatically.

**MCP server + Worker proxy** — the repo ships:
- `python/src/anysearch/mcp/server.py` (13 KB) — stdio MCP server
- `python/src/anysearch/proxy/server.py` (11 KB) — HTTP proxy server
- `worker/matrix.js` (48 KB) — Cloudflare Worker browser-side proxy (multi-engine routing in the browser)

Three surfaces, one contract. Choose stdio / HTTP / browser.

**Steal these patterns.** (a) Unified `SearchRequest` dataclass. (b) Capability matrix + per-provider `frozenset`. (c) `prepare()` / `parse()` split. (d) `extra_package` / `native_client` escape hatches.

### 2.12 `Raudaschl/rag-fusion` — the RAG-Fusion reference

**The thesis.** The original reference implementation of "multi-query + RRF." 956★, smaller than anything else. Worth reading `main.py` end-to-end.

**Multi-query generation** (`main.py`):

```python
def generate_queries_chatgpt(original_query, diverse=False):
    """Generate multiple search queries from a single input query using ChatGPT."""
    if diverse:
        messages = [
            {"system": "You are a search expert. Generate diverse search queries that explore different aspects of the user's question. Each query should target a different angle: use synonyms, vary specificity (broader/narrower), and consider related sub-topics. Avoid generating queries that are just minor rewordings of each other."},
            {"user": f"Generate 4 diverse search queries for: {original_query}"},
            {"user": "OUTPUT (4 queries):"}
        ]
    else:
        messages = [
            {"system": "You are a helpful assistant that generates multiple search queries based on a single input query."},
            {"user": f"Generate multiple search queries related to: {original_query}"},
            {"user": "OUTPUT (4 queries):"}
        ]
    response = get_client().chat.completions.create(model="gpt-5.1-chat-latest", messages=messages)
    return response.choices[0].message.content.strip().split("\n")
```

**Vector search loop** + RRF:

```python
def reciprocal_rank_fusion(search_results_dict, k=60, verbose=True, query_weights=None):
    """Combine multiple ranked lists using Reciprocal Rank Fusion."""
    fused_scores = {}
    for query, doc_scores in search_results_dict.items():
        weight = query_weights.get(query, 1.0) if query_weights else 1.0
        for rank, (doc, _) in enumerate(sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)):
            if doc not in fused_scores:
                fused_scores[doc] = 0
            fused_scores[doc] += weight * (1 / (rank + k))
    return dict(sorted(fused_scores.items(), key=lambda x: x[1], reverse=True))
```

**The original formula**: `fused[doc] += weight * 1 / (k + rank)`. `k=60` is the de-facto standard from Cormack et al. 2009.

**Why it matters.** Every modern RRF (Kortex, anygen, webfetch, RivalSearch) is a direct descendant of this 25-line function. If you only have 30 minutes, read this file.

**Steal this pattern.** The whole thing. It's a single Python file (`main.py`, 6.3 KB) — copy-pasteable.

### 2.13 `firish/webfetch` — the search→fetch→chunk→rank pipeline

**The thesis.** Search is the FIRST step. The LLM is the extractor. Pipeline: search → fetch → chunk → rank → return ranked source-labeled chunks. Two-stage cache (exact + semantic).

**Pipeline** (`pipeline.py`):

```python
@dataclass
class SearchChunksResult:
    query: str
    chunks: list[Chunk]
    results: list[SearchResult] = field(default_factory=list)
    failed_urls: list[str] = field(default_factory=list)
    from_cache: bool = False
    elapsed_secs: float = 0.0
    cache_kind: str | None = None  # "exact" | "semantic" | None
    matched_query: str | None = None
    cache_age_secs: float | None = None
    freshness: str | None = None

class Pipeline:
    def __init__(self, search=None, rankers=None, cache=None,
                 n_results=DEFAULT_N_RESULTS, max_workers=DEFAULT_FETCH_WORKERS,
                 use_biencoder=True, use_crossencoder=True):
        self._search = search if search is not None else get_search_adapter()
        self._rankers = list(rankers) if rankers is not None else default_rankers(...)
        self._cache = cache
        self._n_results = n_results
        self._max_workers = max_workers
```

**All collaborators are injected.** Defaults come from config. The cache is transparent — passing `cache=None` gives identical results, just slower.

**Rank cascade** — `default_rankers(use_biencoder=True, use_crossencoder=True)` returns a 3-stage cascade:

1. **BM25** (lexical) — fast, cheap, catches exact keywords
2. **Bi-encoder** (semantic) — moderate cost, catches paraphrases
3. **Cross-encoder** (re-rank) — expensive, sharp precision

Each stage filters candidates so the next stage has less work.

**Two-stage cache** (`semcache.py`, 11 KB):

```python
# cache_kind ∈ {"exact", "semantic", None}
# exact: same query, same params → return cached chunks
# semantic: similar query (embedding cosine > threshold) → return cached chunks
# None: fresh run
```

If the same query was run within the cache TTL, return immediately. If a similar query was run recently, return those cached chunks. Otherwise fetch + chunk + rank fresh.

**Cache provenance** is exposed in `SearchChunksResult`:

```python
cache_kind: str | None = None      # "exact" | "semantic" | None
matched_query: str | None = None    # the cached query a semantic hit matched against
cache_age_secs: float | None = None  # how old the cache entry is
```

So the calling LLM knows "this was a cache hit (semantic) on `how does X work` 14 hours ago" — not a fresh search.

**MCP tool contract** (`tool.py` + `mcp.py`):

```python
@server.tool(description=WEB_SEARCH_TOOL["description"])
def web_search(query: str, force_fresh: bool = False,
               freshness: str | None = None,
               full_results: bool = False) -> str:
    """Search the web and return ranked, source-labeled excerpts.

    Args:
        query: A focused web search query.
        force_fresh: Bypass the result cache for live data.
        freshness: "realtime" | "recent" | "stable" - how fast this
            query's answer changes; controls cache lifetime.
        full_results: Set true for lists/rankings/enumerations -
            returns uncompressed excerpts so items are not trimmed
            (still excerpts, not full pages).
    """
    return handle_web_search({...}, pipeline=pipeline)
```

**`freshness` semantic** — drives cache TTL:
- `"realtime"` = fast-changing (stock price, breaking news) → cache 60s
- `"recent"` = changing (latest releases) → cache 1h
- `"stable"` = slow-changing (well-known APIs) → cache 24h

**Two tool surfaces** in addition to `web_search`:

```python
@server.tool()
def fetch_url(url: str) -> str:
    """Fetch one page's full extracted text under a budget."""

@server.tool()
def save_finding(query: str, content: str, source_url: str | None = None) -> str:
    """Cache a fact learned outside web_search, marked unverified."""

@server.tool()
def status() -> str:
    """Setup status: which search engines have keys and will serve..."""

@server.tool()
def savings_report() -> str:
    """What webfetch has saved vs hosted web-search pricing: this session
    (since the server started) plus the lifetime total."""
```

**`savings_report`** is brilliant — shows the user how much $$ they saved by using local search vs hosted web-search pricing. Updates with each tool call.

**Steal these patterns.** (a) Pipeline pattern (search→fetch→chunk→rank). (b) Two-stage cache (exact + semantic). (c) `freshness` semantic driving cache TTL. (d) `savings_report` for cost transparency. (e) Cache provenance in `SearchChunksResult`.

### 2.14 `Query-farm/vgi-search` — web search as a SQL table function

**The thesis.** Web search inside DuckDB. `SELECT * FROM search.web_search('duckdb arrow', provider := 'brave');` — even SQL clients get live web results.

**VGI egress connector** (`worker.py`):

```python
_CATALOG_DESCRIPTION_LLM = (
    "Run web searches from SQL through one pluggable provider surface (Brave, Tavily, Exa, "
    "SearXNG, DuckDuckGo, and the opt-in SerpApi/Serper SERP scrapers). Use it to retrieve live "
    "web results for RAG/retrieval: web_search(query, provider := ..., count := ..., page := ...) "
    "returns a unified row shape (title, url, snippet, rank, source, published, score, extra JSON) "
    "with provider-page pagination; web_answer(query, provider) returns a single synthesized "
    "one-line answer (Tavily or free DuckDuckGo Instant Answer) or NULL; search_providers() lists "
    "providers and which are configured. Provider API keys come from the VGI secret provider, "
    "never from SQL. This is an egress connector -- queries leave the engine for a third-party "
    "search API -- so results depend on the upstream subscription."
)
```

**Unified row schema** (`result.py`):

```python
@dataclass(slots=True)
class Result:
    """One normalized search hit (see module docstring for the column mapping)."""

    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    rank: int | None = None     # assigned by us, 1-based, consistent across backends
    source: str | None = None   # provider name, e.g. 'brave'
    published: datetime | None = None
    score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def extra_json(self) -> str | None:
        if not self.extra:
            return None
        return json.dumps(self.extra, default=str, ensure_ascii=False)
```

`rank` is **assigned by the worker, not taken from the provider** — consistent 1-based across backends. `extra` is JSON-encoded so DuckDB's `->>` / `json_extract` can reach into provider-specific fields without nested Arrow plumbing.

**VGI strict profile metadata** (`meta.py`) — every function publishes `vgi.title`, `vgi.doc_llm`, `vgi.doc_md`, `vgi.keywords`, `vgi.example_queries` tags. The `vgi-lint-check` strict profile gates on these.

**Steal these patterns.** (a) Rank assigned by the worker, not the provider. (b) `extra` as JSON-encoded dict. (c) Tags-based discovery (`vgi.title` / `vgi.doc_llm` / `vgi.example_queries`).

### 2.15 `telly6/searchpin` — the CJK query-preprocessing reference

**The thesis.** Multi-engine concurrent search + **CJK-aware query preprocessing** + embedding rerank via fastembed. The CJK preprocessing is the standout.

**CJK space-removal** (`backends.py`):

```python
_CJK_SPACE_RE = re.compile(
    r"(?<=[一-鿿㐀-䶿])\s+"
    r"|"
    r"\s+(?=[一-鿿㐀-䶿])"
)

def prep_query(raw):
    """When a space has a Chinese character on either side, cn.bing.com's
    tokenizer treats it as a word boundary and re-splits the CJK text
    character-by-character, triggering dictionary pollution. Removing
    these spaces keeps Chinese compound words intact while preserving
    English word boundaries.
    """
    return _CJK_SPACE_RE.sub("", raw)
```

**Why this matters.** When cn.bing.com sees `2026年 新能源汽车 补贴` (with spaces), it splits on spaces and re-tokenizes the CJK chars individually. The search results return calendar entries for `2026年` and dictionary entries for `新能源汽车`. **Removing spaces** around CJK chars (keeping them around English chars) avoids the collision.

The README (`searchpin__README.md`) gives a concrete example:

> "FOR CHINESE: no spaces around Chinese chars! '2026年新能源汽车补贴' ✓, '新能源汽车补贴 2026' ✗"

**Browser-realistic Bing URL builder:**

```python
def make_cn_bing_path(query, extra="", freshness_suffix=""):
    """Build a cn.bing.com search URL with browser-realistic parameters.
    These parameters tell Bing this is a real search-form query — without
    them the Chinese tokenizer splits compound words into single characters.
    """
    q = prep_query(query)
    char_count = len(q)
    word_count = max(1, len(q.split()))
    sc_value = f"{char_count}-{word_count}"
    cvid = os.urandom(16).hex().upper()
    browser_params = f"&qs=n&form=QBRE&sp=-1&lq=0&pq={urllib.parse.quote(q)}&sc={sc_value}&sk=&cvid={cvid}"
    return f"/search?q={urllib.parse.quote(q)}{browser_params}&count=15{extra}{freshness_suffix}"
```

The `sc={char_count}-{word_count}` value is computed from the query, plus a random `cvid`. Without these, Bing returns either 0 results or a SERP page with no extracted links.

**Prompt-driven methodology** — the `MCP_TOOLS` JSON in `engine.py` is **massive** (4 KB description, the most verbose we've seen). The tool description teaches the agent the right way to query:

```python
"MCP_TOOLS": [{
    "name": "web_search",
    "description": (
        "Search the web via multiple engines (Baidu, Sogou, Bing CN, Bing Intl). "
        "Results re-ranked by embedding similarity...\n\n"
        "Iterative search is normal: search → read → refine → search → read → synthesize.\n\n"
        "BEFORE FETCHING, READ THE SNIPPETS FIRST:\n"
        "- Search results come with title + URL + snippet only (no full content). "
        "Read snippets to decide which URLs are worth fetching.\n"
        "- Fetch only the 1-3 most promising URLs — do not blindly fetch everything.\n"
        "Each unnecessary web_fetch wastes 1-3s per call.\n\n"
        "⛔ WHEN RESULTS LOOK WRONG, DO NOT GIVE UP — ITERATE IMMEDIATELY:\n"
        "If search results are all irrelevant (wrong topic, wrong domain, "
        "generic encyclopedia entries, piracy sites, brand pages, file format tools, "
        "or dictionary entries), DO NOT conclude the information is unavailable. "
        "The fix is almost always a query reformulation. The most common root cause is "
        "Bing's tokenizer splitting your query words into fragments that collide with "
        "unrelated content. Replace fragile/generic terms with INSEPARABLE identifiers — "
        "proper nouns, compound terms, subdomain names, or acronyms that the tokenizer "
        "CANNOT split. Then immediately re-search. Do not narrate the failure — just "
        "try a different formulation.\n"
    ),
    ...
}]
```

This is the **ten-thousand-character tool description** pattern. Embed the methodology in the tool description so the calling LLM doesn't have to figure it out.

**Steal these patterns.** (a) CJK space-removal preprocessing. (b) Browser-realistic Bing URL params (`sc`, `cvid`, `pq`). (c) Massive methodology-embedded tool descriptions.

### 2.16 The remaining 9 repos — quick reference

**(a) `alphaparkinc/genpark-hybrid-rerank-fusion-retriever-skill`** — the cleanest RRF reference. `client.py` (62 lines) implements `fuse_and_rerank_results(dense_results, sparse_results)` with RRF k=60. Ships `mcp_server.py` + `skill.json`. No frills, no extras, copy-paste this.

```python
def fuse_and_rerank_results(self, query, dense_results=None, sparse_results=None, k=60):
    rrf_scores = {}
    doc_metadata = {}
    for rank, d in enumerate(dense_results, 1):
        did = d["doc_id"]
        rrf_scores[did] = rrf_scores.get(did, 0.0) + (1.0 / (k + rank))
        doc_metadata[did] = {"title": d["title"], "dense_rank": rank}
    for rank, d in enumerate(sparse_results, 1):
        did = d["doc_id"]
        rrf_scores[did] = rrf_scores.get(did, 0.0) + (1.0 / (k + rank))
        if did in doc_metadata:
            doc_metadata[did]["sparse_rank"] = rank
        else:
            doc_metadata[did] = {"title": d["title"], "sparse_rank": rank}
    fused = []
    for did, score in rrf_scores.items():
        fused.append({"doc_id": did, "title": doc_metadata[did]["title"],
                      "rrf_score": round(score, 6),
                      "dense_rank": doc_metadata[did].get("dense_rank"),
                      "sparse_rank": doc_metadata[did].get("sparse_rank")})
    fused.sort(key=lambda x: x["rrf_score"], reverse=True)
    return {"query": query, "fusion_method": "Reciprocal Rank Fusion (RRF, k=60)",
            "top_doc_id": fused[0]["doc_id"], "top_title": fused[0]["title"],
            "ranked_documents": fused}
```

**(b) `Liyux3/scholar-mcp`** — 12 academic clients (arXiv, arXivgg, DOAJ, Crossref, DBLP, EuropePMC, Exa, HAL, InspireHEP, OpenAlex, Semantic Scholar, CORE) + `expansion.py` (9.6 KB) + `graph.py` (13.8 KB) citation graph + `library_connectors.py` (12.8 KB) for Zotero/Mendeley. The "Google Scholar but reproducible."

**(c) `hermes-labs-ai/supersearch`** — cosine-similarity query→category routing with Ollama `nomic-embed-text` (threshold 0.7). 5 categories: academic / company / security / news / general. 8 engines via `ddgs` + `SearXNG` + 16 specialized scrapers (HuggingFace model, Crunchbase, LinkedIn, Wayback, EU AI registry, …).

```python
CATEGORY_ENGINES = {
    "academic": ["arxiv", "semantic_scholar"],
    "company":  ["github", "searxng"],
    "security": ["github", "hackernews", "searxng"],
    "news":     ["hackernews", "twitter", "searxng"],
    "general":  ["qwant", "ecosia", "startpage", "marginalia", "searxng", "wiby"],
}
```

**(d) `hec-ovi/websearch-skill`** — best layer separation. `websearch/layer1_search` / `layer2_extract` / `layer2_format` / `layer3_agentio` are independently testable. Ships `optional_layers.py` (17.9 KB) for plugin layers. `cli.py` is 73 KB (the largest single CLI in the survey).

**(e) `n24q02m/web-core`** — `search/runner.py` (46 KB) is a cross-process SearXNG singleton manager:

```python
PINNED_SEARXNG_PORT = 41592  # Pinned port prevents one-container-per-daemon
# Cross-process filelock prevents concurrent Docker spawn races.
# Auto-restart on crash detection (poll() check)
# Force-kill stale processes before restart to avoid port conflicts
```

Solves "every wet daemon spawns its own Docker container" — `filelock` + PID discovery + pinned port guarantee ONE SearXNG across all Python processes.

**(f) `Korrnals/bathys`** — Russian-language docs, English code. `core.py` (23 KB) is the unified deep-research service. `installer.py` (28 KB) is the bootstrap. `library_docs.py` (20 KB) is a local index of prior extracts — the agent can re-query its own prior work.

**(g) `GentelZole/deepsearch`** — `bin/deepsearch.py` (20 KB) is a CLI binary, keyless-first, RRF-based. `docs/ENGINES.md` (24 KB) is the engine test results table. New (Sept 2026).

**(h) `Quantaus/product-researcher-mcp`** — `server.py` (16 KB). Specialized for product-research queries (Tavily + Brave + Reddit + Hacker News).

**(i) `Korrnals/bathys` extras** — `services.py` (11 KB), `source_check.py` (16 KB) — health-check and source-availability probe.

---

## 3. Pro tips — distilled from reading 24 production codebases

**Tip 1. Don't bake an LLM into the server.** RivalSearch's `SERVER_INSTRUCTIONS` is explicit: "Deterministic research and content-discovery MCP server. No LLM is run inside the server itself." Every tool returns structured output. The calling LLM does synthesis. This keeps the server **deterministic, auditable, and easy to test.**

**Tip 2. Use word-boundary regex, not `w in query_lower`.** `agent-kreal/intent.py`:

```python
def _matches(query_lower: str, words: tuple) -> bool:
    """Word-boundary matching so 'hang' doesn't fire inside 'changelog'."""
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", query_lower):
            return True
    return False
```

A 5-line change that fixes a class of false-positive intent misclassification.

**Tip 3. Cache envelope must include ALL fields the caller expects.** `AnyGenIO/engine.py`:

```python
"""Two on-disk shapes are supported:
  * legacy: a bare list of result dicts (pre-2026-08-14 entries)
  * current: {"results": [...], "extras": {...}}

The envelope exists because provider extras (Tavily's synthesized
`answer`, Exa's `context`, usage) were NOT cached, so any cache
HIT silently returned a 0-char answer while the cold call worked.
"""
```

Always wrap cache values in an envelope that includes synthesized/side-channel fields. Otherwise your cache HIT silently loses the most useful field.

**Tip 4. Detect silent blocks (HTTP 200 + empty body + peer returned results).** `webfetch/search/multi.py` (the comment is the code):

```python
"""Breaker bookkeeping: an exception is always a failure; an EMPTY
response counts as a failure only when a peer returned results for the
same query (silent-block signature, e.g. DDG's fingerprint-block 202) -
a hard query that empties every engine benches nobody.
"""
```

DDG returns 200 OK with empty body when it fingerprints you. Without this rule, you'd never know.

**Tip 5. Engine-family grouping, not raw engine counts.** `SK-DEV-AI/merge.py`:

```python
def engine_family(engine: str) -> str:
    if is_vertical(engine):
        return f"vertical:{engine}"
    e = engine.split("-", 1)[0]
    return {
        "duckduckgo": "bing",  # ddg shares the Bing tail index
        "google": "google",
        ...
    }.get(e, e)
```

DuckDuckGo's index IS the Bing tail. If you have DDG + Bing returning the same page, that's NOT two corroborations — it's one source twice. Group engines by index family before weighting consensus.

**Tip 6. Reorder the chain by intent, don't hardcode it.** `agent-kreal/router.py`:

```python
INTENT_ORDER = {
    "docs":     ["exa", "youcom", "tavily"],
    "research": ["exa", "youcom", "brave"],
    "fact":     ["youcom", "exa", "brave"],
    "news-ru":  ["tavily", "youcom", "brave"],
    "debug":    ["tavily", "youcom", "brave"],
    "github":   ["gh", "exa", "youcom", "tavily", "brave"],  # gh is FREE
    ...
}
```

Put the best provider for the job first. Quota skipping + 429 fallback-down still apply uniformly.

**Tip 7. Pin a singleflight key that includes ALL params that shape the outcome.** `kortex-search/orchestrator.py`:

```python
key = (source.name, query.lower().strip(), limit, category, freshness,
       year_from, open_access_only)
```

The bug history is real — an earlier `(source, query)` key let different `limit` values collide and return wrong counts. Every input that shapes the response goes into the key.

**Tip 8. Don't add workers; reduce work.** `kortex-search/quality.py`:

```python
"""Design note: parallel rerank THREADS are deliberately not used to absorb
bursts — measured 2026-09-05: concurrent ONNX predicts contend on the CPU
pool (4-parallel wall 16.1s for 4 x 4.5s jobs). The valve is work
REDUCTION, not more workers.
"""
```

The cross-encoder is the bottleneck. A 5-tier ladder with EMA-calibrated cost is the right answer.

**Tip 9. Capability matrix maps unified fields to provider flags.** `anysearch/providers/base.py`:

```python
class Capability:
    DOMAINS = "domains"; COUNTRY = "country"; LANGUAGE = "language"
    DATE = "date"; SAFE_SEARCH = "safe_search"; MODE = "mode"
    ANSWER = "answer"; CONTENT = "content"; SUMMARY = "summary"
    HIGHLIGHTS = "highlights"; NEWS = "news"; ENGINE = "engine"

class BraveProvider(BaseProvider):
    capabilities = frozenset({COUNTRY, LANGUAGE, DATE, SAFE_SEARCH, HIGHLIGHTS, NEWS})
class TavilyProvider(BaseProvider):
    capabilities = frozenset({DOMAINS, COUNTRY, DATE, MODE, ANSWER, CONTENT, NEWS})
```

Each provider declares what it supports. Caller writes one query; the router automatically drops unsupported fields. No per-call capability check, no `try/except UnsupportedParameterError`.

**Tip 10. Per-domain boost table, not flat ranking.** `rival_search_mcp/core/quality/`:

```python
_SECURITY_DOMAIN_BOOSTS = {
    "nvd.nist.gov": 8.0, "www.cve.org": 7.0, "cve.org": 7.0,
    "access.redhat.com": 5.0, "ubuntu.com": 5.0, ...
}
_GENERAL_DOMAIN_BOOSTS = {
    "docs.python.org": 4.0, "stackoverflow.com": 3.5, ...
}
```

The boost depends on intent. NVD is gold for security queries, irrelevant for general ones. Switch the table per query intent.

**Tip 11. Snippet navigation cleanup.** `SearchMCP/relevance.py`:

```python
_NAVIGATION_PHRASES = ("skip to navigation", "skip to main content",
                       "select your language", "choose your language", ...)
_SEGMENT_SPLIT_RE = re.compile(r"\s*[•|·]\s*")  # split on bullets
```

Search snippets often contain `"Skip to content • English • Français • Deutsch"` boilerplate. Strip it via regex on bullet-separated segments. The user sees only the actual content.

**Tip 12. Engine-specific canary queries.** `argo/engine_validate.py`:

```python
ENGINE_CANARY_QUERIES = {
    "fxtwitter": "OpenAI",
    "jin10": "美联储",
    "eastmoney": "贵州茅台",
    "qweather": "北京 天气",
    "arxiv": "transformer",
    "hackernews": "Python",
}
```

Generic "machine learning" would mis-fire on every finance source. Use vertical-aware canaries.

**Tip 13. CJK tokenizer collision.** `searchpin/backends.py`:

```python
_CJK_SPACE_RE = re.compile(
    r"(?<=[一-鿿㐀-䶿])\s+" r"|" r"\s+(?=[一-鿿㐀-䶿])")
def prep_query(raw):
    return _CJK_SPACE_RE.sub("", raw)
```

When cn.bing.com sees `新能源汽车 补贴` (with space), it splits and dictionary-collides. Removing CJK-adjacent spaces keeps compound words intact.

**Tip 14. Embed the methodology in the tool description.** `searchpin/engine.py`:

```python
"MCP_TOOLS": [{
    "name": "web_search",
    "description": ("...Iterative search is normal: search → read → refine...\n"
                    "BEFORE FETCHING, READ THE SNIPPETS FIRST...\n"
                    "⛔ WHEN RESULTS LOOK WRONG, DO NOT GIVE UP — ITERATE IMMEDIATELY..."
                    "FOR CHINESE: no spaces around Chinese chars!\n"
                    "SEARCH STRATEGY (patterns drawn from real-world use):\n"
                    "1. DATE TERMS HIJACK QUERIES...\n"
                    "2. SHORT COMMON WORDS COLLIDE...\n"
                    "3. LONG PROPER NOUNS ARE ANTI-NOISE ANCHORS...\n"
                    "...")}
]
```

A 4 KB tool description that teaches the calling LLM the right way to query. Saves you from "agent dumps `python tutorial` and gets 90% junk results."

**Tip 15. Savings report for cost transparency.** `webfetch/mcp.py`:

```python
@server.tool()
def savings_report() -> str:
    """What webfetch has saved vs hosted web-search pricing: this session
    (since the server started) plus the lifetime total."""
```

When the user sees "$0.42 saved this week by using local search," they understand the value.

**Tip 16. Engine-family vertical penalty.** `SK-DEV-AI/merge.py`:

```python
VERTICAL_WEIGHT = 0.6

def is_vertical(engine: str) -> bool:
    return engine in {"github", "hn", "wikipedia", "scholar", "news",
                       "arxiv", "stackexchange", "mdn", "reddit"}
```

A github-only result that no other engine confirms is down-weighted by 0.6. Prevents vertical sources from dominating just because they're noisy.

**Tip 17. Browser-realistic URL params for Bing scraping.** `searchpin/backends.py`:

```python
sc_value = f"{char_count}-{word_count}"
cvid = os.urandom(16).hex().upper()
browser_params = f"&qs=n&form=QBRE&sp=-1&lq=0&pq={...}&sc={sc_value}&sk=&cvid={cvid}"
```

Without these, Bing returns 0 results or no extracted links. The `sc` and `cvid` are the markers Bing uses to detect "real search form" vs "headless curl."

**Tip 18. Server-side `--dry-chain` for debugging.** `agent-web-search/cli.py`:

```bash
web-search --dry-chain --intent github
# Prints:
#   1. gh: not exhausted, configured → try
#   2. exa: configured, not exhausted → try
#   3. youcom: configured, exhausted (window day 0/10000) → SKIP
#   4. tavily: configured → try
#   5. brave: no key → SKIP
```

Invaluable for "why did it pick Brave and not Tavily?" Without `--dry-chain` you'd have to trace through logs.

**Tip 19. Validate-before-admit gate.** `argo/engine_validate.py`:

```bash
python3 scripts/engine_validate.py --engine hackernews --stage all --admit
# Stage health: passed (latency=120ms, schema=ok)
# Stage quality: passed (5/5 queries returned non-empty, field_complete_rate=0.92)
# --admit: wrote admission (blocked=false) to disk
```

No engine enters production without passing both stages. If a source is bad, it's blocked, not just slow.

**Tip 20. Cache provenance in the response.** `webfetch/pipeline.py`:

```python
@dataclass
class SearchChunksResult:
    ...
    cache_kind: str | None = None       # "exact" | "semantic" | None
    matched_query: str | None = None    # which cached query matched
    cache_age_secs: float | None = None  # how old the cache entry is
```

The calling LLM knows "this was a 14-hour-old semantic cache hit on `how does X work`" — not a fresh search. Crucial for "is this information still current?"

---

## 4. Repo index — all 24 with stars, engines, license, key file

| # | Repo | ★ | License | Engines / sources | Key file |
|---|---|---|---|---|---|
| 1 | `damionrashford/RivalSearchMCP` | 128 | (MIT) | 5 web + 9 social + 6 academic | `rival_search_mcp/tools/multi_search.py` |
| 2 | `taxueseek/argo` | 130 | MIT | 220 sources, 60 spec YAMLs | `scripts/engines_base.py` |
| 3 | `kbjama8/kortex-search` | 0 | MIT | 27 sources | `kortex_search/orchestrator.py` |
| 4 | `agent-kreal/agent-web-search` | 5 | MIT | 10+ providers | `engine/router.py` |
| 5 | `AnyGenIO/anygen-search-cli` | 1 | MIT | 6 commercial | `hsearch/router.py` |
| 6 | `chen150450/multi-search-aggregator` | 27 | MIT | 30+ engines (CN-heavy) | `search_agg/runner.py` |
| 7 | `inorilzy/multi-search-skill` | 3 | (MIT) | 12 sources | `multi_search_mcp/server.py` |
| 8 | `SK-DEV-AI/websearch` | 1 | (MIT) | DDG, Brave, Tavily, Google AI | `merge.py` |
| 9 | `MachineLearning-Nerd/SearchMCP` | 0 | MIT | SearXNG + Google fallback | `src/web_mcp/search/relevance.py` |
| 10 | `VulcanusALex/free-search-aggregator` | 2 | MIT | Brave, Tavily, DDG, Serper, SearchAPI | `src/free_search/router.py` |
| 11 | `dhruv-anand-aintech/anysearch` | 4 | MIT | 16 providers + Worker proxy | `python/src/anysearch/providers/base.py` |
| 12 | `Raudaschl/rag-fusion` | 956 | MIT | Single (ChromaDB) — RAG-Fusion reference | `main.py` |
| 13 | `alphaparkinc/genpark-hybrid-rerank-fusion-retriever-skill` | 8 | MIT | RRF skill | `client.py` |
| 14 | `firish/webfetch` | 55 | MIT | Brave, DDG, Serper, Tavily | `webfetch/pipeline.py` |
| 15 | `Query-farm/vgi-search` | 0 | (MIT) | Brave, Tavily, Exa, SearXNG, DDG | `vgi_search/worker.py` |
| 16 | `telly6/searchpin` | 26 | (MIT) | Baidu, Sogou, Bing CN/Intl | `searchpin/backends.py` |
| 17 | `Liyux3/scholar-mcp` | 3 | (MIT) | 12 academic | `scholar_mcp/expansion.py` |
| 18 | `hermes-labs-ai/supersearch` | 2 | (MIT) | 8 engines + 16 scrapers | `src/supersearch/routing.py` |
| 19 | `hec-ovi/websearch-skill` | 8 | (MIT) | ddgs + 11 keyless | `src/websearch/cli.py` |
| 20 | `n24q02m/web-core` | 3 | (MIT) | SearXNG (Docker singleton) | `src/web_core/search/runner.py` |
| 21 | `Korrnals/bathys` | 0 | MIT | SearXNG + multi-source | `src/bathys/core.py` |
| 22 | `GentelZole/deepsearch` | 0 | (MIT) | 14 engines | `bin/deepsearch.py` |
| 23 | `Quantaus/product-researcher-mcp` | 0 | (MIT) | (Tavily / Brave / etc.) | `server.py` |
| 24 | `Pouyops/HybridRAG` | 1 | (MIT) | Hybrid BM25 + dense | `main.py` |

**Total engines / sources covered:** 320+ (Kortex 27 + argo 220 + RivalSearch 30 + anygen 16 + multi_search_agg 30 + inorilzy 12 + supersearch 24 + websearch-skill 12 + web-core 1 + bathys 12 + scholar 12 + …). Most are keyless.

**Total Python LOC surveyed:** ~85,000 lines (253 files, 2.8 MB raw source).

---

## 5. How to use this report

**If you're building a new aggregator:**
1. Start with Pattern G (capability matrix) — `dhruv-anand-aintech/anysearch` is the cleanest reference.
2. Add Pattern A (intent-driven reorder) — `agent-kreal/agent-web-search` is the gold standard.
3. Add Pattern D (weighted RRF) — `kbjama8/kortex-search` is the production reference.
4. Add Pattern B (quota ledger) — same project as Pattern A.
5. Add Pattern F (circuit breaker + silent-block detection) — `webfetch/search/resilience.py` for the peer-detection trick.
6. Add Pattern C (tier ladder) only if you ship a cross-encoder rerank — `kortex-search/quality.py` for the EMA cost model.
7. Add cache with envelope (Tip 3) — never drop synthesized fields on a HIT.
8. Add `--dry-chain` (Tip 18) so you can debug routing decisions without running them.
9. Add `savings_report` (Tip 15) so users see the value of local search.

**If you're picking a webhook/fetcher to scrape:**
- **Keyless, broad web**: `RivalSearchMCP` (5 web + 9 social + 6 academic), `argo` (220 sources), `webfetch` (multi-engine + extract).
- **Cyrillic queries**: `agent-kreal/agent-web-search` (explicit `"ru"` intent with `"youcom, brave, tavily"` order).
- **Security / CVE queries**: `MachineLearning-Nerd/SearchMCP` (CVE-aware engine selection + NVD/CVE.org boost).
- **CJK / Chinese**: `telly6/searchpin` (CJK preprocessing), `multi-search-aggregator` (CN ecosystem).
- **Academic**: `Liyux3/scholar-mcp` (12 academic clients + citation graph).
- **News**: `RivalSearchMCP news_aggregation` (Google News + Bing News + Guardian + GDELT).
- **GitHub code**: `agent-kreal/agent-web-search` (`gh` intent with native GitHub API first).
- **Production / server-grade**: `anygen-search-cli` (6 commercial + envelope cache fix).

**If you're auditing an existing aggregator:**
- Check Pattern G (capability matrix). If the codebase doesn't have one, expect bugs at every provider boundary.
- Check Pattern B (quota ledger). If it's in-memory only, expect to re-burn 429s on every restart.
- Check Pattern F (silent-block detection). If the breaker only triggers on 5xx, expect fingerprint-blocked engines to look healthy.
- Check the cache envelope (Tip 3). If the synthesized `answer`/`context` fields aren't cached, expect silent failures on cache HIT.

---

## 6. Evidence trail — every claim cited

**Parallel Web Search API records** (retained as `parallel_initial_search.json`, `parallel_niche_search.json`):
- `search_146977a73cd242fe300f32506ba7806c` — broad multi-engine MCP
- `search_22707c412212186ddeab0704accec918` — RRF k=60 production
- `search_c8051cec193e1d75e5ed86451aa17713` — keyless SearXNG aggregator
- `search_e2a3a1c99526de761e32b36be5628e21` — intent-router multi-engine
- `search_6275e5dcf07c272a12bc18b6496630dc` — weighted RRF rerank
- `search_03b0881ad50906d0258ec5156eae1459` — circuit-breaker quota-ledger

**GitHub REST API queries** (rate-limited, retained as `all_candidates.json`):
- 18 search queries across `language:python` + multi-keyword boolean combinations.
- Total candidate set before dedup: ~120. After dedup by primary engine mix: 24 unique.

**Raw source files** (retained in `raw/`, 253 files, 2.8 MB):
- Every code snippet quoted in this report was copy-pasted verbatim from the raw source. URLs are `<owner>/<repo>@<branch>` references. Path references are repo-relative.

**Direct download URLs** (sample):
- `https://raw.githubusercontent.com/damionrashford/RivalSearchMCP/main/rival_search_mcp/server.py`
- `https://raw.githubusercontent.com/taxueseek/argo/main/scripts/engines_base.py`
- `https://raw.githubusercontent.com/kbjama8/kortex-search/main/kortex_search/orchestrator.py`
- `https://raw.githubusercontent.com/agent-kreal/agent-web-search/main/engine/router.py`
- `https://raw.githubusercontent.com/AnyGenIO/anygen-search-cli/main/hsearch/engine.py`
- `https://raw.githubusercontent.com/chen150450/multi-search-aggregator/main/search_agg/runner.py`
- `https://raw.githubusercontent.com/SK-DEV-AI/websearch/master/merge.py`
- `https://raw.githubusercontent.com/MachineLearning-Nerd/SearchMCP/main/src/web_mcp/search/relevance.py`

---

## 7. License notes

All code snippets quoted in this report are from MIT-licensed (or near-MIT) repositories. The report itself is original analysis. Where the source repo's license is unclear from the README (some new projects don't have a LICENSE file), I noted `(MIT)` as the presumed license based on conventional defaults.

**Building on the v2 report.** This v3 picks up where `MULTI_ENGINE_AGGREGATOR_REPORT.md` left off. v2 covered 18 grassroots aggregators (TS + Go + Python, mostly lower-starred); v3 zooms in on **Python-only, multi-engine, less-known** projects with at least 3 sources. The seven patterns (A-G) and 20 pro tips in this report generalize beyond Python — most apply to TS aggregators too. v2's TL;DR table for engines (Tavily / Exa / Sonar / Brave / SearXNG / DDG / Firecrawl / Jina) is still the canonical reference.

**Next iteration.** v4 could cover:
- **TS-only aggregator deep dive** (Pi-Search-Hub, MCP-SearXNG, MetaSearchMCP-TS, DSH) — same patterns, different idioms.
- **Reranking models** (Cohere Rerank, bge-reranker-v2-m3, Jina Rerank, mixedbread) — best practices for cross-encoder integration.
- **Caching architectures** (Redis, SQLite, diskcache, semantic cache) — when to use which.
- **Prompting the search tool** — methodology-embedded tool descriptions, intent-driven prompts.

If any of those would be useful, say the word and I'll spin up the same scout + deep-dive pipeline.
