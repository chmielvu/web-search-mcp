---
type: source
title: "Observation: Conditional RankLLM live pipeline verified"
slug: obs-2026-07-16-conditional-rankllm-live-pipeline-verified
status: observation
created: 2026-07-16
updated: 2026-07-16
relevance: high
observed_at: 2026-07-16T13:13:23.622Z
tags: ["rerank", "rankllm", "live-verification", "search"]
source_context: "End-to-end normal pipeline verification of conditional reranking"
---
# ⭐ Observation: Conditional RankLLM live pipeline verified
Normal web-search CLI run b348df7c-ab02-4809-9bf9-1cccc4d23be1 processed 61 candidates and returned 15 unique results. Cohere reranked all 61; OpenRouter hit its 20-second bound; Gemini completed two strict RankLLM sliding windows and was accepted for 30/30 candidates; conditional MMR triggered and reconstructed 30/30. Final provider/model: gemini / gemini-3.1-flash-lite. Fixes included ast.literal_eval decoding of RankLLM regex source literals, normalized-query-only RankLLM input, primary-source risk-token boundary, and constructor stdout suppression.
*Relevance: high*

*Context: End-to-end normal pipeline verification of conditional reranking*

*Tags: rerank rankllm live-verification search*
---
*Observed: 2026-07-16T13:13:23.622Z*