from __future__ import annotations


def get_workflow_doc() -> str:
    """Tool routing quick reference: which tool for what, and how to chain them."""
    return """# Tool Routing Reference

## Tool Selection
| Task | Tool | Key Parameters |
|---|---|---|
| Fast recon (web/YouTube/docs) | quick_web_search | mode, objective, search_queries, query, num_results, repo_url, question |
| Grounded synthesis | gemini_search | query, structured_output |
| Multi-provider discovery | web_search | query, research_goal, rewrite, domain_boost |
| Web + X/Twitter | grok_search | query, research_goal, allowed_domains, excluded_domains |
| Scholarly papers | academic_search | query, sources, year_from, year_to, fields_of_study, venue, sort |
| Read one or many URLs | fetch | url, urls, offset, cursor, ai_summary, focus_query, include_links (known URL contents, including GitHub file URLs) |
| Read a GitHub file | fetch | url (raw.githubusercontent.com or github.com blob URL), offset |
| Cross-repo code discovery | code_search | query, repositories, language, path, filename, regexp, mode, deep |
| Extract captions | youtube_transcript | video_id_or_url, language, translate_to, output_format, backend |
| Site map | generate_sitemap | url, instructions, max_depth, max_breadth, limit, select_paths, exclude_paths, allow_external |

## Query Parameters
- rewrite=true: LLM rewrites for recall (default for discovery)
- rewrite=false: exact literal search (errors, hashes, URLs, quoted phrases)

## Pagination
- fetch: single results use window.next_offset; bulk results use cursor when has_more
- GitHub files: pass the file URL to fetch; page long files with offset.

## Code Tool Boundary
- Known file URL, contents only → fetch (including GitHub file URLs). Cross-repo discovery → code_search, then follow `next` (repository hits route to fetch).

## AI Summaries
- ai_summary=false: return raw page content only (default)
- ai_summary=true: include a detailed source-grounded Gemini summary
- focus_query: bias summary toward a specific topic, term, or comparison

## Filter Parameters
- domain_boost: prioritize certain domains in ranking

## Diagostic Resources
- status://providers: which search providers are configured
- status://features: server feature flags and timeouts
- settings://public: current runtime settings (secrets redacted)
- analytics://schema: DuckDB observability schema
- analytics://reports/{report_name}?days=N: analytics reports (candidate-survival, etc.)
"""
