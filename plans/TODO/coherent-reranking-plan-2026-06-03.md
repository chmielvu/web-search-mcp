# Coherent Reranking Plan

Date: 2026-06-03

Source documents consolidated:

- `critical-analysis-fastmcp-rerank-tool-strategy-2026-06-03.md`
- `observability-action-recommendations-2026-06-03.md`
- `rag-mcp-eval-frameworks-addendum-2026-06-03.md`
- `mcp-eval-llm-judge-frameworks-research-2026-06-03.md`

## Goal

Build a measured reranking system for `web_search` that improves source usefulness without adding unacceptable latency, provider fragility, or hidden agent behavior.

The plan is benchmark-first. No custom Cloud Run deployment should become the default path until local eval proves quality lift and latency fit on this repo's real short web-search candidates.

## Core Decisions

1. Treat reranking as a measured stage, not a guaranteed improvement.
2. Separate **SERP candidate rerank** from future **passage/content rerank**.
3. Start with cheap local baselines before private Cloud Run.
4. Use Infinity as the first custom-serving bakeoff engine.
5. Keep TEI as a valid production candidate, not the default assumption.
6. Preserve current public rerank providers as fallback during migration.
7. Add bypass rules for query classes where rerank hurts or wastes time.

## Scope

In scope:

- Rerank eval harness.
- Local CPU baselines.
- Engine abstraction.
- Model bakeoff.
- Candidate survival analytics.
- Optional private Cloud Run deployment after benchmark signal.

Out of scope:

- Replacing search providers.
- Fetch/content extraction refactors.
- GLiNER hot-path routing.
- Passage-level reranking until SERP rerank is measured.
- GPU deployment before CPU and local baselines fail acceptance targets.

## Target Architecture

```text
provider candidates
  -> merge / RRF
  -> rerank eligibility policy
  -> rerank engine abstraction
  -> selected engine
  -> score normalization
  -> candidate survival event
  -> final lightweight web_search results
```

### Rerank Engine Order

1. `none`: control path, merged/RRF order only.
2. `current_public`: current Voyage/Jina API behavior.
3. `flashrank` or `fastembed`: local MiniLM CPU baseline.
4. `infinity`: custom serving bakeoff, first custom engine.
5. `tei`: custom serving production candidate.
6. `cloud_run`: private deployment wrapper after winning engine is selected.

### Model Bakeoff Set

Required:

- `cross-encoder/ms-marco-MiniLM-L6-v2`
- `BAAI/bge-reranker-v2-m3`
- `Qwen/Qwen3-Reranker-0.6B`
- `mixedbread-ai/mxbai-rerank-base-v2`

Optional:

- `jinaai/jina-reranker-v3`, only if license constraints allow it.

## Phase 0: Evaluation Dataset

Build a rerank dataset from real traces and a small hand-labeled set.

Minimum case count:

- 50 real `web_search` queries from traces.
- 20 hand-labeled gold cases for calibration.

Case categories:

- package/library docs lookup
- exact error or stack trace
- GitHub issue/discussion lookup
- current web/current docs lookup
- comparison/research query
- ambiguous query
- known-good URL/domain expected

Each case stores:

- query
- research_goal
- query_type
- provider set
- pre-rerank candidates
- current final order
- expected good domains/URLs where known
- notes on why top results are useful or not useful

## Phase 1: Deterministic Rerank Metrics

Implement non-LLM metrics first.

Metrics:

- MRR@5
- nDCG@10
- top-3 gold URL/domain hit
- duplicate URL/domain rate
- provider survival rate
- final source diversity
- candidate count before/after rerank
- p50/p95/p99 rerank latency
- timeout rate
- fallback rate

Store:

- pre-rerank candidate set
- post-rerank candidate set
- engine/model metadata
- score distribution
- failure/fallback reason

## Phase 2: LLM-as-Judge Rerank Metrics

Use the DeepEval-style custom judge pattern from the observability/eval plan.

Initial judge metrics:

- `source_usefulness`
- `ranking_quality`

For `ranking_quality`, use pairwise comparison:

```text
List A = pre-rerank order
List B = post-rerank order
Question = which list better satisfies query + research_goal?
```

Store:

- winner
- score
- confidence
- rationale
- evidence candidate IDs

P1 enhancement:

- Add order-swap judging to detect position bias.

## Phase 3: Local Baselines

Implement local CPU baseline before custom serving.

Preferred baseline:

- FlashRank or FastEmbed with MiniLM.

Why:

- Fast enough for short title/snippet candidates.
- No network dependency.
- Cheap way to prove whether rerank helps this MCP's payloads.

Acceptance:

- p95 under 500 ms for 20-50 candidates on the target dev machine or documented benchmark host.
- No worse than current public rerank on `source_usefulness` and `ranking_quality`.
- Clear bypass path if local rerank does not improve a query class.

## Phase 4: Engine Abstraction

Add a stable internal rerank abstraction.

Conceptual interface:

```python
class RerankEngine:
    name: str

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        research_goal: str | None,
        timeout_seconds: float,
    ) -> RerankResult:
        ...
```

Result shape:

- ordered candidate indexes
- scores
- model id
- engine id
- duration_ms
- fallback_reason
- warnings

Hard rule:

- On rerank failure, preserve merged/RRF candidate order.

## Phase 5: Infinity Bakeoff

Use Infinity as the first custom serving engine.

Why:

- Explicit rerank serving support.
- Good fit for testing several open reranker models.
- Better for model bakeoff than committing to TEI first.

Evaluation:

- BGE-v2-m3
- Mixedbread
- Qwen3 if serving path is stable

Acceptance:

- Quality lift over local MiniLM or documented reason to skip.
- p95 meets target on warm service.
- cold-start behavior documented.
- timeout/fallback path verified.

## Phase 6: TEI / Cloud Run Candidate

Use TEI only after benchmark signal.

Valid TEI use:

- model support is clean
- deployment is simpler than Infinity for selected model
- Cloud Run private IAM can be configured

Cloud Run shape:

- private service
- no unauthenticated access
- service account with invoker role
- model loaded at startup
- CPU first
- `min-instances=0` during evaluation
- `min-instances=1` only if hot path needs stable p95
- GPU/L4 only if CPU fails quality/latency targets

## Rerank Bypass Policy

Bypass rerank when:

- candidate count is below threshold
- query is exact literal / stack trace / hash / UUID
- query is navigational and top provider result is exact domain match
- previous eval shows rerank hurts this query class
- rerank budget is exhausted
- engine health is degraded

The bypass must emit an observability event with reason.

## Observability Requirements

Emit events for:

- rerank eligibility decision
- engine selected
- model selected
- candidate count
- duration_ms
- timeout
- fallback reason
- score distribution summary
- provider survival
- final top-k domains

Grafana panels:

- rerank p50/p95/p99 by engine/model
- rerank timeout/fallback rate
- candidate survival by provider
- judge score by engine/model
- rerank lift by query type

## Acceptance Criteria

P0 is done when:

- 50+ rerank eval cases exist.
- deterministic rerank metrics are persisted.
- local MiniLM baseline is measured.
- current public rerank is compared against `none` and local baseline.
- failures preserve merged candidate order.

P1 is done when:

- Infinity bakeoff is measured.
- DeepEval-style `source_usefulness` and `ranking_quality` judges are implemented.
- dashboards show rerank latency and quality lift.
- bypass policy is active and observable.

P2 is done when:

- private Cloud Run candidate is measured.
- selected engine/model has documented latency, cost, quality, and fallback behavior.
- default reranker can be switched by config without code changes.

## Final Recommendation

Do not deploy a custom Cloud Run reranker first.

First build the eval harness, local baseline, engine abstraction, and bypass policy. Then run Infinity and TEI as measured candidates. Make the default engine the one that wins on this repo's real short web-search candidate payloads.
