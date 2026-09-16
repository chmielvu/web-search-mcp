"""crawlctl — FastMCP server exposing a self-hosted Crawl4AI instance to AI agents.

Pipeline: scrape (Crawl4AI REST) -> extract (arbitrate) -> clean -> score ->
write RAG-ready Markdown documents with rich YAML front matter.

Chunking / indexing are intentionally out of scope; emitted files carry
title/url/score/hash/outline front matter so downstream indexers can ingest
without re-parsing.
"""

__version__ = "0.3.0"

from .client import Crawl4AIClient, ServerProfile, extract_markdown  # noqa: F401

__all__ = ["Crawl4AIClient", "ServerProfile", "extract_markdown", "__version__"]
