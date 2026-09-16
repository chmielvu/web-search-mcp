# Multi-Engine Web Search Aggregator Survey — README

**18 grassroots GitHub projects** that combine multiple web search engines for AI agents. Survey, deep-inspection, and pro tips.

## Files in this deliverable

```
MULTI_ENGINE_AGGREGATOR_REPORT.md   # Main report — start here
README.md                            # This file
raw/                                 # 76 verbatim source files from 13 deep-inspected repos
track/                               # Discovery + data-flow notes
```

## TL;DR of the report

Three fusion algorithms dominate the grassroots space, with RRF k=60 being the de-facto standard:

1. **Reciprocal Rank Fusion (k=60)** — Ketch, Argus, Gigaxity, Pi-Search-Hub
2. **First-success cascade + cooldown** — Hermes, DSH
3. **Consensus boost** — MetaSearchMCP

Plus a deeper rubric:
- **Bradley-Terry MLE with order-swapped double-judge** — Agentic-Search-Arena

The 18 actionable pro tips distilled from real production code are in §6 of the main report.

## Top 5 projects to read first

1. **`1broseidon/ketch`** — best Go reference; RRF is reproduced verbatim in `multi.go`
2. **`ihor-sokoliuk/mcp-searxng`** — best TS reference; env-var validation alone is worth the read
3. **`Khamel83/argus`** — best README-as-architecture-doc; tier routing is the clearest pattern
4. **`ncampy/hermes-web-multi-provider`** — best Python cascade design
5. **`gefsikatsinelou/MetaSearchMCP`** — cleanest Pydantic contract (`SearchHit`/`SearchEnvelope`/`SearchReport`)

## How this was built

1. **Authentication** — `GITHUB_TOKEN` (PAT, 5000 req/hr core + 30 search)
2. **Discovery** — `gh search` + PyGithub `search_repositories` with compound queries
3. **Source pull** — `raw.githubusercontent.com` (unauthenticated, fast) → 76 source files
4. **Deep inspection** — read each `README.md`, the canonical source file (provider.py / multi.go / search.ts), and the design doc (AGENTS.md / DESIGN.md) where present
5. **Synthesis** — extract verbatim code snippets, attribute to file:line, distill into 18 pro tips

Total raw source payload: ~430 KB across 76 files.

## Usage

Open `MULTI_ENGINE_AGGREGATOR_REPORT.md` and read top-down. Each section is anchored to a specific source repo.

## License

All raw source files are MIT-licensed per their repos (flagged in report where not). Verbatim prose excerpts ≤ 200 chars each. Synthesis © 2026 Mavis.
