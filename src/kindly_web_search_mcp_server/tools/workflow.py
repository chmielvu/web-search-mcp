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
| Multi-provider discovery | web_search | query, research_goal, rewrite, domain_boost, domain_block, num_results |
| Web + X/Twitter | grok_search | query, research_goal, allowed_domains, excluded_domains |
| Scholarly papers | academic_search | query, sources, year_from, year_to, fields_of_study, venue, sort |
| Read one URL | get_content | url, char_offset, char_length, ai_summary, focus_query, include_links |
| Read 3+ URLs | batch_get_content | urls, max_concurrency, per_item_char_length, total_char_budget, cursor, ai_summary |
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
- get_content: if window.has_more, call again with char_offset=window.next_offset
- batch_get_content: if has_more, call again with cursor from response
- discover_links: if has_more, call again with offset/limit

## AI Summaries
- ai_summary=false: return raw page content only (default)
- ai_summary=true: include a detailed source-grounded Gemini summary
- focus_query: bias summary toward a specific topic, term, or comparison

## Filter Parameters
- site_filters/domain_filters: restrict web_search results to specific domains
- domain_boost: prioritize certain domains in ranking
- domain_block: exclude domains entirely
- strip_selectors: CSS selectors to exclude from content extraction (e.g., "nav, footer")

## Diagostic Resources
- status://providers: which search providers are configured
- status://features: server feature flags and timeouts
- settings://public: current runtime settings (secrets redacted)
- analytics://schema: DuckDB observability schema
- analytics://reports/{report_name}?days=N: analytics reports (candidate-survival, etc.)
"""
