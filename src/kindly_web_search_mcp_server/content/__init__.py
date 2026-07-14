"""Content acquisition, extraction, and conversion to LLM-ready Markdown.

Two-tier pipeline:
- Tier 1: Specialized resolvers (StackExchange, GitHub Issues/Discussions, Wikipedia, arXiv, Telegram) in content/resolvers/
- Tier 2: Generic extraction stages (Jina -> Crawl4AI /md -> local BS4 -> Camoufox last-resort)
"""
