---
type: source
title: "Observation: ProviderGroup staleness in search package"
slug: obs-2026-07-17-providergroup-staleness-in-search-package
status: observation
created: 2026-07-17
updated: 2026-07-17
relevance: high
observed_at: 2026-07-17T11:15:36.830Z
tags: ["search", "provider_registry", "provider-group", "staleness", "dead-code", "paid_serp"]
source_context: "Planning search package refactor"
---
# ⭐ Observation: ProviderGroup staleness in search package
Discovered in search/architecture session 2026-07-17: `ProviderGroup` (FREE/PAID_SERP/SPECIALIZED) is dead metadata. The only consumer is `select_provider_names()` at `search/provider_registry.py:161-175`, which excludes `SPECIALIZED` providers from the auto-pick allowlist unless an `IntentSearchPolicy.specialized_providers` tuple opts them in. No intent policy opts in `tavily`, `jina`, `grok_openrouter`, `hackernews`, `reddit`, `github_graphql` — so they are unreachable through `web_search`. Reclassification to `PAID_SERP` is the fix. Plan files: local://search-package-refactor-langsearch-plan.md steps 5 and 8. Tag/evidence: tests/test_intent_policy.py does not assert specialized providers per intent, only intent→category mapping.
*Relevance: high*

*Context: Planning search package refactor*

*Tags: search provider_registry provider-group staleness dead-code paid_serp*
---
*Observed: 2026-07-17T11:15:36.830Z*