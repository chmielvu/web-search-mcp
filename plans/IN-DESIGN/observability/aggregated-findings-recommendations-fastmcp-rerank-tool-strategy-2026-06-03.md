# Aggregated Findings, Critical Review, and Recommendations
## FastMCP Engineer Analysis: Reranking Latency + Strategic MCP Tool Usage by Coding Agents

**Date**: 2026-06-03  
**Version**: v3 (Readability & Formatting Rewrite)  
**Analysis window**: 48h observability snapshot (2026-06-01 to 2026-06-03) + static codebase review + external cross-validation (web searches, gofastmcp.com/llms.txt, practitioner sources, reranker benchmarks, Cloudflare/Anthropic/Flock/TEI primary sources).

**Sources (active only)**: `plans/observability/` (48h report, stack-review, action-recommendations, deep_review + this document), active plans (`plans/GraphQL-tuning.md`, `plans/gliner/*`, `plans/playright/*`), `src/kindly_web_search_mcp_server/` (server.py, rerank/* including the GCS client added during research, search/orchestrator.py, settings.py, middleware/*, analytics/*, content/*, telemetry.py), pyproject.toml, docs/, CHANGELOG.md, external primary sources (gofastmcp.com full docs + CodeMode page, Cloudflare Code Mode blog, TEI/HF Cloud Run guides, FlockMTL docs + examples, Anthropic MCP engineering notes).

**Scope note (per user instruction)**: Plans under `plans/Done/` were **not read or consulted** for the assessment of other potential updates. Portfolio review and value judgments are limited exclusively to currently active plans at the top level of `plans/`. All prior references to Done/ plans have been removed or qualified.

**This document (v3 rewrite)**: Complete rewrite for coherence, detail, and professional formatting. Previous versions had accumulated duplicate/out-of-order headings, walls of pasted subagent text, and mixed v1/v2 content, making it unreadable. This version:
- Uses logical top-down structure with consistent heading levels.
- Presents rich subagent content (practitioner 10-rec playbook, LLM-as-Judge prompts + Flock SQL + sketches, GCS exact deploy + client, CodeMode mechanics + diffs, plans portfolio) in scannable, detailed form (numbered lists, sub-bullets for Why/How/Measure, proper code fences, tables).
- Preserves every technical depth, subagent IDs, 48h metrics, file:line citations, and scope compliance.
- Adds tables for roadmap and comparisons.
- Short paragraphs + bold action items for readability while remaining comprehensive.

All changes follow project rules (AGENTS.md / CLAUDE.md): documented in CHANGELOG.md under [Unreleased].

---

## Executive Summary

The system is a sophisticated personal multi-provider search/research MCP platform built on FastMCP (~16 tools + prompts + resources, RRF merge, rerank, query rewrite/policy, specialized content extraction ladder, semantic + exact caches, agentic ReAct lane, rich DuckDB/OTel/Langfuse/Grafana analytics).

**48h snapshot (3,319 events, 975 runs, 291 unique queries)** showed a healthy, busy service with workload shifting toward richer agentic/research flows. Cost and latency concentrate in three areas:
- Query rewrite (~12.06s avg / 30.5s p95)
- Rerank (Voyage primary: 6.62s avg / 18.18s p95 on 155 events)
- Agentic Web Research (distinct expensive class: ~27s avg / >99s p95, 10 errors)

**Core tools are healthy and dominant** (web_search 241 req / 0 err; get_content 141). Long-tail synthesis/niche tools (perplexity, grok, youtube_*, composio dedicated, analytics) see little or no production traffic in the window. Agentic (34 calls) was largely test-driven per user clarification.

**Two explicit user focus areas** (with deep research + subagent outputs):

1. **High reranking latency (Voyage and Jina both slow in practice for this workload)**: Validated hotspot. Inputs are short noisy web snippets (title + url + snippet formatted in `rerank/core.py`). Public APIs suffer shared queues, rate limits, and variance.  
   **Primary recommendation**: Custom reranker on Google Cloud Run using HF Text Embeddings Inference (TEI) — first-class cross-encoder/reranker support, predictable sub-500ms p95 on CPU (or <<100ms on L4 GPU) for 50 short docs, low cost at dev scale, privacy, no external rate limits. Models: BAAI/bge-reranker-v2-m3 (primary), jina-reranker variants, ms-marco-MiniLM for fastest CPU. Exact `gcloud` commands, client (`rerank/gcp_cloudrun.py` — already added by research subagent with retry, IAM ID token support for private services, flexible parse), settings, fallback, telemetry, cold-start mitigations (min-instances=1), validation (DuckDB + live probes), and A/B path are in the detailed section below. Hybrid pipeline + fast-paths preserved.

2. **Coding agent clients not using the exposed toolset fully or strategically ("many tools not used at all")**: Real and widely reported in the MCP ecosystem. Agents bypass the intended grammar ("web_search (with rewrite) → get_content/batch or discover_links → (light synthesis only when needed); agentic as budgeted last resort") despite rich existing steering (per-tool "When to use", research_goal, 4 planning prompts, `docs://workflow` resource, DynamicGuidanceMiddleware injecting `agent_guidance` + `suggested_next_tools` + `suggested_prompts`, ExpensiveToolProtectionMiddleware, differentiated rates, Context progress).  
   **Solution via FastMCP 3.x+ advanced features** (project already on 3.2.4): 
   - **Profiles + Visibility** (KINDLY_TOOL_PROFILE=minimal|core|research|full + tags on `@mcp.tool` + `mcp.enable/disable` or Visibility transform) to hide the long tail by default.
   - **ToolSearch (BM25SearchTransform)** + `PromptsAsTools`/`ResourcesAsTools` for on-demand discovery and making workflow guidance visible even to tool-only clients (Cursor/Claude configs).
   - **CodeMode (opt-in only)**: Replaces flat list with meta-tools (`search` + `get_schema` + `execute`). LLM writes small Python orchestrations calling *only* primitives inside a MontySandbox (V8 isolate). One execute can do loops/conditionals/dedup without intermediate LLM context bloat. 99.9% token reduction demonstrated by Cloudflare on 2500-endpoint APIs (2 meta-tools → ~1k tokens fixed footprint). Existing middleware/guidance/visibility/telemetry apply inside the sandbox for free. Layer **after** profiles + transforms (best practice).

**High-signal practitioner evidence** (Cloudflare "Code Mode" blog, Anthropic MCP explorations, modelcontextprotocol SEPs 1004/1821, real post-mortems of 300+ tool servers with 97% stubs causing 5-10k token overhead + selection cliffs, X/Reddit "fewer tools === better", max ~10-20 practical): Descriptions alone are ignored under load. Structural signals (profiles, on-demand discovery, result-level hints, progress, error recovery workflows, session state, analytics-driven hiding) win. One rich server with gating > premature splits.

**LLM-as-Judge** (missing "value" measurement): Per-result (relevance/usefulness for coding tasks, faithfulness for synth) + periodic/batch on DuckDB events (strategic compliance: "did web_search + get_content happen before synthesis?"). Includes exact system/user prompts (JSON-only, coding-focused), FlockMTL SQL for in-DB `llm_complete`/`llm_filter` (local DuckDB only), Python sketches, Langfuse/Grafana integration, sampling/cost controls, gold harness validation. Directly feeds steering (compliance %) and rerank value (lift of expensive stage).

**Other active plans value** (GraphQL-tuning, gliner/*, playright/* only — no Done/ material): High synergy. gliner for entity signals (better query_policy to cut 12s rewrites, rerank features on snippets, cache guardrails, richer guidance for judges/steering). playright/Crawl4AI for remote fetch latency (the real bottleneck in get_content traces). GraphQL for GitHub resolver tails/reliability (high-value specialized content). Pull into main P0/P1 work.

**sussestion**: 
- Steering surface reduction (profiles/Visibility + ToolSearch + PromptsAsTools + systematic hints + usage audit).
- Cache correctness + identity fixes.
- Canonical events + error severity + trace fixes.
- GCS reranker skeleton + KINDLY_RERANK_PROVIDER support (client already present).
- LLM-judge prototype (tables + one CLI report + Flock path) + gold eval harness baseline on existing DuckDB.
- gliner pilot + GraphQL pagination for GitHub.
- Experiments (harness + live probes) **before** large latency/steering changes.
- Full details + acceptance criteria in the Roadmap section.

**Impact expected**: Sub-second rerank p95 (predictable), materially higher agent compliance (measure via DuckDB + new LLM-judge), cache that is both fast *and correct*, cleaner value metrics for expensive stages, compounding wins from aligned active plans.

**Confidence**: High on diagnosis and recs (data + multiple primary sources + subagent depth + code cross-val). Experimental pieces (CodeMode, Flock, TEI GPU on Cloud Run) require testing + quotas.

---

## 1. 48h Observability Snapshot – Key Facts & Pain Points

From the 48h report + stack review + action recommendations + deep review (cross-mapped to live code in server.py, rerank/core.py, orchestrator.py, content/fetch_pipeline.py, agent/mcp.py, cache/page_cache.py, settings.py, telemetry, analytics views):

- **Workload**: 3,319 events, 975 run timelines, 291 unique queries. Avg run 11.39s / p95 53.12s / max 176s. Sustained; shifting richer on 06-02+. Many lightly instrumented runs (620 with 0 rewrite/0 rerank/0 fetch/0 answer markers) — use traces + events together.
- **Tool usage mix (steering / "many not used" signal)**: 
  - Healthy core: `web_search` (241 req / 169 resp / 0 err), `get_content` (141/131/0).
  - Present but lower: `batch_get_content` (21), `academic_search` (10), `discover_links` (6).
  - Synthesis low-volume: `gemini_search` (16), `perplexity_search` (2/1 err — middleware protection appears effective but volume tiny), others near-zero in window.
  - Long-tail cost center: `agentic_web_research` (34 req / 21 resp but only 14 canonical `agentic.research.completed`, 10 errors, 150s+ p95 tails, upstream-dominated in traces: NanoGPT 503s, Semantic Scholar 429s, Jina rerank 403s, HF embeds ~5s).
  - "Evolving toward richer research flows, not just simple search." Core grammar (search → fetch before synth) is documented but not consistently followed.
- **Latency hotspots (rerank + rewrite focus validated)**:
  - `query.rewrite.completed`: 92 events, 12.06s avg / 30.5s p95 / 39s max.
  - `search.rerank.summary`: 155 events (100% voyage in window), 6.62s avg / 18.18s p95 / 51s max.
  - Provider search: 5.26s avg / 17s p95.
  - Cache lookup: 3.19s avg / 11.2s p95 (painful on misses).
  - Agentic: ~27s avg / 131s p95.
  - Remote content fetch dominates get_content traces (local pipeline fast; actual HTTP/browser is the user-visible slow part).
- **Cache**: Cold in the window (95 lookups → 93 miss / 2 expired; 44 stores; **0 hits observed**). Lookups add cost with no amortization. Deep review adds latent correctness issues (page_cache keyed only by URL; domain_boost/block + strip_selectors etc. not part of identity; semantic always pays embed before lookup).
- **Providers**: Healthy diversity on search side (searxng/ddg dominant, composio_llm_search + gemini substantial). Rerank = single-provider (voyage) systemic risk. Health events operational (successes > cooldowns).
- **Errors**: No broad recurring Loki pattern (good). Clustered on agentic (RuntimeError + InternalServerError on two specific desktop PIDs/process instances) → likely stale runtime/hygiene or test-influenced, not platform outage. One perplexity timeout. "Concentrated, not diffuse."
- **Instrumentation & stack quality** (stack review): Good breadth and real structure revealed (local vs upstream distinction strong; remote is the bottleneck). Weaknesses: semantic duplication (agentic response vs completed), cache activity ≠ value/effectiveness, under-instrumented simple runs, soft INFO severity for tool.*.error, fragile canned investigation paths (`find_slow_requests` failed — direct Tempo worked), too much manual stitching of traces + events.
- **Representative traces**: Successful search with partial-failure recovery (40 results → 6 post-rerank); get_content (remote HTTP GET 7.83s dominates 40s root); slow but valid long agentic with sources/KG.

**Deep review cross-validation** (134 files + external MCP baselines): Architecture ambitious and strong for a personal research MCP (multi-provider RRF, precision policy, specialized ladder, analytics). Biggest quick wins called out: cache identity bugs (P0 correctness — silent wrong results), Gemini config, tool profiles + cheap `search_status` tool, eval harness (gold YAML + MRR/nDCG/freshness/provider-marginal/extraction/p95 + LLM-judge), deep rerank opt-in, tracing span fix in content, agentic evidence-pack + post-success recording. "8/10 personal research MCP"; "fastest improvements are not more providers, but tighter cache... and simplifying the agent-facing interface."

**Evidence directly on the two focus areas**:
- Rerank: 155 events, voyage-only, exact p95 numbers + traces; single-provider exposure; snippet-only formatting in `rerank/core.py:204`.
- Client tool strategy: Tool volume skew + low perplexity despite instructions + middleware + "richer flows" evolution; deep review explicit callout on clients choosing expensive paths early + surface size; existing steering is a strong foundation but insufficient per symptoms + universal practitioner reports.

**Critical review of sources**: High quality and complementary. 48h + stack + action = operational data + hygiene checklist. Deep review = static code + external calibration + preventive correctness/UX. Strong alignment on hotspots and many recs. Gaps in sources: under-emphasis on "rerank inputs = only short snippets" (makes latency more salient for this workload) and latent cache *correctness* bugs (0 hits in window made them invisible to obs data); limited objective "value" measurement (did expensive stage improve outcome? did agent follow guidance?); no full gold-set harness yet (analytics scaffolding exists and is usable).

---

## 2. Focus Area 1: High Reranking Latency – Diagnosis & Detailed GCS Custom Solution

**Current state (validated from 48h + code)**: Primary `KINDLY_RERANK_PROVIDER=voyage` (model "rerank-2.5"), Jina v3 fallback already implemented (httpx to `/v1/rerank`, score normalization, recency + MMR post-processing). Stage 2 cross-encoder sits after bi-encoder prefilter (HF embeddings, when candidate set > top_k*2) and before recency/MMR. Inputs formatted as short structured blocks in `rerank/core.py:204` ("Title: ...\nURL: ...\nSnippet: ..."). Gated by `KINDLY_RERANKING_ENABLED`. Telemetry per-stage + summary. Graceful fallback on errors. 6.62s avg / 18.18s p95 (all voyage in window) contributes to user-visible tails even on healthy upstream providers. Jina also reported slow in real use by the user.

**Why public APIs are slow for *this* workload** (short, noisy web title+snippet candidates, 20-100 per rerank call, total tokens per batch often only low hundreds):
- Shared multi-tenant queues + load variance (high p95 tails).
- Rate limits and token billing (Voyage tier-1 ~2000 RPM/8M TPM rerank; Jina base tiers lower; backoffs common).
- Per-request caps + truncation + latency-sensitive recommendations.
- Network + auth + their-side batching overhead.
- No dedicated capacity.

**Reranker Models & Libraries for Web-Search Snippet Workload (Benchmarks + Tradeoffs, Post-Research)**: Inputs are short noisy web snippets (title + url + 20-200 tok snippet) — MS MARCO / BEIR passage style, N=20-100 per call after bi prefilter. Public APIs slow (queues/rates/variance); custom self-host (CPU fast-path or Cloud Run GPU) for predictability/privacy. **All recs below are caveated by recurring practitioner evidence: "rerankers aren't magic"; "test on your actual data"; cases exist where rerank adds latency with low/negative lift or hurts vs strong embed alone or no rerank.**

**Models (nDCG@10 BEIR/MTEB + notes for short noisy web + coding utility)**:
- jina-reranker-v3 (0.6B, listwise "last but not late interaction"): **61.94** BEIR (highest among compared; +4.8% over v2). Excels multi-hop (Hotpot 78+), fact (FEVER 94+), MIRACL multi-lang, CoIR code. Good for relative ordering 20-64 docs. [web:0][web:8]
- BAAI/bge-reranker-v2-m3 (568M, XLM-RoBERTa, multi): ~56.5-57 BEIR / ~0.55 MTEB. "Lightweight... fast inference, easy to deploy" (HF). Balanced but grids show can regress vs smaller on lexical traps. [web:1][web:5]
- mxbai-rerank-base/large: Competitive on subsets; xsmall variant slight worse than no-rerank baseline in Amazon reviews bench (within noise). [web:12][web:34]
- cross-encoder/ms-marco-MiniLM-L-6/12-v2 (22-33M): MS MARCO web/passages trained, tiny, blazing CPU. Sometimes *beats* larger rerankers on canonical "answer vs procedural/topical" cases. [web:2][web:5]
- Ettin (17M-1B ModernBERT, distilled from mxbai-large teacher): 17M +0.051 nDCG over MiniLM-L12 on MTEB at ~half params + high throughput (7k+ pairs/s H100 tiny); 32M beats 568M bge-v2-m3. Strong for speed/acc. [web:5]

Voyage rerank-2 claims +2.8-14% over bge-v2-m3 depending on first stage. [web:11]

**Libs (speed for N~50 short + compat models + Cloud Run/deploy + our integration)**:
- **FlashRank**: ~4MB TinyBERT default / ~34MB MiniLM. **No Torch/Transformers** (ONNX). CPU-only, "super-fast" (tokens + layers). Practitioner reports: ~30ms typical; "cheap insurance"; "matches bge-large at 10x speed" claims. "Detailed benchmarking, TBD" in repo but used in RAG + papers (competitive on some IR tasks). "rerankers" lib wraps for unified API. **Highest-ROI for CPU fast-path / light default rerank**. Low mem, instant cold-start, serverless/Cloud Run friendly (pure py). Easy local integrate (pip + Ranker.rerank). Acc varies by dataset (not SOTA every time). [web:15][web:17][web:20][post:3]
- **rerankers (AnswerDotAI)**: Unifies FlashRank (ONNX CPU), sentence-transformers CrossEncoder, public APIs (Cohere/Jina/Voyage etc). `Reranker('flashrank')` or cross. Simple switch + A/B. [web:38][web:41]
- **TEI (huggingface/text-embeddings-inference)**: Rust, **first-class cross-encoder/reranker** (bge/jina/ms-marco via --model-id; Flash Attention, dynamic batch, OTel/Prom). Prebuilt CPU (`:cpu-1.9`) + CUDA ghcr images ready for Cloud Run. Targets <500ms p95 (50 short) on CPU 2-4vCPU; <<100ms on L4. GPU ~800 pairs/s bge batch. **Best for dedicated predictable self-host**. Client + settings + dispatch *already wired* (gcp_cloudrun provider). [GCS guide + TEI Cloud Run patterns]
- sentence-transformers CrossEncoder + FlagEmbedding (BGE): Simple baseline or BGE-native (FP16/ONNX/quant/layerwise). Good prototype; slower than TEI/Rust/ONNX without tuning.

**Realistic latency (N=20-50 short snippets)**: FlashRank ~30ms CPU (practitioner X); light cross-encoder +31ms mean overhead (real 480-query RAG trace, statistically ns in ~10s query) [web:21]; TEI CPU <500ms p95 target / GPU tens of ms; public APIs 100s ms–seconds + variance. Two-stage (bi + light) often 95% quality of deep at ~1/3 time. [web:26][web:33]

**Recommended models (light but high-quality for web snippets / BEIR-MS MARCO style)**:
- **Primary**: `BAAI/bge-reranker-v2-m3` (or base) — ~568M params, multilingual, strong BEIR, "lightweight... easy to deploy, fast inference" (HF card). TEI-optimized first-class. ~2.5GB.
- **Strong alternative**: `jinaai/jina-reranker-v2-base-multilingual` (or v3 distilled) — excellent multilingual + speed/quality reports; TEI added support.
- **Fastest CPU / dev start**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (or L-12) — tiny (22-33M params, <<1GB), blazing on CPU for web-like ranking (MS MARCO trained). Ideal for low QPS predictable latency. Upgrade path to bge for quality/multilingual.
- Quant/CPU focus: TEI `--dtype float16` (or int8 where supported); custom ONNX INT8 via optimum/sentence-transformers or FlagEmbedding FP16 for bge.



**Why this workload fits custom self-host (with caveats)**: Short noisy snippets are exactly cross-encoder training/eval regime (MS MARCO / BEIR passages). Bi prefilter + MMR already prune/diversify; Stage 2 only re-orders small set. But "rerankers aren't magic" — grids show lift not guaranteed (sometimes zero or negative vs strong embed or no rerank); "test on your actual data" is the universal practitioner lesson from TDS, X, benches, and "Latency Myth" posts. Custom (FlashRank CPU light or TEI Cloud Run) gives predictability/privacy vs public queues, but only justified where evidence shows net win on *our* queries + coding usefulness.

**Open Questions & Tradeoffs (explicitly surfaced via targeted searches for "hurt", "bypass", "test on your data", FlashRank/TEI/Cloud Run latency, short snippets vs long docs)**:
- Does rerank on short noisy web title+url+snippet *ever "hurt" or "make worse"* (low/negative lift for downstream coding agent value)? Yes — multiple sources: mxbai xsmall slight worse than baseline (noise level); BGE-small without rerank outperformed reranked setups in team tests; "added latency but rarely beat plain embeddings on the tricky queries"; "reranking isn’t always the silver bullet... skipping it entirely can lead to better performance with lower latency"; Voyage notes bge can make *worse* than no-rerank on some first-stages. "A reranker is not automatically beneficial. Test it on your actual data before deploying." [web:30][web:34][TDS full grids + analysis]
- When to bypass vs pay for deep? When upstream already strong (text-embedding-3-large or scoped keywords/GLiNER/classify), candidate pool small (<20-30), or query shape = negation / listing / exact identifier / out-of-domain vocab (TDS: rerankers frequently no lift or hurt vs embed alone on those exact cases from prior article). Highest reliable lift on "signal dilution" (buried answer in long para). For our 48h symptoms (rerank p95 hotspot + "value unproven" gap + 0 cache hits): *measure actual relevance/usefulness_for_coding lift* (LLM-judge + gold harness on DuckDB 48h + live probes) before paying the p95 cost or claiming win.
- Latency vs accuracy Pareto specifically for noisy title+url+20-200tok snippets (our case) vs long docs? Short snippets favor tiny/fast (FlashRank/MiniLM/Ettin 17-32M) more than long-ctx listwise SOTA (jina-v3). Few/no public end-to-end p95 + coding-agent "actionability" numbers on *exactly* our short web format + N=20-50.
- Real prod numbers for FlashRank/TEI/Flag on similar RAG/web self-host/Cloud Run? FlashRank: ~30ms cited in X/practitioner, +31ms mean overhead "negligible" in one 480-query CPU RAG trace [web:21]; TEI: targets + GPU throughput in HF/Cloud Run guides, but sparse *our exact short noisy web* Cloud Run L4/CPU p95s. "Test on your data" theme everywhere.
- Quant/ONNX/Flash Attention impact + cold-start/cost on Cloud Run (L4 vs CPU, min-instances)? ONNX/quant big wins for CPU FlashRank/Flag (size + speed, low cold); TEI handles --dtype/FA well in images. Cold-start: min-instances=1 + pre-cache in build (idle rates low ~10x cheaper); GPU L4 quota is real gate for <<100ms. Cost at dev low QPS: negligible vs public per-token.
- "Rerank ever not worth the latency for simple queries (our policy bypass cases)?" Yes — combine with existing query_policy (bypass/light/deep) + new fast local path. Marginal dollar often better spent on embed stage or upstream (expert keywords, classify-before-retrieve, GLiNER entities per active plans) than rerank layer.

**Supported Recommendations (evidence-based refinement of prior TEI focus)**:
1. **Add local ultra-light CPU fast-path (FlashRank or via rerankers[flashrank]) as P0/quick win for the latency hotspot + "many rerank events" symptom**. `KINDLY_RERANK_PROVIDER=flashrank`. ~30ms CPU, $0 marginal, low risk (tiny model), easy to wire (new `rerank/flashrank.py` shim or direct lib call returning list[(idx, float)] tuples matching existing contract). Use as default or policy-driven "light" for simple/precision intents; always-on "cheap insurance" against opposite-meaning or low-signal results. Update core.py dispatch, settings, telemetry (provider="flashrank").
2. **Retain + validate gcp_cloudrun + TEI for deep/quality-critical paths** (when harness shows clear lift > p95 cost). Client, settings (KINDLY_RERANK_GCP_*), dispatch in core.py, and full deploy guide (`plans/GCS-Custom-Reranker-Deployment-Guide.md`) *already exist* from prior subagent work. Use bge-v2-m3 / jina-v3 (or Ettin/MiniLM for speed) in TEI image. min-instances=1, --no-allow-unauthenticated + IAM invoker, OTel. Targets: <500ms p95 CPU (2-4vCPU) or <<100ms L4 for 50 short. Low cost at personal/dev QPS.
3. **Hybrid pipeline + fast-paths (keep bi + MMR; add light + optional deep)**: bi prefilter + FlashRank light (default) + optional TEI "deep" (low confidence, research policy, or explicit). Early-exit on score delta/threshold. Rerank-result caching (query + candidate fingerprint, not just URL). Aggressive upstream caps. Always per-provider timing in DuckDB/OTel.
4. **Experiments / gold harness / LLM-judge *before any default or provider flip* (mandatory, per "test on your data" + obs "value unproven")**: Extend `analytics/evals.py` + new `llm_judge.py` (or Flock SQL on DuckDB events). Gold YAML cases (exact error, docs, freshness, GH/SO, academic, known-URL, multi-provider) + MRR/nDCG@ k / survival / provider-marginal / p95 + LLM-judge (relevance, usefulness_for_coding, coding_signal, faithfulness). Run on 48h data + live probes before/after. Compare relevance@5, compliance rate ("web_search + get_content before synth?"), end-to-end quality, p95. DuckDB queries for per-provider rerank.summary + join to vw_candidate_survival. Only promote to default after measurable net win + no regressions on tricky shapes.
5. **Surface + config updates**: Extend `settings.rerank_provider` + docs (flashrank | gcp_cloudrun | voyage | jina). Add KINDLY_RERANK_* as needed. Keep Jina/Voyage as burst/high-availability fallback. Update CONFIG/ARCHITECTURE/DEVELOPMENT + tests (mock under kindly... namespace).
6. **Synergies with other work**: LLM-judge (§5) directly answers "did this expensive rerank stage improve coding usefulness or just add latency/tokens?". gliner (active plans) for entity signals → better candidates (less rerank cost or early exit), rerank features (entity overlap), cache guardrails, richer judge/steering. GraphQL tuning + playright for fetch tails (real user-visible slow in get_content, not rerank itself).

**Risks / Trade-offs (honest)**: Quality regression on switch (harness + judge first, A/B); ops burden (low with TEI images + min-instances, but quota for L4); "rerank will save us" instead of fixing upstream (keywords, policy, classify, GLiNER). Public Jina/Cohere/Voyage remain good short-term fallback or burst. Over-steering / measurement tax if every change requires full harness run.

The GCS/TEI path (exact gcloud, client in rerank/gcp_cloudrun.py, IAM, monitoring, validation DuckDB steps) remains the detailed self-host *implementation* guide. Research (BEIR numbers, FlashRank real ~30ms + "not magic" cases, TDS grids, X 30ms/100ms, "test on data" theme) refines *priority and caveats*: **FlashRank CPU light-path is the highest-leverage first change for the observed 155-event / 18s p95 symptom and "value" gap**; TEI/gcp for dedicated capacity *when* evidence (harness) justifies. Update the GCS guide with the new tables/open questions. No Exa or Done/ plans used.

See §5 (LLM-as-Judge for rerank value + periodic DuckDB/Grafana/Langfuse), §6 (active plans value: gliner for candidate/rerank/judge quality, GraphQL for GitHub fetch tails that dominate get_content traces, playright for remote content latency). Experiments before code.

**GCP / Cloud Run deployment (TEI preferred — zero custom server code)**:
Exact commands (from HF official Cloud Run guide + research; adapt PROJECT/REGION/MODEL):

```bash
export PROJECT_ID=your-project
export REGION=us-central1
export SERVICE_NAME=kindly-reranker
export MODEL_ID="BAAI/bge-reranker-v2-m3"   # or "cross-encoder/ms-marco-MiniLM-L-6-v2" for fastest CPU

gcloud services enable run.googleapis.com

# CPU (cheap, sufficient for targets, min-instances for warm)
gcloud run deploy $SERVICE_NAME \
  --image=ghcr.io/huggingface/text-embeddings-inference:cpu-1.9 \
  --args="--model-id=${MODEL_ID},--max-batch-tokens=8192,--max-concurrent-requests=32,--port=8080" \
  --set-env-vars="HF_HUB_ENABLE_HF_TRANSFER=1" \
  --cpu=2 --memory=2Gi --min-instances=1 --max-instances=5 --concurrency=32 \
  --port=8080 --region=$REGION --no-allow-unauthenticated

# GPU (L4 for <<100ms p95; request "Total Nvidia L4" quota)
gcloud run deploy $SERVICE_NAME \
  --image=... (cuda variant or appropriate) \
  --args="..." \
  --cpu=8 --memory=32Gi --min-instances=1 \
  --no-cpu-throttling --gpu=1 --gpu-type=nvidia-l4 \
  --region=$REGION --no-allow-unauthenticated
```

Get URL:
```bash
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)')
```

**Custom FastAPI alternative** (if you prefer Python/ONNX control): Multi-stage Dockerfile that pre-downloads the model at build (`RUN python -c "from FlagEmbedding import FlagReranker; ..."` or sentence-transformers), FastAPI `/rerank` accepting `{"query": "...", "texts": [...]}` or Jina-style dicts, returning `{"results": [{"index": , "relevance_score": }]}` or list of (index, score). Deploy with `--source .`.

**Client** (`src/kindly_web_search_mcp_server/rerank/gcp_cloudrun.py` — added by the GCS subagent during research; verified import + ruff clean):
- Async, uses existing `retry_with_backoff`.
- `_normalize_documents` (str or {"title","snippet","url"} → "Title: ...\nURL: ...\nSnippet: ...").
- Flexible `_parse_rerank_results` (handles direct list, {"results": [...]}, {"data": [...]}, "relevance_score" / "score" / "relevance").
- Auth: static `KINDLY_RERANK_GCP_AUTH_TOKEN` (Bearer) **or** automatic Google ID token for private Cloud Run audience (via `google-auth`, lazy import, ADC / SA / metadata server).
- Payload: `{"query": , "texts": [...]}` (TEI standard); optional `top_n`.
- Errors bubble for core fallback logic.
- Matches `voyage_rerank` / `jina_rerank` return shape `list[tuple[int, float]]`.

**Integration** (already wired by the subagent):
- `rerank/__init__.py` exports `gcp_cloudrun_rerank`.
- `settings.py`: `rerank_gcp_cloudrun_url`, `rerank_gcp_model` (default bge-v2-m3), `rerank_gcp_timeout`.
- `rerank/core.py`: added to allowed stage2 providers, dispatch `elif stage2_provider == "gcp_cloudrun"`, model selection, telemetry (provider="gcp_cloudrun" flows through automatically). Stage 1 (bi) + Stage 3 (MMR/recency) + normalization untouched.
- Env: `KINDLY_RERANK_PROVIDER=gcp_cloudrun`, `KINDLY_RERANK_GCP_CLOUDRUN_URL=...`, optional `KINDLY_RERANK_GCP_AUTH_TOKEN` or ADC for IAM.

**Cold starts, cost, monitoring, security, validation**:
- Cold start: `min-instances=1` (primary), model pre-download in image (TEI HF_TRANSFER or custom build), small model choice, 2-4 vCPU / 1-4Gi, startup probe.
- Cost (Tier 1 request-based, approx): min=1 idle cheap (idle rates low; free tier covers much for low QPS). Active use + requests very low. GPU L4 dedicated higher — use only if you need the latency and have quota. Far more predictable/cheaper than public per-token at volume.
- Monitoring: Existing `record_rerank_stage` + OTel spans ("rerank.gcp_cloudrun") + DuckDB analytics. TEI exposes `/metrics` (Prometheus) + `--otlp-endpoint`.
- Security: `--no-allow-unauthenticated` + IAM `run.invoker` (only your SA or the MCP service account). No public. ADC/SA for client.
- Validation/A/B: Flip env + URL. Run `scripts/live_web_search_probe_lib.py` (or equivalent) before/after; capture jsonl. DuckDB: `SELECT provider, PERCENTILE_CONT(0.95) ... FROM ... WHERE event LIKE '%rerank.summary%' GROUP BY provider`. Join candidate views for quality lift (e.g. gold doc position, survival). Micro: direct calls with realistic 50-doc payloads. Add to model_stats / live checks. Compare end-to-end p95 + max_rerank_score / final counts.

**Step-by-step deploy + enable**: See the full self-contained guide at `plans/GCS-Custom-Reranker-Deployment-Guide.md` (produced by the dedicated subagent) for Dockerfile examples, curl/Python test snippets, exact IAM steps, proxy for local testing, cleanup, and more DuckDB queries.

**Fast paths & other quick wins** (in orchestrator + query_policy): Skip rerank or use ultra-light local for simple/precision intents (builds on existing policy bypass/expand); rerank-result caching (exact or semantic on query + candidate hash); aggressive upstream caps + early-exit on score delta/threshold; always instrument per-provider timings.

**Risks / trade-offs**: Quality regression on switch (measure with eval harness + LLM-judge first); self-host cold starts (mitigated); ops burden (low with TEI); token caps on public for very large candidate lists (bi-encoder already prunes). Public Jina/Cohere remain good short-term fast fallback or for burst.

This is the researched, benchmark-backed, open-question-aware recommendation (public APIs slow; FlashRank CPU light-path highest-ROI quick win for latency + "test on your data" reality from multiple sources; TEI/gcp self-host for deep when harness proves value; experiments mandatory before changes). Both Voyage and Jina slow in the 48h + user reports; custom is the path, but model/lib choice + measurement first.

---

## 3. Focus Area 2: Strategic Tool Use by Coding Agents + "Many Tools Not Used"

**Data (48h + server.py analysis)**: Core discovery + fetch dominant and healthy. Long tail of synthesis, niche, video, analytics, and the recently-added agentic lane see little or no organic production traffic (agentic volume largely test-driven in the window per user). ~17 `@mcp.tool` registrations (some conditional) + 3 resources + 4 prompts + conditional register_* for composio/agentic/analytics. Surface described as "cognitively heavy" in deep review.

**Why this is a general MCP problem** (practitioner evidence from subagent): LLMs ignore long prose descriptions under load. Full `tools/list` burns 20-50%+ of context for rich servers. Client caps (e.g. Cursor ~40 tools), poor selection, context cliffs. Real cases: 300+ tool server with ~97% stubs = 5-10k token overhead + degraded selection; 45-tool balancing act at Open Targets; arXiv empirical study showing discovery/registration/config/execution as dominant fault categories. Consensus: "fewer tools === better"; max ~10-20 practical for most agents; progressive disclosure + structural signals win over more docstrings.

**Project already has a strong foundation** (no need to start from zero):
- Per-tool "Key instruction / When to use / research_goal required + examples".
- 4 actionable prompts (`plan_web_research` etc.).
- Resources: `status://providers`, `status://features`, `docs://workflow` (detailed routing + steps + examples).
- `DynamicGuidanceMiddleware` + `gemini_advisory` (injects `agent_guidance` + `suggested_next_tools` + `suggested_prompts` into structured results for core tools; result-aware).
- `ExpensiveToolProtectionMiddleware` (first-attempt block on perplexity/grok with steering message + SessionTracker; allows retry).
- Differentiated rate limits (cheap discovery/fetch vs expensive synthesis).
- `Context` + `report_progress` / `info` in long paths.
- ToolAnnotations on many.
- Analytics (DuckDB events, OTel, Langfuse for agentic, Grafana) for measurement.
- Explicit discovery/fetch/synthesis contract in instructions.

**Gaps vs external best practice + observed symptoms**: No server-level profiles/visibility gating (`KINDLY_TOOL_PROFILE=minimal|core|research|full`). No `PromptsAsTools`/`ResourcesAsTools`/`ToolSearch` transforms (homegrown mcp_compat.py exists but unused and bypasses some middleware). Tags only on prompts. No `search_status()` cheap tool. Chaining hints and error recovery are partial/enhanced but not fully systematic. Low-usage signals visible in data but not yet auto-detected/hidden.

**FastMCP 3.x+ advanced capabilities that directly solve this** (project already resolves 3.2.4; research via web_fetch on gofastmcp.com + llms.txt + installed source validation):

**Visibility + Profiles (server mcp.enable/disable by keys/tags; per-session via ctx)**: Perfect for `KINDLY_TOOL_PROFILE`. Tags additive (any match). Per-session unlock (progressive disclosure). Notifications on list_changed.

**Transforms (composable)**:
- `ToolSearch` (BM25 or regex; `search_tools` + `call_tool` replaces exhaustive list; `always_visible` pins for core primitives; respects visibility).
- `PromptsAsTools` + `ResourcesAsTools`: Make the 4 planning prompts + status/docs workflow visible as tools (routes through full middleware/visibility chain). Critical for tool-only clients (Cursor/Claude often surface tools more reliably than prompts/resources).
- Namespace, ToolTransformation for reshape/rename/hide args.

**CodeMode (experimental v3.1+ "Code to Joy"; requires `fastmcp[code-mode]` extra for MontySandboxProvider/pydantic-monty) + full advanced transforms/visibility in-depth (primary sources: gofastmcp.com fetched docs + llms.txt + Cloudflare "Code Mode" blogs 2025/2026 + subagent + installed 3.2.4 source)**: Replaces exhaustive catalog with 2-3 meta-tools for on-demand discovery + sandboxed orchestration. LLMs write small Python (or in CF JS/TS) using only `await call_tool(name, params)` + return; intermediates stay in sandbox, only final result returns to LLM context. `call_tool` inside proxy hits *full* server pipeline (our middleware/steering/visibility/transforms/telemetry/query_policy/resolvers — zero diff).

**Exact mechanics (from gofastmcp.com/servers/transforms/code-mode + Cloudflare primary)**: Default 3-stage (configurable): 1. `search(query, tags?, detail=brief|detailed|full, limit?)` (BM25, annotates "N of M", tags filter); 2. `get_schema(tools, detail=...)` (params md or full JSON); 3. `execute(code)` (MontySandbox / V8 isolate, limits max_duration_secs/memory/allocations/recursion; default ~30s/100MB baseline, tunable; no direct FS/net/env — only injected call_tool). Discovery tools composable (add GetTags() for category browse; ListTools for small catalogs; custom factories). Detail levels control token cost vs roundtrips (brief=names+desc cheapest; detailed=inline params md often enough to code; full=JSON). `max_tool_calls` default 50 (tunable/None). execute_description override for seeding examples + "search('fetch') or use tags".

**Cloudflare primary evidence (99.9% real prod for 2500+ endpoint API; two blogs fetched)**: "LLMs are better at writing code to call MCP than at calling MCP directly" (enormous TS/JS in training vs contrived tool-call synthetic data). For full Cloudflare API (DNS/Zero Trust/Workers/R2/... 2500+ endpoints): traditional MCP would be 1.17M tokens (context cliff); Code Mode = 2 tools (search + execute) + ~1,000 tokens *fixed* footprint regardless of surface size. Agent uses `search()` (writes JS against typed OpenAPI spec in sandbox to filter by product/path/tag) then `execute()` (writes code that chains real authenticated calls, handles pagination, inspects responses, does control flow — all in one isolate execution; only final data back). V8 Dynamic Worker Loader (ms start, disposable per-execute, cheap, no containers; bindings for safe no-key-leak access to MCP; outbound controlled). Implemented server-side in their MCP (OAuth 2.1 downscope), client-side in Agents SDK (also Goose + Anthropic Claude SDK as "Programmatic Tool Calling"). 99.9% reduction; 81% in one complex batch task comparison; fixed cost solves "hundreds of tools = tens of k tokens upfront". [web:5][web:6 from CF fetches]

**Why CodeMode + advanced features shine (and how they solve the *exact* obs symptoms: "many tools not used at all", long-tail low-volume synthesis/niche/agentic mostly test-driven, clients not following "search→fetch before synth" grammar despite rich middleware/guidance)**: 
- **Hides the tail structurally** (no flat 16-17 tool list for agents to hyperfocus on unused agentic/synth/composio/analytics; default "core" via profiles/visibility shows only discovery+fetch). 
- **Forces *active* strategic discovery** (must `search("fetch")` or `search("synthesis expensive")` or browse tags — teaches grammar; no passive overload).
- **One execute = complex orchestration without context bloat** (fanout N web_search results → conditional batch_get_content on github ones → custom merge/dedup by provider_count/domain → return table or evidence pack; loops/conditionals inside Python, not N LLM turns + full intermediate results in context. Matches our "separation is intentional: search discovers, fetch extracts, AI search synthesizes" + agentic as budgeted last resort).
- **Steering still applies for free** (call_tool proxy = full DynamicGuidanceMiddleware suggested_next + usage hints + ExpensiveToolProtection + rates + SessionTracker + query_policy + telemetry; existing investment leveraged inside sandbox).
- **Measurement hooks** (telemetry already captures inside execute; new LLM-judge compliance "did web_search + get_content before any synthesis?" works on sandboxed runs too; "effective surface" = tools actually discovered/called vs exposed).
- Lighter features (visibility/profiles via KINDLY_TOOL_PROFILE + tags + mcp.disable/enable or Visibility transform; ToolSearch BM25 with always_visible pins for core; PromptsAsTools/ResourcesAsTools to surface the 4 planning prompts + docs://workflow as tools for Cursor/Claude tool-only clients) are universal (no Python bias, zero codegen cost, work for any client) and should be default/on always. CodeMode is *opt-in power-user* for coding agents (Cursor/Claude Code) who treat the MCP as "write a mini research script using primitives".
- Other FastMCP (Sampling for server-initiated LLM in long ops; Elicitation sparingly for expensive confirmation; Tasks for background agentic; ResponseLimiting built-in excellent for page_content/markdown; Context report_progress already partially used — expand). Transforms compose after registers; middleware (our custom + built-in) on execution path.

**Current project state (grep/read server.py:148 mcp=FastMCP, add_middleware x3 for expensive/guidance/rate, no add_transform, tags *only* on 4 prompts, ~17 @mcp.tool some conditional, rich instructions + resources + DynamicGuidance injecting suggested_next/prompts into results, mcp_compat.py unused homegrown)**: Strong custom steering foundation (matches "already uses several well" from steering plan) but no native profiles/visibility/transforms/CodeMode. Surface still "cognitively heavy" per deep review + 48h skew (core dominant; long-tail synth/agentic low/test-only volume; 34 agentic calls but only 14 canonical completed + 10 errors + 150s+ p95). "Intended grammar too implicit."

**Concrete example of what a coding agent would write inside the sandbox for *this* server** (Python; call_tool hits full stack):
```python
# Agent (in execute) discovers then orchestrates
results = await call_tool("web_search", {
    "query": "fastapi lifespan middleware error handling",
    "research_goal": "debug production 500 on startup",
    "num_results": 8,
    "rewrite": True
})
urls = [r["link"] for r in results.get("results", []) if r.get("provider_count", 0) >= 1][:5]
# conditional + batch for efficiency
if any("github.com" in u or "stackoverflow.com" in u for u in urls):
    contents = await call_tool("batch_get_content", {
        "urls": urls,
        "total_char_budget": 18000,
        "per_doc_char_budget": 4000
    })
# custom merge or evidence pack (no N context turns)
return {
    "query": "...",
    "top_sources": [c["url"] for c in contents.get("results", [])],
    "evidence": "see fetched markdown for lifespan example + middleware order note"
}
```
One execute → full workflow + control flow. Intermediates (raw results) never bloat LLM context. Guidance from our middleware enriches the dicts the Python sees.

**Layering best practice (profiles/transforms first; CodeMode opt-in)**: 1. KINDLY_TOOL_PROFILE (default "core" = web_search/get_content*/discover_links/academic/youtube; "research" adds synth; "full" adds agentic/analytics/composio) + tags on @mcp.tool + Visibility or disable. 2. PromptsAsTools + ResourcesAsTools (makes plan_* + docs://workflow visible to tool-only clients; routes through middleware). 3. BM25SearchTransform (always_visible pins core primitives; max_results small). 4. *Opt-in* CodeMode (env KINDLY_ENABLE_CODE_MODE or separate entry; guarded import; sandbox limits tuned to our _resolve_tool_total_timeout + browser headroom ~120s+; execute_description seeded with "core primitives + search('synthesis expensive' or tags) + example above"; GetTags+Search(detailed)+GetSchemas). Middleware/ResponseLimiting always on top. Matches steering plan ("CodeMode opt-in only... philosophy favors external agent-controlled") + CF (server-side for huge surfaces; client-side for apps).

**Practical wiring (after all @mcp.tool / prompt / resource registrations + existing 3 middlewares in server.py)**:
```python
# settings.py additions
KINDLY_TOOL_PROFILE: str = os.environ.get("KINDLY_TOOL_PROFILE", "core")  # minimal|core|research|full
KINDLY_ENABLE_CODE_MODE: bool = os.environ.get("KINDLY_ENABLE_CODE_MODE", "false").lower() == "true"

# server.py (guarded; after imports + mcp = FastMCP(...) + add_middleware x3)
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools, BM25SearchTransform
try:
    from fastmcp.experimental.transforms.code_mode import CodeMode, Search as CodeSearch, GetSchemas, GetTags, MontySandboxProvider
    CODE_MODE_AVAILABLE = True
except Exception:
    CODE_MODE_AVAILABLE = False

# ... all @mcp.tool(..., tags={"search", "discovery", "core", "read-only"}) etc.
# get_content: {"content", "extraction", "follow-up", "core"}
# gemini/perplexity/grok: {"synthesis", "grounded" or "expensive"}
# agentic_web_research: {"agentic", "orchestration", "last-resort"}
# analytics_*: {"observability", "internal"}
# ... (add to all; resources/prompts if supported)

# after last registration
mcp.add_transform(PromptsAsTools(mcp))
mcp.add_transform(ResourcesAsTools(mcp))

always_visible = ["web_search", "get_content", "batch_get_content", "discover_links", "search_status"]
mcp.add_transform(BM25SearchTransform(max_results=20, always_visible=always_visible))

if settings.KINDLY_ENABLE_CODE_MODE and CODE_MODE_AVAILABLE:
    sandbox = MontySandboxProvider(limits={
        "max_duration_secs": 180,  # align to our tool + browser timeouts
        "max_memory": 256_000_000,
    })
    code_mode = CodeMode(
        discovery_tools=[GetTags(), CodeSearch(default_detail="detailed", default_limit=8), GetSchemas()],
        sandbox_provider=sandbox,
        # execute_description= "Core: web_search (rewrite=true default) → get_content/batch... Use search('fetch') or tags. Example: ..."
    )
    mcp.add_transform(code_mode)

# profile/visibility hook (on_list_tools or startup; mcp.disable(tags={"synthesis-heavy", "expensive"}) based on profile)
```

Add cheap `search_status()` tool (exposes profile, active transforms, providers, "followed steering?" hints). Enhance docs://workflow + instructions with "CodeMode users: write orchestrations over primitives". Update tests (IsolatedAsyncioTestCase, patch under kindly...*, verify list_tools small, execute calls primitives + middleware fires, visibility per profile).

**How the combo directly solves the observed problems (48h skew + deep review "cognitively heavy" + "clients choose expensive early" + "many not used")**: Visibility/profiles limit *what is visible* (agent cannot hyperfocus on unused/expensive/agentic if disabled in "core"). ToolSearch/CodeMode make discovery *active and intentional* (must search "fetch" or "synthesis expensive" — forces focus, teaches grammar, no passive 16-tool list). Middleware + result-level hints (already rich) + PromptsAsTools + per-session unlock + progress teach/steer *strategically after* discovery. Telemetry + new LLM-judge (compliance %) close the loop. Matches CF 99.9% real reduction + practitioner post-mortems (300+ tool stub bloat, "fewer tools === better", max ~10-20 practical). One rich server with gating > premature splits.

**Risks / trade-offs / gotchas (critical, with CF context)**: Experimental (docs: "core interface stable but discovery tools/params may evolve"; guard import + env flag + docs "opt-in, test thoroughly"; default OFF). Python bias (strong for our target coding agents Cursor/Claude Code who excel at small script gen/debug; weaker/general agents or non-Py models may emit bad code, wrong awaits, unhandled, infinite loops — profiles + ToolSearch are safer universal default). Sandbox limits bite on long browser fetches / many parallel (tune to existing timeouts; no direct net/fs — good for safety, all via controlled call_tool). Debugging different (errors surface as execute result; enrich with code in spans + guidance in results; no direct ctx.info inside user code). Overhead for simple 1-2 step (use profiles default direct). Over-steering risk (offer "full" profile; measure via telemetry before aggressive hiding). Migration additive/low (after registers). Security: isolated (pydantic-monty/V8), but prompt injection via generated code? Mitigated by no net/fs + our rate/expensive inside call_tool + OAuth downscope in CF model. "Not used" tools: if CodeMode, agent must discover them — seed execute_description + status resources + prompts-as-tools.

**Other FastMCP capabilities reviewed**: Sampling (server-initiated LLM for internal rewrite/cheap synth); Elicitation (sparingly for expensive path confirm); Tasks (background for very long agentic); ResponseLimiting (built-in, excellent for our large markdown outputs); Context (report_progress + info already partial — expand with stage info); strict_input_validation + list_page_size in ctor. Custom middleware patterns already mirror FastMCP on_call_tool / on_list_tools hooks.

**Risks summary**: CodeMode experimental + py bias (mit: guard, flag, profiles first, good seeding); sandbox limits (tune + monitor); debugging surface (enrich); measurement first before aggressive hiding.

Add tags, KINDLY_* , transforms wiring, search_status, tests, docs (ARCHITECTURE/CONFIG/DEVELOPMENT + AGENTS/CLAUDE examples of execute code), CHANGELOG. Order: profiles + AsTools + ToolSearch (P0, universal) → systematic hints/progress → CodeMode eval (opt-in, after telemetry shows need for dynamic chains).

This + visibility directly attacks the "many tools not used" + "hyperfocus on test-only agentic" + "not using strategically" per 48h + deep review. Builds on (does not replace) existing rich middleware/guidance. Low migration cost. Practitioner evidence (CF prod 99.9% for huge surface, Anthropic explorations, SEPs, HN/Reddit "fewer tools", real post-mortems) + our data supports layering lighter features first, CodeMode opt-in for power users.

**Critical applicability & layering best practice** (from dedicated CodeMode subagent + Cloudflare/Anthropic sources):
- **Shines for**: Dynamic/complex workflows (loops over results, conditional fetch based on signals, custom orchestration inside one call). Large/growing catalog with heavy-param tools (web_search alone has 15+ params). Token-sensitive clients or strict caps. Coding-agent users who treat the MCP as "write a mini research script using primitives".
- **Simple profiles win for**: Static subsets, zero codegen cost, universal (any client), predictable, fast to implement (tags + Visibility), good default for cost control.
- **Best together (recommended, aligns with steering plan + obs P0)**: 
  1. Profiles/Visibility (env-driven, default "core" = discovery + fetch only; "full" or "research" unlocks more).
  2. ToolSearch (BM25 + always_visible core pins) for on-demand even in "full".
  3. PromptsAsTools + ResourcesAsTools (makes workflow guidance visible to tool-only clients).
  4. *Opt-in* CodeMode (env `KINDLY_ENABLE_CODE_MODE=true` or separate entrypoint; **NOT default** per project philosophy of external agent-controlled orchestration + steering plan explicit guidance). Use GetTags + Search(detailed) + GetSchemas. Sandbox limits tuned to existing `_resolve_tool_total_timeout_seconds()` + browser headroom.
- Middleware always on top (ResponseLimitingMiddleware is built-in and excellent for large page_content/markdown outputs).
- Add `tags={"search", "discovery", "core", "read-only"}` etc. to `@mcp.tool` (and resources/prompts) for GetTags/Search/Visibility.
- Primitives for CodeMode: focus docs on "core discovery/fetch only"; seed `execute_description` with good example + "search('synthesis') or use tags for others".
- Integration is zero-diff for existing code: `call_tool` inside execute hits full `on_call_tool` + transforms + visibility + DynamicGuidance etc.

**Current project usage (confirmed via grep/read on server.py + middleware)**: Heavy custom middleware for steering (expensive protection + SessionTracker + ToolError on first heavy; DynamicGuidance injecting suggested next + prompts into results; differentiated rates; session tracking). Context used for info/progress in long tools. No transforms (no `add_transform`, no CodeMode/PromptsAsTools/etc.). Tags only on the 4 prompts. No Visibility gating. mcp_compat.py has unused homegrown prompt/resource→tool registration. ~16 tools + conditionals. Rich instructions + per-tool routing already present. Matches steering-plan assessment ("already uses several well" but "intended grammar too implicit" and surface still large).

**Practical wiring (after existing middleware + all @mcp.tool / @mcp.prompt / @mcp.resource registrations)**:
```python
# settings.py
KINDLY_TOOL_PROFILE: str = os.environ.get("KINDLY_TOOL_PROFILE", "core")  # minimal|core|research|full
KINDLY_ENABLE_CODE_MODE: bool = os.environ.get("KINDLY_ENABLE_CODE_MODE", "false").lower() == "true"

# server.py (guarded imports)
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools, BM25SearchTransform
# from fastmcp.experimental.transforms.code_mode import CodeMode, Search as CodeSearch, GetSchemas, GetTags  # optional extra

# after registers...
mcp.add_transform(PromptsAsTools(mcp))
mcp.add_transform(ResourcesAsTools(mcp))

always_visible = ["web_search", "get_content", "batch_get_content", "discover_links", "search_status"]  # example
mcp.add_transform(BM25SearchTransform(max_results=20, always_visible=always_visible))

if settings.KINDLY_ENABLE_CODE_MODE:
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode, Search as CodeSearch, GetSchemas, GetTags
        code_mode = CodeMode(
            discovery_tools=[GetTags(), CodeSearch(default_detail="detailed", default_limit=8), GetSchemas()],
            # sandbox_provider=MontySandboxProvider(limits={"max_duration_secs": 180, ...})
        )
        mcp.add_transform(code_mode)
    except Exception as e:
        logger.warning("CodeMode not available (install fastmcp[code-mode]?): %s", e)

# Visibility / profile logic (at startup or via middleware on_list_tools hook)
# mcp.disable(tags={"synthesis-heavy", "expensive"}, components={"tool"}) etc. based on profile
```

Add tags to decorators, enhance `docs://workflow` + instructions, expose current profile via cheap `search_status()` tool (or existing status resources), update tests/docs/CHANGELOG.

**How the combo solves the observed problems**: Visibility/profiles limit what is *visible* (agent cannot hyperfocus on unused/expensive synthesis or agentic if disabled in default "core"). ToolSearch/CodeMode make discovery *active and intentional* (must search "fetch" or "synthesis expensive" — forces focus and teaches the grammar). Middleware + result hints + prompts-as-tools + per-session unlock + progress teach/steer *strategically* after discovery. Telemetry + new LLM-judge close the loop on compliance ("did run do web_search + get_content before any synthesis?"). Matches Cloudflare 99.9% real-world reduction + practitioner post-mortems.

**Other FastMCP capabilities reviewed (Sampling, Elicitation, Tasks, more Middleware)**: Valuable for teaching in long ops (`ctx.report_progress` with strategic stage info already partially used; expand). Elicitation sparingly for confirming expensive paths. Tasks for very long background agentic if wanted. ResponseLimitingMiddleware (built-in) excellent hygiene for large outputs. Custom middleware patterns already mirror FastMCP's `on_call_tool` / `on_list_tools` hooks.

**Risks / trade-offs (see detailed critical version above with CF context)**: Experimental + Python bias + sandbox limits + debug surface + over-steering (profiles first mitigate). Migration additive. See full risks/gotchas in the CodeMode deep section.

---

## 4. Practitioner Playbook for FastMCP Search/Research Servers (Discovery / Fetch / Synthesis / Agentic Layers)

Synthesized from high-signal battle-tested sources (Cloudflare Code Mode blog + 99.9% case, Anthropic MCP engineering, modelcontextprotocol GitHub discussions + SEPs 1004 Server Profiles / 1821 Dynamic Tool Discovery, real post-mortems (300+ tool stub-bloat with ~97% non-executing tools causing 5-10k token overhead + selection degradation; Open Targets 45-tool balancing act; arXiv empirical MCP fault study), practitioner threads (X/Reddit "fewer tools === better MCP", max ~10 recommended), FastMCP/Prefect transforms + middleware docs) + cross-referenced against this project's 48h data, code (server.py, middleware, analytics), and observability reports.

**Project cross-reference**: Already ahead of many peers (ToolAnnotations, rich per-tool instructions + research_goal, 4 planning prompts, status + workflow resources, DynamicGuidanceMiddleware + ExpensiveToolProtection + rates + SessionTracker, result enrichment with agent_guidance/suggested_next_tools/suggested_prompts, DuckDB/OTel/Grafana/Langfuse for measurement, explicit discovery/fetch/synthesis contract). Gaps: no profiles/visibility, no PromptsAsTools/ResourcesAsTools/ToolSearch/CodeMode, chaining hints partial, low-usage not auto-hidden.

**Observability tie-in**: ~3.3k events / 975 runs; core web_search + get_content healthy; agentic long-tail cost/error (34 req / 10 err); synthesis low-volume; cache cold (0 hits); "cognitively heavy" surface + clients choosing expensive paths early.

**10 Actionable, Prioritized Recommendations** (prioritized by impact on observed symptoms + external evidence; implement in order for compounding effect; full details in the retrieved subagent output for ID `019e8c3e-391f-7fe2-a36c-94e79ad2ab8d`).

1. **Implement tool profiles / dynamic visibility gating (P0 for surface bloat + low-usage tools)**  
   **Why**: Flat exhaustive lists cause 20-50%+ context burn, selection cliffs beyond ~10-20 tools, degraded performance. Cloudflare/Anthropic/FastMCP + SEPs emphasize progressive disclosure. 300+ tool case had 97% stubs.  
   **How here**: Add `KINDLY_TOOL_PROFILE` (default "core") in settings.py. Gate registration or use `on_list_tools` middleware hook (FastMCP supports). Sets: minimal (web_search/get_content/batch), core (adds academic/youtube/discover_links), research (adds gemini/perplexity/grok), full (adds composio/agentic/analytics). Wire conditionally or post-filter. Expose current profile via status or new cheap `search_status` tool. Do not delete tools — hide. Add to instructions + `docs://workflow`. Combine with existing middleware.  
   **Measurement**: DuckDB tool request/response/error counts segmented by profile (add attr to events). "Effective tool surface" = unique tools actually called / exposed. Grafana `mcp_tool_invocations_total` + per-tool panels. Before/after call mix + agentic error rate + p95. Alert on high volume to "hidden" tools.

2. **Add PromptsAsTools + ResourcesAsTools (and evaluate SearchTools/CodeMode opt-in) transforms**  
   **Why**: FastMCP steering plan + docs recommend for tool-only clients (Cursor/Claude often surface tools more reliably). Transforms compose. CodeMode collapses surface to meta-tools for large catalogs (Cloudflare 99.9% token reduction; Anthropic 98.7%).  
   **How here**: `from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools; mcp.add_transform(...)`. Enhance existing prompts (plan/evaluate/gap/suggest) and resources (add tool-chains, error-recovery, cost-latency-guide, provider-routing). For CodeMode: opt-in separate/guarded (keep default direct-tool per philosophy). Add tags/meta. Leverage existing `docs://workflow`.  
   **Measurement**: Analytics on prompt/resource usage (post-transform) vs direct calls. Session tracking + guidance acceptance. Trace volume of list_prompts/read_resource post-transform. Pre/post token/context via evals or traces.

3. **Systematize result-level chaining hints, usage hints in structured returns, and recovery workflows in errors (beyond docstrings)**  
   **Why**: Steering plan + deep review + Anthropic/Cloudflare stress explicit next-steps (LLMs ignore prose under load). Result metadata + ToolError with usage_hint + recovery teach the grammar.  
   **How here**: Already partial via DynamicGuidanceMiddleware. Extend to more paths (youtube_search → transcript; academic; error cases in errors.py). Add `usage_hint` + recovery consistently. Use `ctx.report_progress` more (add to agentic/batch). Enhance batch returns with has_more/cursor. Add to models.  
   **Measurement**: Parse structured fields in DuckDB (tool.*.response). "Steering follow rate" (suggested_next called within N steps of a run, via timelines or tool_trace). Session counters + guidance events. Error reduction on steered paths.

4. **Double down on (and expand) middleware for context injection, rate/backpressure as steering, and session state**  
   **Why**: FastMCP middleware docs highlight on_list_tools (filter/visibility), on_call_tool (guidance/rate), request-scoped state, composition. Rate limits + backpressure act as implicit steering. Project already has excellent examples.  
   **How here**: Existing in `middleware/` (query_guidance, expensive_tool_protection, rate_limits, session_tracking) + server.py wiring. Expand on_list_tools for profile filtering (rec 1). Add more session keys ("has_seen_workflow"). Use for A/B. See FastMCP composition with mounts.  
   **Measurement**: Existing session + middleware.* events. Per-session tool counts + guidance injection counts. Correlate with success/error rates. Analytics reports on session hygiene (stale PID clusters noted in 48h).

5. **Treat resources + prompts as first-class "playbooks" / workflow coach layer**  
   **Why**: Steering plan + FastMCP + Anthropic (pair MCP with skills for procedural knowledge). Resources for persistent context/status; prompts for actionable plans. Exposes "how to chain" without bloating every tool desc.  
   **How here**: Already have `docs://workflow` + status resources + plan_* prompts. Per steering plan: add more (tool-chains, error-recovery, cost-latency, provider-routing). Wire via transforms (rec 2). Surface specialized resolvers in guidance. Consider MCP skills extension.  
   **Measurement**: Usage of resource/prompt tools (post-transform). "Playbook adherence" via run timelines (did agent call plan_* or read workflow before heavy tools?).

6. **Make progress reporting + long-running signals explicit teaching + observability hooks (especially for agentic/search pipelines)**  
   **Why**: FastMCP `ctx.report_progress` + info/debug/warning for opaque ops (prevents timeouts, teaches stages). Valuable for agent UX and debugging expensive tails (agentic 150s+). Project already uses in core paths.  
   **How here**: Expand in `web_search`, `get_content`/`batch`, `agentic_web_research`, content resolvers. Stages like "10/100 normalize...", "N/M URLs complete". Emit structured observability alongside.  
   **Measurement**: Tempo traces + Loki + progress events in dashboards. p95 per stage (rewrite/rerank already hotspots). Agentic vs simple run differentiation.

7. **Detect, measure, and gracefully handle "many tools not used" via analytics-driven visibility + deprecation (effective vs. exposed surface)**  
   **Why**: Tie directly to the prompt. 48h + deep review show skew. External: audit stubs; only register executing tools.  
   **How here**: Use existing `analytics_query`/`analytics_report` + DuckDB (tool counts as in 48h tables) + OTel `mcp_tool_invocations_total`. Add lightweight list_tools instrumentation or infer from profiles. Auto-flag low-call tools for profile demotion or transform hiding. Graceful: profiles hide; transforms can deprecate (rename/namespace/warn).  
   **Measurement**: Explicit dashboards for call volume per tool, call/exposed ratio, error rate by tool, "unused but exposed" candidates. Compare across profiles. Regression tests on analytics. Correlate with quality evals.

8. **Rate limits, backpressure, expensive protection, and strict validation as implicit steering + safety**  
   **Why**: Project middleware already differentiates cheap vs expensive. FastMCP supports strict_input_validation, ToolError. Client diffs addressed by transforms. Backpressure teaches without hard blocks.  
   **How here**: Already wired. Add mask_error_details + clear recovery in errors. Transforms for client variance.  
   **Measurement**: Rate limit / middleware block events. Error classification. Per-tool RPS vs quotas in Grafana.

9. **Prefer one rich server with profiles/transforms/CodeMode opt-in over splits; split only for hard boundaries**  
   **Why**: Evidence from Cloudflare (unify many behind CodeMode), practitioner preference, hierarchical proposals. Splits multiply integration tax.  
   **How here**: Use profiles + transforms + composition/mounting. Opt-in CodeMode. Analytics to monitor cross-tool usage before considering split.  
   **Measurement**: Run-level tool mix diversity; cross-profile comparisons.

10. **(Foundational) Instrument everything for "effective surface" + steering A/B; close the loop with evals + changelog discipline**  
    Leverage DuckDB/MotherDuck + Grafana (existing + recs for terminal success, cache, slow paths, tool mix) + Langfuse for agentic. Add profile/session attrs. Run gold evals post-changes. **All changes in CHANGELOG.md under [Unreleased]**.

**Implementation order** (crosses steering plan + deep review + obs recs): profiles + status tool → transforms (Prompts/Resources + ToolSearch) → systematic hints/errors/progress → more on_list filtering + A/B → CodeMode evaluation (opt-in) → eval harness + changelog.

**Files/refs**: `src/kindly_web_search_mcp_server/server.py` (instructions ~470+, resources ~2373+, prompts ~2598+, middleware wiring), `middleware/` (query_guidance.py, expensive_tool_protection.py, etc.), `analytics/` (tools.py, duckdb_store, views, reports), `settings.py`, tests/test_agent_steering_middleware.py, `CHANGELOG.md`, `plans/observability/web_search_mcp_deep_review.md` + 48h report + action-recommendations, the retrieved practitioner subagent output.

Update docs (ARCHITECTURE, DEVELOPMENT, TESTING, CONFIG) and run `ruff check/format`, focused pytest.

---

## 5. LLM-as-Judge Evaluation of Search Results + Periodic on Stored Data (DuckDB / Flock / Langfuse / Grafana)

**Gap identified** (from 48h + deep review + obs action recs): Current analytics strong on volume/latency/errors (DuckDB events, Langfuse traces for agentic, Grafana panels, eval scaffolding in `analytics/evals.py`) but weak on *quality* and *strategic compliance* ("did the expensive rewrite/rerank help?", "did the agent follow the documented web_search → get_content grammar or jump to synthesis/agentic?", "was the top result actually relevant for a coding task?"). Deep review and obs recs call for eval harness + value metrics. LLM-as-judge is the practical way to close it without full human labels on every run. Leverages existing DuckDB event store, views (candidate survival, run timelines, fetch events), Langfuse, OTel/Grafana, query_policy/rerank/orchestrator signals, and eval tables.

**Full design from dedicated subagent `019e8c3e-1d93-70d3-a595-3c4a6f631419`** (retrieved to resolve "two agents still running"; 88 calls, 275s; actionable; references exact `analytics/evals.py`, telemetry, views, 48h data; Flock per duckdb plans without reading Done/ subtree in main work).

**Track 1: Per-Result / Per-Web_Search (or Post-Rerank) LLM-as-Judge for Individual Result Quality**  
Focus: relevance of title/snippet to query (technical/coding intent), potential usefulness for coding/debug task (leads to good page? actionable?), citation faithfulness (for synthesized), diversity (unique angle/domain/provider agreement). Pointwise primary (scalable, cheap); pairwise secondary for A/B (rewrite/rerank lift).

**Sampling**: Primary from DuckDB `search_events` / views for `tool.web_search.response` (post all stages, includes cache_hit + results), `search.orchestrator.response`, `search.rerank.summary` (post-rerank top_results), `provider.search.result`. Live path: in server.py after normalize or in orchestrator after rerank. Strategy: hash(run_key or normalized_query) % 100 < (sample_rate*100) or RANDOM() on recent window. Limit to top-5 results (high-signal). 1-5% default. Also 100% on eval runs. Enrich with query, research_goal, rewrite_policy, rerank_applied, providers_used, cache_hit. Extract per-result: title, snippet (previewed), link (for hash), score, providers/provider_count, domain. Cross: join `vw_candidate_survival` + `vw_fetch_events` for "was this followed up?" signal (usefulness proxy).

**Prompt Design (Pointwise Primary, Reference-Free; Pairwise for Lift)**: Use litellm (already in project for rewrite) + structured JSON output. Cheap/fast judges (temperature=0, short context). Cache by (normalized_query_hash + result_link_hash or title+snippet_sha + prompt_version + model).

**Exact System Prompt (relevance + usefulness for coding)**:
```
You are an expert LLM judge evaluating web search results for AI coding assistants (e.g., Codex, Claude Code, Cursor). Focus on technical/debugging/coding utility. Be strict but fair. Output ONLY valid minified JSON.

Criteria (1-5 integers; 1=poor, 5=excellent):
- relevance: How well do title + snippet match the query intent? Prioritize exact technical matches (error codes, package names, API versions, stack traces, GitHub issues, docs) over generic. Penalize off-topic or keyword-stuffed.
- usefulness_for_coding: Would this result likely help solve a coding/debug task? (Leads to source code, repro, fix, API ref, discussion with answers, or high-signal page? Or just marketing/SEO noise?)
- coding_signal: true if strong signal for code-related (e.g., contains "error", version, "github.com", "stackoverflow", code keywords, specific lib names in title/snippet).
- diversity_contrib: Does this add unique value (different domain/provider/angle vs. typical web results for this query)? (Use only if set context provided; else null.)

For synthesized answers (gemini/perplexity etc.): also faithfulness (claims in answer supported by cited sources/grounding_chunks?).
Return: {"relevance": int, "usefulness_for_coding": int, "coding_signal": bool, "diversity_contrib": int|null, "faithfulness": int|null, "rationale": "concise 1-2 sentence explanation with evidence from title/snippet/query. Note any rewrite/rerank/policy signals if visible.", "verdict": "high|medium|low"}
```

**User Prompt Template** (and pairwise variant):
```
Query: {query}
Research goal: {research_goal or "general coding assistance"}
Rewrite policy: {bypass|expand} (if known)
Result {idx} (post-rerank score: {score}):
Title: {title}
Snippet: {snippet}
URL: {link}
Domain: {domain}
Providers: {providers} (count: {provider_count})

Evaluate the above. {optional: "Compare to this prior top result for pairwise: ..."}
```

**Pairwise Preference** (for "did rewrite/rerank help?"): Present query + ResultA (title/snippet/score/stage) vs ResultB. Ask "Which is better for a coding task? A better, B better, tie, or A much better. JSON with winner + rationale + delta_explanation."

**Citation Faithfulness (Synth Track)**: For gemini_search/perplexity: pass answer + grounding_chunks/sources/citations. "Does every major claim in the answer have direct support in at least one cited source? Score 1-5 + list unsupported claims."

**Models (cheap first)**: Gemini Flash (gemini/gemini-1.5-flash or 2.0-flash via litellm + KINDLY_GEMINI_API_KEY) — very cheap/fast for snippets. Fallback gpt-4o-mini / Groq/Cerebras (if keys present). Local/zero-cost: Ollama (ollama/llama3.2 or phi3) or HF Inference chat. Flock path (batch).

**Python Functions (or UDFs) Sketch** (new `analytics/llm_judge.py`):
```python
# imports: duckdb, litellm.acompletion, json, hashlib, from ..settings import settings, from .duckdb_store import ..., from .evals import ensure_...

def _result_hash(title: str, snippet: str, link: str) -> str:
    return hashlib.sha256(f"{title}|{snippet[:200]}|{link}".encode()).hexdigest()[:16]

async def judge_single_search_result(
    query: str, title: str, snippet: str, link: str = "", 
    research_goal: str = "", rewrite_policy: str = "", 
    model: str | None = None, prompt_version: str = "v1-pointwise-result-1"
) -> dict:
    model = model or settings.llm_judge_model or "gemini/gemini-1.5-flash"
    # build messages from templates (truncate snippets)
    resp = await acompletion(model=model, messages=[{"role":"system", ...}, {"role":"user", ...}], temperature=0, response_format={"type": "json_object"}, max_tokens=300, ...)
    content = resp.choices[0].message.content
    parsed = json.loads(content)
    parsed["model"] = model
    parsed["prompt_version"] = prompt_version
    parsed["result_hash"] = _result_hash(title, snippet, link)
    return parsed

def ensure_llm_judge_tables(db_path: str | None = None):
    # extend evals.build_eval_table_sql or direct: CREATE TABLE IF NOT EXISTS llm_judge_scores (...)

async def run_live_llm_judges(sample_rate: float = 0.03, days: int = 7, max_judgments: int = 100, db_path=None):
    # query DuckDB for recent events (use views for shredded results)
    # for each if random() < sample_rate: for r in top_results[:5]: judge... ; INSERT/UPSERT (dedupe on run_key + result_hash + model + prompt_v)
    # also attach to Langfuse if trace_id: lf.score(..., name="search_result_relevance@5", value=..., comment=rationale)
```

**Flock SQL UDF / in-DB equivalent (local DuckDB only; materialize output table for MotherDuck/Grafana sync)**:
```sql
-- (after INSTALL flockmtl FROM community; LOAD flockmtl; CREATE SECRET ... for provider)
CREATE MODEL IF NOT EXISTS relevance_judge (TYPE gemini, MODEL_NAME 'gemini-1.5-flash');
CREATE PROMPT IF NOT EXISTS score_search_result (PROMPT '...' /* paste system+user template with {{query}} {{title}} {{snippet}} etc. */ );

SELECT 
    event_id, run_key, 
    llm_complete(
        {'model_name': 'relevance_judge'},
        {'prompt_name': 'score_search_result',
         'context_columns': [
            {'data': query}, 
            {'data': json_extract_string(r.value, '$.title')}, 
            {'data': json_extract_string(r.value, '$.snippet')}
         ]}
    ) AS judge_json
FROM ... json_each(...) AS r
WHERE event_name = 'search.rerank.summary' AND ... sampling condition;
-- Parse JSON or use llm_complete_json variant; INSERT into llm_judge_scores.
-- llm_filter for boolean usefulness; llm_reduce for daily provider summary aggregates.
```

**Track 2: Periodic/Batch LLM-as-Judge on Stored Analytics (DuckDB/MotherDuck + Langfuse + Grafana)**  
Run offline (cron / CLI / scheduled) over historical data. Leverage Flock for in-DB (no full Python roundtrips for simple filters/completes) + Python for complex orchestration/joins with survival.

**Data Sources + Features**: DuckDB search_events + `vw_run_timeline` (rewrite/rerank/fetch/answer counts per run_key), `vw_candidate_survival` (stage progression), `vw_fetch_events` (success + word_count), `vw_search_results` / merged / rewrite_variants (shredded), query rewrite events, provider.search.result. Join run_key + url for "result led to successful get_content". Langfuse traces for agentic (tool sequences); existing scores + custom via SDK. Grafana-exported or analytics_query output. Cross features: query_policy (bypass vs expand), rerank presence/score delta, orchestrator rewrite_plan, research_goal, cache_hit, provider mix, error events.

**Use Cases (High-Signal)**:
- Classify query intent (reuse query_classifier or simple LLM) vs actual tool path (from timeline: did web_search fire first? get_content follow? synthesis after evidence?). Measure "strategic compliance rate" = % runs using documented core flow before expensive synthesis (for coding-like queries).
- Score provider result quality over time (avg LLM relevance @5 per provider, by day; marginal contribution of rerank/rewrite via stage joins).
- Detect stale cache hits (cache_hit=true but low judge relevance/usefulness).
- Summarize error patterns (llm_reduce on error payloads + classified events).
- Aggregate "search effectiveness": avg relevance of top-5 (overall + @k), fraction of results with follow-up fetch success, diversity (unique domains), citation faithfulness for synth.
- "Did rewrite help?": compare judge scores on pre/post-rewrite branches or variant quality reports.
- Eval integration: for gold eval_runs/cases, attach LLM judges to observations (extend deterministic score/verdict).

**Flock MODEL/PROMPT Examples**: See research plan + fetched docs for exact CREATE MODEL (TYPE, MODEL_NAME) + CREATE PROMPT + llm_complete / llm_filter / llm_rerank / llm_reduce. Materialize e.g. to `llm_judge_scores` or `llm_quality_scores` (already synced). Use llm_embedding for semantic clustering of low-quality queries.

**Python Batch Sketch + Storage** (extend `evals.py` / new `llm_judge.py`):
- `run_periodic_batch_judges(..., use_flock=True)`
- Table (extend existing `llm_quality_scores` or add `llm_judge_scores`): score_id, recorded_at, source_event_id, run_key, eval_run_id, target_type ('search_result' | 'synthesis_answer' | 'run_compliance'), target_ref, score_name ('relevance' | 'usefulness_for_coding' | 'strategic_compliance' | 'faithfulness' | 'provider_quality'), score_value, model_name, prompt_version, explanation, payload_json.
- Update `vw_eval_*` + add `vw_llm_judge_summary`.
- Update `reports.py`: new report "llm-judge-quality" or extend `eval_quality_summary`.
- Langfuse: for events with trace_id, use langfuse client to create/attach scores (as already done in agent/runner.py).
- MotherDuck sync: already handles llm_quality_scores + eval tables; extend for new judge table (dedupe on id).

**How to Run Periodically**: CLI extension (in `cli.py` + `analytics/tools.py`): `analytics llm-judge --sample-rate 0.05 --days 30 --use-flock` (or integrate into `analytics-report --report llm-judge-quality`). Cron / scheduled. On new eval runs. MotherDuck path: local Flock job materializes → sync. Best-effort + guard: if not enabled or no keys, skip. Log costs.

**Dashboard Ideas (Grafana / MotherDuck views)**:
- Time series: "LLM Judge Avg Relevance @5 over time" (by provider, by rewrite_mode, by rerank, overall). Panel + threshold alert.
- "Strategic Compliance Rate" (% runs for coding-like queries that did web_search/get_content before any synthesis/agentic; gauge + trend; breakdown by research_goal category).
- "Rerank/rewrite Lift": avg judge score delta (pre vs post stage) or % improvement in top-1 relevance.
- "Follow-up Effectiveness": fraction of judged results that appear in successful fetch events (join survival).
- Provider quality heatmap or table (avg usefulness + error rate + volume).
- Error pattern summary (llm_reduce topics or top failure rationales).
- Cache value: relevance distribution for cache_hit=true vs miss.
- Update `kindly-mcp-quality-dashboard.json` (add panels targeting MotherDuck `llm_judge_scores` / views).
- Langfuse UI: custom LLM-as-Judge evaluators on tool observations (map trace data for query + results snippet; scores appear in traces + analytics).

**Cost/Latency Controls (Avoid Overkill)**:
- Sample 1-5% (configurable `KINDLY_LLM_JUDGE_SAMPLE_RATE` in settings.py; lower for prod).
- Small/fast judge model (Gemini Flash primary; cap tokens/context to snippets only; max 5 results/run).
- Judgment cache: table keyed by content hash + model + prompt_version (skip re-judge identical result for same query class).
- Batching: Flock auto-batching (up to 48x per research); litellm batch or group calls; process in small windows.
- Caps: `KINDLY_LLM_JUDGE_MAX_PER_DAY`, timeout per judge, offline-only (never block tool responses).
- Monitoring: emit observability for judge calls (duration, tokens, cost proxy, model); surface in Grafana + new "llm-judge-cost" report.
- Flock caching + KV-friendly prompts.

**Validation (Correlates with Human/Golden Eval Harness)**:
- Golden set: per deep review recs (YAML/JSON cases covering exact-error, docs, freshness, community (SO/GH), academic, known-URL, multi-source; 20-100 cases). Run through real pipeline → store to eval_* tables + attach LLM judges.
- Metrics: Spearman/Pearson correlation on numeric scores; exact/adjacent agreement % on Likert; binary (useful? high/medium vs low) F1/accuracy vs human labels; off-by-1 accuracy. Per-provider and per-stage breakdowns.
- Inter-judge: run 2 models on same sample, measure agreement + avg for robustness.
- Harness integration: extend `analytics/reports.py` `eval_quality_summary` + future gold runner (as in plans + research_repos examples like hermes golden_eval.py). Compare LLM judge lift vs deterministic (e.g., fetch success, domain match). Re-run after rerank/rewrite/policy changes (before/after).
- Human calibration: periodic manual review of low/high samples + rationales (store in eval_observations or separate).
- Threshold for "proven": e.g., avg relevance @5 >=4.0 on gold + >70% human agreement + measurable compliance lift post-steering changes.

**Integration with Existing + "LLM-as-Judge Periodic Evaluation of DuckDB Stored Data/Grafana/Langfuse"**: The batch job + Flock SQL + materialized tables + sync + Grafana panels + Langfuse score attachment *is* the periodic in-DB + cross-stack judge. Extend `evals.py` `build_eval_view_sql` / add `vw_llm_judge_on_timeline` (join judge scores to run_timeline + survival for "quality of survived candidates"). In `queries.py`: add classifiers for eval/llm-judge questions. Langfuse: already has scores for agentic; extend custom evaluators (UI or SDK) for search observations; offline judges can backfill via trace_id. Grafana: MotherDuck datasource queries the synced judge tables/views alongside existing quality/pipeline. Orchestrator/rerank/policy cross: judge payloads include rewrite_policy/rerank_stage; run aggregates like "avg relevance when rewrite=expand vs bypass". Close the loops: use scores to answer "quality lift?", feed compliance into steering middleware (future), surface in `analytics_query` ("what is current strategic compliance?"), alert on regressions. New events (optional): emit `analytics.llm_judge.applied` for visibility.

**Implementation Sketch Order (Actionable, Low-Risk)**:
1. Add settings (`KINDLY_LLM_JUDGE_*`) + ensure tables (extend evals.py).
2. Implement `analytics/llm_judge.py` (Python path first for control + validation; Flock path for batch).
3. Wire sampling in server.py (post-rerank path) + CLI/report extension + one new report.
4. Update motherduck_sync + views for new table.
5. Add prompts (new file or in query_rewrite_prompts.py style) + basic tests (mock litellm; DuckDB fixture).
6. Grafana panel additions + dashboard version bump.
7. Validation script + gold cases (start small).
8. Docs updates (OBSERVABILITY.md, DEVELOPMENT.md) + CHANGELOG.md [Unreleased] entries.
9. Run focused tests + live probe with sampling enabled.

**Risks/Mitigations (Keep Practical)**: Judge bias/hallucination in rationale (mitigate with strict JSON + short context + validation correlation + multiple models); Flock version compat (local-only per plan; test against pinned DuckDB; fallback to pure Python); cost creep (hard caps + sampling + cache); privacy (snippets are public search results; preview already applied in telemetry); over-reliance (judges augment, not replace, human/gold + deterministic metrics like fetch success/survival).

This is actionable, references the exact plans/code, focuses on high-signal cheap judges (pointwise on snippets + Flock batch + sampling), directly feeds the observability stack, and closes the specified gaps without overkill. Next step: prototype the Python judge + table + one CLI report, baseline on existing DuckDB 48h data, correlate on a small hand-labeled set.

All paths are absolute from the workspace root. Use existing patterns (litellm cascade, emit_*, append_event, views shredding, Langfuse scoring, guarded analytics surfaces) for minimal diff. Follow AGENTS.md for any impl (tests under `kindly_web_search_mcp_server.*` namespace, ruff, pytest focused slice, CHANGELOG).

---

## 6. Value of Other Active /plans/ for Value Based on Observability (Non-Done/ Only)

**Scope note (user instruction)**: Only active plans directly under `plans/` (excluding the entire `plans/Done/` subtree) were read and used. This section covers solely `plans/GraphQL-tuning.md`, the `plans/gliner/` subtree, and the `plans/playright/` subtree. All prior language, citations, or value judgments drawn from any plan under `plans/Done/` have been removed or qualified.

**Active plans assessed vs. 48h snapshot (key signals: rewrite 12s avg, rerank 6.62s/18s p95 on Voyage, agentic long tails, remote content fetch as dominant latency in get_content, core web_search/get_content healthy but synthesis/niche low-volume, cold cache 0 hits + costly lookups, under-instrumented simple runs, single rerank provider risk). Cross-validated by dedicated subagent `019e8c3e-53ce-79a1-b9b3-32e169fa3dc7` (plans-portfolio assessment, 49 calls, 147s; full output retrieved with the two others to resolve "two agents still running") — it independently ranked steering (profiles/ToolSearch/guidance) + observability action items (canonicals, cache panels, severity) + DuckDB LLM-judge/compliance + gliner entity signals + agentic budgets as P0 high-synergy against the exact metrics (tool mix skew, 12s rewrite, 18s rerank, 0 hits, agentic 10 err/150s p95, under-instr 620 runs); gliner + steering + LLM-judge + GraphQL/get-content for fetch as the combo with >sum synergies. Only active plans used here per scope.**

- **plans/GraphQL-tuning.md**: Medium value (parallel track). GitHub is a high-value specialized content resolver (full issue/discussion threads with comments in the fetch pipeline, which observability shows is latency-critical — remote HTTP/browser dominates get_content root time, not local code). The plan provides practitioner pagination guidance: treat each connection as its own stream; use `search(...)` for discovery then `repository.issues(number:)` / `repository.discussion(number:)` for staged hydration; no single universal cursor for deep nesting; keep `first` values small (5-20 top-level, 10-20 for comments); verify search capability via probes rather than assuming parity with REST/UI. Ties directly to content fetch reliability/cost for coding-agent use cases (GitHub issues are common targets). Not a primary rerank or top-level tool steering lever, but improves the "fetch" half of the intended grammar (web_search → get_content) and adds observability hooks for rate-limit/cost on that lane (GITHUB_TOKEN already recommended in docs). Synergies with gliner (entity extraction on hydrated GitHub content) and LLM judges (better source signals for faithfulness/relevance scoring). Recommendation: keep as parallel feature; instrument resolver stage events if missing; adopt staged pagination in the GitHub resolver to reduce errors and tail latency on deep threads.

- **plans/gliner/ (GLINER2_Direct_Integration_Plan.md, GLiNER_Research_Report.md, content-extraction-entity-schema-oss-patterns-2026-06-01.md)**: High value for quality signals that can indirectly address rerank latency cost (better candidates = higher effective precision, potential for early exit or lighter rerank) and tool steering issues (richer metadata in results/guidance reduces need for agents to over-call synthesis or agentic tools when core results are more "typed" and actionable). Strong fit for this coding-assistant domain (tech literals: package names/versions, error codes, stack fragments, HF model ids, repo#issues, CLI flags, release notes, changelogs, API signatures). 

  **Key mappings**:
  - Query side: Augment current pure-regex `query_policy.py` (PRECISION_PATTERNS for URLs/versions/errors/hashes) with GLiNER zero-shot on short queries for additional must-keep literals that regexes miss. This can reduce unnecessary rewrites (the 12s avg hotspot) for technical queries.
  - Result annotation: Attach lightweight entities from title+snippet to WebSearchResult for rerank features (entity overlap score between query and result as explainable boost) and diversity.
  - Fetch side: Post-markdown in fetch_pipeline.py (after specialized resolvers or trafilatura/universal_html), run chunked GLiNER (inspired by gliner-spacy chunk/offset/conf logic + our existing windowing) to produce `entities` + optional structured records. Output extends GetContentResponse without breaking changes. Improves "was this useful?" for LLM judges and cache guardrails (e.g., freshness or correctness keyed on extracted package/version).
  - Internal: Rerank (entity features or guard), page_cache (fingerprint on key entities?), telemetry (add entity_count / top_entity_types dimensions), observability (better candidate survival signals).

  Implementation notes from the plans (non-corporate, direct): Always-on sensible defaults with env knobs (KINDLY_GLNER_MODEL, labels schema, threshold, chunk_size); lazy singleton to control RAM/CPU; post-processing pipeline (validate regex per label, dedup, merge overlaps, normalize) borrowed from production Rust/ONNX examples like pii.engineer; support multiple backends (python-gliner2, ONNX, remote gliner2-mcp) behind an interface.
  Risks acknowledged (first-load time, CPU on long pages, model size) mitigated by small default (base or nano) + chunking at ~800-1200 chars with overlap. High synergy with rerank (richer docs for deep rerank option), steering (entities in suggested_next or guidance make results more "self-explanatory" so agents follow core flow), LLM-judge (entities provide grounded features for relevance/usefulness scoring), and cache fixes (correctness on extracted literals).

  High alignment with observed pain (rewrite cost, rerank as single-provider expensive stage, cold cache not providing value, content fetch remote latency, need for better quality measurement).

- **plans/playright/ (crawl4ai-scrapegraphai-integration-analysis.md, crawl4ai-vs-scrapegraphai-comparative-analysis.md)**: Medium-high value for the content fetch layer, which observability directly flags as a major user-visible latency contributor (get_content traces show remote site/HTTP/browser time dominates; local pipeline is fast). Both libraries are Playwright-based (shared dependency path if we ever move from nodriver).

  Crawl4AI (favored in comparison): strategy-based (CSS/regex/BM25/cosine/LLM), produces clean Markdown optimized for LLMs/RAG, single-pass + optional recursive crawl (BFS/DFS with depth control), JS injection, network interception (block ads/images for speed), screenshots/PDF, stealth. Replaces large parts of current scrape/ (nodriver_worker, universal_html, chromium_pool, extract) with pip dep + AsyncWebCrawler.

  ScrapeGraphAI: Graph/DAG pipelines, heavily LLM-dependent for schema-agnostic extraction (natural language "what to extract"), less emphasis on raw Markdown cleanliness.

  Mapping to snapshot: Improves JS-heavy fallback path (universal_html/nodriver is the slow last resort for many get_content calls); adds structured JSON output (great for LLM judges and entity-like post-processing); interception can reduce remote latency; recursive + JS actions help discover_links and batch use cases. Directly attacks "remote is the bottleneck" finding and supports the strategic "use get_content/batch after search" grammar by making fetch more reliable/structured (agents get better evidence faster, less incentive to jump to synthesis tools).

  Recommendation: Evaluate migration from nodriver to Playwright + one of these (Crawl4AI favored for Markdown + strategies + lower LLM dependency per comparison) as a P1 for fetch latency/quality. Would feed better inputs to rerank (if deep rerank), richer content for judges, and stronger "fetch" signals in compliance metrics.

**Overall for active plans**: Highest immediate value for the two foci (rerank latency cost + strategic tool use / many tools low-usage) are gliner/ (entity signals to make core results better and rewrites cheaper) and playright/ (better fetch to make the intended grammar actually performant and attractive to agents). GraphQL-tuning is solid supporting work for a key specialized resolver that affects fetch tails. These align with snapshot without needing Done/ material. Update active roadmap to prioritize gliner hooks + Playwright + Crawl4AI pilot (tied to get_content improvements and LLM-judge enrichment), with GraphQL pagination as parallel for GitHub content quality.

**Note**: Any earlier broader portfolio language referencing plans under Done/ has been excised. The assessment above is self-contained to the active plans actually read in this correction pass. Cross-validated by the dedicated plans-portfolio subagent.

---

## 7. Prioritized proposals

Incorporate the above (CodeMode + steering plan execution, GCS custom reranker detailed deploy + client already present, LLM-judge on DuckDB/Langfuse with Flock, plans portfolio prioritization, corrected tool usage + unused tail analysis, deeper practitioner CodeMode/Cloudflare/Anthropic evidence, all subagent outputs reformatted for readability).

**Experiments first (per aggregated guidance)**: Build/run gold-set eval harness + probes using existing analytics/evals tables + DuckDB views before large code changes. Baseline current (snippet rerank + semantic cache etc.). Measure relevance lift, latency, compliance % (discovery-before-synthesis), provider marginals, freshness, extraction quality, p95. Tie to 48h data for before/after.

**P0 (Immediate – correctness + steering surface reduction + measurement baseline)**:
- Implement tool profiles / visibility gating (`KINDLY_TOOL_PROFILE` + tags on tools + `mcp.enable/disable` or Visibility transform; default "core"; expose via status or new `search_status()` tool).
- Add PromptsAsTools + ResourcesAsTools transforms (after registers; routes through middleware).
- Adopt ToolSearch (BM25SearchTransform with always_visible core primitives).
- Systematize result-level chaining hints, usage_hints in structured returns, and recovery workflows in errors (extend DynamicGuidanceMiddleware; consistent in ToolError paths).
- Cache identity fixes (include domain_boost/block + page options like strip_selectors in key; or apply filters post-cache) + tests.
- Canonical terminal events (agentic.research.completed only on true success; regression test for dup semantics) + real ERROR severity for tool.*.error (preserve structured) + content trace span fix (`content/fetch_pipeline.py`).
- Pre-execution success record + post-success recording in agent/mcp.py (and regression).
- Gemini config/docs alignment.
- Usage audit in analytics (call/exposed ratio, low-volume flags for profile demotion) + compliance metrics (discovery-before-synthesis via DuckDB timelines or new LLM-judge).
- GCS reranker skeleton (KINDLY_RERANK_PROVIDER=gcp_cloudrun support; client already added; settings + core dispatch; fallback preserved).
- LLM-judge prototype (tables in evals/analytics, Python path + Flock path, one CLI report `analytics llm-judge`, sampling post-rerank, basic Langfuse/Grafana panels).
- Eval harness baseline (gold YAML cases covering segments from deep review; deterministic + attach LLM judges; run on existing DuckDB 48h data for correlation).
- GraphQL pagination pilot for GitHub resolver (staged hydration; instrument cost/rate-limit).
- gliner pilot (entity signals for query_policy must_keep + result annotation + fetch post-markdown + rerank features + cache guard + judge enrichment; lazy singleton + small default model).
- Update docs (ARCHITECTURE, DEVELOPMENT, TESTING, CONFIG, OBSERVABILITY) + all relevant AGENTS/CLAUDE notes + CHANGELOG under [Unreleased].
- Focused tests (existing slice + new for transforms/visibility/judge/gcp) + ruff check/format + live probes.

**P1 (Latency attack + value measurement production + fetch improvements)**:
- GCS custom reranker production deploy + full validation (A/B via probes + DuckDB survival/quality lift; monitoring; cost tracking).
- LLM-judge production (full dashboards, periodic batch job/cron, gold harness correlation >80% target, compliance % feeding steering middleware, alerts).
- Full gold eval harness + before/after for steering/rerank/rewrite changes (MRR/nDCG + LLM-judge + provider-marginal + freshness + extraction + p95).
- get-content rearch (Playwright + Crawl4AI or ScrapeGraphAI pilot for remote latency; structured output for judges/entities; windowing/continuation/status already partial).
- Observability P1 (cache hit-rate panels by type + query class + lookup latency; simple-run markers + type distinction (search-only / fetch-heavy / agentic / answer-producing); repair `find_slow_requests` or document TraceQL; alerts for p95 / agentic err / upstream spikes / missing terminals).
- Rewrite fast paths + policy enhancements (auto|off|force + rewrite_trace; gliner integration into query_policy for literals).
- Deep rerank opt-in (post-initial rerank, fetch richer excerpts for top N then re-rank; parallel + cache mitigate added latency).

**P2 (Advanced)**:
- gliner full integration + quality signals everywhere.
- GraphQL tuning for GitHub (production pagination, cost/rate instrumentation).
- Full adaptive routing / evidence-pack output for agentic.
- Split surfaces or gateway if telemetry shows need (after profiles + transforms prove insufficient).
- Sampling / elicitation / tasks for teaching in long flows.
- More transforms (Namespace, ToolTransformation) + strict validation + pagination if catalog grows.
- Continuous closed-loop (LLM-judge + gold + compliance % driving automatic profile adjustments or alerts).

**Acceptance criteria examples** (for P0 items):
- Profiles: KINDLY_TOOL_PROFILE=core hides synthesis/agentic by default; "full" exposes all; search_status tool returns current profile + providers + features; live probes (Claude Code / Cursor) confirm reduced surface + still discoverable via search or profile change.
- GCS: Deployed instance reachable; `KINDLY_RERANK_PROVIDER=gcp_cloudrun` + URL produces results with p95 <500ms (or target) on realistic 50-doc snippet payloads; fallback to voyage/jina on error; DuckDB shows provider="gcp_cloudrun" in rerank.summary; before/after p95 comparison in report.
- LLM-judge: Tables populated; sample run produces scores + rationale in DuckDB + optional Langfuse; `analytics llm-judge --sample-rate 0.05` works; compliance % query returns meaningful number on 48h data; correlation script vs small hand-labeled set runs.
- Cache: Hits now possible and correct (domain/options included in identity or filters applied post-fetch); 48h-style probe shows hits >0 and no wrong results from domain_boost etc.
- Steering compliance: New metric "% runs following documented web_search + get_content before any synthesis/agentic for coding-like research_goal" tracked in DuckDB or via judge; baseline established; improvement target after profiles + hints.

All under [Unreleased] in CHANGELOG. Experiments (harness/probes) before impl. Measurement first (use existing DuckDB/Grafana/Langfuse + new judge).

---

## 8. Risks, Trade-offs, and Critical Commentary

**Rerank (GCS custom)**: Predictability/privacy/cost-at-volume win vs ops (low with TEI) and cold starts (mitigated by min-instances + pre-cache). Quality regression risk on switch (harness + LLM-judge + live probes first; keep public fallback). GPU quota for L4. Public Jina/Cohere still viable for burst or as fast primary if custom latency not needed.

**FastMCP steering (profiles + transforms + opt-in CodeMode)**: Massive surface + token reduction + forces strategic discovery (hides tail; active search teaches grammar). CodeMode powerful for coding agents on complex chains (99.9% real example) but experimental + Python bias (profiles + ToolSearch safer universal default; layer them). Over-steering risk (offer "full"; measure compliance before aggressive hiding). Sandbox limits (tune to existing timeouts). Debug different (enrich results). Migration low cost (additive; existing middleware applies inside). Philosophy: project favors external control — CodeMode opt-in only, as per steering plan.

**LLM-as-Judge**: High-signal cheap way to close "quality unproven" gap and feed compliance into steering. Bias/hallucination in rationale (strict JSON + short context + multi-model + harness correlation mitigate). Cost (sampling + cache + caps + Flock batching + offline only). Flock local-only (per plans). Validation essential before trusting for decisions.

**Plans portfolio**: High synergies (gliner signals amplify steering + rerank + judges + cache; GraphQL + playright improve the "fetch" half of the grammar that obs shows is the real remote bottleneck). Prioritize measurement (eval harness + judge) before/after any change.

**General**: 48h data is a snapshot (one window; agentic burst + test-influenced). Under-instrumentation of simple runs means some tails may be hidden. Single rerank provider was a real risk (validated). Cache 0 hits masked correctness bugs. External sources (Cloudflare 99.9%, Flock examples, TEI guides) are primary and recent.

**Source critique**: Reports high-quality and complementary. Obs operational/volume-focused; deep review static + external. Gaps in sources addressed in this v3 (snippet limitation for rerank, cache correctness, full LLM-judge + Flock, practitioner 10-rec with "how + measure", active-plans-only portfolio, usage reality correction, CodeMode layering + 99.9% numbers). No major contradictions.

---

## 9. proposals

1. Run experiments first: gold-set eval harness + live probes on existing analytics/evals + DuckDB (baseline current rerank/cache/rewrite/steering compliance). Correlate with small hand-labeled set.
2. P0 steering surface: KINDLY_TOOL_PROFILE + tags + PromptsAsTools + ResourcesAsTools + ToolSearch + search_status tool + systematic hints/usage_hints + usage audit. (Builds directly on existing excellent middleware/guidance.)
3. Cache correctness fixes + tests.
4. Canonical events + error severity + trace fixes.
5. GCS reranker: wire provider support if not fully complete; test the already-added client; document deploy steps from the guide.
6. LLM-judge prototype (tables + Python/Flock path + one CLI report + basic panels).
7. gliner pilot + GraphQL pagination for GitHub.
8. Update all docs + CHANGELOG on landing pieces.
9. Focused tests + ruff + live probes (Claude Code / Cursor style) pre/post.
10. Re-measure on next observability window or synthetic load.

All per AGENTS.md (CHANGELOG first), scope (active plans only), and the spirit of the original request (deep, sourced, practical, cross-validated with subagents + primary research).

**Key file:line citations** (examples; many more in subagent outputs):
- Rerank snippets + Voyage path: `rerank/core.py:204`
- Domain filter only on miss: `server.py:826`
- Pre-execution record: `agent/mcp.py:71`
- Page cache by URL only: `cache/page_cache.py:88`
- Trace span indent: `content/fetch_pipeline.py:158`
- No profiles/search_status/tool visibility yet (confirmed via reads).
- Existing rich guidance/middleware/prompts/resources (server.py instructions ~470+, resources ~2373+, prompts ~2598+).

**References**: gofastmcp.com (CodeMode + transforms + visibility + middleware + llms.txt), Cloudflare Code Mode blog (99.9% numbers + V8/Worker Loader), TEI/HF Cloud Run guides, FlockMTL docs + arXiv, 48h report + deep_review + action-recommendations (plans/observability/), active plans (GraphQL-tuning.md, gliner/*, playright/*), server.py + rerank/* + settings + analytics/* + middleware/* (live code), practitioner cases/SEPs/post-mortems (via subagent research).

---