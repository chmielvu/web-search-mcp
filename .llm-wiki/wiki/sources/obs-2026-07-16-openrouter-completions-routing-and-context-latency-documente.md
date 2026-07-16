---
type: source
title: "Observation: OpenRouter completions routing and context latency documented"
slug: obs-2026-07-16-openrouter-completions-routing-and-context-latency-documente
status: observation
created: 2026-07-16
updated: 2026-07-16
relevance: high
observed_at: 2026-07-16T15:44:49.342Z
tags: ["rerank", "openrouter", "litellm", "latency"]
source_context: "Debugging OpenRouter routing and latency during monotone funnel integration"
---
# ⭐ Observation: OpenRouter completions routing and context latency documented
Added 'openrouter_chat_base_url' (https://openrouter.ai/api/v1) to settings.py and updated 'BoundedSafeLiteLLM._call_completion' in llm_rerank.py to override LiteLLM completions routing when using OpenRouter. This correctly routes the LLM reranker to standard chat completions instead of the rerank endpoint. Findings also show that under real search queries, 30 candidates with real snippets generate prompts of 8,000+ tokens, which naturally cause the free model 'nvidia/nemotron-3-nano-30b-a3b:free' to exceed the 20s timeout and successfully trigger the fallback to Gemini.
*Relevance: high*

*Context: Debugging OpenRouter routing and latency during monotone funnel integration*

*Tags: rerank openrouter litellm latency*
---
*Observed: 2026-07-16T15:44:49.342Z*