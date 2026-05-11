# SearXNG Practitioner Research And Live Tuning Report

Date: 2026-05-11
Repo: web-search-mcp
Target instance: http://localhost:8080

## Executive Findings

The practical fix is not "enable more engines". Practitioner configs do often keep a wider catalog of engines such as Mojeek, Qwant/Brave, repo hosts, package registries, and science engines, but this MCP needs a profile that survives local SearXNG startup and returns relevant results for agent workloads.

The live profile now uses `use_default_settings.engines.keep_only` with 15 enabled engines:

- General default: `duckduckgo`, `startpage`, `wikipedia`
- Coding/Q&A available: `github`, `github code`, `stackoverflow`, `askubuntu`, `superuser`
- Packages/AI: `pypi`, `npm`, `huggingface`
- Science: `arxiv`, `semantic scholar`, `openalex`, `pubmed`

This is deliberately smaller than several practitioner configs because the wider set produced measurable problems locally.

## Practitioner Evidence Used

Public operator configs commonly converge on a whitelist/curated-engine model rather than enabling the full default SearXNG catalog:

- `picsky/enhanced-search-mcp` has a SearXNG `settings.yml` using `use_default_settings.engines.keep_only` and a broad MCP-oriented engine list including code hosts, package registries, science engines, and Mojeek: https://github.com/picsky/enhanced-search-mcp/blob/main/searxng/settings.yml
- `dantebarbieri/homeserver` has a `docker/searxng-ai/settings.yml` profile for AI search that also uses `keep_only` and includes general engines such as DuckDuckGo, Brave, Mojeek, Qwant, Startpage, Google, Bing, Marginalia, and science/news sources: https://github.com/dantebarbieri/homeserver/blob/main/docker/searxng-ai/settings.yml
- `MRNAQA/sourceweave-web-search` has an infrastructure SearXNG profile with `keep_only` including Brave, Qwant, Mojeek, and Wikipedia: https://github.com/MRNAQA/sourceweave-web-search/blob/main/infrastructure/searxng-settings.yml
- SearXNG-powered research tools such as `local-deep-research` expose query-time `categories` and `engines` controls in their SearXNG wrapper, which supports the architectural conclusion that engine selection belongs at query time, not only in global config: https://github.com/LearningCircuit/local-deep-research/blob/main/src/local_deep_research/web_search_engines/engines/search_engine_searxng.py

Interpretation: practitioner configs are useful for candidate discovery, but local live probes must decide the final enabled set.

## Live Tests And Observations

### Startup And Engine Health

After recreating the Docker volumes, `/config` reports 15 enabled engines:

`arxiv, wikipedia, duckduckgo, github, github code, huggingface, npm, openalex, pubmed, pypi, stackoverflow, askubuntu, superuser, semantic scholar, startpage`

Observed log status after final restart:

- No engine-load errors for the enabled engine set.
- `SEARXNG_REDIS_URL` deprecation was removed by switching compose to `SEARXNG_VALKEY_URL` and settings to `valkey.url`.
- Remaining log noise: SearXNG still reports missing limiter config and missing forwarded IP headers during local direct requests. This is not currently a search-quality blocker because limiter is intentionally disabled for local agent workloads.

### Query Quality / Latency

Measured through direct SearXNG JSON API after final config:

| Test | Latency | Result |
|---|---:|---|
| `FastMCP PromptsAsTools official docs gofastmcp.com 2026` | 3.14s | Top results included `gofastmcp.com` welcome/changelog and PyPI FastMCP |
| `"RuntimeError: Event loop is closed" "pytest-asyncio" github issue` | 2.64s | Top results were pytest-asyncio GitHub issues, StackOverflow, and related articles |
| `fastmcp python package latest version pypi` | 2.82s | Top result was PyPI FastMCP, followed by PyPI stats and related FastMCP pages |
| `retrieval augmented generation reranking benchmark` with `categories=science` | 4.71s | Returned arXiv, Semantic Scholar, OpenAlex, PubMed, and related science results |

### Engines Rejected By Live Evidence

- `bing`: previously returned high-rank unrelated pages for exact technical queries. It should remain excluded.
- `brave`: local SearXNG immediately received upstream too-many-requests suspension. It should remain excluded unless the instance has a reliable paid/API/proxy path.
- `mojeek`: useful candidate from practitioner configs, but this Docker path received HTTP 403 during the engine startup probe and SearXNG suspended it for 24h. Do not enable by default until its access path is fixed.
- `mdn` and `microsoft learn`: legitimate engines, but in `categories=it` they outranked Python/GitHub/package results for exact error/package queries. They should only be used through explicit query routing after the MCP supports and tests that path.
- `crossref`: produced useful science results, but timed out during live probes and triggered SearXNG unresponsive-engine errors; OpenAlex/Semantic Scholar/arXiv/PubMed are enough for the science lane for now.
- `docker hub`, `sourcehut`, `gitlab`, `codeberg`, `crates.io`, `lib.rs`, `pkg.go.dev`: plausible from practitioner configs, but the local `it` category degraded badly with these broad repo/package engines. Keep them out of the default enabled profile until per-query routing exists.

## MCP-Specific Conclusions

1. The global SearXNG profile should stay conservative.

The MCP's default `web_search` should continue to benefit from SearXNG's `general` category, where DuckDuckGo and Startpage performed well after removing the full default catalog.

2. Do not steer clients toward `categories=it` yet.

Live testing showed `categories=it` is not a reliable tool surface in the current setup. Before pruning, it returned noisy Docker/Sourcehut/MDN/Microsoft Learn matches. After pruning, it returned zero results for the exact pytest query. That is a signal to avoid advertising category-based coding search until the MCP owns query-time routing and evals.

3. Science category is worth keeping.

`categories=science` produced a useful multi-engine set with arXiv, Semantic Scholar, OpenAlex, PubMed. This is a good candidate for a future explicit MCP mode such as `search_mode="science"` or `searxng_profile="science"`.

4. Per-query engine/category routing should be implemented in the MCP, not by telling clients to guess raw SearXNG knobs.

Other practitioner tools expose `categories`, `engines`, `language`, and `time_range`, but this MCP should probably wrap that in safer presets:

- `default`: use general SearXNG profile only.
- `science`: send `categories=science`.
- `fresh`: prefer provider paths with time filters where supported.
- `exact_error`: keep default general engines, preserve quotes, disable rewrite unless explicitly requested.
- `package`: use default general until package-engine routing is proven useful; current direct package engines did not outperform Startpage/PyPI discovery.

5. The next code fix should be provider/category observability before expanding engines again.

We need per-result telemetry showing effective SearXNG category/engine request and returned engine distribution. Without that, adding engines becomes guesswork and failures look like generic relevance problems.

## Files Changed

- `searxng-settings/searxng-config/settings.yml`
- `searxng-settings/docker-compose.yml`
- `searxng-settings/README.md`
- `CHANGELOG.md`

## Recommended Next Implementation Work

1. Add explicit SearXNG request telemetry to the provider diagnostics: requested categories, requested engines, response engine distribution, latency, and suspension/errors if exposed.
2. Add safe MCP-level search profiles instead of exposing raw SearXNG categories directly.
3. Add a small live eval script for SearXNG profiles using the exact queries in this report.
4. Re-test Mojeek only after changing its access path; do not re-enable it just because practitioner configs mention it.
5. Keep the default SearXNG profile conservative until category/profile evals prove a broader profile is better.




