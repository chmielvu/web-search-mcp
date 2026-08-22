# P2 Implementation Plan — NetworkX Feedback Loop (graph-based ranking + expansion)

**Date**: 2026-08-22d · **Status**: PROPOSED (plan-only; no production code changed)
**Grounding**: live `search_events.duckdb` snapshot at 2026-08-22T15:30Z; installed `networkx==3.6.1`; `search/contracts.py`, `search/planning.py`, `search/outcomes.py`, `search/ranking.py`, `rerank/core.py`, `rerank/conditional_bi.py`, `analytics/judges.py`, `analytics/views.py`, `analytics/writers/{core,schema}.py`; official NetworkX/Docket sources; He et al. TKDE 2017; Microsoft WSDM'13; QCG-RAG (arXiv:2509.21237); Meilisearch synonyms docs.

---

## 0. Verdict (after local-code and targeted-source reassessment)

The topology decision still stands, but the implementation order is corrected. This server is already a six-branch multi-query RRF engine: `plan_search` emits `ORIGINAL_FREE` plus five rewrite/fallback variants (`planning.py:387-436`), and `rank_and_finalize` fuses provider lists plus BM25 before `rerank_results` (`ranking.py:57-152`). Graph fanout must therefore **not** add a seventh retrieval branch by default.

The first online experiment should be **graph-derived related-query seed injection** into the existing rewrite prompt. It exercises the current six-branch contract without inventing another retriever. Graph-derived rank features remain offline/shadow until their label grain and rerank hook are calibrated.

Accepted refinements:
- Use **`nx.bipartite.birank`** on an undirected `nx.Graph`; PageRank is an ablation, not a second live score.
- Materialize judge-derived labels through the existing offline `result_labels` writer; treat fetch data as secondary until query attribution is repaired.
- Derive missing historical canonical IDs from `link` with the repository helper; do not assume `final_results.canonical_result_id` is populated.
- Inject at most four effective rewrite seeds, preserve the original query as seed 0, and pass the effective seeds to both `_rewrite_queries` and the existing Gemma provider arguments.
- Use replay, cost, freshness, and diversity gates before enabling either expansion or ranking.

Rejected / deferred:
- Extra retrieval branch (`GRAPH_EXPANSION_EXTRA_BRANCH`) — Phase 5+, flag-only.
- Pre-rerank `score += graph_score` as the first ranking integration: the >100-candidate bi-encoder and later cross/LLM stages reorder independently, so that line is not a reliable graph hook.
- Fetch-weighted query edges before an explicit run-to-fetch attribution contract exists.
- Community/Louvain fanout and IPS until the current judged graph and fetch attribution are materially larger.

---

## 1. Verified inventory (live snapshot; counts drift as writers run)

| Signal | Table / join | Rows or coverage | Role |
|---|---|---:|---|
| Judge scores | `llm_judgments` (`result_quality`) | 3,025 total judgments / **1,595** result-quality / **1,132** successful URL-joinable | primary label source |
| Query runs | `search_runs` | 668 runs / **517** distinct normalized queries | left-side history |
| Final outputs | `final_results` | 9,355 rows; 8,380 have NULL stored canonical ID | derive identity from `link` for historical rows |
| Catalog | `result_catalog` | 766 rows | optional doc metadata, not the only doc universe |
| Query embeddings | `query_embeddings` joined by `run_key` | 257 rows | guarded cold-start fallback |
| Fetch / dwell | `content_fetches` | 1,055 / **507** with `item_duration_ms > 0` | secondary document signal only today |
| Output attribution | `tool_output_items` | 509 rows; 265 map to a search run through `search_runs.tool_call_id` | partial URL/run bridge |
| Fetch attribution | output/run/fetch intersection | **1** query–document pair in this snapshot | not a query-edge source yet |
| Rewrite lineage | `query_variants` / `query_transforms` | 132 / 242 | write-only analytics today |
| Labels store | `result_labels` | schema, **0 rows** | Phase 1 backfill target |
| Initial judged graph | successful judge + exact `run_key`/URL join | 1,047 query–document pairs; only 44 documents shared by 34 queries | head-only related-query support |
| Entities | — | none | deferred |

The graph builder must use the shared `_canonical_result_id` helper (`analytics/observability_ids.py:23-24`; production outcome writes at `search/outcomes.py:193`) and fall back to the lower-cased `link` when historical `final_results` rows have no stored ID. Existing judge views establish the exact join as `run_key` plus URL (`analytics/views.py:510-513`). The existing usefulness view is URL-level (`analytics/views.py:1381-1400`), not proof that a fetch belongs to a particular search query.

NetworkX is present as a **transitive `yake` dependency** (3.6.1), not pinned. The BiRank symbol imports, but the actual `birank(...)` call imports SciPy; the current venv has no `scipy` (`uv run python` smoke: `ModuleNotFoundError`). Pin/install the runtime dependency explicitly before selecting BiRank. `weighted_projected_graph`, `overlap_weighted_projected_graph`, and `adamic_adar_index` exist. Projection helpers are not implemented for multigraph inputs, so use `nx.Graph`, not `MultiDiGraph`.

---

## 2. Design decisions (revised)

### D1′ Topology + algorithm — judged bipartite `nx.Graph` + BiRank
- Initial graph edges are only successful, time-cutoff `result_quality` labels joined to `search_runs.normalized_query` and a canonicalized result link. Nodes are `query:<normalized>` ↔ `doc:<canonical_result_id>`.
- Use a simple undirected `nx.Graph` with one summed `weight` per query–document pair. Keep raw edge components (`judge_gain`, confidence, position, recorded_at, source) beside the aggregate; do not add parallel edges.
- Primary offline score: `nx.bipartite.birank(G, query_nodes, weight="weight", ...)`, where `query_nodes` is the first partition. The official API returns scores for both partitions and makes `top_personalization`/`alpha` apply to the supplied first partition.
- Run an unpersonalized global BiRank for document-authority diagnostics. For a per-query related-query experiment, use a sparse `top_personalization={query_node: 1.0}` and record the explicit `alpha/beta` configuration; do not claim `0.85` is a learned optimum.
- Ablation: store `nx.pagerank` and a weighted shared-neighbor baseline, but do not blend them live.
- Cold-start: exact normalized-query match first; optional embedding nearest-neighbor fallback joins `query_embeddings` to `search_runs` by `run_key`, requires the configured vector dimension/model, and is skipped on invalid/missing data. A tail query with no graph evidence is neutral/no-op, not a fabricated `0.5` prior.

### D2′ Refresh and persistence — lazy read, worker-safe rebuild
The request path only loads the last successful generation from an in-memory dict. A stale marker may enqueue one rebuild, but it must not write DuckDB or block the event loop. Rebuild reads DuckDB through a `READ_ONLY` connection, then persists through the repository's existing single-writer/dispatch boundary or atomically swaps a sidecar artifact; an external CLI must not open a competing write connection. `graph_features_meta` records generation, source cutoff, algorithm/config hash, built-at, node/edge counts, and failure status. Docket `Cron` automatically reseeds only at a live Worker startup (current source: `chrisguidry/docket/src/docket/dependencies/_cron.py`), so stdio remains lazy/standalone-worker rather than assuming an in-process scheduler.

### D3′ Ranking consumption — shadow first; no false pre-rerank hook
`rank_and_finalize` currently builds RRF/BM25 scores, stores them on `WebSearchResult`, and then calls `rerank_results`. For pools above the conditional bi-encoder limit, `rerank/core.py:67-128` invokes the bi-encoder; `rerank/conditional_bi.py:31-40` preserves incoming order only for smaller pools, while cross-encoder/RankLLM stages set their own ordering and scores. Therefore a pre-rerank `score += w_b · graph_score` is not a consistently effective integration.

Phase 2 stores graph features in a versioned in-memory/sidecar map and shadows per-result lookup, without changing output. After replay proves complementarity, the first live ranking experiment may blend a normalized graph feature **after** `rerank_results` and before `final_results = ranked_pool` (bounded to the returned slate, default-off). A recall-oriented feature must instead be passed explicitly into `rerank_results`/its stage contract; that is a separate higher-blast-radius phase. In either case use a small calibrated weight, `GRAPH_FEEDBACK_ENABLED=false`, shadow `ab_*` metadata, NDCG@10, top-10 unique-domain count, and exact flag-off equivalence.
### D4′ Labels — judge-first, zero-based, defensive
Keep the existing 14-column offline `result_labels` DDL and call `insert_result_labels`; do not invent a `label_age_days` column. Rebuild-time age is derived from `recorded_at`.

1. Backfill successful `llm_judgments` rows with `judgment_kind='result_quality'` by the existing exact `(run_key, judgment_target=final_results.link)` join (`analytics/judges.py:1330-1346`). Persist `source='llm_judge'`, `stage='final'`, the shared canonical ID, raw URL, rubric/model metadata, and raw parsed fields in `payload_json`.
2. Convert the current rubric deterministically for replay: `judge_label = 0` when `intent_match=false`, otherwise `(informativeness - 1) / 3`; retain `confidence` separately as `confidence / 4`. Convert persisted one-based `final_results.rank` to the repository's zero-based `result_labels.position` before the existing writer computes `label / log2(position + 2)` (`analytics/writers/core.py:692-795`).
3. Deduplicate repeated judgments at the `(run_key, canonical_result_id, source, rubric_version)` grain by latest `recorded_at`; apply a source cutoff so later judgments cannot leak into an earlier graph.
4. Initial edge weight is `discounted_gain × confidence_fraction`, min-max normalized only within a rebuild for diagnostics. Do not mix fetch scores into this weight: `content_fetches` has no direct `search_runs` attribution in the live snapshot, and the existing URL-level usefulness view is insufficient.
5. If fetch attribution is later repaired, add it as a separately named auxiliary component with its own coverage/age metrics before combining it with judge labels. Empty/`neutral`/partial verdicts remain skipped and counted.

### D5′ Query expansion — **seed injection, not a new branch** *(revised)*

Existing fanout (do not duplicate):

```
plan_search
  rake_terms + brave_autosuggest
  → _rewrite_queries(seed_queries, support_terms, suggestions)   # LLM, 5 variants
  → 6 QueryBranch rows (ORIGINAL_FREE + 5 roles)
  → retrieve per branch → RRF → rerank
```

`WebSearchRequest.queries` and `SearchPlan.seed_queries` are tuples with a four-seed public contract (`search/contracts.py:45-67`). `query_variants` / `query_transforms` record the existing six-branch lifecycle; they are not graph edges. `should_decompose` is computed in `understanding/adapter.py` and is still not read by `plan_search`.

Graph expansion slots in **before** `_rewrite_queries`, after the existing rake/autosuggest enrichment:

1. Define `base_seed_queries = request.queries if request.queries else (normalized_query,)`. Only when `GRAPH_EXPANSION_ENABLED` is true build `effective_seed_queries = (normalized_query, *base_seed_queries, *related_queries)` with stable deduplication and a hard cap of 4; with the flag off, pass `base_seed_queries` exactly as today.
2. Lookup related queries only from the last successful graph generation. Exact normalized match is preferred; embedding-neighbor fallback is optional and must use `query_embeddings.run_key → search_runs.normalized_query`. If support is below two shared judged documents, return no related queries.
3. Merge only bounded historical `rake_terms` from the selected related runs into the existing `terms`; cap and record the additions so prompt size and LLM cost remain bounded.
4. Pass the effective seeds and merged support terms to `_rewrite_queries`. When expansion is enabled, also pass the effective seeds to the existing `provider_arguments["gemma"]["queries"]`; otherwise retain the current `request.queries` behavior (`planning.py:449-452`).
5. Store graph generation, matched query, related IDs, added seeds/terms, skip reason, and fallback slot in rewrite metadata/payload. Keep `SearchPlan.seed_queries` aligned with the effective tuple.
6. If rewrite fails, use the top related query only in one existing deterministic slot (the `paid_other` fallback) and record that substitution; never create another `QueryBranch`. With `rewrite=False`, graph lookup and provider-argument changes are disabled so the existing deterministic path is identical.
7. Keep `GRAPH_EXPANSION_ENABLED=false` independently from `GRAPH_FEEDBACK_ENABLED`; assert branch count remains 6 and effective seed count never exceeds 4.

Preferred related-query recipe (offline rebuild):
1. Build candidate query pairs with `overlap_weighted_projected_graph(B, query_nodes, jaccard=True)` or an equivalent bounded shared-neighbor query on the original graph.
2. Run `nx.adamic_adar_index(B, ebunch=candidate_pairs)` on the **original bipartite graph `B`**, not on the projected query graph; common neighbors must be documents, and opposite-partition pairs are not valid candidates.
3. Optionally rank a second candidate list with per-query BiRank personalization, then intersect with the same support/age/cap rules.
4. Drop self/near-duplicate text (Jaccard > 0.8), require at least two shared judged documents, cap to five related queries per source, and keep a deterministic tie-break.
5. Materialize `related_queries(query_norm, related_norm, score, method, support_count, source_generation, built_at)`; tail queries with no exact/embedding match remain unchanged.

This borrows the shape—not the effectiveness claim—of query-click similarity work and QCG-RAG's capped query → neighbor query → chunk path. It reuses this server's historical judged queries and existing rewrite/RRF path rather than synthetic Doc2Query nodes or a second retrieval topology.

## 3. Algorithms (what we will actually call)

| Need | API | Notes |
|---|---|---|
| Doc/query authority | `bipartite.birank(B, query_nodes, weight="weight")` | weighted degree normalization; personalization is explicit |
| Global ablation | `nx.pagerank(B, weight="weight")` | offline diagnostic only |
| Related-query candidates | `overlap_weighted_projected_graph(B, query_nodes, jaccard=True)` | simple graph; bounded projection |
| Popularity correction | `nx.adamic_adar_index(B, ebunch=query_pairs)` | query-query pairs on original B; shared neighbors are docs |
| Personalized related list | `birank(..., top_personalization={q: 1.0})` | filter results to the query partition |
| Monitoring | weighted degree, shared-support histogram, BiRank Gini, freshness | log per generation |

---

## 4. Roadmap

### Phase 0 — Hygiene + runtime/identity (½–1 d)
- Obtain approval for direct `networkx>=3.3,<4` **and a compatible direct `scipy` pin** in `pyproject.toml`; stop relying on `yake`'s transitive NetworkX dependency. Do not treat a successful import as a working BiRank install.
- Add the graph generation manifest and an explicit fallback-ID/zero-based-rank contract; do not change public MCP models.
- Define the read-only rebuild plus existing single-writer/sidecar persistence boundary.
- **Accept**: a weighted two-partition BiRank fixture executes under `uv run`, projection rejects `MultiGraph` as expected, historical `final_results` with NULL IDs produce deterministic derived IDs, and rewrite-disabled behavior remains unchanged.

### Phase 1 — Judge label backfill (1–2 d)
- Add an offline adapter from successful `llm_judgments.result_quality` through the existing `final_results` URL join into `result_labels`.
- Persist parsed rubric components and provenance; use zero-based positions and idempotent latest-row deduplication.
- Keep fetch/dwell as a separate coverage report; do not fabricate query edges.
- **Accept**: ≥1,100 successful current-snapshot labels, deterministic replay, empty/error verdicts skipped and counted, focused tests.

### Phase 2 — Graph rebuild + related-query materialization (1–2 d)
- `analytics/graph_rebuild.py`: read cutoff-bounded labels and `search_runs` into `nx.Graph`, compute global BiRank/PageRank, query projection/AA related lists, and generation metrics.
- Persist via the approved writer/sidecar boundary; never direct-write the live DuckDB from an external CLI.
- **Accept**: <5 s at current judged-graph scale; deterministic fixture snapshot; shared-support coverage and zero-degree/Gini metrics; at least one related pair for head queries with ≥2 shared documents.

### Phase 2.5 — Offline expansion replay (½–1 d)
- Run `plan_search`-equivalent planning with graph lookup enabled but no provider calls.
- **Accept**: branch count remains 6; effective seeds ≤4 with original seed 0; `query_variants` remains six branch rows; Gemma sees the same effective seeds; rewrite-disabled output is byte-for-byte unchanged; prompt/cost and related-query coverage are measured.

### Phase 3 — Capped seed injection (1–2 d)
- Add `analytics/query_expansion.py` lookup and the hook after rake/autosuggest, before `_rewrite_queries`.
- Roll out behind `GRAPH_EXPANSION_ENABLED`, with generation pinning, per-query cap, cooldown, and shadow/canary sampling.
- **Accept**: no provider-count increase, no new branch rows, p95 lookup <20 ms from precomputed data, non-regression in judged quality/NDCG/domain diversity, and rollback by flag.

### Phase 4 — Optional graph ranking signal (1–2 d)
- First test a bounded post-rerank slate blend, because that is an honest hook in the current pipeline; record its recall limitation.
- Only then consider a graph-feature argument in `rerank_results`/stage contracts for >100-candidate influence.
- **Accept**: flag-off exact equivalence; time-split replay with leakage guard; calibrated weight; NDCG@10, result-quality, top-10 unique domains, provider cost, and rerank survival all non-regressive.

### Phase 5+
- Optional seventh branch only behind a separate budget/experiment flag.
- `rake_terms` term-bridge nodes or `query_transforms` edges only after their semantics are explicitly labeled; they are not current feedback edges.
- IPS when fetch attribution is substantial; LGBMRanker only after materially more labeled query-document pairs; MotherDuck plus a dedicated Docket Worker for scheduled rebuilds.
---

## 5. Risks

| Risk | Mitigation |
|---|---|
| Extra provider cost from naive fanout | Effective-seed injection only; six fixed branches; hard seed/term caps |
| Historical NULL canonical IDs | Derive with `_canonical_result_id(link)` and verify against production outcome writes |
| Fetch signal mistaken for query feedback | Keep fetch/dwell document-only until an explicit run-to-fetch join has coverage |
| Position/rubric leakage | Use cutoff-bounded successful judgments, exact run+URL joins, zero-based rank, and latest-row deduplication |
| Graph feature silently ignored by reranker | Shadow first; document the <=100 versus >100 candidate behavior; use a real stage contract for future influence |
| Popularity collapse | BiRank degree normalization, Adamic-Adar popularity correction, support caps, age cutoff, and diversity metric |
| AA/projection misuse | Simple `nx.Graph`; project for candidates, run AA on original bipartite B and same-partition pairs only |
| Rebuild stall or concurrent DuckDB writes | Read-only snapshot, existing single-writer/sidecar boundary, generation pinning, previous-good fallback |
| Transitive NetworkX pin drift | Explicit direct dependency approval in Phase 0 |
| Thin head support | Require ≥2 shared judged documents; exact/embedding fallback is no-op when unsupported |

---

## 6. Sources

- Official NetworkX BiRank source shows the runtime `numpy`/`scipy` imports and the weighted sparse iteration: https://networkx.org/documentation/stable/_modules/networkx/algorithms/bipartite/link_analysis.html
- He, Gao, Kan, Wang. *BiRank: Towards Ranking on Bipartite Graphs*. IEEE TKDE 29(1), 2017: https://arxiv.org/abs/1708.04396
- Code exemplar: `AddBirank` calls NetworkX with explicit partitions, personalization, damping, iteration, and weight arguments: https://github.com/Jose-Velasco/multi-model-recommender/blob/main/gnn_utils/transforms.py
- Microsoft WSDM'13 click-through bipartite graph work: query/document similarity and high-quality similar-query finding, not a direct proof of this rollout: https://www.microsoft.com/en-us/research/publication/learning-query-document-similarities-click-bipartite-graph-metadata/
- Query-Centric Graph RAG: capped related-query, neighbor-query, and associated-chunk traversal; uses synthetic Doc2Query nodes, so this plan reuses historical judged queries instead: https://arxiv.org/html/2509.21237v1
- Existing in-repo multi-query + RRF: `search/planning.py:259-487`, `search/ranking.py:57-152`, and `search/merge.py` RRF.
- Existing local identity/labels/feedback seams: `analytics/observability_ids.py:23-24`, `search/outcomes.py:193`, `analytics/judges.py:1330-1346`, `analytics/views.py:510-513,1381-1400`, `analytics/writers/core.py:692-795`.
- Meilisearch synonyms: in-query expansion with exact query precedence, an industry analogy for seed injection rather than an extra retrieval topology: https://www.meilisearch.com/docs/learn/relevancy/synonyms
- Current Docket Cron source: automatic scheduling/reseeding is tied to Worker startup; do not assume stdio owns a persistent scheduler: https://github.com/chrisguidry/docket/blob/main/src/docket/dependencies/_cron.py
- He-inspired reference implementation: `BrianAronson/birankr`; its default `normalizer='HITS'` is not the He symmetric form, while `normalizer='BiRank'` is. NetworkX is preferred at current scale; SciPy is the escape hatch: https://github.com/BrianAronson/birankr
