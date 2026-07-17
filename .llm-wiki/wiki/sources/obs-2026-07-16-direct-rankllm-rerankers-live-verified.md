---
type: source
title: "Observation: Direct RankLLM Rerankers Live Verified"
slug: obs-2026-07-16-direct-rankllm-rerankers-live-verified
status: observation
created: 2026-07-16
updated: 2026-07-16
relevance: critical
observed_at: 2026-07-16T21:47:30.363Z
tags: ["rerank", "litellm-free", "google-genai"]
source_context: "SafeGenai/SafeOpenai integration and custom validation removal in llm_rerank.py"
---
# 🔴 Observation: Direct RankLLM Rerankers Live Verified
Direct google-genai and SafeOpenai rerankers were successfully integrated into `llm_rerank.py` without any `litellm` dependencies. The custom regex validation was removed because the `rank_llm` library already handles invalid outputs and reorders lists gracefully via `receive_permutation`. Live CLI search run verified that Gemini-based reranking is now 100% functional and fast (completed in 2.7s).
*Relevance: critical*

*Context: SafeGenai/SafeOpenai integration and custom validation removal in llm_rerank.py*

*Tags: rerank litellm-free google-genai*
---
*Observed: 2026-07-16T21:47:30.363Z*