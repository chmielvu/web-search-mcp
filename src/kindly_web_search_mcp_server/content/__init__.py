"""Content acquisition, rendering, and finalization to LLM-ready Markdown.

Pipeline:
- Resolver registry (content/resolver_registry.py): deterministic platform
  resolvers return neutral RawDocument candidates first.
- Generic cascade: Jina Reader -> Crawl4AI -> browser (Camoufox) -> Wayback archive.
- Shared MarkdownProcessor evaluation + sole finalize_artifact constructor.
"""
