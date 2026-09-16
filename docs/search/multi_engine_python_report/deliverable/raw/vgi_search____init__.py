"""vgi-search: unified web search as DuckDB SQL functions.

Exposes ``web_search(query, provider, count, offset)`` (table function),
``web_answer(query, provider)`` (scalar), and ``search_providers()`` (discovery)
behind one pluggable provider surface (brave / tavily / exa / ddg / searxng, plus
flagged serpapi / serper). Completes the AI/RAG retrieval stack alongside
vgi-embed and vgi-rerank.
"""

from __future__ import annotations

__version__ = "0.1.0"
