# Ranking, Fusion & Diversity Quality Plan — 2026-09-03

Scope: response to an external critique of `web_search`'s fusion/reranking/diversity
stack, verified line-by-line against current code (not the critique's assumptions)
and against two independent live `web_search` calls made in this session. Several
critique points turned out to be stale (already fixed) or imprecise; two new,
higher-priority bugs were found that the critique didn't mention. This document is
the plan — no code has been changed.

## Method

- Read `search/merge.py`, `search/ranking.py`, `search/planning.py`,
  `search/normalize.py`, `utils/url_canonicalize.py`, `rerank/core.py`,
  `rerank/diversity.py`, `rerank/llm_rerank.py`, `rerank/stage_runner.py`,
  `rerank/stages.py`, `rerank/models.py`, `models.py`, `settings.py`,
  `tools/code_search/models.py` in full or in targeted ranges.
- Cross-checked every claim against `grep`/GitNexus call-site results, not just
  the function under discussion.
- Made two live `web_search` calls in this session against the running MCP server
  and inspected the raw JSON (scores, provider lists, query_shaping) as ground
  truth for what actually reaches an agent, not what the docstring says reaches it.
- External prior art (Elastic, MongoDB, OpenSearch, independent blog posts) pulled
  via `web_search`/`gemini_search` inside this same investigation.

## Verdict on the submitted critique

| # | Critique claim | Verdict | Notes |
|---|---|---|---|
| 1 | Unweighted RRF treats all lists as equal voters | **Confirmed, current** | No `weights` parameter exists anywhere in `reciprocal_rank_fusion`. |
| 2 | Volume bias: `original`/`free` emit more lists than `exa` | **Confirmed, and worse than stated** | It's not just role imbalance — `original` and `free` fire the identical 4 providers (`ddg, qdrant, searxng, degoog`) with two different but overlapping queries. See F1. |
| 4 | Public output hides ranking evidence; scores don't survive | **Partially wrong** | `score`, `hybrid_rrf_score`, `cross_relevance_score`, `provider_count`, `providers` all serialize to the live tool response today (verified by direct call). The real gap is documentation and one dead field, not missing data. See F6. |
| 6 | Near-duplicate pages survive canonicalization; MMR only fires on high similarity | **Confirmed, mechanism worse than stated** | Confirmed the canonicalizer's exact allowlist. Also discovered MMR frequently doesn't run *at all* for typical result-pool sizes — not just "only fires on high similarity." See F3. |
| 7 | MMR ignores RankLLM relevance (hardcoded 1.0) | **Confirmed, current** | `select_diverse_slate` has no relevance parameter in its signature at all. Also contradicts a changelog memory claiming this was fixed — it wasn't, or a regression reintroduced it. |
| — | (not in critique) Fusion score field is meaningless | **New finding** | See F5 — independently discovered and proven with live numbers. |
| — | (not in critique) MMR is dormant for small pools | **New finding, highest priority** | See F3 — this is a bigger problem than "MMR ignores relevance," because most of the time MMR doesn't execute at all. |
| — | (not in critique) `provider_count` under-counts | **New finding** | See F1 — the consensus signal the tool docstring tells agents to trust can be silently wrong. |

## Findings

### F1 — Volume bias is literal same-provider double-firing, and `provider_count` can't see it

`search/planning.py:421-437` constructs six branches:

```python
QueryBranch(role=BranchRole.ORIGINAL, query=normalized_query,  provider_names=original)  # ddg, qdrant, searxng, degoog
QueryBranch(role=BranchRole.FREE,     query=queries[0],        provider_names=free)      # ddg, qdrant, searxng, degoog  <- same 4
QueryBranch(role=BranchRole.SERP1,    query=queries[1],        provider_names=serp1)     # brave only
QueryBranch(role=BranchRole.SERP2,    query=queries[2],        provider_names=serp2)     # 1 of brightdata/serper/search_router
QueryBranch(role=BranchRole.SEMANTIC_TAVILY, query=queries[3], provider_names=semantic_tavily)
QueryBranch(role=BranchRole.SEMANTIC_EXA,    query=queries[4], provider_names=semantic_exa)  # exa only
```

`_ORIGINAL_CANDIDATES` and `_FREE_CANDIDATES` are both
`("ddg", "qdrant", "searxng", "degoog")` (`planning.py:~60-70`). So when both
branches are eligible, each of those four providers is queried **twice** — once
with the raw normalized query, once with an LLM-paraphrased "free" variant — and
each firing produces its own `ProviderRankedResults` (`search/contracts.py:105-109`:
one instance per `(branch_role, provider_name)` pair). `ranking.py:52-56` flattens
every `ProviderRankedResults.results` into a flat `provider_result_lists`, discarding
`branch_role` and `provider_name` at exactly that point. Verified live: for the query
"weighted reciprocal rank fusion RRF hybrid search", the response's `query_shaping`
showed all four `free`-role providers received a near-identical rewritten query
("...Verify the numeric spread WebSearchResult.score", trimmed from the same
research goal text) — i.e. in a real run, DDG gets queried twice with substantially
overlapping text while Exa gets queried once.

This alone would just be "role imbalance" (critique's framing). It's worse, because
of `merge.py:79,83,92`:

```python
merged[key] = _MergedCandidate(result=result, providers=set(result.providers or []))
...
bucket.providers.update(provider for provider in result.providers or [] if provider)
...
"provider_count": len(bucket.providers),
```

`bucket.providers` is a **set of provider names**, not a count of contributing
*lists*. If `ddg` contributes the same URL from both its `original`-branch list and
its `free`-branch list, `bucket.score` (line 82: `bucket.score += 1.0 / (k + rank)`,
unconditional, once per list) gets **two** independent RRF contributions, but
`provider_count` still reports **1** distinct provider. The tool docstring
(`tools/search.py`) explicitly tells agents "evaluate results based on
`provider_count` (>=2 indicates high consensus)" — so a document double-boosted by
one provider querying twice looks, from the public API, exactly like single-provider
coverage. The consensus signal the docstring asks agents to trust can be silently
wrong in the direction that matters (looks weaker than its actual influence on
ranking).

### F2 — No weighting mechanism exists

`reciprocal_rank_fusion` (`merge.py:55-93`, read in full, current signature):

```python
def reciprocal_rank_fusion(
    result_lists: Sequence[Sequence[WebSearchResult]],
    *, k: int = 60, canonicalize: Callable[[str], str] | None = None,
) -> list[tuple[WebSearchResult, float]]:
```

No `weights` parameter, and `settings.py` has no `rrf_role_weights` /
`rrf_provider_weights` or equivalent — confirmed by reading every RRF/MMR-adjacent
setting (`rrf_k=60`, `mmr_lambda_param=0.70`, `diversity_similarity_threshold=0.85`,
`diversity_max_per_host=2`). Weighted RRF is not implemented anywhere in this
codebase today.

### F3 — MMR diversity is dormant for the common (small-pool) case — highest-priority new finding

`rerank/core.py:262-283` (verbatim comment in the code):

```python
# Conditional MMR diversity over the final slate, reusing the
# bi-encoder embeddings already computed upstream. Skipped when no
# embedding context exists (bi-encoder stage did not run).
embedding_ctx = bi_outcome.embedding_context
if embedding_ctx is not None and len(final_results) > 1:
    ...
    if len(slate_embeddings) == len(final_results):
        slate = select_diverse_slate(...)
```

Bi-encoder only runs when the merged candidate pool exceeds its limit
(`rerank/AGENTS.md`: funnel is 100 → 30 → 15; bi-encoder is skipped whenever
`original_count <= 100`). For a typical personal-agent `web_search` call — six
branches, several singleton-provider roles, heavy dedup — the merged pool is
routinely well under 100. In that (common) case `bi_outcome.embedding_context` is
`None`, and the **entire diversity block is skipped unconditionally**: no
embeddings, no cosine check, no host-cap enforcement, nothing. This is a bigger
problem than "MMR ignores relevance" (F4) — most of the time, for the exact
workload this system is tuned for, MMR does not run *at all*, and near-duplicate /
same-host clustering (critique #6) is completely unmitigated.

The fix is cheap: MMR only ever needs to embed the **final slate** (≤15 results,
per `top_k=15` / `FINAL=15` funnel limit), not the full upstream pool. That's a tiny,
unconditional embedding batch, independent of whether the bi-encoder stage ran on
the full pool. See Phase 2 below.

### F4 — When MMR does run, it's relevance-blind and reorders globally

`rerank/diversity.py`, `select_diverse_slate` full signature:

```python
def select_diverse_slate(
    embeddings: Sequence[Sequence[float]],
    urls: Sequence[str],
    *, output_size: int, lambda_param=None, similarity_threshold=None, max_per_host=None,
) -> DiverseSlate:
```

There is **no relevance/score parameter at all** — confirmed by reading the full
function. This contradicts a prior session's changelog memory claiming RankLLM
relevance was threaded into MMR; either that fix never shipped or was reverted.
The module docstring itself says diversity is "conditional": it only reorders when
a pairwise-similarity or host-cap trigger fires, and when it does, the **whole**
final slate is re-derived by the greedy walk (not just the offending pair/region) —
confirmed via the loop bounds (`output_size=len(final_results)`, i.e. the entire
slate, and the loop only exits early via a host-cap-exhausted `break`, after which
untouched tail items are appended in original order — so host-cap is a *soft*
preference, not a hard filter). Once triggered, a document's actual relevance
(cross-encoder or RankLLM score) plays no role in who gets kept versus diversified
away — pure similarity/host-count minimization, which can and will demote a highly
relevant result in favor of a weaker one that merely looks more "different."

### F5 — The public `score` field is a content-free restatement of array position

`rerank/llm_rerank.py:_ranked_permutation` produces `RerankResult(index=..., score=1.0/(60+position))`
(a rank-derived value, correctly interpreted as ordinal information). That value
flows into `stages.py:apply_ranked_results`, which defines a real min-max
normalizer (`normalize_scores_minmax`, lines 12-24) specifically to turn that
ordinal value into a properly spread 0–1 magnitude for display — **but**
`stage_runner.py:_apply_ranked_stage` (the *only* caller of `apply_ranked_results`
in the entire codebase, confirmed by grep) hardcodes `preserve_raw_scores=True`.
Since `preserve_raw_scores=True` always takes the `if` branch
(`stages.py:58`: `raw_scores if preserve_raw_scores else normalize_scores_minmax(raw_scores)`),
`normalize_scores_minmax` is **unreachable dead code**.

Proven live in this session (query: "weighted reciprocal rank fusion RRF hybrid
search"), the `score` field across all 15 results was, to 15 decimal places,
exactly `1.0 / (60 + i)` for `i = 0..14` (the final array position) —
`0.016666666666666666, 0.01639344262295082, 0.016129032258064516, ...,
0.013513513513513514`. **The field literally carries zero information beyond "this
is result #N" — an agent could compute it from array position alone.** Meanwhile
`cross_relevance_score` in the same response ranged `0.6545`–`0.7743` and did *not*
correlate monotonically with position (result #1 had the single highest
`cross_relevance_score` in the whole slate, 0.7743) — i.e. the field that actually
carries differentiated relevance information is not the field named `score`.

The fix requires zero new computation and zero risk to ordering: final order is
already fixed by `ordered_ranked`'s sequence (`stages.py`: `candidates =
[candidates[item.index] for item in ordered_ranked]`), **not** by re-sorting on
the score value. Flipping `preserve_raw_scores=False` at the one call site changes
only what number is written into `.score` — order is unaffected.

### F6 — "Scores don't survive to output" is false; the real gap is documentation and one dead field

Live-verified: `score`, `hybrid_rrf_score`, `cross_relevance_score`, `providers`,
`provider_count` all appear in the raw MCP response today (all tool/CLI response
paths use `response.model_dump(exclude_none=True)` with no field-stripping step,
confirmed by grep across `composio_tools.py`, `quick_web_search.py`, `cli/services/*`,
`tools/*.py`). What's actually wrong:

- `provider_consensus_rrf_score` is permanently `None` in every observed sample —
  its own docstring says "DEPRECATED... Now None; the pipeline uses a single fused
  RRF pass with BM25." This is dead weight in every response payload.
- `raw_score` is populated by only 4 of the ~19 web providers (`degoog`, `exa`,
  `qdrant`, `searxng` — confirmed by grep; `brave`/`ddg` never set it), and even
  for those, `merge.py`'s `_pick_better` (line 29-30) selects the surviving copy of
  a duplicate URL purely by **snippet length**, not by which copy carries a
  `raw_score` — so it is frequently clobbered to `None` even when a contributing
  provider did set it. Live-verified: 0/15 results had a non-null `raw_score` in a
  run where `degoog` and `exa` were both contributing providers.
- The `web_search` tool docstring tells agents to check `provider_count >= 2` and
  says nothing about `score`, `hybrid_rrf_score`, or `cross_relevance_score` — three
  overlapping numeric fields with three different meanings, none of them
  documented, and (per F5) one of them useless.

### F7 — Near-duplicate URL handling is accurate as described

`utils/url_canonicalize.py`, read in full: lowercases scheme/host, strips a leading
`www.`, strips a fixed allowlist (`fbclid, gclid, igshid, mc_cid, mc_eid, mkt_tok,
ref, ref_src`) plus any `utm_*`-prefixed query param, drops the fragment, trims one
trailing slash. This correctly collapses `?utm_source=...` variants of the same
URL, but has and can have no way to collapse cross-domain mirrors (e.g. a
Medium/Dev.to syndication of a blog post) — that requires content-similarity
detection, which today exists nowhere except the MMR path, which per F3 usually
doesn't even run.

### F8 — No per-result fetch hint exists; a reusable pattern already exists elsewhere in this repo

`WebSearchResult` has no `fetch_hint`/next-step field. `tools/code_search/models.py`
already has exactly this shape for a different tool: `CodeSearchPublicNext(action:
str, tool: str, query: dict, why: str | None, confidence: str | None)`
(`models.py:~608-614`). This is a directly reusable pattern rather than a new design.

## External prior art (for Phase 1 weight design)

- **Elastic** `rrf` retriever: per-child-retriever `weight`, default 1, non-negative,
  applied inside the standard RRF sum (GA feature).
- **MongoDB** `$rankFusion`: `combination.weights` is a map from pipeline name to a
  non-negative weight (default 1), applied as `w × 1/(k+rank)` per pipeline, summed.
  MongoDB also ships `$scoreFusion` as a distinct, magnitude-preserving alternative
  with explicit normalization modes (`none`/`sigmoid`/`minMaxScaler`) — i.e. Mongo
  treats "rank fusion" and "expose a well-normalized magnitude" as two deliberately
  separate concerns, which maps directly onto the F5 fix (RRF for *ordering*,
  min-max for *display*, never conflate the two).
- **OpenSearch**: ships RRF today with only a single global rank constant; per-list
  "customizable weights" is explicitly called out as **not yet shipped, on their
  roadmap** — i.e. even a mature reference implementation still treats this as a
  reasonable-but-nontrivial extension, not something to bolt on carelessly.
- Independent write-up (blog.serghei.pl): "the common workaround is a weighted
  variant, Σ wᵣ/(k+r(d))... strictly speaking that is no longer canonical RRF, but
  it is everywhere in practice" — honest framing to carry into this repo's own
  docs: weighted RRF is a widely-adopted practical extension, not a magic fix for a
  weakest-link retriever, and needs eval-driven tuning rather than one-shot guesses.
- Concrete starting ranges from a implementation walkthrough (adaptiverecall.com):
  semantic/vector weight ~0.6–0.7, keyword weight ~0.5–0.6 of a normalized total —
  useful as a sanity check on magnitude, not as literal defaults for this repo's
  6-role architecture.

## Proposed plan

### Phase 0 — Correctness fixes (ship first; no new config surface; net-positive or neutral)

1. **Unbreak the score-normalization dead code (F5).** Change the one call site in
   `rerank/stage_runner.py:_apply_ranked_stage` from `preserve_raw_scores=True` to
   `preserve_raw_scores=False` (or make it stage-dependent if internal consumers
   ever need the raw ordinal — none currently do, since ordering already comes from
   permutation order, not from re-sorting on `.score`). Zero ordering risk. Delete
   or keep `normalize_scores_minmax` — it becomes live code either way.
2. **Delete `provider_consensus_rrf_score` (F6).** Permanently-`None`, self-described
   as deprecated. Matches this repo's stated "remove, don't add" convention (no
   deprecation limbo). Update `models.py`, any serialization tests, and the
   docstring/schema references.
3. **Fix `provider_count`'s same-provider blind spot (F1).** Before flattening
   `provider_result_lists` in `ranking.py`, collapse multiple `ProviderRankedResults`
   that share the same `provider_name` within one run into a single list (keep the
   best rank per canonical URL across the provider's multiple firings, e.g. via a
   small merge-by-url-keep-min-rank step) *before* they reach
   `reciprocal_rank_fusion`. This is the direct fix for "the same provider fired
   twice must not count as two independent voters," and it's what makes
   `provider_count` mean what the tool docstring claims it means. This is
   orthogonal to weighting (Phase 1) — ship independently.

### Phase 1 — Weighted RRF (the critique's core ask)

1. Add `weights: Sequence[float] | None = None` to `reciprocal_rank_fusion`
   (`merge.py`), defaulting to all-`1.0` (exact backward compatibility, matches
   Elastic/Mongo's "default weight = 1" convention). Multiply into the existing
   accumulation: `bucket.score += list_weight / (k + rank)`.
2. In `ranking.py`'s `provider_result_lists` construction loop, `prr.branch_role`
   and `prr.provider_name` are already present on every `ProviderRankedResults` —
   currently discarded. Build a parallel weights list via a small resolver,
   `role_weight[branch_role] * provider_weight.get(provider_name, 1.0)`, sourced
   from new `settings` fields (e.g. `rrf_role_weights: dict[str, float]`,
   `rrf_provider_weights: dict[str, float]`, `rrf_bm25_weight: float = 1.0` for the
   BM25 pseudo-list appended at `ranking.py:~113`). Validate all weights are
   non-negative (mirror Elastic/Mongo's constraint).
3. Starting point for role weights (to be tuned, not shipped as gospel):
   `original=1.0, free=0.6, serp1=1.2, serp2=1.3, semantic_tavily=1.2,
   semantic_exa=1.4, bm25=0.9`. `free` is deliberately discounted below `original`
   because, per F1/Phase-0-item-3, its distinct-evidence value is already partially
   reclaimed by the provider-collapse fix — the weight only needs to account for
   genuine rewrite-driven result differences, not raw duplication.
4. Ship behind the all-`1.0` default first (a verified no-op), then tune using
   Phase 4 below before changing defaults.

### Phase 2 — Activate and fix diversity (closes the biggest silent gap)

1. **Decouple MMR's embedding requirement from the bi-encoder pool-size gate
   (F3).** Right before the diversity check in `rerank/core.py`, if
   `bi_outcome.embedding_context` is `None` (bi-encoder was skipped) but
   `len(final_results) > 1`, compute a small, dedicated embedding batch for just
   the final slate (≤15 items) using the same embedding primitives
   `conditional_bi.py` already calls (`embed_query`/`bi_encoder_rank`). This is the
   single highest-leverage change in this plan: it turns near-duplicate/host
   diversity from "usually doesn't run" into "always available," for a trivial
   embedding cost (≤15 short strings).
2. **Thread relevance into MMR (F4).** Add `relevance_scores: Sequence[float]` to
   `select_diverse_slate`, sourced from each final result's existing
   `cross_relevance_score` (fall back to the RankLLM ordinal position if a
   cross-encoder score isn't available). Use it in the greedy selection as
   standard MMR: `argmax λ·relevance(d) − (1−λ)·max_sim(d, selected)`, replacing
   whatever implicit uniform weighting is used today. Keep `mmr_lambda_param`
   (already configurable, default 0.70) as the knob.
3. Leave the "conditional trigger" performance optimization itself intact (only
   run the walk when a real duplicate/host-overflow exists) — that part of the
   design is sound and cheap; F3's problem was the *embedding precondition*, not
   the conditional-trigger idea.

### Phase 3 — Evidence-rich output for agents

1. **Fix the docstring (F6).** Document all three live score fields precisely:
   `score` (post-fix: normalized 0–1 relevance-adjacent magnitude, highest is
   best), `hybrid_rrf_score` (pre-rerank single-stage fusion score, useful for
   understanding provider consensus before LLM reranking), `cross_relevance_score`
   (raw cross-encoder relevance, most directly comparable across results within one
   response). State plainly that `provider_count` now (post Phase-0-item-3) reflects
   distinct independent sources, not raw list count.
2. **Add a per-result `fetch_hint` (critique Section F), modeled on the existing
   `CodeSearchPublicNext` pattern (F8)** — `{action: "fetch", tool: "fetch", query:
   {url}, why, confidence}` — computed cheaply from data already on the result
   (e.g. `confidence` derived from `provider_count` + `cross_relevance_score`
   thresholds), for at least the top-N results, so agents get an explicit
   continuation instead of having to infer "I should probably fetch this" from
   scattered numeric fields.

### Phase 4 (optional, lower priority) — Deeper cross-domain near-duplicate detection

Phase 2's embedding-availability fix makes MMR's *existing* snippet-embedding
cosine check actually run, but snippet-embedding similarity will still miss
mirrors where provider-generated snippets differ in wording even though the
underlying article is identical (F7). If this remains a problem after Phase 2 ships:
consider a lightweight normalized-title similarity pass (e.g. token-Jaccard or
SimHash over the title) as an additional, independent signal ahead of MMR — cheap,
deterministic, and catches syndicated-content mirrors regardless of snippet wording.
Treat this as a follow-up to evaluate only if Phase 2 doesn't move the needle enough
— don't build it speculatively.

## Explicitly out of scope / not recommended

Given the personal-agent framing (favor deterministic, tunable, low-ceremony fixes
over heavyweight ML infrastructure):

- **Learning-to-rank / trained fusion weights.** The prior-art research (adaptiverecall,
  apxml) explicitly frames LTR as the next step "if you have resources and labeled
  data" — this repo doesn't have a labeled-relevance dataset at that scale, and a
  hand-tunable weighted RRF gets most of the value at a fraction of the complexity.
- **Per-query dynamic weight learning / bandit-based weight adaptation.** Real
  complexity for a personal-agent workload that doesn't have the query volume to
  converge such a system meaningfully.
- **A full content-fetch-and-diff dedup pass** (fetch every candidate URL's full
  text to detect mirrors before returning results) — defeats the purpose of a fast
  search tool; Phase 4's title-similarity approach gets most of the benefit for a
  fraction of the latency cost.

## Verification strategy

- Phase 0 items 1–2: unit-level — assert `apply_ranked_results` output score range
  spans more than the trivial `1/(60+pos)` band on a synthetic ranked-results input;
  assert `provider_consensus_rrf_score` is gone from the model and from a sample
  `model_dump()`.
- Phase 0 item 3 / Phase 1: this repo already has a real frozen-replay evaluation
  harness, `scripts/rerank_eval_fusion.py` — it replays captured live search runs
  through `SearchRelevanceJudge` across `overall, research_goal_usefulness,
  source_quality, relevance`, using intent-stratified sampling, currently only to
  sweep `RRF_VARIANTS = (20, 40, 60, 80)` for the global `k`. Extend it with a
  weight-grid sweep (role-weight vectors) alongside the existing `k` grid, reusing
  the same judge and sampling — this gives weight tuning the same evidentiary bar
  the project already applies to `k`, rather than shipping the critique's example
  weights (`semantic_exa=1.4`, etc.) as unverified guesses.
- Phase 2: add a case to `rerank_eval_diversity.py` covering a small (<100)
  candidate pool with two near-duplicate items, asserting diversity's embedding
  precondition is satisfied and the trigger fires — today that scenario silently
  no-ops.
- Run `gitnexus_impact` on `reciprocal_rank_fusion`, `apply_ranked_results`, and
  `select_diverse_slate` before editing, per this repo's standing rule, and
  `gitnexus_detect_changes` before committing each phase.

## Sequencing & effort summary

| Phase | Change | Effort | Risk | Depends on |
|---|---|---|---|---|
| 0.1 | Flip `preserve_raw_scores` for display | Trivial (1 line) | None — order unaffected | — |
| 0.2 | Delete `provider_consensus_rrf_score` | Small | None — always-null field | — |
| 0.3 | Collapse same-provider multi-branch lists before RRF | Small–Medium | Low | — |
| 1 | Weighted RRF (`weights` param + role/provider resolver) | Medium | Low if shipped all-1.0 first | 0.3 (cleaner signal to weight) |
| 2.1 | Decouple MMR embeddings from bi-encoder gate | Small–Medium | Low | — |
| 2.2 | Thread relevance into MMR | Small | Low | 2.1 (otherwise untestable in the common case) |
| 3 | Docstring fix + `fetch_hint` | Small | None | 0.1, 0.3 (so the docs describe the fixed behavior) |
| 4 | Title-similarity near-dup pass | Medium | Low | Evaluate only if 2 is insufficient |

Recommended order: **0 → 1 → 2 → 3**, with 4 deferred pending evaluation of 2's
impact. Phase 0 is pure correctness and should ship regardless of what happens
with weighting. Phase 2.1 (the MMR activation-gap fix) is the single highest-value
item in this plan for a personal-agent workload specifically, because it's the
difference between diversity logic existing in the codebase and diversity logic
ever actually running.
