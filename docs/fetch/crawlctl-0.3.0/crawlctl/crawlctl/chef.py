"""Chonkie MarkdownChef backend + combiners.

IMPORTANT (verified against chonkie 1.7.0 source, see RESEARCH.md):
``chonkie.MarkdownChef`` (v1.3.1+, 2025-09) is a *structural parser*, NOT a
cleaner. Its API is::

    MarkdownChef(tokenizer="character")
    .parse(text) -> MarkdownDocument(content, tables, code, images, chunks)

where tables/code/images carry char-offset spans (``start_index``/``end_index``)
over the preserved input text. It performs no syntax normalization, no link
debris removal and no cleaning of any kind (the prototype assumed
``cook/clean/sanitize`` methods — none exist in any chonkie release).

Therefore crawlctl integrates it as a *structure oracle*, not a bulk cleaner:

* ``native``        — deterministic full pipeline (default, zero extra deps)
* ``markdownchef``  — MarkdownChef.parse() -> table spans become hash-protected
  regions the native pipeline must not junk-drop; structure stats enrich the
  result. Full native cleaning still runs (Chonkie cleans nothing).
* ``auto``          — markdownchef when chonkie is importable, else native.

Known MarkdownChef weaknesses (validated in tests): 4+ backtick/tilde fences
yield None spans, so every span is validated (non-None, in-range, resolvable
text) and the adapter falls back gracefully on any mismatch.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .cleaner import CleanResult, CleaningOptions, block_hash, clean_markdown
from .siteprof import detect_content_type

_CHEF = None
_CHEF_TRIED = False


def _get_chef():
    """Lazy, defensive MarkdownChef singleton. Returns (chef, None) or (None, None)."""
    global _CHEF, _CHEF_TRIED
    if _CHEF_TRIED:
        return _CHEF, None
    _CHEF_TRIED = True
    if os.getenv("CRAWLCTL_DISABLE_CHONKIE", "0") == "1":
        return None, None
    try:
        from chonkie import MarkdownChef  # type: ignore

        _CHEF = MarkdownChef(tokenizer="character")
    except Exception:
        _CHEF = None
    return _CHEF, None


def _split_blocks(snippet: str) -> List[str]:
    return re.split(r"\n\s*\n", snippet)


def chef_structure(text: str) -> Tuple[Optional[dict], Optional[str]]:
    """Parse structure with MarkdownChef.

    Returns ({"n_tables", "n_code", "n_images", "protected_hashes"}, None)
    or (None, reason) when unavailable/unusable. Spans are validated against
    the input text; unusable spans are skipped, not fatal.
    """
    chef, _ = _get_chef()
    if chef is None:
        return None, "chonkie MarkdownChef unavailable"
    try:
        doc = chef.parse(text)
    except Exception as exc:
        return None, f"MarkdownChef.parse failed: {exc}"

    content = getattr(doc, "content", None)
    if not isinstance(content, str) or len(content) != len(text):
        return None, "MarkdownChef content mismatch (span offsets unreliable)"

    protected: set = set()
    n_tables = n_code = n_images = 0

    def _span_ok(obj) -> Optional[Tuple[int, int]]:
        s = getattr(obj, "start_index", None)
        e = getattr(obj, "end_index", None)
        if not isinstance(s, int) or not isinstance(e, int):
            return None
        if s < 0 or e <= s or e > len(text):
            return None
        return s, e

    for tbl in (getattr(doc, "tables", None) or []):
        n_tables += 1
        span = _span_ok(tbl)
        if not span:
            continue
        snippet = text[span[0]:span[1]]
        # protect every blank-line-delimited block inside the table span
        for part in _split_blocks(snippet):
            if part.strip():
                protected.add(block_hash(part))

    for code in (getattr(doc, "code", None) or []):
        n_code += 1
        _span_ok(code)  # code is already fence-masked natively; validation only

    for img in (getattr(doc, "images", None) or []):
        n_images += 1

    return ({"n_tables": n_tables, "n_code": n_code, "n_images": n_images,
             "protected_hashes": protected}, None)


def clean_with_backend(text: str, opts: CleaningOptions,
                       backend: str = "auto") -> CleanResult:
    """Clean markdown with the selected backend: native | markdownchef | auto."""
    backend = (backend or "auto").lower()
    if backend == "native":
        return clean_markdown(text, opts)
    if backend in ("markdownchef", "auto"):
        structure, reason = chef_structure(text)
        if structure is None:
            res = clean_markdown(text, opts)
            if backend == "markdownchef" and reason:
                res.warnings.append(f"markdownchef fallback: {reason}")
            res.structure["chef_used"] = False
            return res
        opts.protected_hashes = structure["protected_hashes"]
        res = clean_markdown(text, opts)
        res.structure.update(structure)
        res.structure["n_tables"] = structure["n_tables"]
        res.structure["chef_used"] = True
        return res
    raise ValueError(f"unknown cleaner backend: {backend}")


def backend_available(backend: str) -> bool:
    if backend == "native":
        return True
    if backend in ("markdownchef", "auto"):
        return _get_chef()[0] is not None
    return False


def backend_report() -> dict:
    return {
        "native": True,
        "markdownchef": backend_available("markdownchef"),
    }
