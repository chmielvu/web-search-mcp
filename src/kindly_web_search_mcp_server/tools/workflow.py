from __future__ import annotations


def get_workflow_doc() -> str:
    """Complete research workflow: tool routing, result evaluation, gap analysis, depth strategy."""
    return """# Web Search Workflow

## Reconnaissance
Start with `quick_web_search` for initial topic scoping before deeper research.

## Tool routing
| Task | Tool | Why |
|---|---|---|
| Initial recon | quick_web_search | Fast synthesized answer with citations |
| Find URLs | web_search | Multi-provider merge, provider_count signal |
| Web + X/Twitter | grok_search | Real-time web and social data |
| Scholarly papers | academic_search | 6 sources, field/venue/year filters |
| Read one URL | get_content | 7-stage resolution, pagination-aware, optional Gemini summary |
| Read 3+ URLs | batch_get_content | Parallel fetch, char budget, cursor, per-item Gemini summaries |
| Discover links | discover_links | Page/sitemap link extraction |
| Find videos | youtube_search | SearXNG YouTube engine |
| Extract captions | youtube_transcript | Timestamped/text/JSON, translation |
| Similar pages | composio_similarlinks | Neural similarity from known URL |
| Multi-step research | agentic_web_research | ReAct agent, experimental |

## Query
rewrite=true for normal discovery; rewrite=false for exact literals (errors, URLs, hashes).
num_results: 3=fast, 5=standard, 7=broad. Max 10.

## Depth
- quick: quick_web_search or gemini_search
- medium: web_search(5) -> batch_get_content(2-3) -> gemini_search
- deep: web_search(7) -> batch_get_content(5) -> academic_search

## Result evaluation
1. provider_count: 2+ stronger signal; 1 or missing = verify
2. Snippet quality: specific facts > generic text. Domain hints: github.com->issue/PR, stackoverflow.com->Q&A
3. Decision: 3+ promising -> batch_get_content. 1-2 -> get_content each. Off-topic -> refine. Sparse -> broaden

## Pagination
- get_content: check window.has_more. If true, call again with char_offset=window.next_offset
- get_content: summary_mode=brief|detailed adds a Gemini URL-context summary; use focus_query to bias it
- batch_get_content: check has_more and cursor. If true, call again with cursor
- batch_get_content: summary_mode=brief|detailed adds per-item Gemini summaries; use focus_query to bias them

## Gap analysis
- Factual gaps: unverified claims/dates/numbers
- Source gaps: only one type (blogs, no official docs)
- Depth gaps: check window.has_more and batch_get_content has_more
- Terminate when: 3 independent sources agree, 2 rounds with no new info, or depth budget exhausted

## Iteration
- Round 1: broad (num_results=5-7). Round 2: targeted (2-3 queries). Round 3: pinpoint (rewrite=false)
- Use composio_similarlinks on best URL from round 1
- For video: youtube_search -> pick best -> youtube_transcript

## Academic
academic_search first -> get_content on selected papers. Cross-check with 2+ independent papers. Separate surveys from implementation papers.

## Source triage
Official docs > GitHub issues/PRs > papers > community sources. Flag single-source claims. Prefer dated sources with concrete examples.
"""
