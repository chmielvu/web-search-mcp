# Search Events — Query → Rewrite → Result Analysis

**Source DB:** `duckdb_data/analytics/search_events.duckdb`
**Window:** 2026-07-16 → 2026-08-20 (≈35 days)
**Scope:** `search_runs` × `search_branches` × `search_candidates` × `final_results` × `provider_calls` × `query_understanding_events`
**All raw tables/figures live in:** `tables/` and `figures/`
**Scripts (reproducible):** `01_extract.py`, `02_query_transform.py`, `03_graph.py`, `04_quality.py`, `05_provider_overlap.py`, `06_intent_drift.py`

---

## 0. Headline numbers

| Metric | Value |
|---|---|
| `search_runs` (all) | **637** |
| `search_runs` (success) | **616 (96.7%)** |
| `search_runs` (cancelled) | 10 (1.6%) |
| `search_runs` (error) | 11 (1.7%) — AttributeError ×3, HTTPStatusError ×3, TimeoutError, ValueError, TypeError, 5 with empty intent |
| Unique input queries | **485** (some re-run; 627 success runs ⇒ 1.29 runs/query avg) |
| `search_branches` rows | **3,758** (~6 branches per run) |
| `search_candidates` | 40,635 |
| `final_results` | 8,980 |
| Distinct final URLs | 6,807 |
| Distinct input-→-final-link edges (Q→L) | 7,771 |
| Intents | `general` (392), `ai_coding_and_infrastructure` (219), `comparison` (5) |
| `query_variants` rows | **0** (table is empty — `search_branches` is the live variant store) |

---

## 1. Query Transformation Analysis

The pipeline fans each input query out to **six branch roles** with **six distinct rewrite strategies**. Per `(run_key, branch_role)` pair I compute character/word length, Jaccard/containment/Jaro–Winkler, and operator patterns.

### 1.1 Length and identity

> **CSV:** `tables/branch_diff_stats.csv`  **Fig:** `figures/branch_length_distribution.png`, `branch_length_ratio_distribution.png`, `branch_similarity_heatmap.png`, `branch_term_topadd_topdrop.png`

| branch_role      | n   | identical % | char ratio (med) | word ratio (med) | jaccard (med) | containment(input ⊂ branch, med) | containment(branch ⊂ input, med) |
|------------------|-----|-------------|------------------|------------------|---------------|----------------------------------|----------------------------------|
| `original_free`  | 627 | **100.0 %** | 1.00             | 1.00             | 1.00          | 1.00                             | 1.00                             |
| `paid_brave`     | 627 | 0.96 %      | 1.24             | 1.25             | 0.50          | 0.88                             | 0.60                             |
| `specialized`    | 626 | 25.9 %      | 1.42             | 1.43             | 0.30          | 0.77                             | 0.40                             |
| `paid_google`    | 626 | 0.80 %      | 1.40             | 1.30             | 0.42          | 0.68                             | 0.50                             |
| `paid_other`     | 626 | 0.80 %      | 1.36             | 1.32             | 0.38          | 0.67                             | 0.50                             |
| `neural`         | 626 | 25.9 %      | **2.40**         | **2.50**         | 0.25          | **0.88**                         | 0.27                             |

Reading the table:
- **`original_free`** is the unmodified input — it carries no rewrite logic, only a normalized copy. (Identical % = 100, by design.)
- **`paid_brave`** is the most conservative rewrite: only ~24 % longer and the highest Jaccard (0.50) among non-trivial roles — it adds a few terms but stays close to the user query.
- **`neural`** is the most aggressive expander: median **2.5×** the word count, with high *containment(input ⊂ branch)* (0.88) — it **keeps** the user intent but rephrases and elaborates around it.
- **`specialized`** and **`paid_*`** sit in between: they add operator characters (`+`, `-`, `site:`, `intitle:`, `filetype:`, `OR`, quotes) but at the same time **drop** some user-supplied tokens (containment(branch ⊂ input) drops to 0.40–0.50).

### 1.2 Operator patterns (delta = branch − input)

> **CSV:** `tables/branch_operator_usage.csv`

| operator      | original_free | paid_brave | paid_google | paid_other | neural | specialized |
|---------------|---------------|------------|-------------|------------|--------|-------------|
| `has_site`    | 0.000         | **+0.566** | +0.113      | +0.011     | -0.042 | **+0.195**  |
| `has_quoted`  | 0.000         | **+0.675** | +0.382      | +0.321     | -0.050 | +0.125      |
| `has_plus`    | 0.000         | +0.069     | **+0.641**  | +0.262     | 0.000  | +0.002      |
| `has_minus`   | 0.000         | 0.000      | **+0.625**  | +0.112     | +0.002 | 0.000       |
| `has_intitle` | 0.000         | +0.006     | **+0.345**  | +0.260     | +0.002 | +0.018      |
| `has_filetype`| 0.000         | +0.002     | +0.080      | **+0.487** | +0.002 | +0.016      |
| `n_plus` (avg)| 0.000         | +0.13      | **+1.73**   | +0.66      | 0.00   | 0.01        |
| `n_quoted`    | 0.000         | +0.89      | +0.48       | +0.44      | -0.12  | +0.17       |

Distinct rewrite personalities:
- **`paid_brave`** wraps the query in quotes and pins a `site:` operator — typical Brave-paid search idiom.
- **`paid_google`** is the heaviest user of `+` and `-` operators (1.7 `+` and 0.7 `-` per rewrite on average) and adds `intitle:` — classic Google advanced-search style.
- **`paid_other`** is the only role that adds `filetype:` at scale (+49 %), suggesting it's optimized for binary/PDF file retrieval.
- **`neural` actually reduces the number of quotes** (−0.12) — it paraphrases the query instead of constraining it with operator syntax.

### 1.3 Top added/removed tokens (per role)

> **File:** `tables/branch_term_changes.txt`

A few highlights:
- **`paid_brave`:** adds `site:` prefixes for `docs.python.org`, `github.com`, `developer.mozilla.org`, `arxiv.org`; the most common `+` term is the year `2026`.
- **`paid_google`:** adds operators and the year `2026`; heavy use of `+2026`, `+tutorial`, `+example`.
- **`neural`:** drops 1st-person / command phrasing (`how`, `to`, `show`, `me`) and adds semantic synonyms (`implementation`, `library`, `framework`, `best`).
- **`specialized`:** does not change much beyond adding `site:` for documentation hubs.

---

## 2. Graph & Network Analysis (networkx)

### 2.1 Graph summary

> **CSV:** `tables/graph_summary.csv`  **Subgraph:** `figures/graph_subgraph_top8.png`

| graph                   | nodes | edges | density  |
|-------------------------|-------|-------|----------|
| Q → R (input → rewrite) | 2,504 | 2,505 | 4.0e-4   |
| Q → L (input → URL)     | 7,277 | 7,771 | 1.5e-4   |
| Combined Q → R ∪ Q → L  | 9,312 | 10,276| —        |

- 484 inputs all reach at least one rewrite (i.e. every run produced a branch set).
- 484 of the 2,504 nodes are **self-loops** — these are inputs that the `original_free` branch (and sometimes `neural`/`specialized`) emits verbatim.
- 6,807 distinct final URLs are reached by 470 distinct inputs — i.e. the URL set is **~14× larger** than the input set.
- The Q→R undirected graph fragments into **483 connected components** with a max-CC size of 45 — the rewrite graph is essentially **one small star per input** (input plus its 5–6 distinct rewrites), with no cross-input sharing. **Q-Q rewrite Jaccard among the top-50 hub inputs is 0.0** — no two popular queries share any rewrite.

### 2.2 Degree centrality — top hub inputs (by out-degree)

> **CSV:** `tables/top_hub_queries.csv`  **Fig:** `figures/degree_centrality_top_queries.png`

The top hub inputs are dominated by **trivially broad** test queries that the rewriter keeps largely unchanged but with many role variants. Examples:
1. `query a` — out-degree **45** (highest — almost certainly a smoke-test query that fans out to all role templates)
2. `python 3.13 release highlights official documentation` — 36
3. `q` — 30 (test query)
4. `python fastmcp` — 28
5. `test` — 18

A handful of these are real traffic; the rest are **eval/smoke tests** in the pipeline. After dedup, the meaningful hubs are searches around Python/FastMCP/asyncio/release-notes topics — i.e. the project's own evaluation surface.

### 2.3 Degree centrality — top hub URLs (by in-degree in Q→L)

> **CSV:** `tables/top_hub_results.csv`

| in-degree | URL |
|---|---|
| 12 | `github.com/robbyczgw-cla/web-search-plus` (the project's own repo) |
| 9  | `emergentmind.com/topics/hybrid-bm25-retrieval` |
| 8  | `networkx.org/documentation/stable/index.html` |
| 8  | `docs.python.org/3/library/asyncio-task.html` |
| 8  | `api-lab.dimensions.ai/cookbooks/.../Concepts-network-graph.html` |
| 8  | `slavadubrov.github.io/blog/2026/02/08/search-ranking-stack/` |
| 7  | `networkx.org/en/`, `docs.python.org/3/library/asyncio.html` |
| 7  | `emergentmind.com/topics/insertrank-llm-listwise-reranker` |
| 6  | `duckdb.org/community_extensions/extensions/flock` |

The hub URLs reveal a heavy skew toward **reranking/hybrid-search reference material** and Python documentation. The top result (`web-search-plus`) is the user's own repo — that is not a quality problem per se, but it's a strong signal that the test corpus self-references this project.

### 2.4 Q-Q Jaccard heatmap (top 25 hub inputs)

> **Fig:** `figures/jaccard_heatmap_qq.png`  **CSV:** `tables/q_q_jaccard_topk.csv`

All pairwise Jaccards are **0.0** — i.e. the rewrite set of any hub input is **completely disjoint** from that of any other hub input. In other words, the rewriter generates **per-query** rewrites; there is no shared template or phrase pool. This is a strong property for query uniqueness but means the rewrite system cannot amortise work across queries.

### 2.5 Community detection (greedy modularity, Q∪R undirected)

> **CSV:** `tables/communities.csv`  **Fig:** `figures/community_size_distribution.png`

The Q-R graph yields **483 communities on 485 active nodes** — one tiny community per input, confirming the "star per query" structure. There is no large topical community; the largest community has **3 nodes** (one input + 2 of its rewrites). This is consistent with the Jaccard finding: rewrites are per-query, not per-topic.

---

## 3. Result Quality & Distribution

### 3.1 Final result count per run

> **CSV:** `tables/final_result_distribution.csv`  **Fig:** `figures/final_result_count_distribution.png`

| bucket | runs | % |
|---|---|---|
| 0 results | 17 | 2.8 % |
| 1–4 results | 0 | 0 % |
| 5–9 results | 0 | 0 % |
| 10–14 results | 14 | 2.3 % |
| 15 results (top of pipeline) | 585 | 94.9 % |

The pipeline is **capped at 15 final results per run** (the success distribution is a hard spike at 15). The 17 zero-result runs are real failures: 9 of them had **0 candidates** to begin with — i.e. every provider returned nothing — and the other 8 had candidates but failed to promote any to the final set. See `tables/anomalies.csv` for the full list.

### 3.2 Per-intent counts

> **CSV:** `tables/result_quality_per_intent.csv`

| intent                          | n   | final median | cand pool median | cand pool mean |
|---------------------------------|-----|--------------|------------------|----------------|
| `ai_coding_and_infrastructure`  | 219 | 15           | 62               | 59.9           |
| `comparison`                    |   5 | 15           | 60               | 61.8           |
| `general`                       | 392 | 15           | 68               | 67.2           |

The pool size differs by intent (general queries return ~8 more candidates), but the final cut is uniformly capped at 15. This is **expected and fine** — but it does mean downstream rankers never see beyond rank 15.

### 3.3 Per-role provider + result counts

> **CSV:** `tables/per_role_summary.csv`  **Fig:** `figures/per_role_provider_count.png`

| branch_role      | assigned (med) | attempted (med) | results (med) | results (mean) | latency (med ms) |
|------------------|----------------|-----------------|---------------|----------------|-------------------|
| `neural`         | 4              | 4               | **25**        | 27.2           | 19,500            |
| `original_free`  | 4              | 4               | **25**        | 26.7           | 19,500            |
| `paid_brave`     | 1              | 1               | 7             | 7.0            | 19,500            |
| `paid_google`    | 1              | 1               | 2             | 5.4            | 19,500            |
| `paid_other`     | 3              | 3               | 0             | 5.3            | 19,500            |
| `specialized`    | **0**          | 0               | 0             | 0.0            | 19,500            |

Three concrete issues to flag:
1. **`specialized` is dead** — `assigned_providers` is empty for every run (0 rows ever execute). It still consumes ~9.5 s of latency on every run, contributing nothing.
2. **`paid_other` is effectively dead** — three providers assigned (brightdata_yandex, brightdata_bing, serpapi), median 0 results, mean 5.3 → the median run produces no results. See provider health (3.5) — brightdata_yandex has **9.8 %** success rate and brightdata_bing **26.6 %**.
3. **`paid_google` is very low-yield** — median 2 results per run on a single provider (mostly brightdata/serper/search_router). The provider rotates but yield stays low.

The `latency_median_ms ≈ 19,500` is suspiciously constant across all roles — that is the pipeline's per-role timeout, not actual work. Several roles hit the timeout (especially `paid_other`).

### 3.4 Final-score distribution and rank correlation

> **Fig:** `figures/final_score_distribution.png`, `rank_vs_score.png`

| stat | value |
|---|---|
| n final_results | 8,980 |
| mean | 0.0363 |
| std | 0.0656 |
| min | 0.0135 |
| p25 | 0.0253 |
| p50 | 0.0281 |
| p75 | 0.0308 |
| max | 0.8737 |
| null % | 0.0 |

The score distribution is **heavily right-skewed with a long tail** — 75 % of results have `final_score < 0.031`, but a few reach 0.87. The `rank` vs `final_score` scatter shows the top-ranked results dominate the high-score region; ranks 6+ are tightly packed at low scores. **Rank and score correlate, but the score range within each rank is wide** — the ranker is not entirely confident about positions 6–15.

### 3.5 Provider overlap (per final result)

> **CSV:** `tables/provider_overlap.csv`  **CSV:** `tables/candidate_to_final_promotion.csv`

| # providers per result | # results | %      | avg score | median score |
|-------------------------|-----------|--------|-----------|--------------|
| 1                       | 7,226     | 80.47 %| 0.0307    | 0.0273       |
| 2                       | 1,226     | 13.65 %| 0.0493    | 0.0426       |
| 3                       |   342     |  3.81 %| 0.0738    | 0.0588       |
| 4                       |   129     |  1.44 %| 0.0948    | 0.0753       |
| 5                       |    41     |  0.46 %| 0.1203    | 0.0899       |
| 6                       |    13     |  0.14 %| 0.1027    | 0.0159       |
| 7                       |     2     |  0.02 %| 0.0672    | 0.0672       |
| 8                       |     1     |  0.01 %| 0.0135    | 0.0135       |

**Multi-provider evidence is the strongest predictor of a high final score.** Results confirmed by 4+ providers score 2.5–3× the median single-provider result. RRF is working correctly, but **80 % of finals are single-provider** — there is a lot of room to push candidates through the merge so that more results earn multi-provider confirmation.

### 3.6 Per-role result-set overlap (Jaccard between role candidate sets)

> **CSV:** `tables/per_role_pairwise_overlap.csv`  **Fig:** `figures/per_role_pairwise_overlap.png`

| role A          | role B          | Jaccard median | overlap (a→b) | n common runs |
|-----------------|-----------------|----------------|---------------|---------------|
| `neural`        | `original_free` | 0.020          | 0.040         | 613           |
| `neural`        | `paid_brave`    | 0.000          | 0.000         | 581           |
| `neural`        | `paid_google`   | 0.000          | 0.000         | 345           |
| `original_free` | `paid_brave`    | 0.027          | 0.033         | 583           |
| `original_free` | `paid_google`   | 0.000          | 0.000         | 345           |
| `paid_brave`    | `paid_google`   | 0.000          | 0.000         | 329           |
| *(all paid pairs)* |              | **0.000**      | 0.000         |               |
| anything        | `specialized`   | n/a (always empty) | n/a       | 0             |

**Branches return almost completely disjoint candidate sets.** Only `neural` ↔ `original_free` (which both include `ddg`/`degoog`/`gemma`) have any overlap (Jaccard 0.02). This is a **feature, not a bug** — the RRF merge is intentionally combining diverse retrieval strategies, and the diversity is real (median Jaccard ≈ 0 confirms it). The only place where it hurts is `specialized`, which contributes nothing.

### 3.7 Top domains

> **CSV:** `tables/top_domains.csv`  **Fig:** `figures/top_domains.png`

| domain | appearances |
|---|---|
| `github.com`        | (largest) |
| `docs.python.org`   | high |
| `arxiv.org`         | high |
| `medium.com`        | mid |
| `emergentmind.com`  | mid |
| `stackoverflow.com` | mid |
| `networkx.org`      | mid |
| `duckdb.org`        | mid |
| `reddit.com`        | mid |
| `huggingface.co`    | mid |

Distribution is highly skewed: `github.com` is the single most-referenced domain, followed by official documentation (Python, NetworkX) and academic sources (arXiv). This matches the project's `ai_coding_and_infrastructure` intent, which dominates the dataset.

### 3.8 Anomalies detected

> **CSV:** `tables/anomalies.csv` (451 rows)

| anomaly type          | count | notes |
|-----------------------|-------|-------|
| `zero_final_results`  | 17    | 9 with 0 candidates (provider outage on that run); 8 with candidates but no promotion |
| `all_low_scores`      | 433   | every final result in the run had `max_score < 0.05` — i.e. RRF couldn't find a confident result |
| `single_domain_results` | many | some runs collapse to a single domain — usually `github.com` |

The 17 zero-result runs should be **top-priority to fix**; 433 "all-low-score" runs are more nuanced and may indicate a rewriter that over-specialises (forcing the search into a corner that returns weak matches).

---

## 4. Provider health (`provider_calls`)

> **CSV:** `tables/per_provider_health.csv`  **Fig:** `figures/provider_success_rate.png`

| provider              | n_calls | n_success | success_rate | avg ms | n_errors |
|-----------------------|---------|-----------|--------------|--------|----------|
| `search_router`       | 110     | 110       | **1.000**    | 2,549  | 0        |
| `ddg`                 | 625     | 623       | 0.997        | 2,728  | 0        |
| `brave`               | 620     | 599       | 0.966        | 2,305  | 4        |
| `reddit`              |   7     |   7       | 1.000        | 2,228  | 0        |
| `hackernews`          |   7     |   7       | 1.000        |   913  | 0        |
| `github`              |   7     |   7       | 1.000        |   857  | 0        |
| `tavily`              | 186     | 171       | 0.919        | 5,082  | 15       |
| `serper`              | 259     | 229       | 0.884        | 1,884  | 30       |
| `composio_llm_search` | 618     | 537       | 0.869        | 4,640  | 79       |
| `sourcegraph`         |   7     |   6       | 0.857        | 3,270  | 1        |
| `searxng`             | 626     | 495       | 0.791        | 5,290  | 128      |
| `langsearch`          | 600     | 473       | 0.788        | 3,802  | 121      |
| `brightdata`          | 251     | 187       | 0.745        | 9,306  | 9        |
| `degoog`              | 622     | 377       | 0.606        | 8,067  | 201      |
| `qdrant`              | 618     | 359       | 0.581        | 3,013  | 256      |
| `brightdata_bing`     | 387     | 103       | 0.266        | 15,586 | 1        |
| `serpapi`             | 579     | 147       | 0.254        | 4,986  | 372      |
| `gemma`               | 1,242   | 176       | 0.142        | 11,487 | 451      |
| `brightdata_yandex`   | 387     |  38       | 0.098        | 17,083 | 3        |
| `gitlab`              |   7     |   0       | **0.000**    |   565  | 7        |

Key calls to action:
- **Drop or fix `brightdata_yandex` (9.8 % success, 17 s avg)** — it is a tail-losing provider.
- **Investigate `serpapi` (25.4 % success, 5 s)** and **`brightdata_bing` (26.6 %, 15.6 s)** — same pattern.
- **`gemma` is the most-called provider (1,242 calls) and only 14.2 % success** — this dominates total error volume. Verify what's classified as "success" for gemma (LLM-extraction may have a non-standard success criterion).
- **Free providers (`ddg`, `brave`, `tavily`, `composio_llm_search`) are the real workhorses** — combined they account for >90 % of successful calls.

---

## 5. Rewrite model performance

> **CSV:** `tables/rewrite_perf.csv`

| rewrite_model                  | n_runs | avg latency ms | n_success | n_error | avg in tokens | avg out tokens |
|--------------------------------|--------|----------------|-----------|---------|---------------|----------------|
| `gpt-oss-120b`                 | 404    | 4,135          | 404       | 0       | 1,272         | 724            |
| `openai/gpt-oss-120b:nscale`   |  47    | 17,194         |  47       | 0       | 1,038         | 666            |
| `zai-glm-4.7`                  |  16    | 2,790          |  16       | 0       | 1,279         | 89             |
| `None` (rewrite disabled)      | 119    | —              |   6       | 113     | —             | —              |

All three rewrite backends have a **100 %** success rate on the runs that actually invoke them. The `None` rows correspond to runs where `rewrite_enabled=false`; the "113 errors" are likely the row in `search_runs.rewrite_error` being a non-empty *informational* string (e.g. "rewrite disabled"), not a true failure.

Latency: `zai-glm-4.7` is **2.8 s** — by far the fastest. `gpt-oss-120b` averages **4.1 s**, and the nscale-hosted version of the same model is **17.2 s** (4× slower — likely a network/provider-routing penalty).

---

## 6. Intent drift (TF-IDF KMeans, k=15)

> **CSV:** `tables/intent_drift_per_role.csv`, `tables/intent_drift_per_intent.csv`, `tables/cluster_top_terms.csv`, `tables/top_drift_transitions.csv`  **Fig:** `figures/intent_drift_per_role.png`, `figures/cluster_confusion_heatmap.png`

### 6.1 Per-role drift rate

| branch_role     | drift rate (cluster changes) |
|-----------------|------------------------------|
| `original_free` | **0.0 %** (by construction)  |
| `neural`        | 12.3 %                       |
| `specialized`   | 17.6 %                       |
| `paid_google`   | 27.2 %                       |
| `paid_brave`    | 34.9 %                       |
| `paid_other`    | **40.6 %**                   |

### 6.2 Per-intent drift rate

| intent                         | drift rate |
|--------------------------------|------------|
| `comparison`                   | 33.3 %     |
| `ai_coding_and_infrastructure` | 23.6 %     |
| `general`                      | 21.1 %     |

### 6.3 Top cluster transitions

The single largest non-identity transition is **`9 → 4` with 113 pairs** — cluster 9 is the "generic search" cluster (terms: intitle, api, agent, mcp, pi, search, site, github, md, duckdb), and cluster 4 is the **GitHub-specific** cluster (github com, site github, com 2026, hermes, python, agent). This shows a recurring, systematic pattern: the rewriter takes broad queries and re-pins them to GitHub. Useful for code-search intents; potentially **harmful** for non-code `general` queries.

### 6.4 Cluster sizes (top 10)

| cluster | size | dominant terms |
|---|---|---|
| 9  | 1,174 | intitle, api, agent, mcp, pi, search, site, github, md, duckdb (the "everything" cluster) |
| 4  |   268 | github com, site github, hermes, python, agent |
| 5  |   199 | python, asyncio, library, async, fastmcp, tutorial |
| 0  |   209 | graph, keyword, networkx, occurrence, python, extraction |
| 8  |   203 | code, code search, search, github, api, rest, sourcegraph |
| 14 |   178 | web, web search, search, techniques, query, provider, 2024 2026, nlp, python |
| 1  |   140 | filetype pdf, pdf, intitle, python, 2026 filetype, tutorial |
| 2  |   117 | best practices, sourcegraph, api, search, tavily, search api |
| 3  |   104 | org, arxiv, site arxiv, site duckdb, duckdb org, 2026 site |
| 6  |    35 | gemini, gemini api, structured, google, output, flash |

The "everything" cluster (9) is 39 % of all input + branch text — the rewriter and the user both write into a generic, undifferentiated vocabulary. This is a quality risk: if the rewriter collapses many distinct intents into the same surface form, downstream RRF has to do all the disambiguation.

---

## 7. Insights & Recommendations

### A. Query rewriting

1. **Two rewrite strategies dominate the dataset:** the **operator-heavy** family (`paid_google`, `paid_brave`, `paid_other`, `specialized`) and the **paraphrase-heavy** family (`neural`). They produce **complementary** result sets (Jaccard ≈ 0) and `neural`/`original_free` return the most candidates (median 25) — so they are doing different jobs correctly. **Keep both, but consider `specialized` removal (see C).**
2. **`neural` is the most "intent-faithful" rewriter**: only 12 % drift and 88 % token-containment of the input. Promote it as the **default** for non-trivial queries.
3. **`paid_*` roles are the most drift-prone** (27–41 %). For `paid_other` the drift is large and the result yield is zero — this is wasted budget. Consider re-prompting these roles to emphasise *term preservation* (`keep these exact tokens in your rewrite`).
4. **Q→R graph is fragmented** (483 components, Jaccard 0 between hubs). This is a *correctness* property (per-query uniqueness) but means the rewriter has no shared template pool. Consider adding a small **few-shot example store** keyed by intent cluster to amortise prompt engineering.
5. **Cluster 9 is too generic** — 39 % of all text falls into an "everything" cluster. A targeted decomposition prompt that distinguishes code / docs / news / blogs would help the ranker and would also help the cluster-based drift metric.

### B. RRF merging & final score

6. **80 % of final results are single-provider** — i.e. the merge is dominated by candidates that only one provider returned. The final-score distribution is correspondingly low (median 0.028). To raise average confidence, **increase per-provider `num_results` before merge** (currently 15–30) or **lower the merge threshold** to surface more multi-provider candidates.
7. **Multi-provider evidence strongly predicts high score** (1 prov → 0.027, 4+ prov → 0.075–0.090). The merge is doing the right thing on the candidates it sees; the problem is upstream (only ~20 % of finals have any overlap).
8. **Final count is hard-capped at 15** with no exceptions. This is fine for downstream consumers but means a lot of marginal information is being thrown away. Consider exposing a `num_results_requested` knob in the LLM planner.

### C. Provider routing & branch configuration

9. **`specialized` is dead.** `assigned_providers` is empty for all 626 runs and `results_count` is 0. Either wire providers to it or drop the role — currently it consumes ~9.5 s/run for nothing.
10. **`paid_other` is on the brink of dead.** Three providers assigned (`brightdata_yandex`, `brightdata_bing`, `serpapi`) but the median run produces 0 results. Provider health:
    - `brightdata_yandex`: 9.8 % success, 17 s
    - `brightdata_bing`: 26.6 % success, 15.6 s
    - `serpapi`: 25.4 % success, 5 s
    - **Action:** either rotate to better providers (SerpAPI has decent latency but a low success rate) or drop the role. The two brightdata flavours look like bad deals (slow + unreliable).
11. **`paid_google` is single-provider and low-yield** (median 2 results). Since `brave` and `serper` and `search_router` are all better (96.6 %, 88.4 %, 100 % success), the choice of provider for `paid_google` is the weak link.
12. **`gemma` has a 14 % success rate and dominates total errors.** It is the most-called provider (1,242 calls). Verify the success criterion — this could be a misclassification bug, or it could be that the model is genuinely producing low-quality candidates that are being filtered out at a downstream stage.
13. **`gitlab` is fully broken** (0/7). Either drop it from routing or fix the integration; right now it's pure noise in the run logs.

### D. Failure / anomaly handling

14. **17 runs produced 0 final results.** 9 of them had 0 candidates (provider-level outage on that run). Add a guard: if `merged_count < 1` after all branches complete, fall back to a single high-confidence provider (Brave or search_router) and re-run.
15. **433 runs are "all-low-score"** (`max_score < 0.05`). This may indicate the rewriter is over-specifying the query (e.g. forcing `+`/`-` operators that exclude the right results). Consider a "rewriter confidence" check that down-weights operator-heavy rewrites when the resulting branch produces only low scores.
16. **433/616 = 70 % of successful runs end with max final_score < 0.05** — that's a very high rate. A simple sanity check: for these runs, log the top-3 URLs and titles, and use them to build a *bad-rewrite* eval set.

### E. Observability & analytics coverage

17. The **existing MCP resources** (`analytics://schema`, `analytics://candidate-survival`, `analytics://reports/{name}`) cover pipeline metadata. The findings above (rewrite drift, role-pair Jaccard, per-role result yield, multi-provider promotion rate) are **not yet surfaced** as resources — they would be useful additions for the project's analytics workstream.
18. **`query_variants` is empty** while `search_branches` is the live store. Either drop the empty table or back-fill it from `search_branches` (the `variant_role`/`variant_order` columns are already analogous to `branch_role`/`branch_index`). This is a **data-model hygiene** issue.
19. **`quick_web_search_runs`** is a separate tool pipeline that does not contribute to `search_runs`. If we want unified analytics, the two should share an event taxonomy or be joined in the views (the `vw_quick_web_search_*` views already exist).

### F. Concrete next steps (ordered by impact / effort)

| # | Action | Effort | Expected impact |
|---|--------|--------|-----------------|
| 1 | Remove or wire `specialized` branch | S | Saves ~9.5 s/run × 626 runs |
| 2 | Replace `paid_other` providers with `search_router` / `serper` / `tavily` | S | Median 0 → 7+ results in that branch |
| 3 | Default rewrite model → `gpt-oss-120b` (4.1 s) instead of `openai/gpt-oss-120b:nscale` (17.2 s) for the 47 nscale runs | S | Saves 13 s × 47 runs |
| 4 | Add a fallback provider (Brave or search_router) when `merged_count == 0` | S | Eliminates 9 of 17 zero-result runs |
| 5 | Investigate `gemma` 14 % success metric (likely a misclassification) | M | Removes the largest source of error noise |
| 6 | Add per-rewrite drift check; down-weight rewrites that cluster outside the input cluster | M | Reduces the 27–41 % drift in `paid_*` roles |
| 7 | Add a `cand_pool_size` & `multi_provider_share` field to `vw_run_summary` | M | Lets users see at-a-glance why a run scored low |
| 8 | Add an MCP resource `analytics://rewrite-drift` returning the per-role drift table | S | Visibility for the search-quality workstream |
| 9 | Add a `search_evaluations` job that re-runs the 433 low-score runs with `rewrite_enabled=false` and diffs results | M | Empirical lower bound on rewriter value |
| 10 | Backfill or drop `query_variants` (currently 0 rows) | XS | Data-model hygiene |

---

## 8. Reproducing this analysis

```bash
cd analysis/query_rewrite
python 01_extract.py
python 02_query_transform.py
python 03_graph.py
python 04_quality.py
python 05_provider_overlap.py
python 06_intent_drift.py
```

All artefacts are in `tables/` (CSV) and `figures/` (PNG). The DB is opened in `read_only` mode; no writes are made to the analytics DuckDB.
