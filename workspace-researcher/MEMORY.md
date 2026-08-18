# workspace-researcher — Memory

## Research History

| Date | Topic | Task class | Tool bucket | Major gaps | Notable sources |
|---|---|---|---|---|---|
| 2026-08-17 | code_search MCP tool review vs. established public code search solutions (excl. self-hosted Sourcegraph/GitLab) | standard | web_search (GitHub/Sourcegraph/grep.app/searchcode/Exa/GitLab/livegrep/PublicWWW docs), local code reads, read (docs pages), scout_query_plan + scout_citation_audit | No quantitative retrieval benchmark; livegrep corpus size unverified; grep.app corpus from vendor claim only | docs.github.com code-search syntax & REST docs; sourcegraph.com/docs/api + stream-api; vercel.com blog (grep.app MCP); docs.exa.ai/reference/context; searchcode.com; docs.gitlab.com/api/search/; github/github-mcp-server README |
| 2026-08-17 | Reassessment: verified external "code_search workflow enabler" doc (claims vs 10 reference links) | standard | read (links: oh-my-pi, pydantic-deepagents CHANGELOG, grep-mcp, HN API, community discussion, exa blog, github-mcp README), local code cross-checks | Document's citations largely fabricated ([5] oh-my-pi, [9], [62], [32], [61] mismatched) though code-level claims accurate; grep.app symbol search and GitHub-MCP symbol: claims false; deep-hydration wiki obs stale (fixed in code) | reports/code_search_review_reassessment_2026-08-17.md; github.py:1130-1131; sourcegraph.py:303-327; query.py:529-538 |
