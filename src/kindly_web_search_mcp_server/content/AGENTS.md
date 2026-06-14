# AGENTS.md - Content Resolution

This directory implements the staged fallback pipeline for extracting LLM-ready markdown from URLs.

## Structure

content/
|-- resolver.py              # 7-stage fallback pipeline orchestrator
|-- stackexchange.py         # StackExchange API (full thread: Q + A + comments)
|-- github_issues.py         # GitHub Issues API (GraphQL)
|-- github_discussions.py    # GitHub Discussions API (GraphQL)
|-- wikipedia.py             # Wikipedia API (MediaWiki Action API)
|-- arxiv.py                 # arXiv (Atom API + PDF -> Markdown)
|-- http_extract.py          # HTTP extraction (trafilatura primary)
-- universal_html.py        # Universal HTML (nodriver headless browser)

## Pipeline Stages (in order)

1. **StackExchange API** - Full thread extraction for SO/SE sites
2. **GitHub Issues API** - GraphQL-based issue extraction
3. **GitHub Discussions API** - GraphQL-based discussion extraction
4. **Wikipedia API** - MediaWiki Action API
5. **arXiv** - Atom API + PDF to Markdown conversion
6. **HTTP Extraction** - trafilatura (fast, no browser)
7. **Universal HTML** - nodriver headless browser (JS-heavy sites)

## Key Patterns

### Adding a New Content Resolver
1. Create module in content/ with parse_x_url() and fetch_x_markdown()
2. Add import and handler stage in content/resolver.py
3. Write unit tests in tests/test_x.py mocking the API

### Fallback Behavior
- Each stage returns None if it cannot handle the URL
- Pipeline continues to next stage until one succeeds
- Universal HTML is the final catch-all for JS-rendered content

## Testing
pytest tests/test_page_content_resolver.py -v

## Conventions
- All resolvers return LLM-ready markdown with metadata
- Errors are logged but don't break the pipeline
- GITHUB_TOKEN recommended for better GitHub Issue extraction
