"""Content acquisition, extraction, and conversion to LLM-ready Markdown.

Two-tier pipeline:
- Tier 1: Specialized resolvers (StackExchange, GitHub, Wikipedia, arXiv)
- Tier 2: Crawl4AI remote (primary) → fallback (Jina Reader → trafilatura)
"""
