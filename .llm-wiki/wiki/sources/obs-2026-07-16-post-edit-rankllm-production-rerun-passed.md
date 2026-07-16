---
type: source
title: "Observation: Post-edit RankLLM production rerun passed"
slug: obs-2026-07-16-post-edit-rankllm-production-rerun-passed
status: observation
created: 2026-07-16
updated: 2026-07-16
relevance: high
observed_at: 2026-07-16T13:27:53.210Z
tags: ["rerank", "rankllm", "production", "smoke", "verification"]
source_context: "Fresh real-life verification after code edits"
---
# ⭐ Observation: Post-edit RankLLM production rerun passed
Fresh normal CLI run 23df2bb5-d1b0-4d22-8c42-3388f01ea4ab after subsequent source edits succeeded for query 'Cohere Rerank v4 calibration threshold guidance'. It persisted 61 unique candidates and 15/15 unique final links. Cohere reranked 61/61 in 468 ms; OpenRouter was bounded at 20 seconds; Gemini RankLLM was accepted for 30/30; MMR triggered and reconstructed 30/30; final pool remained 61/61. Pipeline duration 49.318 seconds. LiteLLM still emitted an async logging-worker RuntimeWarning, but response and persistence succeeded.
*Relevance: high*

*Context: Fresh real-life verification after code edits*

*Tags: rerank rankllm production smoke verification*
---
*Observed: 2026-07-16T13:27:53.210Z*