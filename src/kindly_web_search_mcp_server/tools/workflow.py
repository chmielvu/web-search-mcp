from __future__ import annotations


def get_workflow_doc() -> str:
    """Tool routing quick reference: which tool for what, and how to chain them."""
    return """# Tool Routing Reference

## Tool Selection
| Task | Tool | Key Parameters |
|---|---|---|
| Route a natural-language task | recommend_command | task |
| Fast recon | quick_web_search | objective, search_queries |
| Grounded synthesis | gemini_search | query, structured_output |
| Multi-provider discovery | web_search | query, research_goal, rewrite, domain_boost, num_results |
| Web + X/Twitter | grok_search | query, research_goal, allowed_domains, excluded_domains |
| Scholarly papers | academic_search | query, sources, year_from, year_to, fields_of_study, venue, sort |
| Read one or many URLs | fetch | url, urls, offset, cursor, ai_summary, focus_query, include_links |
| Search a cached repository | code_fetch | repository, query, max_matches, language, filename, path_glob, exclude_glob, case_sensitive, cursor |
| Inspect a cached file | code_fetch | repository, path, start_line, end_line |
| Discover links | discover_links | url, max_links, include_external, same_domain_only |
| Similar pages | composio_similarlinks | url |
| Find videos | youtube_search | query, num_results |
| Extract captions | youtube_transcript | video_id_or_url, language, translate_to, format, backend |
| Site map | generate_sitemap | url, instructions, max_depth, max_breadth, limit, select_paths, exclude_paths, allow_external |

## Query Parameters
- rewrite=true: LLM rewrites for recall (default for discovery)
- rewrite=false: exact literal search (errors, hashes, URLs, quoted phrases)
- num_results: 3=fast, 5=standard, 7=broad (max 10)

## Pagination
- fetch: single results use window.next_offset; bulk results use cursor when has_more
- discover_links: if has_more, call again with offset/limit
- code_fetch: pass query for repository-wide search — query returns match lines, so follow hits with path to read whole files. When has_more is true, continue with the returned next_cursor. Filters: --language, --filename, --path-glob, --exclude-glob, --case-sensitive. Pass path without query only for focused reads.

## AI Summaries
- ai_summary=false: return raw page content only (default)
- ai_summary=true: include a detailed source-grounded Gemini summary
- focus_query: bias summary toward a specific topic, term, or comparison

## Filter Parameters
- domain_boost: prioritize certain domains in ranking
- strip_selectors: CSS selectors to exclude from content extraction (e.g., "nav, footer")

## Diagostic Resources
- status://providers: which search providers are configured
- status://features: server feature flags and timeouts
- settings://public: current runtime settings (secrets redacted)
- analytics://schema: DuckDB observability schema
- analytics://reports/{report_name}?days=N: analytics reports (candidate-survival, etc.)
"""
