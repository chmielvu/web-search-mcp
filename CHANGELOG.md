# Changelog

## [Unreleased]

### Changed — Reranking Pipeline Overhaul
- **MMR now uses reranker scores for relevance** instead of embedding cosine similarity. The cross-encoder/LLM reranker scores are min-max normalized and used as the MMR relevance term; embeddings are only used for the diversity (document-to-document) term. This fixes the critical issue where MMR ignored expensive reranker scores and recomputed relevance from weaker embedding similarity.
- **MMR lambda default changed from 0.5 to 0.7** — relevance-weighted for web search (was 50/50 relevance/diversity, now 70/30). Research consensus: λ=0.7-0.8 for precision search.
- **Cross-encoder document construction enriched** — now includes Domain, Providers, ProviderCount in addition to Title, Snippet, URL. Snippet moved to second position (after Title) for better semantic importance with Cohere rerank v4.
- **Bi-encoder filter widened from top_k*2 to top_k*3** (20→30 candidates for top_k=10) — reduces recall bottleneck, gives cross-encoder more candidates to evaluate.
- **LLM reranker prompt now includes query_type and research_goal** — the cross-encoder already received this context; the LLM reranker (most capable of using it) now gets it too.
- **LLM reranker scores changed from 1/position to exponential decay** `exp(-0.3*(pos-1))` — smoother relevance gradient (1.0, 0.74, 0.55, 0.41... instead of 1.0, 0.5, 0.33, 0.25...).
- **Score propagation fixed after MMR** — candidate scores are now updated to reflect MMR ordering using exponential decay, so final scores match final ranking order. Previously, scores reflected pre-MMR LLM ranking while order reflected MMR — causing score-order inconsistency in analytics.
- **Reranker fallback chain simplified to ONE chain**: `cohere_fast -> cohere_fast_openrouter -> voyage`. Always tries in this order regardless of configured engine. Jina and GCP CloudRun rerankers removed entirely (GCP deprecated, Jina redundant).

### Fixed
- **Critical: branch deadline race causing 0 results on every non-cached search.** `branch_executor.py` set `branch_deadline = provider_group_deadline_seconds` (15s), identical to the `dispatch_providers` internal deadline. Due to `asyncio.wait` timer scheduling order, the branch timer (scheduled first) fires before the provider timer, cancelling the branch task before `dispatch_providers` can return partial results. Fix: `branch_deadline = provider_deadline + DEFAULT_DRAIN_TIMEOUT_SECONDS + 2.0` (20s), giving dispatch_providers time to return fast-provider results before the branch cancels.
- **Branch drain now non-blocking.** Changed `await asyncio.wait(pending, timeout=drain)` to `asyncio.create_task(_branch_drain())`, matching the pattern already used in `provider_dispatch.py`. Saves up to 3s on branch timeout.
- **Composio search toolkit version updated** from `20260424_00` to `20260618_00`. The old version required external TAVILY/EXA/SERPAPI API keys on the Composio platform. The new version uses Composio's managed auth (`NO_AUTH` scheme).

## [0.4.0] — 2026-06-28

### Added
- **TinyBERT-4L ONNX INT8 intent classifier** — replaces LLM-backed query understanding as primary path. 83% accuracy, 84% F1 macro across 6 search intents. ~5ms latency vs ~60s LLM.
- **6-class SearchIntent system** — expanded from 4 intents (`general`, `ai_coding`, `digital_humanities`, `comparison`) to 6 (`general`, `ai_coding_and_infrastructure`, `digital_humanities`, `comparison`, `social_media`, `news`). Updated all intents.py, intent_policy.py, schema, prompts, analytics judges.
- **Dockerized classifier service** on VPS at port 8686. FastAPI with /health and /classify endpoints. CPU-only torch image, 300MB RAM, auto-restart.
- **Persistent SSH tunnel** via systemd user service + autossh (port 18686 → VPS:8686). Auto-restarts on connection drop.
- **Training pipeline** — distilabel + Gemini API for synthetic data generation, custom GeminiLLM class, class-weighted WeightedTrainer, ONNX export + INT8 quantization.
- Classification report: general=0.86, social_media=0.87, digital_humanities=1.00, comparison=0.76, ai_coding=0.79, news=0.73.

### Changed
- `resolve_query_understanding` in resolver.py — ONNX classifier is primary path, LLM query understanding is fallback (only when classifier service is down).
- Added `intent_classifier_url`, `intent_classifier_timeout_seconds`, `intent_classifier_confidence_threshold`, `intent_classifier_enabled` settings.
- Intent aliases now support both `ai_coding` (old) and `ai_coding_and_infrastructure` (new) for backward compatibility.
- Training dataset expanded from 120 → 538 unique records across 6 labels.

### Files (new)
- `training/generate_synthetic.py` — distilabel pipeline for synthetic data generation
- `training/generate_augment.py` — targeted augmentation for weak classes
- `training/train_tinybert.py` — TinyBERT-4L fine-tuning script
- `training/intent_classifier/` — Docker service files
- `src/.../search/understanding/onnx_classifier.py` — HTTP client for ONNX service
- `src/.../search/intent_policy.py` — intent routing to SearXNG categories
- `tests/test_intent_policy.py`

### Architecture
```
Pipeline: query → ONNX classifier (~5ms) → intent
               ↓ (if service down)
               LLM query understanding (~60s, fallback)
```
