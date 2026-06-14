# AGENTS.md - Tools

This directory contains the MCP tool implementations exposed by the server.

## Structure

tools/
|-- __init__.py              # Tool exports
|-- web_search.py            # web_search tool - multi-provider search
|-- get_content.py           # get_content tool - single URL extraction
|-- gemini_search.py         # gemini_search tool - grounded answers with citations
|-- perplexity_search.py     # perplexity_search tool - deep AI research
|-- youtube_transcript.py    # youtube_transcript tool - video transcripts
-- youtube_search.py        # youtube_search tool - YouTube video results

## Tool Contracts

- **web_search** - Returns lightweight results only (title, link, snippet, provider_count) - no page_content
- **get_content** - Returns LLM-ready markdown for a single URL
- **perplexity_search** - Returns AI-synthesized answers with citations (Perplexity Sonar)
- **gemini_search** - Returns grounded answers with citations (Gemini + Google Search)
- **youtube_transcript** - Returns video transcripts with optional translation/formatting
- **youtube_search** - Returns YouTube video results via SearXNG YouTube engine

## Separation Principle
Search discovers, fetch extracts, AI search synthesizes. This separation is intentional.

## Testing
pytest tests/test_tool_descriptions.py tests/test_server.py -v
