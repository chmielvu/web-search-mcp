---
type: source
title: "Observation: brd_json=1 stale code in Bright Data builders"
slug: obs-2026-07-17-brd-json-1-stale-code-in-bright-data-builders
status: observation
created: 2026-07-17
updated: 2026-07-17
relevance: high
observed_at: 2026-07-17T11:15:36.884Z
tags: ["search", "brightdata", "brd_json", "staleness", "tests"]
source_context: "Planning search package refactor"
---
# ⭐ Observation: brd_json=1 stale code in Bright Data builders
Discovered in search/architecture session 2026-07-17: `build_google_url` (`search/brightdata_common.py:86`) and `build_bing_url` (`search/brightdata_common.py:105`) still append `&brd_json=1`. The Yandex fix from 2026-07-14 removed it from Yandex only. Both Google and Bing consumers call `response.json()` on the result, so the flag is dead weight. Four tests assert the stale behavior: `tests/test_brightdata_common.py:38,73` and `tests/test_brightdata_engines.py:21,30`. Fix: drop the two URL appends and flip the four tests from `assertIn` to `assertNotIn`. The Yandex builder already omits the flag, so flipping its test passes without further code change. Plan: local://search-package-refactor-langsearch-plan.md step 6.
*Relevance: high*

*Context: Planning search package refactor*

*Tags: search brightdata brd_json staleness tests*
---
*Observed: 2026-07-17T11:15:36.884Z*