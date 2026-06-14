# AGENTS.md - Scraping

This directory implements web scraping capabilities for content extraction.

## Structure

scrape/
|-- universal_html.py        # nodriver-based browser extraction (JS-heavy sites)
|-- chromium_pool.py         # Pooled browser instances for reuse
-- http_extract.py          # trafilatura primary extraction (no browser)

## Components

### Universal HTML (universal_html.py)
- nodriver-based headless browser
- Handles JavaScript-rendered content
- Used as final fallback in content resolution pipeline

### Chromium Pool (chromium_pool.py)
- Pooled browser instances for performance
- Reuses browser contexts across extractions
- Configurable pool size

### HTTP Extract (http_extract.py)
- trafilatura-based extraction
- Fast, no browser overhead
- Primary extraction method for static content

## Key Patterns
- Browser pool is initialized on first use
- Extractions have configurable timeouts
- Errors fall back to next pipeline stage

## Testing
pytest tests/test_universal_html.py -v (if exists)
