---
type: source
title: "Observation: Gemini promoted to primary RankLLM stage"
slug: obs-2026-07-16-gemini-promoted-to-primary-rankllm-stage
status: observation
created: 2026-07-16
updated: 2026-07-16
relevance: critical
observed_at: 2026-07-16T15:50:48.515Z
tags: ["rerank", "gemini", "openrouter", "optimization", "latency"]
source_context: "Promoting Gemini to primary RankLLM stage for production latency optimization"
---
# 🔴 Observation: Gemini promoted to primary RankLLM stage
Promoted Gemini ('gemini-3.1-flash-lite') to be the primary RankLLM coordinator and moved OpenRouter to the fallback position. This change reduces the production pipeline latency from ~80-100s (when waiting for OpenRouter's free model to timeout under large contexts) to ~20-25s. Verified that Gemini executes successfully on 30 candidates, returning the complete permutation and final results in under 2 seconds, and is correctly recorded in analytics as 'success' status.
*Relevance: critical*

*Context: Promoting Gemini to primary RankLLM stage for production latency optimization*

*Tags: rerank gemini openrouter optimization latency*
---
*Observed: 2026-07-16T15:50:48.515Z*