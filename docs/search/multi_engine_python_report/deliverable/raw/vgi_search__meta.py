"""Shared per-object discovery/description metadata for the ``vgi-lint`` strict profile.

Every function and table the worker exposes must surface a consistent set of
discovery tags so agents and humans can find and understand it. The
``vgi-lint-check`` strict profile gates on:

- ``vgi.title`` (VGI124)        -- human-friendly display name (must not
  normalize-equal the machine name; carry an extra word).
- ``vgi.doc_llm`` (VGI112)      -- a Markdown narrative aimed at an LLM/agent
  (what it does, when to use it, inputs/outputs, edge cases).
- ``vgi.doc_md`` (VGI113)       -- a Markdown narrative for human docs (overview
  + usage + notes); DISTINCT content from ``vgi.doc_llm``.
- ``vgi.keywords`` (VGI138)     -- a JSON array of search terms/synonyms.

Per-object ``vgi.source_url`` is intentionally NOT emitted: VGI139 wants the
``source_url`` to live only on the catalog object (set via ``Catalog(source_url=...)``
in ``search_worker.py``), not duplicated on every function/schema.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vgi.metadata import FunctionExample


def example_queries_json(examples: Sequence[FunctionExample]) -> str:
    """Serialize function examples as a described ``vgi.example_queries`` JSON list (VGI515).

    The native ``duckdb_functions().examples`` carrier drops each example's
    description, so every function must ALSO publish a ``vgi.example_queries``
    tag: a JSON list of ``{"description", "sql"}`` objects. Building it from the
    same :class:`~vgi.metadata.FunctionExample` list the function already
    declares keeps the two byte-identical.

    Args:
        examples: The function's declared examples (each with ``sql`` + ``description``).

    Returns:
        A JSON array literal of ``{"description": ..., "sql": ...}`` objects.
    """
    return json.dumps([{"description": e.description, "sql": e.sql} for e in examples])


def _keywords_json(keywords: Sequence[str]) -> str:
    """Serialize search terms as a JSON array string (VGI138).

    Args:
        keywords: Search terms / synonyms.

    Returns:
        A JSON array literal like ``["web search","rag"]``.
    """
    return json.dumps(list(keywords))


def object_tags(
    *,
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: Sequence[str],
) -> dict[str, str]:
    """Build the standard per-object discovery/description tags.

    Args:
        title: Human-friendly display name (VGI124).
        doc_llm: Markdown narrative for an LLM/agent audience (VGI112).
        doc_md: Markdown narrative for human docs (VGI113); distinct from ``doc_llm``.
        keywords: Search terms / synonyms, emitted as a JSON array (VGI138).

    Returns:
        A tag mapping ready to merge into a function's ``Meta.tags``.
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": _keywords_json(keywords),
    }
